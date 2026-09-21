"""Aggregation queries for the owner dashboard. All tenant-scoped."""

import uuid
from datetime import timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import SpamEvent, Submission, Widget, utcnow


def _scoped(stmt: Select, tenant_id: uuid.UUID) -> Select:
    return stmt.where(Submission.tenant_id == tenant_id)


def list_submissions(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    widget_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Submission], int]:
    filters = [Submission.tenant_id == tenant_id]
    if widget_id is not None:
        filters.append(Submission.widget_id == widget_id)

    total = int(db.execute(select(func.count(Submission.id)).where(*filters)).scalar_one())
    rows = list(
        db.execute(
            select(Submission)
            .where(*filters)
            .order_by(Submission.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return rows, total


def _count_since(db: Session, tenant_id: uuid.UUID, days: float) -> int:
    since = utcnow() - timedelta(days=days)
    return int(
        db.execute(
            select(func.count(Submission.id)).where(
                Submission.tenant_id == tenant_id, Submission.created_at >= since
            )
        ).scalar_one()
    )


def summary(db: Session, tenant_id: uuid.UUID) -> dict:
    total_widgets = int(
        db.execute(
            select(func.count(Widget.id)).where(Widget.tenant_id == tenant_id)
        ).scalar_one()
    )
    active_widgets = int(
        db.execute(
            select(func.count(Widget.id)).where(
                Widget.tenant_id == tenant_id, Widget.active.is_(True)
            )
        ).scalar_one()
    )
    total_submissions = int(
        db.execute(
            select(func.count(Submission.id)).where(Submission.tenant_id == tenant_id)
        ).scalar_one()
    )
    enriched = int(
        db.execute(
            select(func.count(Submission.id)).where(
                Submission.tenant_id == tenant_id, Submission.geo_status == "enriched"
            )
        ).scalar_one()
    )
    spam_blocked = int(
        db.execute(
            select(func.count(SpamEvent.id)).where(SpamEvent.tenant_id == tenant_id)
        ).scalar_one()
    )

    per_widget = [
        {
            "widget_id": str(row.id),
            "public_id": row.public_id,
            "name": row.name,
            "active": row.active,
            "submissions": int(row.submissions or 0),
        }
        for row in db.execute(
            select(
                Widget.id,
                Widget.public_id,
                Widget.name,
                Widget.active,
                func.count(Submission.id).label("submissions"),
            )
            .select_from(Widget)
            .outerjoin(Submission, Submission.widget_id == Widget.id)
            .where(Widget.tenant_id == tenant_id)
            .group_by(Widget.id, Widget.public_id, Widget.name, Widget.active)
            .order_by(func.count(Submission.id).desc())
        ).all()
    ]

    return {
        "total_widgets": total_widgets,
        "active_widgets": active_widgets,
        "total_submissions": total_submissions,
        "submissions_last_24h": _count_since(db, tenant_id, 1),
        "submissions_last_7_days": _count_since(db, tenant_id, 7),
        "spam_blocked": spam_blocked,
        "enrichment_success_rate": round(enriched / total_submissions, 4)
        if total_submissions
        else 0.0,
        "per_widget": per_widget,
    }


def widget_stats(db: Session, tenant_id: uuid.UUID, widget: Widget, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)

    by_day = [
        {"date": row.day.date().isoformat(), "count": int(row.count)}
        for row in db.execute(
            select(
                func.date_trunc("day", Submission.created_at).label("day"),
                func.count(Submission.id).label("count"),
            )
            .where(
                Submission.tenant_id == tenant_id,
                Submission.widget_id == widget.id,
                Submission.created_at >= since,
            )
            .group_by("day")
            .order_by("day")
        ).all()
    ]

    by_country = [
        {
            "country": row.country,
            "country_code": row.country_code,
            "count": int(row.count),
        }
        for row in db.execute(
            select(
                Submission.country,
                Submission.country_code,
                func.count(Submission.id).label("count"),
            )
            .where(Submission.tenant_id == tenant_id, Submission.widget_id == widget.id)
            .group_by(Submission.country, Submission.country_code)
            .order_by(func.count(Submission.id).desc())
        ).all()
    ]

    total = int(
        db.execute(
            select(func.count(Submission.id)).where(
                Submission.tenant_id == tenant_id, Submission.widget_id == widget.id
            )
        ).scalar_one()
    )
    last_7 = int(
        db.execute(
            select(func.count(Submission.id)).where(
                Submission.tenant_id == tenant_id,
                Submission.widget_id == widget.id,
                Submission.created_at >= utcnow() - timedelta(days=7),
            )
        ).scalar_one()
    )
    spam = int(
        db.execute(
            select(func.count(SpamEvent.id)).where(SpamEvent.widget_id == widget.id)
        ).scalar_one()
    )

    return {
        "widget_id": widget.id,
        "public_id": widget.public_id,
        "name": widget.name,
        "total_submissions": total,
        "submissions_last_7_days": last_7,
        "spam_blocked": spam,
        "by_day": by_day,
        "by_country": by_country,
    }
