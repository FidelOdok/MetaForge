"""Distributor MCP adapter (MET-434).

One ``McpToolServer`` per distributor (Digi-Key, Mouser, Nexar/Octopart).
Each instance wraps a concrete :class:`DistributorAdapter` and exposes
the four canonical methods as MCP tools:

* ``<distributor>.search``           — keyword search
* ``<distributor>.get_product``      — full part details by MPN
* ``<distributor>.get_pricing``      — quantity-tier price breaks by MPN
* ``<distributor>.get_availability`` — stock / lead time / MOQ by MPN

The ``distributor_id`` namespace (``digikey`` / ``mouser`` / ``nexar``)
is derived from ``adapter.name`` so every distributor's tools share the
same schema shapes and one wrapper class serves all three. Adding a
fourth distributor (Arrow, Avnet, Newark) is a one-line change in
``tool_registry/bootstrap.py``; no new wrapper needed.

Layer note: imports a ``digital_twin``-adjacent concept (the
``DistributorAdapter`` base lives under ``tool_registry/tools/``
already, so there is no cross-layer leak).
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.distributors.base import DistributorAdapter

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.distributors.mcp_adapter")


_RESOURCE_LIMITS = ResourceLimits(max_memory_mb=256, max_cpu_seconds=30, max_disk_mb=64)


class DistributorMcpServer(McpToolServer):
    """MCP wrapper for a :class:`DistributorAdapter` instance.

    Construction is pure config — no I/O. The wrapped adapter owns
    transport (httpx pool, OAuth token cache, rate limiter) and is
    closed explicitly via :meth:`close`.
    """

    def __init__(self, adapter: DistributorAdapter) -> None:
        # ``adapter.name`` is "DigiKey" / "Mouser" / "Nexar" — lower-case
        # for the MCP namespace so tool ids stay stable across casing
        # changes in the wrapped client.
        distributor_id = adapter.name.lower()
        super().__init__(adapter_id=distributor_id, version="0.1.0")
        self._adapter = adapter
        self._distributor_id = distributor_id
        self._register_tools()

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        did = self._distributor_id
        display = self._adapter.name  # e.g. "DigiKey"

        self.register_tool(
            manifest=ToolManifest(
                tool_id=f"{did}.search",
                adapter_id=did,
                name=f"{display} Search",
                description=(
                    f"Keyword search against the {display} catalog. Returns "
                    "ranked parts with stock / lead time / lifecycle metadata. "
                    "Returns an empty list when credentials are missing or the "
                    "API call fails — never raises (errors are observability-only)."
                ),
                capability="distributor_search",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Free-text query (MPN, family, function).",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 10,
                            "description": "Maximum hits to return.",
                        },
                    },
                    "required": ["query"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "results": {"type": "array"},
                        "count": {"type": "integer"},
                    },
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_search,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id=f"{did}.get_product",
                adapter_id=did,
                name=f"{display} Get Product",
                description=(
                    f"Full part record from {display} keyed on MPN. Returns "
                    "specs + package + datasheet URL + lifecycle. Returns "
                    "``null`` when the part is unknown or the API is unavailable."
                ),
                capability="distributor_get_product",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mpn": {
                            "type": "string",
                            "description": "Manufacturer Part Number.",
                        },
                    },
                    "required": ["mpn"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "part": {"type": ["object", "null"]},
                    },
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_get_product,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id=f"{did}.get_pricing",
                adapter_id=did,
                name=f"{display} Get Pricing",
                description=(
                    f"Quantity-tier price breaks from {display} keyed on MPN. "
                    "Returns an empty list when the part is unknown or the API "
                    "is unavailable."
                ),
                capability="distributor_pricing",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mpn": {
                            "type": "string",
                            "description": "Manufacturer Part Number.",
                        },
                    },
                    "required": ["mpn"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "breaks": {"type": "array"},
                        "count": {"type": "integer"},
                    },
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_get_pricing,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id=f"{did}.get_availability",
                adapter_id=did,
                name=f"{display} Get Availability",
                description=(
                    f"Stock / lead time / MOQ from {display} keyed on MPN. "
                    "Returns ``null`` when the part is unknown or the API "
                    "is unavailable."
                ),
                capability="distributor_availability",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mpn": {
                            "type": "string",
                            "description": "Manufacturer Part Number.",
                        },
                    },
                    "required": ["mpn"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "availability": {"type": ["object", "null"]},
                    },
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_get_availability,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = self._require_str(arguments, "query")
        limit = self._coerce_limit(arguments.get("limit", 10))
        with tracer.start_as_current_span(f"{self._distributor_id}.mcp.search") as span:
            span.set_attribute("distributor.query_length", len(query))
            span.set_attribute("distributor.limit", limit)
            results = await self._adapter.search_parts(query=query, limit=limit)
            span.set_attribute("distributor.result_count", len(results))
            logger.info(
                "distributor_search",
                distributor=self._distributor_id,
                query=query[:80],
                count=len(results),
            )
            return {
                "results": [r.model_dump(mode="json") for r in results],
                "count": len(results),
            }

    async def handle_get_product(self, arguments: dict[str, Any]) -> dict[str, Any]:
        mpn = self._require_str(arguments, "mpn")
        with tracer.start_as_current_span(f"{self._distributor_id}.mcp.get_product") as span:
            span.set_attribute("distributor.mpn", mpn)
            part = await self._adapter.get_part_details(mpn=mpn)
            span.set_attribute("distributor.found", part is not None)
            return {"part": part.model_dump(mode="json") if part is not None else None}

    async def handle_get_pricing(self, arguments: dict[str, Any]) -> dict[str, Any]:
        mpn = self._require_str(arguments, "mpn")
        with tracer.start_as_current_span(f"{self._distributor_id}.mcp.get_pricing") as span:
            span.set_attribute("distributor.mpn", mpn)
            breaks = await self._adapter.get_pricing(mpn=mpn)
            span.set_attribute("distributor.break_count", len(breaks))
            return {
                "breaks": [b.model_dump(mode="json") for b in breaks],
                "count": len(breaks),
            }

    async def handle_get_availability(self, arguments: dict[str, Any]) -> dict[str, Any]:
        mpn = self._require_str(arguments, "mpn")
        with tracer.start_as_current_span(f"{self._distributor_id}.mcp.get_availability") as span:
            span.set_attribute("distributor.mpn", mpn)
            info = await self._adapter.get_availability(mpn=mpn)
            span.set_attribute("distributor.found", info is not None)
            return {
                "availability": info.model_dump(mode="json") if info is not None else None,
            }

    async def close(self) -> None:
        """Release the wrapped adapter's HTTP pool."""
        await self._adapter.close()

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_str(arguments: dict[str, Any], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key!r} is required and must be a non-empty string")
        return value.strip()

    @staticmethod
    def _coerce_limit(raw: Any) -> int:
        try:
            limit = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("'limit' must be an integer") from exc
        if limit < 1 or limit > 50:
            raise ValueError("'limit' must be in [1, 50]")
        return limit
