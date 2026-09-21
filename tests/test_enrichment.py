"""PROBE 4 — the geo fallback chain degrades, never fails."""

import pytest

from app.config import settings
from app.enrichment import enrich_ip
from app.enrichment.chain import ENRICHED, SKIPPED, UNAVAILABLE
from app.enrichment.providers import GeoProvider, GeoResult, ProviderError
from app.models import Submission

ORIGIN = {"Origin": "http://localhost:5500"}


class AlwaysFails(GeoProvider):
    name = "always-fails"

    async def lookup(self, ip):
        raise ProviderError("upstream is down")


class Crashes(GeoProvider):
    """A provider that misbehaves in an unplanned way, not a polite ProviderError."""

    name = "crashes"

    async def lookup(self, ip):
        raise RuntimeError("unexpected explosion")


class Answers(GeoProvider):
    name = "answers"

    def __init__(self):
        self.calls = 0

    async def lookup(self, ip):
        self.calls += 1
        return GeoResult(self.name, "Kenya", "KE", "Nairobi County", "Nairobi")


# ------------------------------------------------------------------ unit level
async def test_provider_a_answers_and_b_is_never_called():
    a, b = Answers(), Answers()
    a.name, b.name = "provider-a", "provider-b"

    status, result = await enrich_ip("8.8.8.8", [a, b])

    assert status == ENRICHED
    assert result.provider == "provider-a"
    assert b.calls == 0, "provider B must not be called when A succeeds"


async def test_provider_a_down_falls_through_to_b():
    b = Answers()
    b.name = "provider-b"

    status, result = await enrich_ip("8.8.8.8", [AlwaysFails(), b])

    assert status == ENRICHED
    assert result.provider == "provider-b"
    assert b.calls == 1


async def test_a_crashing_provider_does_not_break_the_chain():
    b = Answers()
    status, result = await enrich_ip("8.8.8.8", [Crashes(), b])
    assert status == ENRICHED
    assert result.provider == "answers"


async def test_all_providers_down_returns_unavailable_and_never_raises():
    status, result = await enrich_ip("8.8.8.8", [AlwaysFails(), Crashes()])
    assert status == UNAVAILABLE
    assert result is None


async def test_private_ip_is_skipped_when_no_dev_fallback_is_configured():
    original = settings.geo_dev_fallback_ip
    settings.geo_dev_fallback_ip = ""
    try:
        status, result = await enrich_ip("127.0.0.1", [Answers()])
        assert status == SKIPPED
        assert result is None
    finally:
        settings.geo_dev_fallback_ip = original


# ----------------------------------------------------- end-to-end via the API
def test_submission_is_enriched_by_mock_provider_a(client, widget_a, db):
    settings.geo_mock_a_up = True
    settings.geo_mock_b_up = True

    assert client.post(
        "/api/public/submissions",
        headers=ORIGIN,
        json={"widget_id": widget_a.public_id, "data": {"email": "a@b.co"}},
    ).status_code == 201

    stored = db.query(Submission).one()
    assert stored.geo_status == "enriched"
    assert stored.geo_provider == "mock-provider-a"
    assert stored.country == "Kenya"
    assert stored.city == "Nairobi"


def test_provider_a_down_submission_enriched_by_provider_b(client, widget_a, db):
    settings.geo_mock_a_up = False
    settings.geo_mock_b_up = True

    assert client.post(
        "/api/public/submissions",
        headers=ORIGIN,
        json={"widget_id": widget_a.public_id, "data": {"email": "a@b.co"}},
    ).status_code == 201

    stored = db.query(Submission).one()
    assert stored.geo_status == "enriched"
    assert stored.geo_provider == "mock-provider-b"
    assert stored.country == "Germany"


def test_both_providers_down_submission_still_succeeds_without_geo(client, widget_a, db):
    settings.geo_mock_a_up = False
    settings.geo_mock_b_up = False

    response = client.post(
        "/api/public/submissions",
        headers=ORIGIN,
        json={"widget_id": widget_a.public_id, "data": {"email": "survivor@example.com"}},
    )
    assert response.status_code == 201, "a dead enrichment chain must not fail the submission"

    stored = db.query(Submission).one()
    assert stored.geo_status == "unavailable"
    assert stored.geo_provider is None
    assert stored.country is None
    # The lead itself — the thing that actually matters — is intact.
    assert stored.data == {"email": "survivor@example.com"}


@pytest.mark.parametrize(
    "a_up,b_up,expected_provider",
    [(True, True, "mock-provider-a"), (False, True, "mock-provider-b"), (False, False, None)],
)
def test_fallback_matrix(client, widget_a, db, a_up, b_up, expected_provider):
    settings.geo_mock_a_up = a_up
    settings.geo_mock_b_up = b_up

    client.post(
        "/api/public/submissions",
        headers=ORIGIN,
        json={"widget_id": widget_a.public_id, "data": {"email": "matrix@example.com"}},
    )

    stored = db.query(Submission).one()
    assert stored.geo_provider == expected_provider
