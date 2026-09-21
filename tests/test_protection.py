"""PROBE 3 and PROBE 6 — rate limiting under a burst, and spam controls."""

from app.config import settings
from app.models import SpamEvent, Submission
from app.ratelimit import SlidingWindowLimiter, SubmissionRateLimiter

ORIGIN = {"Origin": "http://localhost:5500"}


def submit(client, widget, email="visitor@example.com", **extra):
    body = {"widget_id": widget.public_id, "data": {"email": email}}
    body.update(extra)
    return client.post("/api/public/submissions", headers=ORIGIN, json=body)


# ------------------------------------------------------------------ unit level
def test_sliding_window_allows_up_to_the_limit_then_refuses():
    limiter = SlidingWindowLimiter(limit=3, window_seconds=60)
    assert [limiter.check("k").allowed for _ in range(3)] == [True, True, True]

    refused = limiter.check("k")
    assert refused.allowed is False
    assert refused.retry_after >= 1


def test_buckets_are_independent_per_key():
    limiter = SlidingWindowLimiter(limit=1, window_seconds=60)
    assert limiter.check("ip:1.1.1.1").allowed is True
    assert limiter.check("ip:1.1.1.1").allowed is False
    assert limiter.check("ip:2.2.2.2").allowed is True


def test_ip_and_widget_buckets_are_checked_separately():
    limiter = SubmissionRateLimiter(ip_per_minute=100, widget_per_minute=2)
    assert limiter.check("1.1.1.1", "w1").allowed is True
    assert limiter.check("2.2.2.2", "w1").allowed is True
    # Different IPs, same widget: the widget bucket is what stops the third.
    blocked = limiter.check("3.3.3.3", "w1")
    assert blocked.allowed is False
    assert blocked.scope == "widget"


# ----------------------------------------------------------------- HTTP level
def test_burst_returns_429_and_the_service_keeps_serving(client, widget_a, db):
    settings.rate_limit_ip_per_minute = 5

    statuses = [submit(client, widget_a).status_code for _ in range(12)]

    assert statuses.count(201) == 5, statuses
    assert 429 in statuses, statuses

    limited = submit(client, widget_a)
    assert limited.status_code == 429
    assert limited.json()["error"]
    assert limited.json()["detail"]["scope"] == "ip"

    # The flood must not take the rest of the API down.
    assert client.get("/healthz").status_code == 200
    assert client.get(f"/api/public/widgets/{widget_a.public_id}/config").status_code == 200
    # And only the allowed requests were stored.
    assert db.query(Submission).count() == 5


def test_a_legitimate_request_from_another_ip_still_succeeds_during_a_flood(client, widget_a):
    settings.rate_limit_ip_per_minute = 3
    settings.rate_limit_widget_per_minute = 1000

    for _ in range(6):
        client.post(
            "/api/public/submissions",
            headers={**ORIGIN, "X-Forwarded-For": "203.0.113.9"},
            json={"widget_id": widget_a.public_id, "data": {"email": "flood@example.com"}},
        )

    innocent = client.post(
        "/api/public/submissions",
        headers={**ORIGIN, "X-Forwarded-For": "198.51.100.4"},
        json={"widget_id": widget_a.public_id, "data": {"email": "real@example.com"}},
    )
    assert innocent.status_code == 201


def test_rate_limit_headers_are_exposed(client, widget_a):
    settings.rate_limit_ip_per_minute = 10
    response = submit(client, widget_a)
    assert response.headers["x-ratelimit-limit"] == "10"
    assert response.headers["x-ratelimit-remaining"] == "9"


# ----------------------------------------------------------------------- spam
def test_filled_honeypot_is_silently_dropped(client, widget_a, db):
    response = submit(client, widget_a, honeypot="http://buy-cheap-pills.example")

    # Looks exactly like success to the bot...
    assert response.status_code == 201
    assert response.json()["status"] == "ok"
    assert response.json()["id"] is None

    # ...but nothing was stored.
    assert db.query(Submission).count() == 0
    spam = db.query(SpamEvent).one()
    assert spam.reason == "honeypot_filled"
    assert spam.widget_id == widget_a.id


def test_empty_honeypot_passes_through(client, widget_a, db):
    assert submit(client, widget_a, honeypot="").status_code == 201
    assert db.query(Submission).count() == 1


def test_implausibly_fast_fill_is_treated_as_spam(client, widget_a, db):
    settings.min_fill_seconds = 2.0

    assert submit(client, widget_a, elapsed_ms=120).status_code == 201
    assert db.query(Submission).count() == 0
    assert db.query(SpamEvent).one().reason == "submitted_too_fast"

    assert submit(client, widget_a, elapsed_ms=9000).status_code == 201
    assert db.query(Submission).count() == 1


def test_spam_blocked_count_reaches_the_dashboard(client, widget_a, headers_a):
    submit(client, widget_a, honeypot="bot")
    submit(client, widget_a, honeypot="bot")
    submit(client, widget_a)

    summary = client.get("/api/dashboard/summary", headers=headers_a).json()
    assert summary["spam_blocked"] == 2
    assert summary["total_submissions"] == 1
