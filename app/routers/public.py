"""Public, cross-origin endpoints: widget bundle, config, and submissions.

These three routes are the only ones the open internet touches. Everything
here assumes the caller is hostile until validated.
"""

import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import client_ip
from app.errors import AppError
from app.ratelimit import limiter
from app.schemas import PublicWidgetConfig, SubmissionAccepted, SubmissionCreate
from app.services import submission_service, widget_service
from app.spam import classify

logger = logging.getLogger(__name__)
router = APIRouter(tags=["public"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# One year, immutable: the version lives in the URL, so this file can never
# go stale — a new release ships a new URL instead of expiring an old one.
BUNDLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


def _bundle_source() -> str:
    return (STATIC_DIR / "widget.js").read_text(encoding="utf-8")


def _bundle_etag(source: str) -> str:
    return '"' + hashlib.sha256(source.encode("utf-8")).hexdigest()[:32] + '"'


# GET and HEAD: caches and proxies routinely probe an asset with HEAD.
@router.api_route("/embed/widget.{version}.js", methods=["GET", "HEAD"])
def serve_widget_bundle(
    version: str,
    if_none_match: str | None = Header(default=None),
) -> Response:
    """Versioned widget bundle. Cache forever; a new release is a new URL."""
    if version != settings.widget_bundle_version:
        raise AppError(
            f"unknown bundle version '{version}'; current is '{settings.widget_bundle_version}'",
            status_code=404,
        )

    source = _bundle_source()
    etag = _bundle_etag(source)
    headers = {
        "Cache-Control": BUNDLE_CACHE_CONTROL,
        "ETag": etag,
        "Access-Control-Allow-Origin": "*",
        "X-Widget-Version": version,
    }

    if if_none_match and if_none_match.strip() == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    return Response(
        content=source,
        media_type="application/javascript; charset=utf-8",
        headers=headers,
    )


@router.api_route(
    "/api/public/widgets/{public_id}/config",
    methods=["GET", "HEAD"],
    response_model=PublicWidgetConfig,
)
def widget_config(
    public_id: str,
    request: Request,
    if_none_match: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Response:
    """Small, short-lived cached config payload. ETag = the config version."""
    widget = widget_service.get_active_widget_by_public_id(db, public_id)

    origin = request.headers.get("origin")
    if not widget_service.origin_allowed(widget, origin):
        logger.warning("config.origin_rejected widget=%s origin=%s", public_id, origin)
        raise AppError("this origin is not allowed to embed this widget", status_code=403)

    etag = f'"{widget.public_id}-v{widget.config_version}"'
    headers = {
        "Cache-Control": f"public, max-age={settings.config_cache_max_age}",
        "ETag": etag,
        "Vary": "Origin",
    }

    if if_none_match and if_none_match.strip() == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    config = widget_service.public_config(widget)
    return JSONResponse(content=config.model_dump(), headers=headers)


@router.post(
    "/api/public/submissions",
    response_model=SubmissionAccepted,
    status_code=status.HTTP_201_CREATED,
)
async def create_submission(
    payload: SubmissionCreate,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> SubmissionAccepted:
    """The hardened public submission endpoint.

    Order matters: identify the widget, then rate limit, then spam, then
    per-widget validation, then store. Cheap rejections happen first.
    """
    ip = client_ip(request)
    origin = request.headers.get("origin")

    widget = widget_service.get_active_widget_by_public_id(db, payload.widget_id)

    if not widget_service.origin_allowed(widget, origin):
        logger.warning("submission.origin_rejected widget=%s origin=%s", widget.public_id, origin)
        raise AppError("this origin is not allowed to submit to this widget", status_code=403)

    decision = limiter.check(ip, widget.public_id)
    if not decision.allowed:
        logger.warning(
            "submission.rate_limited scope=%s ip=%s widget=%s", decision.scope, ip, widget.public_id
        )
        raise AppError(
            "too many requests — slow down",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"scope": decision.scope, "limit": decision.limit},
        )
    response.headers["X-RateLimit-Limit"] = str(decision.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)

    verdict = classify(payload.honeypot, payload.elapsed_ms)
    if verdict.is_spam:
        submission_service.record_spam(db, widget, ip, verdict.reason)
        # Same shape a real submission gets: the bot learns nothing.
        return SubmissionAccepted(id=None, message=widget.success_message)

    submission = await submission_service.create_submission(
        db,
        widget,
        payload,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
        referer=request.headers.get("referer"),
        origin=origin,
        idempotency_key=idempotency_key,
    )
    return SubmissionAccepted(id=submission.id, message=widget.success_message)
