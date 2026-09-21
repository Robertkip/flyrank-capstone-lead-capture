"""In-process sliding-window rate limiter.

Two independent buckets guard the public submission endpoint: one per client
IP (stops a single flooder) and one per widget (stops a distributed flood from
burying one customer's endpoint). Over the limit returns 429 with Retry-After,
and every other route keeps serving normally.

Limits are read from settings on every check rather than captured at import,
so configuration is never frozen into an object built at start-up.

Single-process by design — see the README's limitations note. Swapping the
storage for Redis is a drop-in change behind ``SlidingWindowLimiter``.
"""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    scope: str = ""


class SlidingWindowLimiter:
    """Counts hits per key inside a rolling window of ``window_seconds``.

    ``limit`` may be an int or a zero-argument callable, so the effective limit
    can follow configuration instead of being fixed at construction.
    """

    def __init__(self, limit: int | Callable[[], int], window_seconds: float = 60.0) -> None:
        self._limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        return int(self._limit() if callable(self._limit) else self._limit)

    def check(self, key: str, *, scope: str = "") -> LimitDecision:
        limit = self.limit
        now = time.monotonic()
        cutoff = now - self.window

        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= limit:
                retry_after = max(1, int(bucket[0] + self.window - now) + 1)
                return LimitDecision(False, limit, 0, retry_after, scope)

            bucket.append(now)
            return LimitDecision(True, limit, limit - len(bucket), 0, scope)

    def reset(self, key: str | None = None) -> None:
        """Clear state — used by tests to keep cases independent."""
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


class SubmissionRateLimiter:
    """The two buckets the submission endpoint consults, in order."""

    def __init__(
        self,
        ip_per_minute: int | Callable[[], int],
        widget_per_minute: int | Callable[[], int],
        enabled: bool | Callable[[], bool] = True,
    ) -> None:
        self._enabled = enabled
        self.ip = SlidingWindowLimiter(ip_per_minute)
        self.widget = SlidingWindowLimiter(widget_per_minute)

    @property
    def enabled(self) -> bool:
        return bool(self._enabled() if callable(self._enabled) else self._enabled)

    def check(self, ip: str, widget_public_id: str) -> LimitDecision:
        if not self.enabled:
            return LimitDecision(True, 0, 0, 0, "disabled")

        ip_decision = self.ip.check(f"ip:{ip}", scope="ip")
        if not ip_decision.allowed:
            return ip_decision

        widget_decision = self.widget.check(f"widget:{widget_public_id}", scope="widget")
        if not widget_decision.allowed:
            return widget_decision

        # Both allowed: report the bucket the caller is closest to exhausting,
        # so X-RateLimit-Remaining means something to the client.
        return min(ip_decision, widget_decision, key=lambda d: d.remaining)

    def reset(self) -> None:
        self.ip.reset()
        self.widget.reset()


def build_limiter() -> SubmissionRateLimiter:
    from app.config import settings

    return SubmissionRateLimiter(
        ip_per_minute=lambda: settings.rate_limit_ip_per_minute,
        widget_per_minute=lambda: settings.rate_limit_widget_per_minute,
        enabled=lambda: settings.rate_limit_enabled,
    )


limiter = build_limiter()
