"""Nexar/Octopart distributor adapter -- GraphQL API, OAuth2 (MET-176)."""

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
from tool_registry.tools.nexar.queries import PART_DETAILS_QUERY, SEARCH_PARTS_QUERY

logger = structlog.get_logger(__name__)
tracer = get_tracer("distributors.nexar")

_TOKEN_URL = "https://identity.nexar.com/connect/token"
_GRAPHQL_URL = "https://api.nexar.com/graphql"

_LIFECYCLE_MAP: dict[str, LifecycleStatus] = {
    "Production": LifecycleStatus.ACTIVE,
    "Active": LifecycleStatus.ACTIVE,
    "NRND": LifecycleStatus.NRND,
    "Not Recommended for New Designs": LifecycleStatus.NRND,
    "End of Life": LifecycleStatus.EOL,
    "EOL": LifecycleStatus.EOL,
    "Obsolete": LifecycleStatus.OBSOLETE,
}


class NexarAdapter(DistributorAdapter):
    """Nexar/Octopart GraphQL API adapter with OAuth2 client credentials.

    Reads NEXAR_CLIENT_ID and NEXAR_CLIENT_SECRET from environment.
    Aggregates pricing and availability from multiple distributors.
    Rate-limited to 10 req/s.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        self._client_id = client_id or os.environ.get("NEXAR_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("NEXAR_CLIENT_SECRET", "")
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._rate_limiter = TokenBucketRateLimiter(rate=10.0, burst=10)

    @property
    def name(self) -> str:
        return "Nexar"

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _ensure_token(self) -> str:
        """Get a valid OAuth2 token, refreshing if expired."""
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token

        with tracer.start_as_current_span("nexar.auth") as span:
            try:
                resp = await self._client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "scope": "supply.domain",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                self._access_token = data["access_token"]
                self._token_expires_at = time.monotonic() + data.get("expires_in", 3600) - 60
                logger.info("nexar_token_refreshed")
                return self._access_token  # type: ignore[return-value]
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("nexar_auth_failed", error=str(exc))
                raise

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Execute a GraphQL query against the Nexar API."""
        await self._rate_limiter.acquire()
        token = await self._ensure_token()
        resp = await self._client.post(
            _GRAPHQL_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables},
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Nexar GraphQL errors: {data['errors']}")
        return data.get("data", {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_parts(self, query: str, limit: int = 10) -> list[PartSearchResult]:
        with tracer.start_as_current_span("nexar.search_parts") as span:
            span.set_attribute("query", query)
            try:
                data = await self._graphql(SEARCH_PARTS_QUERY, {"query": query, "limit": limit})
                return self._map_search_results(data)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("nexar_search_failed", query=query, error=str(exc))
                return []

    async def get_part_details(self, mpn: str) -> PartDetail | None:
        with tracer.start_as_current_span("nexar.get_part_details") as span:
            span.set_attribute("mpn", mpn)
            try:
                data = await self._graphql(PART_DETAILS_QUERY, {"mpn": mpn})
                return self._map_part_detail(data)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("nexar_details_failed", mpn=mpn, error=str(exc))
                return None

    async def get_pricing(self, mpn: str) -> list[PricingBreak]:
        with tracer.start_as_current_span("nexar.get_pricing") as span:
            span.set_attribute("mpn", mpn)
            try:
                data = await self._graphql(PART_DETAILS_QUERY, {"mpn": mpn})
                return self._map_pricing(data)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("nexar_pricing_failed", mpn=mpn, error=str(exc))
                return []

    async def get_availability(self, mpn: str) -> AvailabilityInfo | None:
        with tracer.start_as_current_span("nexar.get_availability") as span:
            span.set_attribute("mpn", mpn)
            try:
                data = await self._graphql(PART_DETAILS_QUERY, {"mpn": mpn})
                return self._map_availability(data)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning("nexar_availability_failed", mpn=mpn, error=str(exc))
                return None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Response mapping
    # ------------------------------------------------------------------

    def _map_search_results(self, data: dict[str, Any]) -> list[PartSearchResult]:
        results: list[PartSearchResult] = []
        search = data.get("supSearch", {})
        for result in search.get("results", []):
            part = result.get("part", {})
            best_offer = self._best_offer(part)
            results.append(
                PartSearchResult(
                    mpn=part.get("mpn", ""),
                    manufacturer=part.get("manufacturer", {}).get("name", ""),
                    description=part.get("shortDescription", ""),
                    distributor="Nexar",
                    distributor_pn="",
                    stock_qty=best_offer.get("inventoryLevel", 0) if best_offer else 0,
                    lead_time_days=best_offer.get("factoryLeadDays") if best_offer else None,
                    lifecycle_status=self._extract_lifecycle(part),
                    datasheet_url=(
                        part.get("bestDatasheet", {}).get("url")
                        if part.get("bestDatasheet")
                        else None
                    ),
                )
            )
        return results

    def _map_part_detail(self, data: dict[str, Any]) -> PartDetail | None:
        search = data.get("supSearchMpn", {})
        results_list = search.get("results", [])
        if not results_list:
            return None
        part = results_list[0].get("part", {})

        specs: dict[str, Any] = {}
        for spec in part.get("specs", []):
            attr = spec.get("attribute", {})
            specs[attr.get("name", "")] = spec.get("displayValue", "")

        best_offer = self._best_offer(part)
        return PartDetail(
            mpn=part.get("mpn", ""),
            manufacturer=part.get("manufacturer", {}).get("name", ""),
            description=part.get("shortDescription", ""),
            distributor="Nexar",
            distributor_pn="",
            stock_qty=best_offer.get("inventoryLevel", 0) if best_offer else 0,
            lead_time_days=best_offer.get("factoryLeadDays") if best_offer else None,
            lifecycle_status=self._extract_lifecycle(part),
            datasheet_url=(
                part.get("bestDatasheet", {}).get("url") if part.get("bestDatasheet") else None
            ),
            specs=specs,
            package=specs.get("Package / Case", ""),
            category=part.get("category", {}).get("name", "") if part.get("category") else "",
        )

    def _map_pricing(self, data: dict[str, Any]) -> list[PricingBreak]:
        search = data.get("supSearchMpn", {})
        results_list = search.get("results", [])
        if not results_list:
            return []
        part = results_list[0].get("part", {})

        breaks: list[PricingBreak] = []
        for seller in part.get("sellers", []):
            for offer in seller.get("offers", []):
                for price in offer.get("prices", []):
                    breaks.append(
                        PricingBreak(
                            quantity=price.get("quantity", 1),
                            unit_price=price.get("price", 0.0),
                            currency=price.get("currency", "USD"),
                        )
                    )
        # De-duplicate by quantity, keeping the lowest price
        by_qty: dict[int, PricingBreak] = {}
        for b in breaks:
            if b.quantity not in by_qty or b.unit_price < by_qty[b.quantity].unit_price:
                by_qty[b.quantity] = b
        return sorted(by_qty.values(), key=lambda x: x.quantity)

    def _map_availability(self, data: dict[str, Any]) -> AvailabilityInfo | None:
        search = data.get("supSearchMpn", {})
        results_list = search.get("results", [])
        if not results_list:
            return None
        part = results_list[0].get("part", {})

        total_stock = 0
        min_lead = None
        min_moq = 1
        for seller in part.get("sellers", []):
            for offer in seller.get("offers", []):
                inv = offer.get("inventoryLevel", 0)
                if isinstance(inv, int):
                    total_stock += inv
                lead = offer.get("factoryLeadDays")
                if lead is not None and (min_lead is None or lead < min_lead):
                    min_lead = lead
                moq = offer.get("moq")
                if moq is not None and moq > 0:
                    min_moq = min(min_moq, moq) if min_moq > 1 else moq

        return AvailabilityInfo(
            stock_qty=total_stock,
            lead_time_days=min_lead,
            minimum_order_qty=min_moq if min_moq >= 1 else 1,
            factory_stock=None,
            on_order_qty=None,
        )

    @staticmethod
    def _best_offer(part: dict[str, Any]) -> dict[str, Any] | None:
        """Find the seller offer with the highest inventory."""
        best: dict[str, Any] | None = None
        best_inv = -1
        for seller in part.get("sellers", []):
            for offer in seller.get("offers", []):
                inv = offer.get("inventoryLevel", 0)
                if isinstance(inv, int) and inv > best_inv:
                    best = offer
                    best_inv = inv
        return best

    @staticmethod
    def _extract_lifecycle(part: dict[str, Any]) -> LifecycleStatus:
        """Extract lifecycle status from part specs."""
        for spec in part.get("specs", []):
            attr = spec.get("attribute", {})
            if attr.get("name", "").lower() in ("lifecycle status", "lifecycle"):
                return _LIFECYCLE_MAP.get(spec.get("displayValue", ""), LifecycleStatus.UNKNOWN)
        return LifecycleStatus.UNKNOWN
