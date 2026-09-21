"""Liveness and readiness."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)) -> dict:
    db.execute(select(1))
    return {
        "status": "ready",
        "database": "ok",
        "geo_mode": settings.geo_mode,
        "outbox_worker": settings.outbox_worker_enabled,
    }
