"""The two real geo providers, tested at the HTTP boundary.

The mock providers prove the *chain*; these prove the *parsing* — that each
provider reads the real API's response shape correctly, and turns each of that
API's failure shapes into a ProviderError rather than a crash or a bad row.

Responses below are the actual payload shapes ip-api.com and ipapi.co return.
Mocked with respx so the suite stays deterministic and needs no network — the
free tiers are rate-limited and would otherwise make these tests flaky.
"""

import httpx
import pytest
import respx

from app.enrichment.chain import ENRICHED, UNAVAILABLE, enrich_ip
from app.enrichment.providers import IpApiComProvider, IpapiCoProvider, ProviderError

IP = "8.8.8.8"

IP_API_SUCCESS = {
    "status": "success",
    "country": "United States",
    "countryCode": "US",
    "regionName": "Virginia",
    "city": "Ashburn",
}
IP_API_FAIL = {"status": "fail", "message": "private range"}

IPAPI_CO_SUCCESS = {
    "ip": "8.8.8.8",
    "city": "Mountain View",
    "region": "California",
    "country_code": "US",
    "country_name": "United States",
}
IPAPI_CO_RATE_LIMITED = {"error": True, "reason": "RateLimited"}


# ------------------------------------------------------------- provider A
@respx.mock
async def test_ip_api_parses_a_successful_response():
    respx.get(url__startswith="http://ip-api.com/json/").mock(
        return_value=httpx.Response(200, json=IP_API_SUCCESS)
    )

    result = await IpApiComProvider().lookup(IP)

    assert result.provider == "ip-api.com"
    assert result.country == "United States"
    assert result.country_code == "US"
    assert result.region == "Virginia"
    assert result.city == "Ashburn"


@respx.mock
async def test_ip_api_treats_a_200_with_status_fail_as_an_error():
    """ip-api.com signals failure in the body, not the status code."""
    respx.get(url__startswith="http://ip-api.com/json/").mock(
        return_value=httpx.Response(200, json=IP_API_FAIL)
    )

    with pytest.raises(ProviderError, match="private range"):
        await IpApiComProvider().lookup(IP)


@respx.mock
async def test_ip_api_raises_on_http_error_and_on_junk_body():
    respx.get(url__startswith="http://ip-api.com/json/").mock(
        return_value=httpx.Response(503, text="upstream down")
    )
    with pytest.raises(ProviderError):
        await IpApiComProvider().lookup(IP)

    respx.get(url__startswith="http://ip-api.com/json/").mock(
        return_value=httpx.Response(200, text="<html>not json</html>")
    )
    with pytest.raises(ProviderError):
        await IpApiComProvider().lookup(IP)


@respx.mock
async def test_ip_api_raises_on_timeout():
    respx.get(url__startswith="http://ip-api.com/json/").mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )
    with pytest.raises(ProviderError):
        await IpApiComProvider().lookup(IP)


# ------------------------------------------------------------- provider B
@respx.mock
async def test_ipapi_co_parses_a_successful_response():
    respx.get(url__startswith="https://ipapi.co/").mock(
        return_value=httpx.Response(200, json=IPAPI_CO_SUCCESS)
    )

    result = await IpapiCoProvider().lookup(IP)

    assert result.provider == "ipapi.co"
    # Note the different key names from provider A — this is the mapping that
    # would otherwise only be exercised in production.
    assert result.country == "United States"
    assert result.country_code == "US"
    assert result.region == "California"
    assert result.city == "Mountain View"


@respx.mock
async def test_ipapi_co_treats_a_200_with_error_true_as_an_error():
    """The real free tier answers 200 + {"error": true} when rate limited."""
    respx.get(url__startswith="https://ipapi.co/").mock(
        return_value=httpx.Response(200, json=IPAPI_CO_RATE_LIMITED)
    )

    with pytest.raises(ProviderError, match="RateLimited"):
        await IpapiCoProvider().lookup(IP)


@respx.mock
async def test_ipapi_co_raises_on_429_status():
    """And sometimes it rate-limits with a real 429 instead — as it did live."""
    respx.get(url__startswith="https://ipapi.co/").mock(
        return_value=httpx.Response(429, text="Too Many Requests")
    )

    with pytest.raises(ProviderError):
        await IpapiCoProvider().lookup(IP)


# ------------------------------------------------- both, wired as a real chain
@respx.mock
async def test_live_chain_falls_through_from_a_to_b():
    respx.get(url__startswith="http://ip-api.com/json/").mock(
        return_value=httpx.Response(500, text="boom")
    )
    respx.get(url__startswith="https://ipapi.co/").mock(
        return_value=httpx.Response(200, json=IPAPI_CO_SUCCESS)
    )

    status, result = await enrich_ip(IP, [IpApiComProvider(), IpapiCoProvider()])

    assert status == ENRICHED
    assert result.provider == "ipapi.co"
    assert result.city == "Mountain View"


@respx.mock
async def test_live_chain_degrades_when_both_real_providers_fail():
    """Exactly what happened against the real APIs: A dead, B rate-limited."""
    respx.get(url__startswith="http://ip-api.com/json/").mock(
        side_effect=httpx.ConnectError("no route to host")
    )
    respx.get(url__startswith="https://ipapi.co/").mock(
        return_value=httpx.Response(429, text="Too Many Requests")
    )

    status, result = await enrich_ip(IP, [IpApiComProvider(), IpapiCoProvider()])

    assert status == UNAVAILABLE
    assert result is None
