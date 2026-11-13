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

__all__ = ["GatewayMachineView", "RemoteMachineLibrary"]

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

    async def dispatch_job(self, machine_id: str, work_product_id: str, by: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "POST",
            "/v1/library/jobs",
            json={"machine_id": machine_id, "work_product_id": work_product_id, "by": by},
        )
        return result

    async def job_status(self, job_id: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "GET", f"/v1/library/jobs/{quote(job_id, safe='')}/status"
        )
        return result

    async def record_job_result(self, job_id: str, by: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "POST", f"/v1/library/jobs/{quote(job_id, safe='')}/result", json={"by": by}
        )
        return result

    # FORGE-451 / FORGE-446: processes and materials.

    @staticmethod
    def _plural(kind: str) -> str:
        return {
            "process": "processes",
            "material": "materials",
            "component": "components",
            "appearance": "appearances",
            "ergonomic_limit": "ergonomic-limits",
        }[kind]

    async def list_entries(self, kind: str, family: str | None) -> dict[str, Any]:
        query = f"?{urlencode({'family': family})}" if family else ""
        result: dict[str, Any] = await self._gateway.request(
            "GET", f"/v1/library/{self._plural(kind)}{query}"
        )
        return result

    async def get_entry(self, kind: str, ref: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "GET", f"/v1/library/{self._plural(kind)}/{quote(ref, safe='')}"
        )
        return result

    async def register_entry(self, kind: str, data: dict[str, Any], by: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "POST", f"/v1/library/{self._plural(kind)}", json={**data, "by": by}
        )
        return result

    async def update_entry(
        self, kind: str, ref: str, changes: dict[str, Any], by: str
    ) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "PATCH",
            f"/v1/library/{self._plural(kind)}/{quote(ref, safe='')}",
            json={"changes": changes, "by": by},
        )
        return result

    async def process_limits(self, ref: str, material: str | None) -> dict[str, Any]:
        query = f"?{urlencode({'material': material})}" if material else ""
        result: dict[str, Any] = await self._gateway.request(
            "GET", f"/v1/library/processes/{quote(ref, safe='')}/limits{query}"
        )
        return result

    async def standard_part(
        self, family: str, size: str, length_mm: float | None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"family": family, "size": size}
        if length_mm is not None:
            params["length_mm"] = length_mm
        result: dict[str, Any] = await self._gateway.request(
            "GET", f"/v1/library/components/standard?{urlencode(params)}"
        )
        return result


class GatewayMachineView:
    """A read-only, synchronous view of the gateway's machines for this process.

    The sidecar's DFM looks a selected machine up in the process library
    (``library.get`` and ``library.list``, the ``SqliteMachineLibrary``
    surface). Without this view that library was an empty in-memory store,
    so a printer registered through ``library.*`` and selected in the
    manufacturing context read as "no machine is selected" and the exact
    build-volume check never ran in the sidecar. Reads only: writes go
    through ``RemoteMachineLibrary``.
    """

    def __init__(
        self,
        gateway_url: str,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base = gateway_url.rstrip("/")
        self._client = client
        self._timeout = timeout_seconds

    def _get(self, path: str) -> Any:
        url = f"{self._base}{path}"
        if self._client is not None:
            response = self._client.get(url)
        else:
            with httpx.Client(timeout=self._timeout) as http:
                response = http.get(url)
        if response.status_code == 404:
            raise KeyError(path)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _entry(row: dict[str, Any]) -> Any:
        from twin_core.library.machines import Capability, MachineInstance, MachineModel

        body = dict(row)
        layer = body.pop("layer", None)
        caps = {k: Capability(**v) for k, v in (body.pop("capabilities", None) or {}).items()}
        cls = MachineModel if layer == "model" else MachineInstance
        fields = getattr(cls, "__dataclass_fields__", {})
        return cls(capabilities=caps, **{k: v for k, v in body.items() if k in fields})

    def get(self, machine_id: str) -> Any:
        data = self._get(f"/v1/library/machines/{quote(machine_id, safe='')}")
        return self._entry(data.get("machine", data))

    def list(self, *, layer: str | None = None, kind: str | None = None) -> list[Any]:
        query = {k: v for k, v in (("layer", layer), ("kind", kind)) if v}
        suffix = f"?{urlencode(query)}" if query else ""
        data = self._get(f"/v1/library/machines{suffix}")
        return [self._entry(r) for r in data.get("machines", [])]


def remote_material_source(gateway_url: str, *, client: httpx.AsyncClient | None = None) -> Any:
    """A material source for this process that asks the gateway's library (FORGE-446)."""
    from twin_core.library.material_resolver import MaterialResolutionError

    base = gateway_url.rstrip("/")

    async def resolve(name: str) -> dict[str, Any]:
        owned = client is None
        http = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)
        try:
            response = await http.get(f"{base}/v1/library/materials/resolve", params={"name": name})
        finally:
            if owned:
                await http.aclose()
        if response.status_code == 422:
            detail = response.json().get("detail") or {}
            raise MaterialResolutionError(
                name, list(detail.get("candidates") or []), str(detail.get("message") or "")
            )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    return resolve
