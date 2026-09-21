"""The background worker that drains the outbox table.

Claiming uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so several workers can run
without ever handling the same event twice — that is where this design's
idempotency comes from. Each failure schedules an exponential-backoff retry;
after ``max_attempts`` the event is marked ``failed`` and an alert is logged.
"""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import OutboxEvent, utcnow
from app.outbox.handlers import handle

logger = logging.getLogger(__name__)


def claim_due_events(db: Session, limit: int = 20) -> list[OutboxEvent]:
    """Lock a batch of events that are pending and due, skipping locked rows."""
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.status == "pending", OutboxEvent.next_attempt_at <= utcnow())
        .order_by(OutboxEvent.next_attempt_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(db.execute(stmt).scalars().all())


def _backoff_delay(attempts: int) -> timedelta:
    return timedelta(seconds=settings.outbox_backoff_base_seconds ** max(1, attempts))


def process_event(db: Session, event: OutboxEvent) -> str:
    """Run one event's handler and record the outcome. Never raises."""
    event.attempts += 1
    try:
        result = handle(event.event_type, event.payload)
    except Exception as exc:
        event.last_error = f"{type(exc).__name__}: {exc}"
        if event.attempts >= event.max_attempts:
            event.status = "failed"
            # Dead letter. In production this is where a pager fires.
            logger.error(
                "ALERT outbox.dead_letter event_id=%s type=%s attempts=%s error=%s",
                event.id,
                event.event_type,
                event.attempts,
                event.last_error,
            )
            return "failed"

        event.next_attempt_at = utcnow() + _backoff_delay(event.attempts)
        logger.warning(
            "outbox.retry event_id=%s type=%s attempt=%s/%s next_attempt=%s error=%s",
            event.id,
            event.event_type,
            event.attempts,
            event.max_attempts,
            event.next_attempt_at.isoformat(),
            event.last_error,
        )
        return "retry"

    event.status = "delivered"
    event.delivered_at = utcnow()
    event.last_error = None
    logger.info(
        "outbox.delivered event_id=%s type=%s result=%s", event.id, event.event_type, result
    )
    return "delivered"


def process_due_events(db: Session, limit: int = 20) -> dict[str, int]:
    """Drain one batch. Returns a count per outcome."""
    counts = {"delivered": 0, "retry": 0, "failed": 0}
    for event in claim_due_events(db, limit=limit):
        counts[process_event(db, event)] += 1
    db.commit()
    return counts


class OutboxWorker:
    """Owns the polling loop; started and stopped by the app lifespan."""

    def __init__(self, poll_seconds: float | None = None) -> None:
        self.poll_seconds = poll_seconds or settings.outbox_poll_seconds
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def _loop(self) -> None:
        logger.info("outbox.worker_started poll_seconds=%s", self.poll_seconds)
        while not self._stopping.is_set():
            try:
                await asyncio.to_thread(self._drain_once)
            except Exception as exc:  # the loop itself must survive anything
                logger.exception("outbox.worker_iteration_failed error=%r", exc)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass
        logger.info("outbox.worker_stopped")

    def _drain_once(self) -> None:
        db = SessionLocal()
        try:
            counts = process_due_events(db)
            if any(counts.values()):
                logger.info("outbox.batch %s", counts)
        finally:
            db.close()

    def start(self) -> None:
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        finally:
            self._task = None
