"""Authenticated widget CRUD. Every route is scoped to the caller's tenant."""

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_tenant
from app.models import Tenant, Widget
from app.schemas import WidgetCreate, WidgetOut, WidgetUpdate
from app.services import widget_service

router = APIRouter(prefix="/api/widgets", tags=["widgets"])


def _to_out(widget: Widget) -> WidgetOut:
    out = WidgetOut.model_validate(widget)
    out.embed_snippet = widget_service.embed_snippet(widget.public_id)
    out.config_url = widget_service.config_url(widget.public_id)
    return out


@router.post("", response_model=WidgetOut, status_code=status.HTTP_201_CREATED)
def create_widget(
    payload: WidgetCreate,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> WidgetOut:
    return _to_out(widget_service.create_widget(db, tenant.id, payload))


@router.get("", response_model=list[WidgetOut])
def list_widgets(
    tenant: Tenant = Depends(current_tenant), db: Session = Depends(get_db)
) -> list[WidgetOut]:
    return [_to_out(w) for w in widget_service.list_widgets(db, tenant.id)]


@router.get("/{widget_id}", response_model=WidgetOut)
def get_widget(
    widget_id: uuid.UUID,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> WidgetOut:
    return _to_out(widget_service.get_widget_for_tenant(db, tenant.id, widget_id))


@router.patch("/{widget_id}", response_model=WidgetOut)
def update_widget(
    widget_id: uuid.UUID,
    payload: WidgetUpdate,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> WidgetOut:
    return _to_out(widget_service.update_widget(db, tenant.id, widget_id, payload))


@router.delete("/{widget_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_widget(
    widget_id: uuid.UUID,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> Response:
    widget_service.delete_widget(db, tenant.id, widget_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{widget_id}/embed", response_model=dict)
def get_embed_snippet(
    widget_id: uuid.UUID,
    tenant: Tenant = Depends(current_tenant),
    db: Session = Depends(get_db),
) -> dict:
    widget = widget_service.get_widget_for_tenant(db, tenant.id, widget_id)
    return {
        "public_id": widget.public_id,
        "embed_snippet": widget_service.embed_snippet(widget.public_id),
        "config_url": widget_service.config_url(widget.public_id),
        "submit_url": widget_service.submit_url(),
    }
