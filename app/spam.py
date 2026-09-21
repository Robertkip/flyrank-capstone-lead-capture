"""Spam controls for the public submission endpoint.

Two cheap, deterministic checks that need no third-party service:

* **Honeypot** — a hidden input real users never see. Filled = bot.
* **Fill-time heuristic** — a form submitted faster than a human can type it.

A blocked submission gets the same success-shaped response a real one gets, so
a bot learns nothing about why it failed.
"""

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class SpamVerdict:
    is_spam: bool
    reason: str = ""


def classify(honeypot: str | None, elapsed_ms: int | None) -> SpamVerdict:
    if honeypot is not None and honeypot.strip() != "":
        return SpamVerdict(True, "honeypot_filled")

    if elapsed_ms is not None and elapsed_ms < settings.min_fill_seconds * 1000:
        return SpamVerdict(True, "submitted_too_fast")

    return SpamVerdict(False)
