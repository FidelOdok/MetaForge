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
)
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


class MouserAdapter(DistributorAdapter):
    """Mouser API adapter using API key authentication.

    Reads MOUSER_API_KEY from environment.
    Rate-limited to 30 req/min (0.5 req/s).
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
                await self._rate_limiter.acquire()
                resp = await self._client.post(
                    _SEARCH_URL,
                    params={"apiKey": self._api_key},
                    json={
                        "SearchByKeywordRequest": {
                            "keyword": query,
                            "records": limit,
                        }
                    },
                )
                resp.raise_for_status()
                return self._map_search_results(resp.json())
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("mouser_search_failed", query=query, error=str(exc))
                return []

    async def get_part_details(self, mpn: str) -> PartDetail | None:
        with tracer.start_as_current_span("mouser.get_part_details") as span:
            span.set_attribute("mpn", mpn)
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.post(
                    _PART_URL,
                    params={"apiKey": self._api_key},
                    json={
                        "SearchByPartRequest": {
                            "mouserPartNumber": mpn,
                            "partSearchOptions": "Exact",
                        }
                    },
                )
                resp.raise_for_status()
                return self._map_part_detail(resp.json())
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("mouser_details_failed", mpn=mpn, error=str(exc))
                return None

    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        with tracer.start_as_current_span("mouser.get_pricing") as span:
            span.set_attribute("mpn", mpn)
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.post(
                    _PART_URL,
                    params={"apiKey": self._api_key},
                    json={
                        "SearchByPartRequest": {
                            "mouserPartNumber": mpn,
                            "partSearchOptions": "Exact",
                        }
                    },
                )
                resp.raise_for_status()
                return self._map_pricing(resp.json())
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("mouser_pricing_failed", mpn=mpn, error=str(exc))
                return []

    async def get_availability(self, mpn: str) -> AvailabilityInfo | None:
        with tracer.start_as_current_span("mouser.get_availability") as span:
            span.set_attribute("mpn", mpn)
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.post(
                    _PART_URL,
                    params={"apiKey": self._api_key},
                    json={
                        "SearchByPartRequest": {
                            "mouserPartNumber": mpn,
                            "partSearchOptions": "Exact",
                        }
                    },
                )
                resp.raise_for_status()
                return self._map_availability(resp.json())
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
            price_str = bp.get("Price", "0").replace("$", "").replace(",", "")
            try:
                price = float(price_str)
            except (ValueError, TypeError):
                price = 0.0
            breaks.append(
                PricingBreak(
                    quantity=bp.get("Quantity", 1),
                    unit_price=price,
                    currency=bp.get("Currency", "USD"),
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
