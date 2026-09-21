"""Side-effect handlers invoked by the outbox worker.

Each handler either completes or raises. Raising is normal: the worker's job
is to retry, back off, and eventually dead-letter. Nothing in here runs on the
request path, so nothing in here can slow down or break a submission.
"""

import logging
import smtplib
from email.message import EmailMessage

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class SideEffectError(RuntimeError):
    """A side effect failed. The worker decides whether to retry."""


def _forced_failure_guard() -> None:
    """PROBE 5 switch: make every side effect throw, on purpose."""
    if settings.side_effect_force_fail:
        raise SideEffectError("SIDE_EFFECT_FORCE_FAIL=true — deliberate side-effect failure")


def send_confirmation_email(payload: dict) -> str:
    _forced_failure_guard()

    to_address = payload.get("notify_email")
    if not to_address:
        return "skipped: widget has no notify_email"

    subject = f"New submission on '{payload.get('widget_name', 'your widget')}'"
    lines = [f"{k}: {v}" for k, v in (payload.get("data") or {}).items()]
    geo = payload.get("geo") or {}
    if geo.get("city") or geo.get("country"):
        lines.append(f"location: {geo.get('city') or '?'}, {geo.get('country') or '?'}")
    body = "A new lead arrived.\n\n" + "\n".join(lines) + "\n"

    if settings.email_mode == "console":
        logger.info("email.console to=%s subject=%s\n%s", to_address, subject, body)
        return f"logged to console for {to_address}"

    message = EmailMessage()
    message["From"] = settings.email_from
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as smtp:
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise SideEffectError(f"smtp delivery failed: {exc}") from exc

    return f"sent via smtp to {to_address}"


def send_webhook(payload: dict) -> str:
    _forced_failure_guard()

    url = payload.get("notify_webhook_url")
    if not url:
        return "skipped: widget has no notify_webhook_url"

    try:
        response = httpx.post(
            url,
            json={
                "event": "submission.created",
                # The submission id doubles as the idempotency key a receiver
                # can use to ignore a duplicate delivery.
                "submission_id": payload.get("submission_id"),
                "widget_id": payload.get("widget_public_id"),
                "data": payload.get("data"),
                "geo": payload.get("geo"),
                "created_at": payload.get("created_at"),
            },
            timeout=5.0,
            headers={"Idempotency-Key": str(payload.get("submission_id"))},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SideEffectError(f"webhook delivery failed: {exc}") from exc

    return f"webhook delivered to {url} ({response.status_code})"


def handle(event_type: str, payload: dict) -> str:
    """Dispatch one outbox event. Returns a human-readable result string."""
    if event_type == "submission.email":
        return send_confirmation_email(payload)
    if event_type == "submission.webhook":
        return send_webhook(payload)
    raise SideEffectError(f"unknown event_type '{event_type}'")
