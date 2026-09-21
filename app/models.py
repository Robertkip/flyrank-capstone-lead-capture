"""SQLAlchemy models — the persistence layer.

Every tenant-owned row carries ``tenant_id`` so isolation is enforced in the
query itself, never merely in the UI.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Tenant(Base):
    """A customer account. Owns widgets and, transitively, submissions."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    widgets: Mapped[list["Widget"]] = relationship(back_populates="tenant")


class Widget(Base):
    """A configurable embeddable widget belonging to exactly one tenant."""

    __tablename__ = "widgets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # Short, non-guessable public handle used in the embed snippet.
    public_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # signup_form | cta | popover
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    button_text: Mapped[str] = mapped_column(String(100), nullable=False, default="Submit")
    success_message: Mapped[str] = mapped_column(
        String(300), nullable=False, default="Thanks! We'll be in touch."
    )
    # [{"name": "email", "label": "Email", "type": "email", "required": true}, ...]
    fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # {"theme": "light", "position": "inline", "accent": "#2563eb"}
    display: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Empty list = any origin may embed. Non-empty = allow-list enforced server-side.
    allowed_origins: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    notify_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notify_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Bumped on every edit; used as the config ETag so caches bust on change.
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    tenant: Mapped[Tenant] = relationship(back_populates="widgets")

    __table_args__ = (Index("ix_widgets_tenant_id_created_at", "tenant_id", "created_at"),)


class Submission(Base):
    """One captured lead. ``tenant_id`` is denormalised for isolated reads."""

    __tablename__ = "submissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    widget_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("widgets.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    referer: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Enrichment result. geo_status: enriched | unavailable | skipped
    geo_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unavailable")
    geo_provider: Mapped[str | None] = mapped_column(String(48), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Same key + same widget => the stored row is returned instead of a duplicate.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_submissions_widget_id_created_at", "widget_id", "created_at"),
        Index("ix_submissions_tenant_id_created_at", "tenant_id", "created_at"),
        UniqueConstraint("widget_id", "idempotency_key", name="uq_submissions_widget_idem"),
    )


class OutboxEvent(Base):
    """A queued side effect, written in the same transaction as its submission.

    The request path never performs the side effect; a background worker drains
    this table with retries and dead-letters whatever will not succeed.
    """

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # pending | delivered | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_outbox_status_next_attempt", "status", "next_attempt_at"),)


class SpamEvent(Base):
    """Blocked submissions, kept for the dashboard's 'spam blocked' counter."""

    __tablename__ = "spam_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    widget_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("widgets.id", ondelete="CASCADE"), nullable=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(48), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_spam_events_tenant_created", "tenant_id", "created_at"),)
