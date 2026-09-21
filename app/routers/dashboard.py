"""Authenticated owner dashboard: submissions and aggregate stats."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_tenant
from app.models import Tenant
from app.schemas import DashboardSummary, SubmissionOut, SubmissionPage, WidgetStats
from app.services import dashboard_service, widget_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def summary(
    tenant: Tenant = Depends(current_tenant), db: Session = Depends(get_db)
) -> DashboardSummary:
    return DashboardSummary(**dashboard_service.summary(db, tenant.id))


@router.get("/submissions", response_model=SubmissionPage)
def list_submissions(
    widget_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> SubmissionPage:
    if widget_id is not None:
        # Proves the widget belongs to this tenant before it can filter by it.
        widget_service.get_widget_for_tenant(db, tenant.id, widget_id)

    rows, total = dashboard_service.list_submissions(
        db, tenant.id, widget_id=widget_id, limit=limit, offset=offset
    )
    return SubmissionPage(
        items=[SubmissionOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/widgets/{widget_id}/stats", response_model=WidgetStats)
def widget_stats(
    widget_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=365),
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> WidgetStats:
    widget = widget_service.get_widget_for_tenant(db, tenant.id, widget_id)
    return WidgetStats(**dashboard_service.widget_stats(db, tenant.id, widget, days=days))
