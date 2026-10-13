"""The in-process ``vendor.*`` binding (FORGE-427): same shapes as the routes."""

from __future__ import annotations

from typing import Any

from api_gateway.vendors import service
from api_gateway.vendors.connectors import VendorError


def _error(exc: Exception) -> ValueError:
    if isinstance(exc, VendorError):
        return ValueError(f"{exc.kind}: {exc}")
    return ValueError(str(exc))


class LocalVendorService:
    async def list_connectors(self) -> dict[str, Any]:
        return {"connectors": await service.connector_report()}

    async def quote(self, request: dict[str, Any], by: str) -> dict[str, Any]:
        try:
            return await service.quote(request, by=by)
        except (VendorError, service.VendorServiceError) as exc:
            raise _error(exc) from exc

    async def place_order(self, quote_id: str, by: str) -> dict[str, Any]:
        try:
            return await service.place_order(quote_id, by=by)
        except (VendorError, service.VendorServiceError) as exc:
            raise _error(exc) from exc

    async def order_status(self, order_id: str) -> dict[str, Any]:
        try:
            return await service.order_status(order_id)
        except (VendorError, service.VendorServiceError) as exc:
            raise _error(exc) from exc

    async def handoffs(self, service_name: str, work_product_ids: list[str]) -> dict[str, Any]:
        from api_gateway.vendors.handoffs import handoffs_for

        return await handoffs_for(service_name, work_product_ids)


def make_vendor_service() -> LocalVendorService:
    return LocalVendorService()
