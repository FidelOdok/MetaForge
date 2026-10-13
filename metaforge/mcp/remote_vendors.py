"""Vendor connectors from the sidecar, through the gateway (FORGE-427).

Credentials, quotes and orders live in the gateway; this calls its
``/v1/vendors`` routes and returns their shapes. No ``api_gateway`` import.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote as urlquote

import httpx

from metaforge.mcp.remote_flows import _Gateway

__all__ = ["RemoteVendorService"]

#: A vendor quote can take a while (file upload, pricing engine).
DEFAULT_TIMEOUT_SECONDS = 120.0


class RemoteVendorService:
    def __init__(
        self,
        gateway_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._gateway = _Gateway(gateway_url, client, timeout_seconds)

    async def list_connectors(self) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request("GET", "/v1/vendors/connectors")
        return result

    async def quote(self, request: dict[str, Any], by: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "POST", "/v1/vendors/quotes", json={**request, "by": by}
        )
        return result

    async def place_order(self, quote_id: str, by: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "POST", "/v1/vendors/orders", json={"quote_id": quote_id, "by": by}
        )
        return result

    async def order_status(self, order_id: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "GET", f"/v1/vendors/orders/{urlquote(order_id, safe='')}/status"
        )
        return result

    async def handoffs(self, service_name: str, work_product_ids: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "POST",
            "/v1/vendors/handoffs",
            json={"service": service_name, "work_product_ids": work_product_ids},
        )
        return result
