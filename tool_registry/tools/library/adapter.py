"""MCP adapter for the machine library (FORGE-451).

``library.*`` lets an agent see which machines a person can make parts on and
what each can do, with the source of every figure. Reads run freely; the two
writes (``register_machine``, ``update_machine``) are not in
``mcp_core.annotations.READ_ONLY``, so the approval gate holds them for a
person like every other write.

The adapter takes one service object with five coroutines, so it does not care
whether the library is in this process (the gateway) or reached over HTTP (the
sidecar, ``metaforge.mcp.remote_library``). Both return the gateway route
shapes.
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.library.adapter")

MACHINE_KINDS = [
    "fdm_printer",
    "resin_printer",
    "sls_printer",
    "cnc_mill",
    "cnc_router",
    "cnc_lathe",
    "laser_cutter",
    "plasma_cutter",
    "waterjet",
    "press_brake",
    "welder",
    "other",
]


class MachineLibraryService(Protocol):
    async def list_machines(self, layer: str | None, kind: str | None) -> dict[str, Any]: ...

    async def get_machine(self, machine_id: str) -> dict[str, Any]: ...

    async def register_machine(self, data: dict[str, Any], by: str) -> dict[str, Any]: ...

    async def update_machine(
        self, machine_id: str, changes: dict[str, Any], by: str
    ) -> dict[str, Any]: ...

    async def machine_limits(self, machine_id: str) -> dict[str, Any]: ...

    async def discover_machine(self, connection: dict[str, Any], name: str) -> dict[str, Any]: ...


_CAPABILITY_HELP = (
    "capabilities is an object keyed by name, each {value, unit, source_kind, source_ref}. "
    "source_kind is measurement or manufacturer_spec (both need source_ref: the spec sheet "
    "URL or the measurement note), machine_report (read from the machine's controller by "
    "library.discover_machine, with the endpoint and time), user (the person said so) "
    "or assumption (flagged for "
    "review). Names: build_volume_mm and travel_mm ([x, y, z] in mm); nozzle_mm, "
    "layer_height_min_mm, min_feature_mm, min_wall_mm, tolerance_mm, min_bend_radius_mm, "
    "min_tool_diameter_mm, max_sheet_thickness_mm (mm); max_overhang_deg (deg); "
    "max_part_mass_kg (kg); spindle_max_rpm (rpm); laser_power_w (W); materials (list of "
    "names, unit 'list'). Never fill a value you were not given: leave it out."
)

_CAPABILITIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "properties": {
            "value": {},
            "unit": {"type": "string"},
            "source_kind": {
                "type": "string",
                "enum": [
                    "measurement",
                    "manufacturer_spec",
                    "machine_report",
                    "user",
                    "assumption",
                ],
            },
            "source_ref": {"type": "string"},
        },
        "required": ["value", "unit", "source_kind"],
    },
}

_CONNECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "How to reach the machine: {protocol: octoprint|moonraker|prusalink|none, url, "
        "credential_env}. credential_env names an environment variable on the gateway, "
        "starting METAFORGE_MACHINE_, "
        "that holds the API key; never put a key or password here."
    ),
}


def _limits() -> ResourceLimits:
    return ResourceLimits(max_memory_mb=128, max_cpu_seconds=10, max_disk_mb=1)


class LibraryServer(McpToolServer):
    """Serves ``library.list_machines`` and its siblings."""

    def __init__(self, library: MachineLibraryService) -> None:
        super().__init__(adapter_id="library", version="0.1.0")
        self._library = library
        self.register_tool(
            manifest=ToolManifest(
                tool_id="library.list_machines",
                adapter_id="library",
                name="List Machines",
                description=(
                    "The machines in the person's machine library: models (what a machine "
                    "type can do) and instances (a machine they own or a vendor runs, with "
                    "status). layer narrows to model or instance; kind to one machine kind. "
                    "Read this before ranking processes: a process with no available machine "
                    "is a vendor job or not an option."
                ),
                capability="machine_library",
                input_schema={
                    "type": "object",
                    "properties": {
                        "layer": {"type": "string", "enum": ["model", "instance"]},
                        "kind": {"type": "string", "enum": MACHINE_KINDS},
                    },
                },
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=self.list_machines,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="library.get_machine",
                adapter_id="library",
                name="Get Machine",
                description=(
                    "One machine with every capability, its source, and its version history."
                ),
                capability="machine_library",
                input_schema={
                    "type": "object",
                    "properties": {"machine_id": {"type": "string"}},
                    "required": ["machine_id"],
                },
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=self.get_machine,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="library.machine_limits",
                adapter_id="library",
                name="Machine Limits",
                description=(
                    "A machine instance's effective limits (its own capabilities over its "
                    "model's), shaped to pass straight on: 'candidate' is a "
                    "rank_manufacturing_processes candidate and 'limits' are "
                    "check_manufacturability limits, each citing its source. needs_review "
                    "lists figures that are assumptions. A limit the machine has no figure "
                    "for is absent, so the check reads it as unknown."
                ),
                capability="machine_library",
                input_schema={
                    "type": "object",
                    "properties": {"machine_id": {"type": "string"}},
                    "required": ["machine_id"],
                },
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=self.machine_limits,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="library.register_machine",
                adapter_id="library",
                name="Register Machine",
                description=(
                    "Add a machine model (layer: model; kind, make, model, processes, "
                    "capabilities) or a machine instance (layer: instance; name, kind, "
                    "model_id, processes, route in_house or vendor, location, status, "
                    "capabilities that override the model, connection). Held for the "
                    "person's approval. " + _CAPABILITY_HELP
                ),
                capability="machine_library",
                input_schema={
                    "type": "object",
                    "properties": {
                        "layer": {"type": "string", "enum": ["model", "instance"]},
                        "kind": {"type": "string", "enum": MACHINE_KINDS},
                        "make": {"type": "string"},
                        "model": {"type": "string"},
                        "name": {"type": "string"},
                        "model_id": {"type": "string"},
                        "processes": {"type": "array", "items": {"type": "string"}},
                        "route": {"type": "string", "enum": ["in_house", "vendor"]},
                        "location": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["available", "busy", "offline", "retired"],
                        },
                        "capabilities": _CAPABILITIES_SCHEMA,
                        "connection": _CONNECTION_SCHEMA,
                    },
                    "required": ["layer", "kind"],
                },
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=self.register_machine,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="library.update_machine",
                adapter_id="library",
                name="Update Machine",
                description=(
                    "Change a machine: processes, capabilities (merged by name), and for an "
                    "instance status, location, name, route or connection. The old version "
                    "is kept. Held for the person's approval. " + _CAPABILITY_HELP
                ),
                capability="machine_library",
                input_schema={
                    "type": "object",
                    "properties": {
                        "machine_id": {"type": "string"},
                        "changes": {"type": "object"},
                    },
                    "required": ["machine_id", "changes"],
                },
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=self.update_machine,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="library.discover_machine",
                adapter_id="library",
                name="Discover Machine",
                description=(
                    "Read a 3D printer's own controller (OctoPrint, Moonraker for Klipper, "
                    "or PrusaLink) and return a proposed machine instance: build volume or "
                    "axis travel, nozzle and current state, each recorded as machine_report "
                    "with the endpoint and time read. Registers nothing: show the person the "
                    "entry and its missing figures, then call library.register_machine "
                    "(held). connection is {protocol, url, credential_env}; the key is read "
                    "on the gateway from credential_env, which must start METAFORGE_MACHINE_."
                ),
                capability="machine_library",
                input_schema={
                    "type": "object",
                    "properties": {
                        "connection": _CONNECTION_SCHEMA,
                        "name": {
                            "type": "string",
                            "description": "What the person calls it; else the hostname.",
                        },
                    },
                    "required": ["connection"],
                },
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=self.discover_machine,
        )

    async def discover_machine(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("library.discover_machine"):
            connection = arguments.get("connection")
            if not isinstance(connection, dict):
                raise ValueError("connection must be an object")
            return await self._library.discover_machine(
                connection, str(arguments.get("name") or "")
            )

    async def list_machines(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("library.list_machines"):
            return await self._library.list_machines(
                arguments.get("layer") or None, arguments.get("kind") or None
            )

    async def get_machine(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("library.get_machine"):
            return await self._library.get_machine(str(arguments["machine_id"]))

    async def machine_limits(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("library.machine_limits") as span:
            span.set_attribute("machine.id", str(arguments["machine_id"]))
            return await self._library.machine_limits(str(arguments["machine_id"]))

    async def register_machine(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("library.register_machine"):
            data = dict(arguments)
            result = await self._library.register_machine(data, "mcp")
            logger.info("library_machine_registered", machine=result.get("machine", {}).get("id"))
            return result

    async def update_machine(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("library.update_machine"):
            machine_id = str(arguments["machine_id"])
            changes = arguments.get("changes") or {}
            if not isinstance(changes, dict):
                raise ValueError("changes must be an object")
            result = await self._library.update_machine(machine_id, changes, "mcp")
            logger.info("library_machine_updated", machine=machine_id)
            return result
