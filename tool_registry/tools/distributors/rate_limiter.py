"""Token-bucket rate limiter for distributor API calls."""

from __future__ import annotations

import asyncio
import time

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("distributors.rate_limiter")


class TokenBucketRateLimiter:
    """Async token-bucket rate limiter.

    Parameters
    ----------
    rate : float
        Maximum requests per second.
    burst : int
        Maximum burst size (bucket capacity).
    """

    def __init__(self, rate: float, burst: int | None = None) -> None:
        self._rate = rate
        self._burst = burst if burst is not None else max(1, int(rate))
        self._tokens = float(self._burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume one."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # Calculate wait time for next token
                wait = (1.0 - self._tokens) / self._rate
                # Release lock while waiting
                self._lock.release()
                try:
                    await asyncio.sleep(wait)
                finally:
                    await self._lock.acquire()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now
