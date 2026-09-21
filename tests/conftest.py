"""Test fixtures.

Every test runs against a real Postgres schema (created once, truncated between
tests) so the JSONB columns, indexes and FOR UPDATE SKIP LOCKED behaviour under
test are the same ones production uses.
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings

# Point the app at the test database before anything imports the engine.
settings.database_url = os.getenv("TEST_DATABASE_URL", settings.test_database_url)
settings.outbox_worker_enabled = False  # tests drive the worker explicitly
settings.geo_mode = "mock"
settings.email_mode = "console"

import app.db as app_db  # noqa: E402

app_db.engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
app_db.SessionLocal = sessionmaker(bind=app_db.engine, autoflush=False, expire_on_commit=False)

from app.db import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Tenant, Widget  # noqa: E402
from app.ratelimit import limiter  # noqa: E402
from app.schemas import WidgetCreate, WidgetField  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import widget_service  # noqa: E402

TABLES = ["outbox_events", "spam_events", "submissions", "widgets", "tenants"]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(bind=app_db.engine)
    yield
    Base.metadata.drop_all(bind=app_db.engine)


@pytest.fixture(autouse=True)
def _clean_state():
    with app_db.engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))

    limiter.reset()
    settings.rate_limit_enabled = True
    settings.rate_limit_ip_per_minute = 30
    settings.rate_limit_widget_per_minute = 120
    settings.geo_mock_a_up = True
    settings.geo_mock_b_up = True
    settings.side_effect_force_fail = False
    settings.min_fill_seconds = 1.5
    yield


@pytest.fixture
def db():
    session = app_db.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _make_tenant(db, email: str) -> Tenant:
    tenant = Tenant(email=email, name=email.split("@")[0], password_hash=hash_password("password123"))
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@pytest.fixture
def tenant_a(db) -> Tenant:
    return _make_tenant(db, f"a-{uuid.uuid4().hex[:8]}@example.com")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return _make_tenant(db, f"b-{uuid.uuid4().hex[:8]}@example.com")


def auth_header(client: TestClient, email: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def headers_a(client, tenant_a) -> dict:
    return auth_header(client, tenant_a.email)


@pytest.fixture
def headers_b(client, tenant_b) -> dict:
    return auth_header(client, tenant_b.email)


def make_widget(db, tenant: Tenant, **overrides) -> Widget:
    payload = WidgetCreate(
        name=overrides.pop("name", "Newsletter"),
        title=overrides.pop("title", "Join us"),
        fields=overrides.pop(
            "fields",
            [
                WidgetField(name="email", label="Email", type="email", required=True),
                WidgetField(name="first_name", label="First name", type="text"),
            ],
        ),
        **overrides,
    )
    return widget_service.create_widget(db, tenant.id, payload)


@pytest.fixture
def widget_a(db, tenant_a) -> Widget:
    return make_widget(db, tenant_a)


@pytest.fixture
def widget_b(db, tenant_b) -> Widget:
    return make_widget(db, tenant_b, name="Globex waitlist", title="Early access")
