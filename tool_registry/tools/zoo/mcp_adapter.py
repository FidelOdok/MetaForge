"""Zoo MCP adapter: ``zoo.*``, cloud CAD conversion and mass properties.

Three tools, split by what each one does to the world (``mcp_core.annotations``):

- ``zoo.convert`` uploads a workspace file to Zoo and writes the converted
  file(s) back under ``output/zoo/<operation_id>/``, where
  ``twin.commit_geometry(file_path=..., format=...)`` can take them. A file up
  to 25 MB converts in the call; a larger one is queued.
- ``zoo.get_conversion`` follows a queued conversion and writes its files once
  it completes.
- ``zoo.mass_properties`` uploads a file and reports volume, surface area,
  centre of mass, bounding box and (given a density) mass. Writes nothing.

All three send the file to Zoo's servers. In-process like ``meshy``: no
container, only a token.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.zoo.client import (
    AREA_UNIT,
    EXPORT_FORMATS,
    IMPORT_FORMATS,
    LENGTH_UNITS,
    MAX_UPLOAD_BYTES,
    VOLUME_UNIT,
    ZooClient,
    ZooError,
    ZooOperation,
    detect_format,
    safe_output_name,
    validate_export_format,
    validate_operation_id,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.zoo.mcp_adapter")

# Network-bound: one upload of up to 100 MB and its converted result.
_RESOURCE_LIMITS = ResourceLimits(max_memory_mb=512, max_cpu_seconds=300, max_disk_mb=1024)

_MAX_WAIT_SECONDS = 120
_DEFAULT_WAIT_SECONDS = 60
_OUTPUT_SUBDIR = Path("output") / "zoo"

# Mesh formats carry triangles, not surfaces, so a STEP made from one is a
# faceted solid. Said in the result so nobody mistakes it for real CAD.
_MESH_FORMATS = frozenset({"stl", "obj", "ply", "fbx", "gltf"})

_SRC_FORMAT_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": list(IMPORT_FORMATS),
    "description": (
        "Format of the input. Optional when the suffix says it (.step, .sldprt, "
        ".x_t, .catpart, .ipt, .stl, ...); required for .prt (nx or creo)."
    ),
}
_FILE_PATH_SCHEMA: dict[str, Any] = {
    "type": "string",
    "description": "Path of the input file on the adapter workspace.",
}
_WAIT_SCHEMA: dict[str, Any] = {
    "type": "integer",
    "minimum": 0,
    "maximum": _MAX_WAIT_SECONDS,
    "default": _DEFAULT_WAIT_SECONDS,
}
_FILES_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "operation_id": {"type": "string"},
        "status": {"type": "string"},
        "files": {"type": "array"},
        "file_path": {"type": "string"},
        "note": {"type": "string"},
    },
}


class ZooMcpServer(McpToolServer):
    """MCP wrapper over :class:`ZooClient`."""

    poll_interval_seconds = 3.0

    def __init__(self, client: ZooClient, workspace_dir: Path | None = None) -> None:
        super().__init__(adapter_id="zoo", version="0.1.0")
        self._client = client
        self._workspace = workspace_dir or Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))
        self._register_tools()

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="zoo.convert",
                adapter_id="zoo",
                name="Zoo Convert CAD File",
                description=(
                    "Convert a CAD or mesh file on the adapter workspace with Zoo's "
                    "cloud API and write the result back to the workspace. Reads "
                    "STEP, SolidWorks (.sldprt), CATIA, NX, Creo, Inventor, "
                    "Parasolid, ACIS, STL, OBJ, PLY, FBX and glTF; writes STEP, "
                    "STL, GLB, glTF, OBJ, PLY or FBX. Use it to bring a supplier's "
                    "native CAD part into MetaForge as STEP, or to make an STL for "
                    "a printer. The file is uploaded to Zoo. Returns 'file_path': "
                    "pass it unchanged to twin.commit_geometry with format set to "
                    "output_format and source_tool 'zoo.convert'. A file over 25 MB "
                    "is queued: the result then has status 'in_progress' and an "
                    "operation_id for zoo.get_conversion. Converting a mesh (STL, "
                    "OBJ, ...) to STEP gives a faceted solid, not editable CAD."
                ),
                capability="cad_conversion",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": _FILE_PATH_SCHEMA,
                        "output_format": {"type": "string", "enum": list(EXPORT_FORMATS)},
                        "src_format": _SRC_FORMAT_SCHEMA,
                        "wait_seconds": {
                            **_WAIT_SCHEMA,
                            "description": "How long to wait for a queued conversion.",
                        },
                    },
                    "required": ["file_path", "output_format"],
                },
                output_schema=_FILES_OUTPUT,
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_convert,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="zoo.get_conversion",
                adapter_id="zoo",
                name="Zoo Conversion Status",
                description=(
                    "Follow a queued zoo.convert by its operation_id. Waits up to "
                    "'wait_seconds' (default 60, max 120); once the conversion has "
                    "completed it writes the files to the workspace and returns "
                    "their file_path, exactly as zoo.convert does. A failed "
                    "conversion reports Zoo's own reason."
                ),
                capability="cad_conversion",
                input_schema={
                    "type": "object",
                    "properties": {
                        "operation_id": {"type": "string"},
                        "wait_seconds": _WAIT_SCHEMA,
                    },
                    "required": ["operation_id"],
                },
                output_schema=_FILES_OUTPUT,
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_get_conversion,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="zoo.mass_properties",
                adapter_id="zoo",
                name="Zoo Mass Properties",
                description=(
                    "Measure a CAD or mesh file on the adapter workspace with Zoo's "
                    "cloud API: volume, surface area, centre of mass and bounding "
                    "box, plus mass when 'material_density' (kg/m^3) is given, e.g. "
                    "2700 for aluminium, 1240 for PLA. The file is uploaded to Zoo; "
                    "nothing is written. Zoo assumes the file is at its true scale "
                    "in 'length_unit' (default mm): an STL carries no units, so "
                    "check the bounding box looks right before trusting a mass."
                ),
                capability="mass_properties",
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": _FILE_PATH_SCHEMA,
                        "src_format": _SRC_FORMAT_SCHEMA,
                        "length_unit": {
                            "type": "string",
                            "enum": list(LENGTH_UNITS),
                            "default": "mm",
                        },
                        "material_density": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "description": "Density in kg/m^3; omit to skip mass.",
                        },
                    },
                    "required": ["file_path"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "units": {"type": "object"},
                        "volume": {"type": "number"},
                        "surface_area": {"type": "number"},
                        "center_of_mass": {"type": "object"},
                        "bounding_box": {"type": "object"},
                        "mass": {"type": "number"},
                    },
                },
                phase=2,
                resource_limits=_RESOURCE_LIMITS,
            ),
            handler=self.handle_mass_properties,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_convert(self, arguments: dict[str, Any]) -> dict[str, Any]:
        relative = _require_str(arguments, "file_path")
        wait = _coerce_wait(arguments.get("wait_seconds"))
        with tracer.start_as_current_span("zoo.mcp.convert") as span:
            try:
                path = self._input_file(relative)
                src_format = detect_format(path, _optional_str(arguments, "src_format"))
                output_format = validate_export_format(arguments.get("output_format"))
                span.set_attribute("zoo.src_format", src_format)
                span.set_attribute("zoo.output_format", output_format)
                operation = await self._client.convert(
                    path.read_bytes(), src_format=src_format, output_format=output_format
                )
                operation = await self._await(operation, wait)
                result = self._conversion_result(operation, output_format)
            except ZooError as exc:
                span.record_exception(exc)
                raise ValueError(f"zoo.convert failed: {exc}") from exc
            if src_format in _MESH_FORMATS and output_format == "step":
                result["note"] = (
                    "converted from a mesh: the STEP is a faceted solid, not editable CAD"
                )
            return result

    async def handle_get_conversion(self, arguments: dict[str, Any]) -> dict[str, Any]:
        operation_id = _require_str(arguments, "operation_id")
        wait = _coerce_wait(arguments.get("wait_seconds"))
        with tracer.start_as_current_span("zoo.mcp.get_conversion") as span:
            span.set_attribute("zoo.operation_id", operation_id)
            try:
                operation = await self._await(await self._client.get_operation(operation_id), wait)
                if operation.type not in (None, "file_conversion"):
                    raise ZooError(
                        f"operation {operation_id} is a {operation.type}, not a conversion"
                    )
                output_format = str(operation.raw.get("output_format") or "")
                return self._conversion_result(operation, output_format)
            except ZooError as exc:
                span.record_exception(exc)
                raise ValueError(f"zoo.get_conversion failed: {exc}") from exc

    async def handle_mass_properties(self, arguments: dict[str, Any]) -> dict[str, Any]:
        relative = _require_str(arguments, "file_path")
        length_unit = arguments.get("length_unit") or "mm"
        if length_unit not in LENGTH_UNITS:
            raise ValueError(f"'length_unit' must be one of {list(LENGTH_UNITS)}")
        density = _optional_density(arguments.get("material_density"))
        with tracer.start_as_current_span("zoo.mcp.mass_properties") as span:
            try:
                path = self._input_file(relative)
                src_format = detect_format(path, _optional_str(arguments, "src_format"))
                span.set_attribute("zoo.src_format", src_format)
                data = path.read_bytes()
                calls: dict[str, Any] = {
                    "volume": self._measure(
                        "volume", data, src_format, output_unit=VOLUME_UNIT[length_unit]
                    ),
                    "surface_area": self._measure(
                        "surface-area", data, src_format, output_unit=AREA_UNIT[length_unit]
                    ),
                    "center_of_mass": self._measure(
                        "center-of-mass", data, src_format, output_unit=length_unit
                    ),
                    "bounding_box": self._measure(
                        "bounding-box", data, src_format, output_unit=length_unit
                    ),
                }
                if density is not None:
                    calls["mass"] = self._measure(
                        "mass",
                        data,
                        src_format,
                        material_density=density,
                        material_density_unit="kg:m3",
                        output_unit="g",
                    )
                values = dict(zip(calls, await asyncio.gather(*calls.values()), strict=True))
            except ZooError as exc:
                span.record_exception(exc)
                raise ValueError(f"zoo.mass_properties failed: {exc}") from exc
        units = {
            "length": length_unit,
            "area": AREA_UNIT[length_unit],
            "volume": VOLUME_UNIT[length_unit],
        }
        result: dict[str, Any] = {"file_path": relative, "src_format": src_format, "units": units}
        result.update(values)
        if density is not None:
            units["mass"] = "g"
            result["material_density_kg_m3"] = density
        logger.info("zoo_mass_properties", src_format=src_format, with_mass=density is not None)
        return result

    async def close(self) -> None:
        """Release the client's HTTP pool."""
        await self._client.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _input_file(self, relative: str) -> Path:
        """A file under the workspace; anything that escapes it is refused."""
        root = self._workspace.resolve()
        path = Path(relative)
        resolved = (path if path.is_absolute() else root / path).resolve()
        if not resolved.is_relative_to(root):
            raise ZooError(f"{relative!r} is outside the adapter workspace")
        if not resolved.is_file():
            raise ZooError(f"no file at {relative!r} on the adapter workspace")
        size = resolved.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise ZooError(f"{relative!r} is {size // (1024 * 1024)} MB; the limit is 100 MB")
        if size == 0:
            raise ZooError(f"{relative!r} is empty")
        return resolved

    async def _await(self, operation: ZooOperation, wait: int) -> ZooOperation:
        """Poll a queued operation until it finishes or ``wait`` runs out."""
        deadline = time.monotonic() + wait
        while operation.pending and time.monotonic() < deadline:
            await asyncio.sleep(self.poll_interval_seconds)
            operation = await self._client.get_operation(operation.id)
        if operation.status == "failed":
            raise ZooError(operation.error or "Zoo reported the operation failed")
        return operation

    async def _measure(self, endpoint: str, data: bytes, src_format: str, **query: Any) -> Any:
        operation = await self._client.measure(endpoint, data, src_format=src_format, **query)
        operation = await self._await(operation, _MAX_WAIT_SECONDS)
        if operation.pending:
            raise ZooError(f"Zoo is still measuring ({endpoint}); try again shortly")
        key = endpoint.replace("-", "_")
        if key not in operation.raw:
            raise ZooError(f"Zoo's {endpoint} result carried no '{key}'")
        return operation.raw[key]

    def _conversion_result(self, operation: ZooOperation, output_format: str) -> dict[str, Any]:
        if operation.pending:
            return {
                "operation_id": operation.id,
                "status": operation.status,
                "files": [],
                "note": "still converting; call zoo.get_conversion with this operation_id",
            }
        if not operation.outputs:
            raise ZooError("Zoo completed the conversion but returned no files")
        validate_operation_id(operation.id)
        files: list[dict[str, Any]] = []
        for name, data in sorted(operation.outputs.items()):
            relative = _OUTPUT_SUBDIR / operation.id / safe_output_name(name)
            _write_atomic(self._workspace / relative, data)
            files.append(
                {
                    "format": relative.suffix.lstrip(".").lower(),
                    "file_path": relative.as_posix(),
                    "size_bytes": len(data),
                }
            )
        primary = next((f for f in files if f["format"] == output_format), files[0])
        logger.info("zoo_converted", operation_id=operation.id, file_count=len(files))
        return {
            "operation_id": operation.id,
            "status": operation.status,
            "files": files,
            "file_path": primary["file_path"],
        }


def _write_atomic(dest: Path, data: bytes) -> None:
    """Write via a sibling and rename, so a crash never leaves half a file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    try:
        partial.write_bytes(data)
        partial.replace(dest)
    except OSError:
        partial.unlink(missing_ok=True)
        raise


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key!r} is required and must be a non-empty string")
    return value.strip()


def _optional_str(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_density(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("'material_density' must be a number in kg/m^3") from exc
    if value <= 0:
        raise ValueError("'material_density' must be greater than 0")
    return value


def _coerce_wait(raw: Any) -> int:
    if raw is None:
        return _DEFAULT_WAIT_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("'wait_seconds' must be an integer") from exc
    if value < 0 or value > _MAX_WAIT_SECONDS:
        raise ValueError(f"'wait_seconds' must be in [0, {_MAX_WAIT_SECONDS}]")
    return value
