"""Arrow distributor adapter: Arrow itemservice v4 (FORGE-613).

Reads ``ARROW_LOGIN`` and ``ARROW_API_KEY``. One token search answers
search, details, pricing and availability; responses are cached
(``ARROW_CACHE_TTL_SECONDS``, default 6 h).

The response is deeply nested (part, then web sites, then sources by
region, then source parts with prices and availability). Only the fields
whose meaning is clear are mapped: part number, manufacturer, description,
datasheet, stock on hand, price breaks with their currency and the minimum
order quantity. Lead time and lifecycle are left unknown rather than
guessed, since their units and codes were not confirmed. Written from
Arrow's published API without a live key; a field it does not return reads
as missing.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer
from tool_registry.tools.distributors.base import (
    AvailabilityInfo,
    DistributorAdapter,
    PartDetail,
    PartSearchResult,
    PricingBreak,
)
from tool_registry.tools.distributors.cache import ResponseCache
from tool_registry.tools.distributors.rate_limiter import TokenBucketRateLimiter

logger = structlog.get_logger(__name__)
tracer = get_tracer("distributors.arrow")

_URL = "https://api.arrow.com/itemservice/v4/en/search/token"


class ArrowAdapter(DistributorAdapter):
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        login: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        self._login = login or os.environ.get("ARROW_LOGIN", "")
        self._api_key = api_key or os.environ.get("ARROW_API_KEY", "")
        self._rate_limiter = TokenBucketRateLimiter(rate=1.0, burst=2)
        self._cache = ResponseCache(
            "Arrow",
            ttl_seconds=float(os.environ.get("ARROW_CACHE_TTL_SECONDS") or 6 * 3600),
            daily_limit=int(os.environ["ARROW_DAILY_LIMIT"])
            if os.environ.get("ARROW_DAILY_LIMIT")
            else None,
        )

    @property
    def name(self) -> str:
        return "Arrow"

    async def _parts(self, token: str, rows: int) -> list[dict[str, Any]]:
        params = {
            "login": self._login,
            "apikey": self._api_key,
            "search_token": token,
            "rows": str(rows),
        }

        async def fetch() -> dict[str, Any]:
            await self._rate_limiter.acquire()
            resp = await self._client.get(_URL, params=params)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

        data = await self._cache.get(ResponseCache.key(token, rows), fetch)
        out: list[dict[str, Any]] = []
        for block in (data.get("itemserviceresult") or {}).get("data") or []:
            out.extend(p for p in block.get("PartList") or [] if isinstance(p, dict))
        return out

    async def _by_mpn(self, mpn: str) -> dict[str, Any] | None:
        parts = await self._parts(mpn, 10)
        exact = [p for p in parts if str(p.get("partNum") or "").lower() == mpn.lower()]
        return (exact or [None])[0]

    async def search_parts(self, query: str, limit: int = 10) -> list[PartSearchResult]:
        with tracer.start_as_current_span("arrow.search_parts") as span:
            span.set_attribute("query", query)
            try:
                return [_result(p) for p in await self._parts(query, limit)]
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("arrow_search_failed", query=query, error=str(exc))
                return []

    async def get_part_details(self, mpn: str) -> PartDetail | None:
        with tracer.start_as_current_span("arrow.get_part_details") as span:
            span.set_attribute("mpn", mpn)
            try:
                part = await self._by_mpn(mpn)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("arrow_details_failed", mpn=mpn, error=str(exc))
                return None
            return PartDetail(**_result(part).model_dump()) if part else None

    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        with tracer.start_as_current_span("arrow.get_pricing") as span:
            span.set_attribute("mpn", mpn)
            try:
                part = await self._by_mpn(mpn)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("arrow_pricing_failed", mpn=mpn, error=str(exc))
                return []
            breaks: dict[int, PricingBreak] = {}
            for currency, source_part in _source_parts(part or {}):
                resale = (source_part.get("Prices") or {}).get("resaleList") or []
                for tier in resale:
                    try:
                        qty = max(1, int(tier["minQty"]))
                        price = float(tier["price"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    best = breaks.get(qty)
                    if best is None or price < best.unit_price:
                        breaks[qty] = PricingBreak(
                            quantity=qty, unit_price=price, currency=currency
                        )
            return [breaks[q] for q in sorted(breaks)]

    async def get_availability(self, mpn: str) -> AvailabilityInfo | None:
        with tracer.start_as_current_span("arrow.get_availability") as span:
            span.set_attribute("mpn", mpn)
            try:
                part = await self._by_mpn(mpn)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("arrow_availability_failed", mpn=mpn, error=str(exc))
                return None
            if part is None:
                return None
            moqs = [
                int(sp["minimumOrderQuantity"])
                for _, sp in _source_parts(part)
                if str(sp.get("minimumOrderQuantity") or "").isdigit()
            ]
            return AvailabilityInfo(
                stock_qty=_stock(part),
                lead_time_days=None,
                minimum_order_qty=max(1, min(moqs)) if moqs else 1,
            )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _source_parts(part: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for site in (part.get("InvOrg") or {}).get("webSites") or []:
        for source in site.get("sources") or []:
            currency = str(source.get("currency") or "")
            if not currency:
                continue
            for source_part in source.get("sourceParts") or []:
                if isinstance(source_part, dict):
                    out.append((currency, source_part))
    return out


def _stock(part: dict[str, Any]) -> int:
    total = 0
    for _, source_part in _source_parts(part):
        for avail in source_part.get("Availability") or []:
            try:
                total += int(avail.get("fohQty") or 0)
            except (TypeError, ValueError):
                continue
    return total


def _result(part: dict[str, Any]) -> PartSearchResult:
    resources = [r for r in part.get("resources") or [] if isinstance(r, dict)]
    datasheet = next((r.get("uri") for r in resources if r.get("type") == "datasheet"), None)
    return PartSearchResult(
        mpn=str(part.get("partNum") or ""),
        manufacturer=str((part.get("manufacturer") or {}).get("mfrName") or ""),
        description=str(part.get("desc") or ""),
        distributor="Arrow",
        distributor_pn=str(part.get("itemId") or ""),
        stock_qty=_stock(part),
        lead_time_days=None,
        datasheet_url=datasheet,
    )
