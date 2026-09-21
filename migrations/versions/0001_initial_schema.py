"""initial schema: tenants, widgets, submissions, outbox_events, spam_events

Revision ID: 0001
Revises:
Create Date: 2026-08-29
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tenants_email", "tenants", ["email"], unique=True)

    op.create_table(
        "widgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("public_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("button_text", sa.String(100), nullable=False, server_default="Submit"),
        sa.Column("success_message", sa.String(300), nullable=False, server_default="Thanks!"),
        sa.Column("fields", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("display", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("allowed_origins", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("notify_webhook_url", sa.Text(), nullable=True),
        sa.Column("notify_email", sa.String(255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_widgets_public_id", "widgets", ["public_id"], unique=True)
    op.create_index("ix_widgets_tenant_id_created_at", "widgets", ["tenant_id", "created_at"])

    op.create_table(
        "submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "widget_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("widgets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("referer", sa.Text(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=True),
        sa.Column("geo_status", sa.String(24), nullable=False, server_default="unavailable"),
        sa.Column("geo_provider", sa.String(48), nullable=True),
        sa.Column("country", sa.String(80), nullable=True),
        sa.Column("country_code", sa.String(8), nullable=True),
        sa.Column("region", sa.String(120), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_submissions_widget_id_created_at", "submissions", ["widget_id", "created_at"]
    )
    op.create_index(
        "ix_submissions_tenant_id_created_at", "submissions", ["tenant_id", "created_at"]
    )
    op.create_unique_constraint(
        "uq_submissions_widget_idem", "submissions", ["widget_id", "idempotency_key"]
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_outbox_status_next_attempt", "outbox_events", ["status", "next_attempt_at"]
    )

    op.create_table(
        "spam_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "widget_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("widgets.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(48), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_spam_events_tenant_created", "spam_events", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_table("spam_events")
    op.drop_table("outbox_events")
    op.drop_table("submissions")
    op.drop_table("widgets")
    op.drop_table("tenants")
