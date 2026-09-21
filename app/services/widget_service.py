"""Widget business logic.

Every read and write is scoped by ``tenant_id`` in the query itself. A tenant
asking for another tenant's widget gets a 404 — the same answer as a widget
that does not exist, so the API never confirms that someone else's id is real.
"""

import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import AppError
from app.models import Widget
from app.schemas import PublicWidgetConfig, WidgetCreate, WidgetUpdate

PUBLIC_ID_BYTES = 8


def generate_public_id(db: Session) -> str:
    """A short, unguessable handle. Retries on the astronomically rare clash."""
    for _ in range(5):
        candidate = secrets.token_hex(PUBLIC_ID_BYTES)
        exists = db.execute(
            select(Widget.id).where(Widget.public_id == candidate)
        ).scalar_one_or_none()
        if exists is None:
            return candidate
    raise AppError("could not allocate a widget id", status_code=500)


def embed_snippet(public_id: str) -> str:
    return (
        f'<script src="{settings.base_url}/embed/widget.'
        f'{settings.widget_bundle_version}.js?id={public_id}" async></script>'
    )


def config_url(public_id: str) -> str:
    return f"{settings.base_url}/api/public/widgets/{public_id}/config"


def submit_url() -> str:
    return f"{settings.base_url}/api/public/submissions"


def create_widget(db: Session, tenant_id: uuid.UUID, payload: WidgetCreate) -> Widget:
    widget = Widget(
        tenant_id=tenant_id,
        public_id=generate_public_id(db),
        name=payload.name,
        type=payload.type,
        title=payload.title,
        description=payload.description,
        button_text=payload.button_text,
        success_message=payload.success_message,
        fields=[f.model_dump() for f in payload.fields],
        display=payload.display,
        allowed_origins=payload.allowed_origins,
        notify_webhook_url=payload.notify_webhook_url,
        notify_email=payload.notify_email,
        active=payload.active,
        config_version=1,
    )
    db.add(widget)
    db.commit()
    db.refresh(widget)
    return widget


def list_widgets(db: Session, tenant_id: uuid.UUID) -> list[Widget]:
    stmt = (
        select(Widget).where(Widget.tenant_id == tenant_id).order_by(Widget.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def get_widget_for_tenant(db: Session, tenant_id: uuid.UUID, widget_id: uuid.UUID) -> Widget:
    stmt = select(Widget).where(Widget.id == widget_id, Widget.tenant_id == tenant_id)
    widget = db.execute(stmt).scalar_one_or_none()
    if widget is None:
        raise AppError("widget not found", status_code=404)
    return widget


def update_widget(
    db: Session, tenant_id: uuid.UUID, widget_id: uuid.UUID, payload: WidgetUpdate
) -> Widget:
    widget = get_widget_for_tenant(db, tenant_id, widget_id)
    changes = payload.model_dump(exclude_unset=True)

    if "fields" in changes and changes["fields"] is not None:
        changes["fields"] = [dict(f) for f in changes["fields"]]

    for key, value in changes.items():
        setattr(widget, key, value)

    if changes:
        # New version => new config ETag => caches refetch instead of serving stale.
        widget.config_version += 1

    db.commit()
    db.refresh(widget)
    return widget


def delete_widget(db: Session, tenant_id: uuid.UUID, widget_id: uuid.UUID) -> None:
    widget = get_widget_for_tenant(db, tenant_id, widget_id)
    db.delete(widget)
    db.commit()


def get_active_widget_by_public_id(db: Session, public_id: str) -> Widget:
    """Public lookup used by the config and submission endpoints."""
    stmt = select(Widget).where(Widget.public_id == public_id)
    widget = db.execute(stmt).scalar_one_or_none()
    if widget is None:
        raise AppError("widget not found", status_code=404)
    if not widget.active:
        raise AppError("widget is not active", status_code=404)
    return widget


def public_config(widget: Widget) -> PublicWidgetConfig:
    return PublicWidgetConfig(
        id=widget.public_id,
        version=widget.config_version,
        type=widget.type,
        title=widget.title,
        description=widget.description,
        button_text=widget.button_text,
        success_message=widget.success_message,
        fields=[
            {
                "name": f.get("name"),
                "label": f.get("label"),
                "type": f.get("type", "text"),
                "required": bool(f.get("required", False)),
                "placeholder": f.get("placeholder"),
            }
            for f in widget.fields
        ],
        display=widget.display or {},
        honeypot_field=settings.honeypot_field,
        submit_url=submit_url(),
    )


def origin_allowed(widget: Widget, origin: str | None) -> bool:
    """An empty allow-list means the widget may be embedded anywhere."""
    allowed = widget.allowed_origins or []
    if not allowed:
        return True
    return origin is not None and origin.rstrip("/") in [a.rstrip("/") for a in allowed]


def count_widgets(db: Session, tenant_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.count(Widget.id)).where(Widget.tenant_id == tenant_id)
        ).scalar_one()
    )
