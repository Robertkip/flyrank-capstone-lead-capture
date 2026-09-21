"""Submission business logic — the hardened path, in order.

    validate (done at the boundary) -> rate limit -> spam -> enrich
    -> store + queue side effects (one transaction) -> respond

Enrichment and side effects are the two places a dependency can be down. Both
degrade: enrichment records "unavailable", side effects are queued rather than
performed, so neither can turn a good submission into a failure.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.enrichment import enrich_ip
from app.errors import AppError
from app.models import OutboxEvent, SpamEvent, Submission, Widget
from app.schemas import SubmissionCreate

logger = logging.getLogger(__name__)


def validate_against_widget(widget: Widget, data: dict) -> dict:
    """Check the payload against *this widget's* declared fields.

    Unknown keys are rejected rather than ignored, so a customer's dashboard
    never fills up with fields they did not configure.
    """
    declared = {f["name"]: f for f in (widget.fields or [])}
    errors: list[dict] = []

    for key in data:
        if key not in declared:
            errors.append({"field": key, "message": "unknown field for this widget"})

    cleaned: dict = {}
    for name, spec in declared.items():
        value = data.get(name)
        is_blank = value is None or (isinstance(value, str) and value.strip() == "")

        if spec.get("required") and is_blank:
            errors.append({"field": name, "message": "this field is required"})
            continue
        if is_blank:
            continue

        text = str(value).strip()
        if len(text) > settings.max_field_length:
            errors.append(
                {"field": name, "message": f"must be at most {settings.max_field_length} characters"}
            )
            continue

        if spec.get("type") == "email" and ("@" not in text or "." not in text.split("@")[-1]):
            errors.append({"field": name, "message": "must be a valid email address"})
            continue
        if spec.get("type") == "number":
            try:
                float(text)
            except ValueError:
                errors.append({"field": name, "message": "must be a number"})
                continue
        if spec.get("type") == "url" and not text.startswith(("http://", "https://")):
            errors.append({"field": name, "message": "must start with http:// or https://"})
            continue

        cleaned[name] = text

    if errors:
        raise AppError("validation_failed", status_code=422, detail=errors)

    return cleaned


def record_spam(db: Session, widget: Widget, ip: str | None, reason: str) -> None:
    db.add(SpamEvent(widget_id=widget.id, tenant_id=widget.tenant_id, reason=reason, ip=ip))
    db.commit()
    logger.info("submission.spam_blocked widget=%s reason=%s ip=%s", widget.public_id, reason, ip)


def find_by_idempotency_key(
    db: Session, widget_id: uuid.UUID, key: str | None
) -> Submission | None:
    if not key:
        return None
    stmt = select(Submission).where(
        Submission.widget_id == widget_id, Submission.idempotency_key == key
    )
    return db.execute(stmt).scalar_one_or_none()


def _side_effect_payload(widget: Widget, submission: Submission) -> dict:
    return {
        "submission_id": str(submission.id),
        "widget_public_id": widget.public_id,
        "widget_name": widget.name,
        "notify_email": widget.notify_email,
        "notify_webhook_url": widget.notify_webhook_url,
        "data": submission.data,
        "geo": {
            "status": submission.geo_status,
            "provider": submission.geo_provider,
            "country": submission.country,
            "country_code": submission.country_code,
            "region": submission.region,
            "city": submission.city,
        },
        "created_at": submission.created_at.isoformat() if submission.created_at else None,
    }


async def create_submission(
    db: Session,
    widget: Widget,
    payload: SubmissionCreate,
    *,
    ip: str | None,
    user_agent: str | None,
    referer: str | None,
    origin: str | None,
    idempotency_key: str | None = None,
) -> Submission:
    """Store one submission and queue its side effects atomically."""
    cleaned = validate_against_widget(widget, payload.data)

    existing = find_by_idempotency_key(db, widget.id, idempotency_key)
    if existing is not None:
        logger.info("submission.idempotent_replay id=%s key=%s", existing.id, idempotency_key)
        return existing

    # Enrichment happens before the write so the row lands complete, but its
    # failure is already absorbed inside enrich_ip — it cannot raise.
    geo_status, geo = await enrich_ip(ip)

    submission = Submission(
        widget_id=widget.id,
        tenant_id=widget.tenant_id,
        data=cleaned,
        ip=ip,
        user_agent=user_agent,
        referer=referer,
        origin=origin,
        geo_status=geo_status,
        geo_provider=geo.provider if geo else None,
        country=geo.country if geo else None,
        country_code=geo.country_code if geo else None,
        region=geo.region if geo else None,
        city=geo.city if geo else None,
        idempotency_key=idempotency_key,
    )
    db.add(submission)

    try:
        db.flush()
    except IntegrityError:
        # Two concurrent requests carried the same idempotency key; the loser
        # returns the row the winner stored.
        db.rollback()
        duplicate = find_by_idempotency_key(db, widget.id, idempotency_key)
        if duplicate is not None:
            return duplicate
        raise

    # Same transaction as the submission: if the row is stored, the side effect
    # is queued; if the row is not, nothing is queued. No lost work, no ghosts.
    effect_payload = _side_effect_payload(widget, submission)
    if widget.notify_email:
        db.add(
            OutboxEvent(
                submission_id=submission.id,
                event_type="submission.email",
                payload=effect_payload,
                max_attempts=settings.outbox_max_attempts,
            )
        )
    if widget.notify_webhook_url:
        db.add(
            OutboxEvent(
                submission_id=submission.id,
                event_type="submission.webhook",
                payload=effect_payload,
                max_attempts=settings.outbox_max_attempts,
            )
        )

    db.commit()
    db.refresh(submission)
    logger.info(
        "submission.stored id=%s widget=%s geo_status=%s provider=%s",
        submission.id,
        widget.public_id,
        submission.geo_status,
        submission.geo_provider,
    )
    return submission
