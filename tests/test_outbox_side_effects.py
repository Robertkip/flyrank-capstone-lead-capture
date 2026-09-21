"""PROBE 5 — a failing side effect never stops a submission being stored.

Also covers the background job's retry, backoff and dead-letter behaviour.
"""

from datetime import timedelta

from app.config import settings
from app.models import OutboxEvent, Submission, utcnow
from app.outbox.worker import process_due_events
from app.services import widget_service
from tests.conftest import make_widget

ORIGIN = {"Origin": "http://localhost:5500"}


def submit(client, widget, email="lead@example.com"):
    return client.post(
        "/api/public/submissions",
        headers=ORIGIN,
        json={"widget_id": widget.public_id, "data": {"email": email}},
    )


def widget_with_email(db, tenant):
    return make_widget(db, tenant, notify_email="owner@example.com")


# ------------------------------------------------- the request path is safe
def test_side_effect_is_queued_not_performed_on_the_request_path(client, db, tenant_a):
    widget = widget_with_email(db, tenant_a)

    assert submit(client, widget).status_code == 201

    event = db.query(OutboxEvent).one()
    assert event.event_type == "submission.email"
    assert event.status == "pending", "the request path must not perform the side effect"
    assert event.attempts == 0
    assert event.submission_id == db.query(Submission).one().id


def test_failing_side_effect_does_not_prevent_storage(client, db, tenant_a):
    """PROBE 5, exactly as the brief words it."""
    settings.side_effect_force_fail = True
    widget = widget_with_email(db, tenant_a)

    response = submit(client, widget, "must-survive@example.com")

    assert response.status_code == 201
    assert response.json()["status"] == "ok"

    stored = db.query(Submission).one()
    assert stored.data == {"email": "must-survive@example.com"}

    # Now let the worker try, and fail, repeatedly. The submission is untouched.
    for _ in range(settings.outbox_max_attempts + 1):
        event = db.query(OutboxEvent).one()
        event.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
        process_due_events(db)

    db.expire_all()
    assert db.query(OutboxEvent).one().status == "failed"
    assert db.query(Submission).count() == 1


def test_no_outbox_event_when_the_widget_has_no_notifications(client, db, tenant_a, widget_a):
    assert submit(client, widget_a).status_code == 201
    assert db.query(OutboxEvent).count() == 0


def test_both_email_and_webhook_events_are_queued(client, db, tenant_a):
    widget = make_widget(
        db,
        tenant_a,
        notify_email="owner@example.com",
        notify_webhook_url="http://localhost:9/hook",
    )
    submit(client, widget)

    types = {e.event_type for e in db.query(OutboxEvent).all()}
    assert types == {"submission.email", "submission.webhook"}


# ------------------------------------------------------ the worker's behaviour
def test_worker_delivers_a_healthy_event(client, db, tenant_a):
    settings.email_mode = "console"
    widget = widget_with_email(db, tenant_a)
    submit(client, widget)

    counts = process_due_events(db)

    assert counts["delivered"] == 1
    event = db.query(OutboxEvent).one()
    assert event.status == "delivered"
    assert event.delivered_at is not None
    assert event.attempts == 1
    assert event.last_error is None


def test_worker_retries_with_growing_backoff_before_giving_up(client, db, tenant_a):
    settings.side_effect_force_fail = True
    widget = widget_with_email(db, tenant_a)
    submit(client, widget)

    event = db.query(OutboxEvent).one()
    assert event.max_attempts == settings.outbox_max_attempts

    delays = []
    for _ in range(settings.outbox_max_attempts - 1):
        before = utcnow()
        event.next_attempt_at = before - timedelta(seconds=1)
        db.commit()

        counts = process_due_events(db)
        assert counts["retry"] == 1

        db.refresh(event)
        assert event.status == "pending"
        assert event.last_error
        delays.append((event.next_attempt_at - before).total_seconds())

    # Backoff must grow, not stay flat.
    assert delays == sorted(delays)
    assert delays[-1] > delays[0]

    # The final attempt dead-letters.
    event.next_attempt_at = utcnow() - timedelta(seconds=1)
    db.commit()
    counts = process_due_events(db)

    assert counts["failed"] == 1
    db.refresh(event)
    assert event.status == "failed"
    assert event.attempts == settings.outbox_max_attempts


def test_worker_ignores_events_that_are_not_due_yet(client, db, tenant_a):
    widget = widget_with_email(db, tenant_a)
    submit(client, widget)

    event = db.query(OutboxEvent).one()
    event.next_attempt_at = utcnow() + timedelta(hours=1)
    db.commit()

    assert process_due_events(db) == {"delivered": 0, "retry": 0, "failed": 0}
    db.refresh(event)
    assert event.status == "pending"


def test_a_delivered_event_is_never_processed_twice(client, db, tenant_a):
    widget = widget_with_email(db, tenant_a)
    submit(client, widget)

    assert process_due_events(db)["delivered"] == 1
    # Second drain finds nothing: idempotent delivery, no duplicate emails.
    assert process_due_events(db) == {"delivered": 0, "retry": 0, "failed": 0}
    assert db.query(OutboxEvent).one().attempts == 1


def test_webhook_to_an_unreachable_host_is_retried_not_fatal(client, db, tenant_a):
    widget = make_widget(
        db, tenant_a, notify_webhook_url="http://127.0.0.1:9/does-not-exist"
    )
    assert submit(client, widget).status_code == 201

    counts = process_due_events(db)

    assert counts["retry"] == 1
    event = db.query(OutboxEvent).one()
    assert event.status == "pending"
    assert "webhook delivery failed" in event.last_error
    assert db.query(Submission).count() == 1


def test_embed_snippet_points_at_the_versioned_bundle(widget_a):
    snippet = widget_service.embed_snippet(widget_a.public_id)
    assert f"/embed/widget.{settings.widget_bundle_version}.js" in snippet
    assert f"?id={widget_a.public_id}" in snippet
    assert snippet.startswith("<script") and snippet.endswith("</script>")
