"""A response cache and daily call budget for distributor APIs (FORGE-613).

Some distributor APIs cap calls per day (Mouser's search API allows a fixed
number of calls a day). A BOM check asks the same part several times
(details, pricing, availability) and a design asks the same parts again on
every iteration, so without a cache a single busy day spends the allowance.

:class:`ResponseCache` keeps each parsed response for ``ttl_seconds``, keyed on
the request, and counts real calls against ``daily_limit`` per UTC day. When
the budget is spent it serves a stale entry if there is one (and says so in
the log), and otherwise refuses, so the caller reports "no data" rather than
the distributor locking the key.

Per process: the gateway and the MCP sidecar each keep their own.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["DailyLimitReachedError", "ResponseCache"]


class DailyLimitReachedError(RuntimeError):
    """The day's call budget is spent and nothing cached answers the request."""


class ResponseCache:
    def __init__(
        self,
        name: str,
        *,
        ttl_seconds: float,
        daily_limit: int | None = None,
        clock: Callable[[], float] = time.time,
        max_entries: int = 5000,
    ) -> None:
        self.name = name
        self.ttl_seconds = ttl_seconds
        self.daily_limit = daily_limit
        self._clock = clock
        self._max_entries = max_entries
        self._entries: dict[str, tuple[float, Any]] = {}
        self._day = ""
        self.calls_today = 0
        self.hits = 0

    @staticmethod
    def key(*parts: Any) -> str:
        return json.dumps(parts, sort_keys=True, default=str)

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self._clock()))

    def _roll_day(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self.calls_today = today, 0

    @property
    def remaining_today(self) -> int | None:
        self._roll_day()
        return None if self.daily_limit is None else max(0, self.daily_limit - self.calls_today)

    async def get(self, key: str, fetch: Callable[[], Awaitable[Any]]) -> Any:
        """The cached value if fresh; otherwise ``fetch()`` within the budget."""
        now = self._clock()
        cached = self._entries.get(key)
        if cached is not None and now - cached[0] < self.ttl_seconds:
            self.hits += 1
            return cached[1]
        self._roll_day()
        if self.daily_limit is not None and self.calls_today >= self.daily_limit:
            if cached is not None:
                logger.warning(
                    "distributor_daily_limit_serving_stale",
                    distributor=self.name,
                    age_seconds=round(now - cached[0]),
                )
                return cached[1]
            logger.warning(
                "distributor_daily_limit_reached", distributor=self.name, limit=self.daily_limit
            )
            raise DailyLimitReachedError(
                f"{self.name}: the daily limit of {self.daily_limit} calls is spent"
            )
        self.calls_today += 1
        value = await fetch()
        if len(self._entries) >= self._max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k][0])
            self._entries.pop(oldest)
        self._entries[key] = (now, value)
        return value
