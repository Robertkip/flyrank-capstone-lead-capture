"""Auth is required, and one tenant can never reach another tenant's data."""

from tests.conftest import make_widget

CROSS_ORIGIN = {"Origin": "http://localhost:5500"}


def test_register_then_login_issues_a_token(client):
    register = client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "name": "New Co", "password": "a-good-password"},
    )
    assert register.status_code == 201
    assert register.json()["token_type"] == "bearer"

    login = client.post(
        "/api/auth/login", json={"email": "new@example.com", "password": "a-good-password"}
    )
    assert login.status_code == 200
    assert login.json()["access_token"]


def test_duplicate_email_is_rejected(client):
    body = {"email": "dupe@example.com", "name": "Dupe", "password": "a-good-password"}
    assert client.post("/api/auth/register", json=body).status_code == 201
    conflict = client.post("/api/auth/register", json=body)
    assert conflict.status_code == 409
    assert conflict.json()["error"]


def test_wrong_password_is_401_with_a_generic_message(client, tenant_a):
    response = client.post(
        "/api/auth/login", json={"email": tenant_a.email, "password": "not-the-password"}
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid email or password"


def test_widget_routes_reject_missing_and_invalid_auth(client):
    assert client.get("/api/widgets").status_code == 401
    assert client.get("/api/widgets", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.get("/api/dashboard/summary").status_code == 401


def test_crud_lifecycle(client, headers_a):
    created = client.post(
        "/api/widgets",
        headers=headers_a,
        json={
            "name": "Newsletter",
            "title": "Join us",
            "fields": [{"name": "email", "label": "Email", "type": "email", "required": True}],
        },
    )
    assert created.status_code == 201
    widget = created.json()
    assert widget["public_id"]
    assert widget["config_version"] == 1
    assert f'?id={widget["public_id"]}' in widget["embed_snippet"]

    listed = client.get("/api/widgets", headers=headers_a)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    patched = client.patch(
        f"/api/widgets/{widget['id']}", headers=headers_a, json={"title": "Join the list"}
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Join the list"
    # A config change must bust the cache.
    assert patched.json()["config_version"] == 2

    assert client.delete(f"/api/widgets/{widget['id']}", headers=headers_a).status_code == 204
    assert client.get(f"/api/widgets/{widget['id']}", headers=headers_a).status_code == 404


def test_invalid_widget_payload_is_422_not_500(client, headers_a):
    # No fields at all.
    assert client.post(
        "/api/widgets", headers=headers_a, json={"name": "x", "title": "y", "fields": []}
    ).status_code == 422

    # Unknown top-level key.
    assert client.post(
        "/api/widgets",
        headers=headers_a,
        json={"name": "x", "title": "y", "fields": [{"name": "email", "label": "E"}], "sneaky": 1},
    ).status_code == 422

    # A field name colliding with the honeypot is refused.
    bad = client.post(
        "/api/widgets",
        headers=headers_a,
        json={"name": "x", "title": "y", "fields": [{"name": "website_url", "label": "Site"}]},
    )
    assert bad.status_code == 422


def test_tenant_b_cannot_read_or_modify_tenant_a_widgets(client, headers_b, widget_a):
    assert client.get(f"/api/widgets/{widget_a.id}", headers=headers_b).status_code == 404
    assert client.patch(
        f"/api/widgets/{widget_a.id}", headers=headers_b, json={"title": "hijacked"}
    ).status_code == 404
    assert client.delete(f"/api/widgets/{widget_a.id}", headers=headers_b).status_code == 404
    assert client.get("/api/widgets", headers=headers_b).json() == []


def test_tenant_b_cannot_see_tenant_a_submissions(client, db, headers_a, headers_b, tenant_a):
    widget = make_widget(db, tenant_a, name="A's widget")
    posted = client.post(
        "/api/public/submissions",
        headers=CROSS_ORIGIN,
        json={"widget_id": widget.public_id, "data": {"email": "lead@example.com"}},
    )
    assert posted.status_code == 201

    assert client.get("/api/dashboard/submissions", headers=headers_a).json()["total"] == 1
    assert client.get("/api/dashboard/submissions", headers=headers_b).json()["total"] == 0

    # Filtering by another tenant's widget id is refused, not silently empty.
    assert client.get(
        f"/api/dashboard/submissions?widget_id={widget.id}", headers=headers_b
    ).status_code == 404
    assert client.get(
        f"/api/dashboard/widgets/{widget.id}/stats", headers=headers_b
    ).status_code == 404
