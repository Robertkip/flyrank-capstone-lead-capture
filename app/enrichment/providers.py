"""IP -> geolocation providers.

Each provider is an independent unit with one method, ``lookup``. It either
returns a ``GeoResult`` or raises. The chain in ``chain.py`` is what decides
what to do about a raised exception — providers never know about each other.
"""

import ipaddress
from dataclasses import dataclass

import httpx

from app.config import settings


@dataclass(frozen=True)
class GeoResult:
    provider: str
    country: str | None = None
    country_code: str | None = None
    region: str | None = None
    city: str | None = None


class ProviderError(RuntimeError):
    """A provider could not answer. The chain moves on to the next one."""


class GeoProvider:
    name = "base"

    async def lookup(self, ip: str) -> GeoResult:  # pragma: no cover - interface
        raise NotImplementedError


class IpApiComProvider(GeoProvider):
    """Provider A — ip-api.com. Free, no key, 45 req/min."""

    name = "ip-api.com"

    async def lookup(self, ip: str) -> GeoResult:
        url = f"http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,regionName,city"
        try:
            async with httpx.AsyncClient(timeout=settings.geo_timeout_seconds) as client:
                response = await client.get(url)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"{self.name}: {exc}") from exc

        if body.get("status") != "success":
            raise ProviderError(f"{self.name}: {body.get('message', 'lookup failed')}")

        return GeoResult(
            provider=self.name,
            country=body.get("country"),
            country_code=body.get("countryCode"),
            region=body.get("regionName"),
            city=body.get("city"),
        )


class IpapiCoProvider(GeoProvider):
    """Provider B — ipapi.co. Free tier ~1,000 lookups/day, no card."""

    name = "ipapi.co"

    async def lookup(self, ip: str) -> GeoResult:
        url = f"https://ipapi.co/{ip}/json/"
        try:
            async with httpx.AsyncClient(timeout=settings.geo_timeout_seconds) as client:
                response = await client.get(url)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"{self.name}: {exc}") from exc

        if body.get("error"):
            raise ProviderError(f"{self.name}: {body.get('reason', 'lookup failed')}")

        return GeoResult(
            provider=self.name,
            country=body.get("country_name"),
            country_code=body.get("country_code"),
            region=body.get("region"),
            city=body.get("city"),
        )


class MockProvider(GeoProvider):
    """Deterministic stand-in used to prove the fallback chain.

    ``up`` is read from settings on every call, so a provider can be toggled
    "down" at runtime without restarting anything.
    """

    def __init__(self, name: str, up_flag: str, result: GeoResult) -> None:
        self.name = name
        self._up_flag = up_flag
        self._result = result

    @property
    def up(self) -> bool:
        return bool(getattr(settings, self._up_flag))

    async def lookup(self, ip: str) -> GeoResult:
        if not self.up:
            raise ProviderError(f"{self.name}: provider is down (toggled off)")
        return GeoResult(
            provider=self.name,
            country=self._result.country,
            country_code=self._result.country_code,
            region=self._result.region,
            city=self._result.city,
        )


MOCK_A = MockProvider(
    "mock-provider-a",
    "geo_mock_a_up",
    GeoResult("mock-provider-a", "Kenya", "KE", "Nairobi County", "Nairobi"),
)
MOCK_B = MockProvider(
    "mock-provider-b",
    "geo_mock_b_up",
    GeoResult("mock-provider-b", "Germany", "DE", "Berlin", "Berlin"),
)


def is_public_ip(ip: str | None) -> bool:
    """Loopback and private ranges have no geography worth looking up."""
    if not ip:
        return False
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return parsed.is_global
