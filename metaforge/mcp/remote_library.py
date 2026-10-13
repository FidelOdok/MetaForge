"""The machine library from the sidecar, through the gateway (FORGE-451).

The library is the gateway's SQLite file. A sidecar with its own copy would
hold machines the dashboard never shows, so ``library.*`` here calls the
gateway's ``/v1/library/machines`` routes and returns their shapes, the same
as ``api_gateway.library.mcp_bindings``. No ``api_gateway`` import, for the
reason ``remote_flows`` gives.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

import httpx

from metaforge.mcp.remote_flows import _Gateway

__all__ = ["RemoteMachineLibrary"]

DEFAULT_TIMEOUT_SECONDS = 30.0


class RemoteMachineLibrary:
    """``library.*`` against the gateway's store."""

    def __init__(
        self,
        gateway_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._gateway = _Gateway(gateway_url, client, timeout_seconds)

    async def list_machines(self, layer: str | None, kind: str | None) -> dict[str, Any]:
        query = urlencode({k: v for k, v in (("layer", layer), ("kind", kind)) if v})
        path = "/v1/library/machines" + (f"?{query}" if query else "")
        result: dict[str, Any] = await self._gateway.request("GET", path)
        return result

    async def get_machine(self, machine_id: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "GET", f"/v1/library/machines/{quote(machine_id, safe='')}"
        )
        return result

    async def register_machine(self, data: dict[str, Any], by: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "POST", "/v1/library/machines", json={**data, "by": by}
        )
        return result

    async def update_machine(
        self, machine_id: str, changes: dict[str, Any], by: str
    ) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "PATCH",
            f"/v1/library/machines/{quote(machine_id, safe='')}",
            json={"changes": changes, "by": by},
        )
        return result

    async def machine_limits(self, machine_id: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "GET", f"/v1/library/machines/{quote(machine_id, safe='')}/limits"
        )
        return result

    async def discover_machine(self, connection: dict[str, Any], name: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "POST", "/v1/library/discover", json={"connection": connection, "name": name}
        )
        return result
