"""PROBE 1's second half — a stored submission is visible via the dashboard API."""

from app.config import settings
from tests.conftest import make_widget

ORIGIN = {"Origin": "http://localhost:5500"}


def submit(client, widget, email):
    return client.post(
        "/api/public/submissions",
        headers=ORIGIN,
        json={"widget_id": widget.public_id, "data": {"email": email}},
    )


def test_submission_from_a_second_origin_is_visible_to_its_owner(client, widget_a, headers_a):
    posted = submit(client, widget_a, "probe1@example.com")
    assert posted.status_code == 201
    submission_id = posted.json()["id"]

    page = client.get("/api/dashboard/submissions", headers=headers_a)
    assert page.status_code == 200
    body = page.json()

    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == submission_id
    assert item["data"] == {"email": "probe1@example.com"}
    assert item["country"] == "Kenya"
    assert item["geo_status"] == "enriched"


def test_submissions_are_paginated_and_newest_first(client, widget_a, headers_a):
    settings.rate_limit_ip_per_minute = 100
    for i in range(5):
        submit(client, widget_a, f"lead{i}@example.com")

    page = client.get("/api/dashboard/submissions?limit=2&offset=0", headers=headers_a).json()
    assert page["total"] == 5
    assert page["limit"] == 2
    assert len(page["items"]) == 2
    assert page["items"][0]["data"]["email"] == "lead4@example.com"

    second = client.get("/api/dashboard/submissions?limit=2&offset=2", headers=headers_a).json()
    assert second["items"][0]["data"]["email"] == "lead2@example.com"


def test_summary_aggregates_across_widgets(client, db, tenant_a, headers_a):
    settings.rate_limit_ip_per_minute = 100
    first = make_widget(db, tenant_a, name="First")
    second = make_widget(db, tenant_a, name="Second")

    submit(client, first, "a@example.com")
    submit(client, first, "b@example.com")
    submit(client, second, "c@example.com")

    summary = client.get("/api/dashboard/summary", headers=headers_a).json()

    assert summary["total_widgets"] == 2
    assert summary["active_widgets"] == 2
    assert summary["total_submissions"] == 3
    assert summary["submissions_last_24h"] == 3
    assert summary["submissions_last_7_days"] == 3
    assert summary["enrichment_success_rate"] == 1.0

    counts = {w["name"]: w["submissions"] for w in summary["per_widget"]}
    assert counts == {"First": 2, "Second": 1}


def test_enrichment_success_rate_reflects_a_dead_provider_chain(client, widget_a, headers_a):
    submit(client, widget_a, "enriched@example.com")

    settings.geo_mock_a_up = False
    settings.geo_mock_b_up = False
    submit(client, widget_a, "not-enriched@example.com")

    summary = client.get("/api/dashboard/summary", headers=headers_a).json()
    assert summary["total_submissions"] == 2
    assert summary["enrichment_success_rate"] == 0.5


def test_widget_stats_bucket_by_day_and_country(client, widget_a, headers_a):
    settings.rate_limit_ip_per_minute = 100
    submit(client, widget_a, "one@example.com")

    settings.geo_mock_a_up = False  # second lead comes from Germany via provider B
    submit(client, widget_a, "two@example.com")

    stats = client.get(
        f"/api/dashboard/widgets/{widget_a.id}/stats", headers=headers_a
    ).json()

    assert stats["public_id"] == widget_a.public_id
    assert stats["total_submissions"] == 2
    assert stats["submissions_last_7_days"] == 2
    assert len(stats["by_day"]) == 1
    assert stats["by_day"][0]["count"] == 2

    countries = {row["country"]: row["count"] for row in stats["by_country"]}
    assert countries == {"Kenya": 1, "Germany": 1}


def test_empty_dashboard_does_not_divide_by_zero(client, headers_a):
    summary = client.get("/api/dashboard/summary", headers=headers_a).json()
    assert summary["total_submissions"] == 0
    assert summary["enrichment_success_rate"] == 0.0


def test_health_endpoints(client):
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"
