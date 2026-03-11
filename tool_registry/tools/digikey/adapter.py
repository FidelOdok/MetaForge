"""Digi-Key distributor adapter -- OAuth2 client credentials, REST API (MET-174)."""

from __future__ import annotations

import os
import time
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
tracer = get_tracer("distributors.digikey")

# Digi-Key API base URLs
_TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
_SEARCH_URL = "https://api.digikey.com/products/v4/search/keyword"
_PRODUCT_URL = "https://api.digikey.com/products/v4/search/{mpn}/productdetails"

_LIFECYCLE_MAP: dict[str, LifecycleStatus] = {
    "Active": LifecycleStatus.ACTIVE,
    "Not Recommended for New Designs": LifecycleStatus.NRND,
    "Obsolete": LifecycleStatus.OBSOLETE,
    "Last Time Buy": LifecycleStatus.EOL,
    "Discontinued": LifecycleStatus.EOL,
}


class DigiKeyAdapter(DistributorAdapter):
    """Digi-Key API adapter using OAuth2 client credentials.

    Reads DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET from environment.
    Rate-limited to 1000 req/min (~16.67 req/s).
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        self._client_id = client_id or os.environ.get("DIGIKEY_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("DIGIKEY_CLIENT_SECRET", "")
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._rate_limiter = TokenBucketRateLimiter(rate=1000.0 / 60.0, burst=50)

    @property
    def name(self) -> str:
        return "DigiKey"

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _ensure_token(self) -> str:
        """Get a valid OAuth2 token, refreshing if expired."""
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token

        with tracer.start_as_current_span("digikey.auth") as span:
            try:
                resp = await self._client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                self._access_token = data["access_token"]
                self._token_expires_at = time.monotonic() + data.get("expires_in", 3600) - 60
                logger.info("digikey_token_refreshed")
                return self._access_token  # type: ignore[return-value]
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("digikey_auth_failed", error=str(exc))
                raise

    async def _headers(self) -> dict[str, str]:
        token = await self._ensure_token()
        return {
            "Authorization": f"Bearer {token}",
            "X-DIGIKEY-Client-Id": self._client_id,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_parts(self, query: str, limit: int = 10) -> list[PartSearchResult]:
        with tracer.start_as_current_span("digikey.search_parts") as span:
            span.set_attribute("query", query)
            try:
                await self._rate_limiter.acquire()
                headers = await self._headers()
                resp = await self._client.post(
                    _SEARCH_URL,
                    headers=headers,
                    json={"Keywords": query, "RecordCount": limit},
                )
                resp.raise_for_status()
                return self._map_search_results(resp.json())
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("digikey_search_failed", query=query, error=str(exc))
                return []

    async def get_part_details(self, mpn: str) -> PartDetail | None:
        with tracer.start_as_current_span("digikey.get_part_details") as span:
            span.set_attribute("mpn", mpn)
            try:
                await self._rate_limiter.acquire()
                headers = await self._headers()
                url = _PRODUCT_URL.format(mpn=mpn)
                resp = await self._client.get(url, headers=headers)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return self._map_part_detail(resp.json())
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("digikey_details_failed", mpn=mpn, error=str(exc))
                return None

    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        with tracer.start_as_current_span("digikey.get_pricing") as span:
            span.set_attribute("mpn", mpn)
            try:
                await self._rate_limiter.acquire()
                headers = await self._headers()
                url = _PRODUCT_URL.format(mpn=mpn)
                resp = await self._client.get(url, headers=headers)
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                return self._map_pricing(resp.json())
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("digikey_pricing_failed", mpn=mpn, error=str(exc))
                return []

    async def get_availability(self, mpn: str) -> AvailabilityInfo | None:
        with tracer.start_as_current_span("digikey.get_availability") as span:
            span.set_attribute("mpn", mpn)
            try:
                await self._rate_limiter.acquire()
                headers = await self._headers()
                url = _PRODUCT_URL.format(mpn=mpn)
                resp = await self._client.get(url, headers=headers)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return self._map_availability(resp.json())
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("digikey_availability_failed", mpn=mpn, error=str(exc))
                return None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Response mapping
    # ------------------------------------------------------------------

    def _map_search_results(self, data: dict[str, Any]) -> list[PartSearchResult]:
        results: list[PartSearchResult] = []
        for product in data.get("Products", []):
            results.append(
                PartSearchResult(
                    mpn=product.get("ManufacturerPartNumber", ""),
                    manufacturer=product.get("Manufacturer", {}).get("Name", ""),
                    description=product.get("ProductDescription", ""),
                    distributor="DigiKey",
                    distributor_pn=product.get("DigiKeyPartNumber", ""),
                    stock_qty=product.get("QuantityAvailable", 0),
                    lead_time_days=_parse_lead_time(product.get("ManufacturerLeadWeeks")),
                    lifecycle_status=_map_lifecycle(product.get("ProductStatus", "")),
                    datasheet_url=product.get("DatasheetUrl"),
                )
            )
        return results

    def _map_part_detail(self, data: dict[str, Any]) -> PartDetail:
        specs: dict[str, Any] = {}
        for param in data.get("Parameters", []):
            specs[param.get("ParameterText", "")] = param.get("ValueText", "")

        return PartDetail(
            mpn=data.get("ManufacturerPartNumber", ""),
            manufacturer=data.get("Manufacturer", {}).get("Name", ""),
            description=data.get("ProductDescription", ""),
            distributor="DigiKey",
            distributor_pn=data.get("DigiKeyPartNumber", ""),
            stock_qty=data.get("QuantityAvailable", 0),
            lead_time_days=_parse_lead_time(data.get("ManufacturerLeadWeeks")),
            lifecycle_status=_map_lifecycle(data.get("ProductStatus", "")),
            datasheet_url=data.get("DatasheetUrl"),
            specs=specs,
            package=specs.get("Package / Case", ""),
            category=data.get("Category", {}).get("Name", ""),
        )

    def _map_pricing(self, data: dict[str, Any]) -> list[PricingBreak]:
        breaks: list[PricingBreak] = []
        for bp in data.get("StandardPricing", []):
            breaks.append(
                PricingBreak(
                    quantity=bp.get("BreakQuantity", 1),
                    unit_price=bp.get("UnitPrice", 0.0),
                    currency="USD",
                )
            )
        return breaks

    def _map_availability(self, data: dict[str, Any]) -> AvailabilityInfo:
        return AvailabilityInfo(
            stock_qty=data.get("QuantityAvailable", 0),
            lead_time_days=_parse_lead_time(data.get("ManufacturerLeadWeeks")),
            minimum_order_qty=data.get("MinimumOrderQuantity", 1),
            factory_stock=data.get("QuantityOnOrder"),
            on_order_qty=data.get("QuantityOnOrder"),
        )


def _parse_lead_time(weeks: Any) -> int | None:
    """Convert lead-time weeks string to days."""
    if weeks is None:
        return None
    try:
        return int(float(str(weeks)) * 7)
    except (ValueError, TypeError):
        return None


def _map_lifecycle(status: str) -> LifecycleStatus:
    return _LIFECYCLE_MAP.get(status, LifecycleStatus.UNKNOWN)
