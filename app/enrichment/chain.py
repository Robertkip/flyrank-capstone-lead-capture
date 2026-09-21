"""The provider fallback chain.

Try provider A. If it raises, try provider B. If every provider is down,
return "unavailable" — never an exception. Enrichment is a nice-to-have, so a
dead upstream must degrade the submission, not destroy it.
"""

import logging

from app.config import settings
from app.enrichment.providers import (
    MOCK_A,
    MOCK_B,
    GeoProvider,
    GeoResult,
    IpApiComProvider,
    IpapiCoProvider,
    ProviderError,
    is_public_ip,
)

logger = logging.getLogger(__name__)

UNAVAILABLE = "unavailable"
ENRICHED = "enriched"
SKIPPED = "skipped"


def build_chain() -> list[GeoProvider]:
    """Live providers in production, deterministic mocks when proving fallback."""
    if settings.geo_mode == "live":
        return [IpApiComProvider(), IpapiCoProvider()]
    return [MOCK_A, MOCK_B]


async def enrich_ip(ip: str | None, chain: list[GeoProvider] | None = None) -> tuple[str, GeoResult | None]:
    """Return ``(status, result_or_None)``. This function never raises."""
    providers = chain if chain is not None else build_chain()

    lookup_ip = ip
    if not is_public_ip(lookup_ip):
        # Local/private address: substitute a public IP so local demos still
        # exercise the chain instead of silently skipping it.
        if settings.geo_dev_fallback_ip:
            lookup_ip = settings.geo_dev_fallback_ip
        else:
            logger.info("geo.skipped ip=%s reason=not_public", ip)
            return SKIPPED, None

    errors: list[str] = []
    for provider in providers:
        try:
            result = await provider.lookup(lookup_ip)
        except ProviderError as exc:
            errors.append(str(exc))
            logger.warning("geo.provider_failed provider=%s error=%s", provider.name, exc)
            continue
        except Exception as exc:  # a provider must never crash the request
            errors.append(f"{provider.name}: unexpected {exc!r}")
            logger.warning("geo.provider_crashed provider=%s error=%r", provider.name, exc)
            continue

        logger.info("geo.enriched provider=%s ip=%s", provider.name, lookup_ip)
        return ENRICHED, result

    logger.warning("geo.all_providers_failed ip=%s errors=%s", lookup_ip, errors)
    return UNAVAILABLE, None
