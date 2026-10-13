"""Farnell distributor adapter: element14 Product Search API (FORGE-613).

Farnell (Newark in the US, element14 in Asia-Pacific) prices in the store's
own currency, so a UK project gets GBP prices directly. Reads
``FARNELL_API_KEY``; ``FARNELL_STORE`` picks the store (default
``uk.farnell.com``) and with it the currency.

The request and response field names follow element14's published Product
Search API (``/catalog/products`` with ``term=any:`` or ``term=manuPartNum:``
and the ``large`` response group, which carries prices and stock). They were
written without a live key to test against, so a field the API does not
return reads as missing (unknown), never invented.
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
    LifecycleStatus,
    PartDetail,
    PartSearchResult,
    PricingBreak,
)
from tool_registry.tools.distributors.cache import ResponseCache
from tool_registry.tools.distributors.rate_limiter import TokenBucketRateLimiter

logger = structlog.get_logger(__name__)
tracer = get_tracer("distributors.farnell")

_URL = "https://api.element14.com/catalog/products"

#: The currency each store prices in. A store not listed needs
#: ``FARNELL_CURRENCY`` set, rather than a guess.
STORE_CURRENCY = {
    "uk.farnell.com": "GBP",
    "ie.farnell.com": "EUR",
    "de.farnell.com": "EUR",
    "fr.farnell.com": "EUR",
    "it.farnell.com": "EUR",
    "es.farnell.com": "EUR",
    "nl.farnell.com": "EUR",
    "www.newark.com": "USD",
}

#: The response's root key depends on the kind of search made.
_RETURN_ROOTS = (
    "keywordSearchReturn",
    "manufacturerPartNumberSearchReturn",
    "premierFarnellPartNumberReturn",
)

_STATUS = {
    "STOCKED": LifecycleStatus.ACTIVE,
    "DIRECT_SHIP": LifecycleStatus.ACTIVE,
    "ON_DEMAND": LifecycleStatus.ACTIVE,
    "NOT_RECOMMENDED_FOR_NEW_DESIGNS": LifecycleStatus.NRND,
    "NO_LONGER_STOCKED": LifecycleStatus.EOL,
    "NO_LONGER_MANUFACTURED": LifecycleStatus.OBSOLETE,
}


class FarnellAdapter(DistributorAdapter):
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        store: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        self._api_key = api_key or os.environ.get("FARNELL_API_KEY", "")
        self._store = (store or os.environ.get("FARNELL_STORE") or "uk.farnell.com").strip()
        self._currency = (
            os.environ.get("FARNELL_CURRENCY") or STORE_CURRENCY.get(self._store) or ""
        ).upper()
        # element14 publishes per-second and per-day caps for its keys; stay
        # under two calls a second and cache answers for six hours.
        self._rate_limiter = TokenBucketRateLimiter(rate=2.0, burst=2)
        self._cache = ResponseCache(
            "Farnell",
            ttl_seconds=float(os.environ.get("FARNELL_CACHE_TTL_SECONDS") or 6 * 3600),
            daily_limit=int(os.environ["FARNELL_DAILY_LIMIT"])
            if os.environ.get("FARNELL_DAILY_LIMIT")
            else None,
        )

    @property
    def name(self) -> str:
        return "Farnell"

    async def _products(self, term: str, limit: int) -> list[dict[str, Any]]:
        params = {
            "term": term,
            "storeInfo.id": self._store,
            "resultsSettings.offset": "0",
            "resultsSettings.numberOfResults": str(limit),
            "resultsSettings.responseGroup": "large",
            "callInfo.responseDataFormat": "json",
            "callInfo.apiKey": self._api_key,
        }

        async def fetch() -> dict[str, Any]:
            await self._rate_limiter.acquire()
            resp = await self._client.get(_URL, params=params)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            return data

        data = await self._cache.get(ResponseCache.key(self._store, term, limit), fetch)
        for root in (
            "keywordSearchReturn",
            "manufacturerPartNumberSearchReturn",
            "premierFarnellPartNumberReturn",
        ):
            if isinstance(data.get(root), dict):
                products = data[root].get("products") or []
                return [p for p in products if isinstance(p, dict)]
        return []

    async def _by_mpn(self, mpn: str) -> dict[str, Any] | None:
        products = await self._products(f"manuPartNum:{mpn}", 5)
        exact = [p for p in products if _mpn(p).lower() == mpn.lower()]
        return (exact or products or [None])[0]

    async def search_parts(self, query: str, limit: int = 10) -> list[PartSearchResult]:
        with tracer.start_as_current_span("farnell.search_parts") as span:
            span.set_attribute("query", query)
            try:
                return [self._result(p) for p in await self._products(f"any:{query}", limit)]
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("farnell_search_failed", query=query, error=str(exc))
                return []

    async def get_part_details(self, mpn: str) -> PartDetail | None:
        with tracer.start_as_current_span("farnell.get_part_details") as span:
            span.set_attribute("mpn", mpn)
            try:
                product = await self._by_mpn(mpn)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("farnell_details_failed", mpn=mpn, error=str(exc))
                return None
            if product is None:
                return None
            specs = {
                str(a.get("attributeLabel")): a.get("attributeValue")
                for a in product.get("attributes") or []
                if isinstance(a, dict) and a.get("attributeLabel")
            }
            return PartDetail(
                **self._result(product).model_dump(),
                specs=specs,
                package=str(specs.get("IC Case / Package") or specs.get("Package / Case") or ""),
            )

    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        with tracer.start_as_current_span("farnell.get_pricing") as span:
            span.set_attribute("mpn", mpn)
            if not self._currency:
                logger.warning("farnell_currency_unknown", store=self._store)
                return []
            try:
                product = await self._by_mpn(mpn)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("farnell_pricing_failed", mpn=mpn, error=str(exc))
                return []
            breaks = []
            for price in (product or {}).get("prices") or []:
                try:
                    breaks.append(
                        PricingBreak(
                            quantity=max(1, int(price["from"])),
                            unit_price=float(price["cost"]),
                            currency=self._currency,
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            return breaks

    async def get_availability(self, mpn: str) -> AvailabilityInfo | None:
        with tracer.start_as_current_span("farnell.get_availability") as span:
            span.set_attribute("mpn", mpn)
            try:
                product = await self._by_mpn(mpn)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("farnell_availability_failed", mpn=mpn, error=str(exc))
                return None
            if product is None:
                return None
            return AvailabilityInfo(
                stock_qty=_stock(product),
                lead_time_days=_lead_days(product),
                minimum_order_qty=_int(product.get("translatedMinimumOrderQuality")) or 1,
            )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _result(self, p: dict[str, Any]) -> PartSearchResult:
        sheets = [d.get("url") for d in p.get("datasheets") or [] if isinstance(d, dict)]
        return PartSearchResult(
            mpn=_mpn(p),
            manufacturer=str(p.get("brandName") or p.get("vendorName") or ""),
            description=str(p.get("displayName") or ""),
            distributor="Farnell",
            distributor_pn=str(p.get("sku") or ""),
            stock_qty=_stock(p),
            lead_time_days=_lead_days(p),
            lifecycle_status=_STATUS.get(
                str(p.get("productStatus") or ""), LifecycleStatus.UNKNOWN
            ),
            datasheet_url=next((u for u in sheets if u), None),
            product_url=(f"https://{self._store}/{p['sku']}" if p.get("sku") else None),
        )


def _mpn(p: dict[str, Any]) -> str:
    return str(p.get("translatedManufacturerPartNumber") or p.get("manufacturerPartNumber") or "")


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stock(p: dict[str, Any]) -> int:
    stock = p.get("stock") if isinstance(p.get("stock"), dict) else {}
    return max(0, _int(stock.get("level")) or _int(p.get("inv")) or 0)


def _lead_days(p: dict[str, Any]) -> int | None:
    stock = p.get("stock") if isinstance(p.get("stock"), dict) else {}
    return _int(stock.get("leastLeadTime"))
