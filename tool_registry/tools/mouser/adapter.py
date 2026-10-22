"""Mouser distributor adapter -- API key auth, REST API (MET-175)."""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer
from tool_registry.tools.distributors.base import (
    AvailabilityInfo,
    DistributorAdapter,
    LifecycleStatus,
    PartDetail,
    PartSearchResult,
    PricingBreak,
    parse_price,
)
from tool_registry.tools.distributors.cache import ResponseCache
from tool_registry.tools.distributors.rate_limiter import TokenBucketRateLimiter

logger = structlog.get_logger(__name__)
tracer = get_tracer("distributors.mouser")

_SEARCH_URL = "https://api.mouser.com/api/v2/search/keyword"
_PART_URL = "https://api.mouser.com/api/v2/search/partnumber"

_LIFECYCLE_MAP: dict[str, LifecycleStatus] = {
    "New Product": LifecycleStatus.ACTIVE,
    "": LifecycleStatus.ACTIVE,  # Mouser often omits status for active parts
    "Not Recommended for New Designs": LifecycleStatus.NRND,
    "End of Life": LifecycleStatus.EOL,
    "Obsolete": LifecycleStatus.OBSOLETE,
}


#: FORGE-613: Mouser caps search calls per day. Both are settable because the
#: allowance belongs to the key, not to this code.
DEFAULT_DAILY_LIMIT = 1000
DEFAULT_CACHE_TTL_SECONDS = 6 * 3600


class MouserAdapter(DistributorAdapter):
    """Mouser API adapter using API key authentication.

    Reads MOUSER_API_KEY from environment.
    Rate-limited to 30 req/min (0.5 req/s). Responses are cached
    (``MOUSER_CACHE_TTL_SECONDS``, default 6 h) and real calls are counted
    against ``MOUSER_DAILY_LIMIT`` (default 1000) per UTC day (FORGE-613).
    Details, pricing and availability for one part share a single call.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        self._api_key = api_key or os.environ.get("MOUSER_API_KEY", "")
        self._rate_limiter = TokenBucketRateLimiter(rate=30.0 / 60.0, burst=5)
        self._cache = ResponseCache(
            "Mouser",
            ttl_seconds=float(
                os.environ.get("MOUSER_CACHE_TTL_SECONDS") or DEFAULT_CACHE_TTL_SECONDS
            ),
            daily_limit=int(os.environ.get("MOUSER_DAILY_LIMIT") or DEFAULT_DAILY_LIMIT),
        )

    async def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """One Mouser call, through the cache and the daily budget."""

        async def fetch() -> dict[str, Any]:
            await self._rate_limiter.acquire()
            resp = await self._client.post(url, params={"apiKey": self._api_key}, json=body)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

        result: dict[str, Any] = await self._cache.get(ResponseCache.key(url, body), fetch)
        return result

    @staticmethod
    def _part_body(mpn: str) -> dict[str, Any]:
        return {"SearchByPartRequest": {"mouserPartNumber": mpn, "partSearchOptions": "Exact"}}

    @property
    def name(self) -> str:
        return "Mouser"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_parts(self, query: str, limit: int = 10) -> list[PartSearchResult]:
        with tracer.start_as_current_span("mouser.search_parts") as span:
            span.set_attribute("query", query)
            try:
                data = await self._post(
                    _SEARCH_URL,
                    {"SearchByKeywordRequest": {"keyword": query, "records": limit}},
                )
                return self._map_search_results(data)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("mouser_search_failed", query=query, error=str(exc))
                return []

    async def get_part_details(self, mpn: str) -> PartDetail | None:
        with tracer.start_as_current_span("mouser.get_part_details") as span:
            span.set_attribute("mpn", mpn)
            try:
                data = await self._post(_PART_URL, self._part_body(mpn))
                return self._map_part_detail(data)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("mouser_details_failed", mpn=mpn, error=str(exc))
                return None

    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        with tracer.start_as_current_span("mouser.get_pricing") as span:
            span.set_attribute("mpn", mpn)
            try:
                data = await self._post(_PART_URL, self._part_body(mpn))
                return self._map_pricing(data)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("mouser_pricing_failed", mpn=mpn, error=str(exc))
                return []

    async def get_availability(self, mpn: str) -> AvailabilityInfo | None:
        with tracer.start_as_current_span("mouser.get_availability") as span:
            span.set_attribute("mpn", mpn)
            try:
                data = await self._post(_PART_URL, self._part_body(mpn))
                return self._map_availability(data)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("mouser_availability_failed", mpn=mpn, error=str(exc))
                return None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Response mapping
    # ------------------------------------------------------------------

    def _map_search_results(self, data: dict[str, Any]) -> list[PartSearchResult]:
        results: list[PartSearchResult] = []
        parts = data.get("SearchResults", {}).get("Parts", [])
        for part in parts:
            results.append(
                PartSearchResult(
                    mpn=part.get("ManufacturerPartNumber", ""),
                    manufacturer=part.get("Manufacturer", ""),
                    description=part.get("Description", ""),
                    distributor="Mouser",
                    distributor_pn=part.get("MouserPartNumber", ""),
                    stock_qty=_parse_stock(part.get("Availability", "")),
                    lead_time_days=_parse_lead_time(part.get("LeadTime", "")),
                    lifecycle_status=_map_lifecycle(part.get("LifecycleStatus", "")),
                    datasheet_url=part.get("DataSheetUrl"),
                    product_url=part.get("ProductDetailUrl"),
                )
            )
        return results

    def _map_part_detail(self, data: dict[str, Any]) -> PartDetail | None:
        parts = data.get("SearchResults", {}).get("Parts", [])
        if not parts:
            return None
        part = parts[0]

        specs: dict[str, Any] = {}
        for attr in part.get("ProductAttributes", []):
            specs[attr.get("AttributeName", "")] = attr.get("AttributeValue", "")

        return PartDetail(
            mpn=part.get("ManufacturerPartNumber", ""),
            manufacturer=part.get("Manufacturer", ""),
            description=part.get("Description", ""),
            distributor="Mouser",
            distributor_pn=part.get("MouserPartNumber", ""),
            stock_qty=_parse_stock(part.get("Availability", "")),
            lead_time_days=_parse_lead_time(part.get("LeadTime", "")),
            lifecycle_status=_map_lifecycle(part.get("LifecycleStatus", "")),
            datasheet_url=part.get("DataSheetUrl"),
            product_url=part.get("ProductDetailUrl"),
            image_url=part.get("ImagePath") or None,
            specs=specs,
            package=specs.get("Package / Case", ""),
            category=part.get("Category", ""),
        )

    def _map_pricing(self, data: dict[str, Any]) -> list[PricingBreak]:
        parts = data.get("SearchResults", {}).get("Parts", [])
        if not parts:
            return []
        breaks: list[PricingBreak] = []
        for bp in parts[0].get("PriceBreaks", []):
            # Mouser prices in the key's currency ("£89.42", "$1.23"); a break
            # whose price or currency cannot be read is dropped and logged,
            # never kept as 0.0 or given a guessed currency.
            price = parse_price(bp.get("Price"))
            currency = str(bp.get("Currency") or "").strip().upper()
            if price is None or not currency:
                logger.warning(
                    "mouser_price_unreadable",
                    price=bp.get("Price"),
                    currency=bp.get("Currency"),
                )
                continue
            breaks.append(
                PricingBreak(
                    quantity=bp.get("Quantity", 1),
                    unit_price=price,
                    currency=currency,
                )
            )
        return breaks

    def _map_availability(self, data: dict[str, Any]) -> AvailabilityInfo | None:
        parts = data.get("SearchResults", {}).get("Parts", [])
        if not parts:
            return None
        part = parts[0]
        return AvailabilityInfo(
            stock_qty=_parse_stock(part.get("Availability", "")),
            lead_time_days=_parse_lead_time(part.get("LeadTime", "")),
            minimum_order_qty=part.get("Min", 1) or 1,
            factory_stock=None,
            on_order_qty=None,
        )


def _parse_stock(availability: str) -> int:
    """Parse Mouser availability string like '1,500 In Stock' to int."""
    if not availability:
        return 0
    try:
        numeric = availability.split(" ")[0].replace(",", "")
        return int(numeric)
    except (ValueError, IndexError):
        return 0


def _parse_lead_time(lead_time: str) -> int | None:
    """Parse Mouser lead time string like '6 Weeks' to days."""
    if not lead_time:
        return None
    try:
        parts = lead_time.strip().split()
        value = int(parts[0])
        unit = parts[1].lower() if len(parts) > 1 else "days"
        if "week" in unit:
            return value * 7
        if "day" in unit:
            return value
        return value
    except (ValueError, IndexError):
        return None


def _map_lifecycle(status: str) -> LifecycleStatus:
    return _LIFECYCLE_MAP.get(status, LifecycleStatus.UNKNOWN)
