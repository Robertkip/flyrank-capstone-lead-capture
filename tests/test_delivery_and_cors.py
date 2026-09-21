"""Widget delivery: cache headers, versioned bundle, CORS and preflight."""

from app.config import settings

CROSS_ORIGIN = "http://localhost:5500"


def test_bundle_is_versioned_and_cached_forever(client):
    response = client.get(f"/embed/widget.{settings.widget_bundle_version}.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]

    cache_control = response.headers["cache-control"]
    assert "max-age=31536000" in cache_control
    assert "immutable" in cache_control
    assert response.headers["etag"]
    # Readable from any origin — that is the point of an embeddable bundle.
    assert response.headers["access-control-allow-origin"] == "*"


def test_bundle_revalidates_with_etag(client):
    first = client.get(f"/embed/widget.{settings.widget_bundle_version}.js")
    second = client.get(
        f"/embed/widget.{settings.widget_bundle_version}.js",
        headers={"If-None-Match": first.headers["etag"]},
    )
    assert second.status_code == 304


def test_unknown_bundle_version_is_404(client):
    assert client.get("/embed/widget.v999.js").status_code == 404


def test_config_is_small_public_and_short_lived(client, widget_a):
    response = client.get(
        f"/api/public/widgets/{widget_a.public_id}/config", headers={"Origin": CROSS_ORIGIN}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == f"public, max-age={settings.config_cache_max_age}"
    assert response.headers["etag"] == f'"{widget_a.public_id}-v1"'
    assert response.headers["access-control-allow-origin"] == "*"
    assert len(response.content) < 2048, "config payload should stay small"

    body = response.json()
    assert body["id"] == widget_a.public_id
    assert body["honeypot_field"] == settings.honeypot_field
    assert body["submit_url"].endswith("/api/public/submissions")
    # No tenant data may leak to the public internet.
    assert "tenant_id" not in body
    assert "notify_email" not in body


def test_config_etag_changes_when_the_widget_changes(client, headers_a, db, tenant_a):
    from tests.conftest import make_widget

    widget = make_widget(db, tenant_a)
    first = client.get(f"/api/public/widgets/{widget.public_id}/config")
    assert client.get(
        f"/api/public/widgets/{widget.public_id}/config",
        headers={"If-None-Match": first.headers["etag"]},
    ).status_code == 304

    client.patch(f"/api/widgets/{widget.id}", headers=headers_a, json={"title": "Changed"})

    stale = client.get(
        f"/api/public/widgets/{widget.public_id}/config",
        headers={"If-None-Match": first.headers["etag"]},
    )
    assert stale.status_code == 200, "an edited widget must not serve a 304"
    assert stale.json()["title"] == "Changed"


def test_inactive_and_unknown_widgets_are_404(client, db, headers_a, widget_a):
    assert client.get("/api/public/widgets/does-not-exist/config").status_code == 404

    client.patch(f"/api/widgets/{widget_a.id}", headers=headers_a, json={"active": False})
    assert client.get(f"/api/public/widgets/{widget_a.public_id}/config").status_code == 404


def test_preflight_is_answered_for_the_submission_endpoint(client):
    response = client.options(
        "/api/public/submissions",
        headers={
            "Origin": CROSS_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,idempotency-key",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "content-type" in allowed_headers
    assert "idempotency-key" in allowed_headers


def test_cross_origin_post_carries_cors_headers(client, widget_a):
    response = client.post(
        "/api/public/submissions",
        headers={"Origin": CROSS_ORIGIN},
        json={"widget_id": widget_a.public_id, "data": {"email": "visitor@example.com"}},
    )
    assert response.status_code == 201
    assert response.headers["access-control-allow-origin"] == "*"


def test_per_widget_origin_allowlist_is_enforced(client, db, tenant_a):
    from tests.conftest import make_widget

    widget = make_widget(db, tenant_a, allowed_origins=["http://allowed.example"])

    blocked = client.post(
        "/api/public/submissions",
        headers={"Origin": "http://evil.example"},
        json={"widget_id": widget.public_id, "data": {"email": "a@b.co"}},
    )
    assert blocked.status_code == 403

    allowed = client.post(
        "/api/public/submissions",
        headers={"Origin": "http://allowed.example"},
        json={"widget_id": widget.public_id, "data": {"email": "a@b.co"}},
    )
    assert allowed.status_code == 201


def test_head_works_on_cache_facing_assets(client, widget_a):
    """Caches and proxies probe assets with HEAD; 405 would break them."""
    bundle = client.head(f"/embed/widget.{settings.widget_bundle_version}.js")
    assert bundle.status_code == 200
    assert "max-age=31536000" in bundle.headers["cache-control"]

    config = client.head(f"/api/public/widgets/{widget_a.public_id}/config")
    assert config.status_code == 200
    assert config.headers["etag"] == f'"{widget_a.public_id}-v1"'
