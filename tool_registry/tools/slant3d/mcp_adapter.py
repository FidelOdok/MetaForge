"""Slant 3D MCP adapter: ``slant3d.*``, print-farm quoting for an STL.

Four tools:

- ``slant3d.analyze_part`` uploads a workspace STL to Slant 3D, which slices
  it and reports size, volume, weight, print time and whether it needs
  supports. It creates a file on the Slant account and spends nothing.
- ``slant3d.quote`` prices an analysed part in a filament and quantity, in
  USD, print cost only (shipping is priced when an order is drafted).
- ``slant3d.get_part`` and ``slant3d.list_filaments`` read.

There is no order tool: an order charges the account and ships a part, and
that is a decision for a person, not a ticked tool box.

In-process like ``meshy``: no container, only a key.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.slant3d.client import (
    MATERIALS,
    MAX_QUANTITY,
    MAX_UPLOAD_BYTES,
    Slant3DClient,
    Slant3DError,
    Slant3DFile,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.slant3d.mcp_adapter")

# Network-bound: a JSON call or one upload of up to 100 MB.
_RESOURCE_LIMITS = ResourceLimits(max_memory_mb=384, max_cpu_seconds=240, max_disk_mb=256)

_CAPABILITY = "print_quote"

_PART_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_id": {"type": "string"},
        "name": {"type": "string"},
        "metrics": {"type": "object"},
    },
}

# Slant's estimate fields, renamed to carry their unit.
_QUOTE_FIELDS = {
    "quantity": "quantity",
    "pricePerUnit": "price_per_unit_usd",
    "subtotal": "subtotal_usd",
    "bodyChargeCost": "multi_body_charge_usd",
    "oversizeSurcharge": "oversize_surcharge_usd",
    "total": "total_usd",
    "totalMaterial": "total_material",
    "estimatedPrintTime": "print_time_per_unit",
}


class Slant3DMcpServer(McpToolServer):
    """MCP wrapper over :class:`Slant3DClient`."""

    def __init__(self, client: Slant3DClient, workspace_dir: Path | None = None) -> None:
        super().__init__(adapter_id="slant3d", version="0.1.0")
        self._client = client
        self._workspace = workspace_dir or Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))
        self._register_tools()

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="slant3d.analyze_part",
                adapter_id="slant3d",
                name="Slant 3D Analyze Part",
                description=(
                    "Upload an STL from the adapter workspace to the Slant 3D print "
                    "farm, which slices it and reports its size (mm), volume (cm3), "
                    "weight (g), print time (minutes), whether it needs supports and "
                    "how many separate bodies it has. Returns a file_id for "
                    "slant3d.quote. Free, but the file is sent to Slant 3D and kept "
                    "on the account. STL only, in millimetres: convert a STEP or "
                    "GLB first (cadquery.export_geometry, zoo.convert or "
                    "tripo.convert)."
                ),
                capability=_CAPABILITY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Workspace-relative path of the .stl file.",
                        }
                    },
                    "required": ["file_path"],
                },
                output_schema=_PART_OUTPUT,
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_analyze_part,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="slant3d.quote",
                adapter_id="slant3d",
                name="Slant 3D Quote",
                description=(
                    "Price printing an analysed part on the Slant 3D farm, in USD: "
                    "per unit, the multi-body and oversize surcharges, and the "
                    "total for 'quantity'. Print cost only; shipping and tax are "
                    "not included. Without 'filament_id' it prices PLA black; get "
                    "ids from slant3d.list_filaments. Places no order and spends "
                    "nothing."
                ),
                capability=_CAPABILITY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_id": {
                            "type": "string",
                            "description": "From slant3d.analyze_part.",
                        },
                        "filament_id": {"type": "string"},
                        "quantity": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_QUANTITY,
                            "default": 1,
                        },
                    },
                    "required": ["file_id"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string"},
                        "currency": {"type": "string"},
                        "total_usd": {"type": "number"},
                        "price_per_unit_usd": {"type": "number"},
                    },
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_quote,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="slant3d.get_part",
                adapter_id="slant3d",
                name="Slant 3D Get Part",
                description=(
                    "Read back an analysed part by file_id: its metrics and any "
                    "production notes the Slant 3D team added. Read-only."
                ),
                capability=_CAPABILITY,
                input_schema={
                    "type": "object",
                    "properties": {"file_id": {"type": "string"}},
                    "required": ["file_id"],
                },
                output_schema=_PART_OUTPUT,
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_get_part,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="slant3d.list_filaments",
                adapter_id="slant3d",
                name="Slant 3D Filaments",
                description=(
                    "The filaments the Slant 3D farm prints in, with each one's id, "
                    "material (PLA, PETG or OPM), colour and whether it is in "
                    "stock. Filter by 'materials' and 'colors' (partial match, so "
                    "'black' also finds 'matte black'). Read-only."
                ),
                capability=_CAPABILITY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "materials": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(MATERIALS)},
                        },
                        "colors": {"type": "array", "items": {"type": "string"}},
                    },
                },
                output_schema={
                    "type": "object",
                    "properties": {"filaments": {"type": "array"}, "count": {"type": "integer"}},
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_list_filaments,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_analyze_part(self, arguments: dict[str, Any]) -> dict[str, Any]:
        relative = _require_str(arguments, "file_path")
        with tracer.start_as_current_span("slant3d.mcp.analyze_part") as span:
            try:
                path = self._input_file(relative)
                file = await self._client.upload_stl(path.read_bytes(), path.name)
            except Slant3DError as exc:
                span.record_exception(exc)
                raise ValueError(f"slant3d.analyze_part failed: {exc}") from exc
            span.set_attribute("slant3d.file_id", file.id)
            return _part(file)

    async def handle_quote(self, arguments: dict[str, Any]) -> dict[str, Any]:
        file_id = _require_str(arguments, "file_id")
        filament_id = arguments.get("filament_id")
        if filament_id is not None and not isinstance(filament_id, str):
            raise ValueError("'filament_id' must be a string")
        quantity = _coerce_quantity(arguments.get("quantity"))
        with tracer.start_as_current_span("slant3d.mcp.quote") as span:
            span.set_attribute("slant3d.file_id", file_id)
            try:
                raw = await self._client.estimate(
                    file_id, filament_id=filament_id or None, quantity=quantity
                )
            except Slant3DError as exc:
                span.record_exception(exc)
                raise ValueError(f"slant3d.quote failed: {exc}") from exc
            result: dict[str, Any] = {
                "file_id": file_id,
                "filament_id": filament_id or "default (PLA black)",
                "currency": "USD",
            }
            for key, name in _QUOTE_FIELDS.items():
                if isinstance(raw.get(key), (int, float)):
                    result[name] = raw[key]
            if "total_usd" not in result:
                raise ValueError("slant3d.quote failed: Slant 3D returned no total")
            result["note"] = "Print cost only; shipping and tax are added when an order is drafted."
            logger.info("slant3d_quoted", file_id=file_id, quantity=quantity)
            return result

    async def handle_get_part(self, arguments: dict[str, Any]) -> dict[str, Any]:
        file_id = _require_str(arguments, "file_id")
        with tracer.start_as_current_span("slant3d.mcp.get_part"):
            try:
                file = await self._client.get_file(file_id)
            except Slant3DError as exc:
                raise ValueError(f"slant3d.get_part failed: {exc}") from exc
            return _part(file)

    async def handle_list_filaments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        materials = _str_list(arguments, "materials")
        colors = _str_list(arguments, "colors")
        with tracer.start_as_current_span("slant3d.mcp.list_filaments"):
            try:
                raw = await self._client.list_filaments(materials=materials, colors=colors)
            except Slant3DError as exc:
                raise ValueError(f"slant3d.list_filaments failed: {exc}") from exc
            filaments = [
                {
                    "filament_id": f.get("publicId"),
                    "name": f.get("name"),
                    "material": f.get("profile"),
                    "color": f.get("color"),
                    "hex": f.get("hexValue"),
                    "available": f.get("available"),
                }
                for f in raw
            ]
            return {"filaments": filaments, "count": len(filaments)}

    async def close(self) -> None:
        """Release the client's HTTP pool."""
        await self._client.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _input_file(self, relative: str) -> Path:
        """An STL under the workspace; anything that escapes it is refused."""
        root = self._workspace.resolve()
        path = Path(relative)
        resolved = (path if path.is_absolute() else root / path).resolve()
        if not resolved.is_relative_to(root):
            raise Slant3DError(f"{relative!r} is outside the adapter workspace")
        if resolved.suffix.lower() != ".stl":
            raise Slant3DError(
                f"{relative!r} is not an STL; Slant 3D takes STL only, so convert it first"
            )
        if not resolved.is_file():
            raise Slant3DError(f"no file at {relative!r} on the adapter workspace")
        size = resolved.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise Slant3DError(f"{relative!r} is {size // (1024 * 1024)} MB; the limit is 100 MB")
        if size == 0:
            raise Slant3DError(f"{relative!r} is empty")
        return resolved


def _part(file: Slant3DFile) -> dict[str, Any]:
    result: dict[str, Any] = {"file_id": file.id, "name": file.name, "metrics": file.metrics}
    if file.notes:
        result["notes"] = file.notes
    return result


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key!r} is required and must be a non-empty string")
    return value.strip()


def _str_list(arguments: dict[str, Any], key: str) -> list[str] | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{key!r} must be a list of non-empty strings")
    return [v.strip() for v in value] or None


def _coerce_quantity(raw: Any) -> int:
    if raw is None:
        return 1
    if isinstance(raw, bool):
        raise ValueError("'quantity' must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("'quantity' must be an integer") from exc
    if not 1 <= value <= MAX_QUANTITY:
        raise ValueError(f"'quantity' must be between 1 and {MAX_QUANTITY}")
    return value
