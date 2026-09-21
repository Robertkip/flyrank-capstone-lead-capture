"""PROBE 2 — malformed and oversized payloads get clean 4xx JSON, never a 500."""

import json

from app.config import settings

ORIGIN = {"Origin": "http://localhost:5500"}


def post(client, body, **kwargs):
    return client.post("/api/public/submissions", headers=ORIGIN, json=body, **kwargs)


def test_valid_submission_is_stored(client, widget_a, db):
    from app.models import Submission

    response = post(
        client,
        {
            "widget_id": widget_a.public_id,
            "data": {"email": "visitor@example.com", "first_name": "Vee"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ok"
    assert body["id"]

    stored = db.query(Submission).one()
    assert stored.data == {"email": "visitor@example.com", "first_name": "Vee"}
    assert stored.widget_id == widget_a.id
    assert stored.tenant_id == widget_a.tenant_id


def test_missing_required_field_is_422(client, widget_a):
    response = post(client, {"widget_id": widget_a.public_id, "data": {"first_name": "Vee"}})
    assert response.status_code == 422
    assert response.json()["error"] == "validation_failed"
    assert any(d["field"] == "email" for d in response.json()["detail"])


def test_bad_email_is_422(client, widget_a):
    response = post(client, {"widget_id": widget_a.public_id, "data": {"email": "not-an-email"}})
    assert response.status_code == 422
    assert response.json()["detail"][0]["field"] == "email"


def test_unknown_field_is_rejected(client, widget_a):
    response = post(
        client,
        {"widget_id": widget_a.public_id, "data": {"email": "a@b.co", "is_admin": "true"}},
    )
    assert response.status_code == 422
    assert any(d["field"] == "is_admin" for d in response.json()["detail"])


def test_malformed_json_is_4xx_not_500(client, widget_a):
    broken_json = '{"widget_id": "abc", "data": {'
    response = client.post(
        "/api/public/submissions",
        headers={**ORIGIN, "Content-Type": "application/json"},
        content=broken_json.encode(),
    )
    assert 400 <= response.status_code < 500
    assert response.json()["error"]


def test_missing_widget_id_is_422(client):
    response = post(client, {"data": {"email": "a@b.co"}})
    assert response.status_code == 422


def test_unknown_widget_is_404(client):
    response = post(client, {"widget_id": "0123456789abcdef", "data": {"email": "a@b.co"}})
    assert response.status_code == 404


def test_extra_top_level_keys_are_refused(client, widget_a):
    response = post(
        client,
        {"widget_id": widget_a.public_id, "data": {"email": "a@b.co"}, "tenant_id": "hijack"},
    )
    assert response.status_code == 422


def test_nested_structures_in_data_are_refused(client, widget_a):
    response = post(client, {"widget_id": widget_a.public_id, "data": {"email": {"ne": None}}})
    assert response.status_code == 422


def test_oversized_payload_is_413(client, widget_a):
    huge = {
        "widget_id": widget_a.public_id,
        "data": {"email": "a@b.co", "first_name": "x" * (settings.max_body_bytes + 5000)},
    }
    response = client.post(
        "/api/public/submissions",
        headers={**ORIGIN, "Content-Type": "application/json"},
        content=json.dumps(huge).encode(),
    )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


def test_oversized_single_field_under_body_limit_is_422(client, widget_a):
    response = post(
        client,
        {
            "widget_id": widget_a.public_id,
            "data": {"email": "a@b.co", "first_name": "x" * (settings.max_field_length + 10)},
        },
    )
    assert response.status_code == 422


def test_no_endpoint_returns_500_for_hostile_input(client, widget_a):
    hostile = [
        {"widget_id": widget_a.public_id, "data": {"email": "a@b.co "}},
        {"widget_id": widget_a.public_id, "data": {"email": "<script>alert(1)</script>@b.co"}},
        {"widget_id": "'; DROP TABLE submissions; --", "data": {"email": "a@b.co"}},
        {"widget_id": widget_a.public_id, "data": {"email": "a@b.co"}, "elapsed_ms": -1},
        {"widget_id": widget_a.public_id, "data": None},
    ]
    for body in hostile:
        response = post(client, body)
        assert response.status_code < 500, f"{body} produced {response.status_code}"


def test_idempotency_key_stores_one_row_for_a_retry(client, widget_a, db):
    from app.models import Submission

    body = {"widget_id": widget_a.public_id, "data": {"email": "retry@example.com"}}
    headers = {**ORIGIN, "Idempotency-Key": "client-generated-key-1"}

    first = client.post("/api/public/submissions", headers=headers, json=body)
    second = client.post("/api/public/submissions", headers=headers, json=body)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert db.query(Submission).count() == 1


def test_malformed_json_reports_body_not_a_byte_offset(client):
    response = client.post(
        "/api/public/submissions",
        headers={**ORIGIN, "Content-Type": "application/json"},
        content='{"widget_id": "abc", "data": {'.encode(),
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["field"] == "body"
