"""``library.*`` tools for processes and materials (FORGE-451, FORGE-446).

Registered on the same ``library`` adapter as the machine tools. Reads are
read-only; ``register_*`` and ``update_*`` are additive writes held for the
person. The service is the same object the machine tools use (in-process in
the gateway, over HTTP from the sidecar).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest

tracer = get_tracer("tool_registry.tools.library.catalog_tools")

PROCESS_FAMILIES = [
    "additive",
    "subtractive",
    "cutting",
    "forming",
    "joining",
    "moulding",
    "casting",
    "pcb_fabrication",
    "pcb_assembly",
    "finishing",
    "other",
]
MATERIAL_FAMILIES = [
    "metal",
    "polymer",
    "filament",
    "resin",
    "wood",
    "sheet_good",
    "composite",
    "laminate",
    "ceramic",
    "elastomer",
    "other",
]

_SOURCES = (
    "Every value is {value, unit, source_kind, source_ref}. source_kind is standard, "
    "datasheet, manufacturer_spec, supplier or measurement (each needs source_ref: the "
    "standard and clause, the datasheet URL, the measurement note), user (the person said "
    "so) or assumption (flagged for review). Never fill a value you were not given."
)
_RULES = (
    "rules is a list of {kind, min and/or max (mm or deg), material_family (optional: the "
    "rule applies only to that family), source_kind, source_ref}. kind is wall_thickness, "
    "hole_diameter, inside_corner_radius, bend_radius, feature_size (mm), draft_angle, "
    "overhang_angle (deg), envelope (max_dims_mm [x, y, z]) or tolerance (value, mm). "
)
_PROPERTIES = (
    "properties is an object keyed by name: density_kg_m3 (kg/m3), youngs_modulus_mpa, "
    "youngs_modulus_across_mpa, yield_strength_mpa, yield_strength_across_mpa, "
    "ultimate_strength_mpa, fatigue_strength_mpa (MPa), poisson_ratio (-), elongation_pct "
    "(%), thermal_conductivity_w_mk (W/mK), cte_um_mk (um/mK), max_service_temp_c, "
    "melting_point_c (C), cost_per_kg (unit with currency, e.g. GBP/kg). "
)


def catalog_tool_ids() -> set[str]:
    """Every tool id this module registers.

    The ids are assembled per kind, which a literal-id source scan cannot
    see, so coverage checks import this (the same as ``distributor_tool_ids``).
    """
    ids = {"library.process_limits"}
    for kind, plural in (("process", "processes"), ("material", "materials")):
        ids |= {
            f"library.list_{plural}",
            f"library.get_{kind}",
            f"library.register_{kind}",
            f"library.update_{kind}",
        }
    return ids


class CatalogService(Protocol):
    async def list_entries(self, kind: str, family: str | None) -> dict[str, Any]: ...

    async def get_entry(self, kind: str, ref: str) -> dict[str, Any]: ...

    async def register_entry(self, kind: str, data: dict[str, Any], by: str) -> dict[str, Any]: ...

    async def update_entry(
        self, kind: str, ref: str, changes: dict[str, Any], by: str
    ) -> dict[str, Any]: ...

    async def process_limits(self, ref: str, material: str | None) -> dict[str, Any]: ...


Register = Callable[..., None]


def _limits() -> ResourceLimits:
    return ResourceLimits(max_memory_mb=128, max_cpu_seconds=10, max_disk_mb=1)


def register_catalog_tools(register: Register, service: CatalogService) -> None:
    """Add the process and material tools through the server's ``register_tool``."""

    def tool(
        tool_id: str,
        name: str,
        description: str,
        schema: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> None:
        async def traced(arguments: dict[str, Any]) -> dict[str, Any]:
            with tracer.start_as_current_span(tool_id):
                return await handler(arguments)

        register(
            manifest=ToolManifest(
                tool_id=tool_id,
                adapter_id="library",
                name=name,
                description=description,
                capability="material_process_library",
                input_schema=schema,
                output_schema={"type": "object"},
                phase=1,
                resource_limits=_limits(),
            ),
            handler=traced,
        )

    ref_schema = {
        "type": "object",
        "properties": {"ref": {"type": "string", "description": "Entry id or name."}},
        "required": ["ref"],
    }

    for kind, plural, families in (
        ("process", "processes", PROCESS_FAMILIES),
        ("material", "materials", MATERIAL_FAMILIES),
    ):

        async def list_(args: dict[str, Any], kind: str = kind) -> dict[str, Any]:
            return await service.list_entries(kind, args.get("family") or None)

        async def get_(args: dict[str, Any], kind: str = kind) -> dict[str, Any]:
            return await service.get_entry(kind, str(args["ref"]))

        async def register_(args: dict[str, Any], kind: str = kind) -> dict[str, Any]:
            return await service.register_entry(kind, dict(args), "mcp")

        async def update_(args: dict[str, Any], kind: str = kind) -> dict[str, Any]:
            changes = args.get("changes") or {}
            if not isinstance(changes, dict):
                raise ValueError("changes must be an object")
            return await service.update_entry(kind, str(args["ref"]), changes, "mcp")

        tool(
            f"library.list_{plural}",
            f"List {plural.title()}",
            (
                f"The {plural} in the library with every "
                f"{'rule' if kind == 'process' else 'property'} and its source; family "
                "narrows the list. Read this before choosing a "
                f"{kind} so the choice rests on recorded figures."
            ),
            {"type": "object", "properties": {"family": {"type": "string", "enum": families}}},
            list_,
        )
        tool(
            f"library.get_{kind}",
            f"Get {kind.title()}",
            f"One {kind} by id or name, with its sources and version history.",
            ref_schema,
            get_,
        )
        if kind == "process":
            register_desc = (
                "Add a manufacturing process: name (identifier, e.g. cnc_milling), title, "
                "family, machine_kinds (the machine library's kinds that run it), "
                "material_families it can make, and rules. "
                + _RULES
                + _SOURCES
                + " Held for the person's approval."
            )
            register_props: dict[str, Any] = {
                "name": {"type": "string"},
                "title": {"type": "string"},
                "family": {"type": "string", "enum": families},
                "machine_kinds": {"type": "array", "items": {"type": "string"}},
                "material_families": {
                    "type": "array",
                    "items": {"type": "string", "enum": MATERIAL_FAMILIES},
                },
                "rules": {"type": "array", "items": {"type": "object"}},
                "notes": {"type": "string"},
            }
        else:
            register_desc = (
                "Add a material: name (identifier, e.g. aluminium_6061_t6), title, family, "
                "designation (the standard grade name), forms, processes it suits, and "
                "properties. " + _PROPERTIES + _SOURCES + " Held for the person's approval."
            )
            register_props = {
                "name": {"type": "string"},
                "title": {"type": "string"},
                "family": {"type": "string", "enum": families},
                "designation": {"type": "string"},
                "forms": {"type": "array", "items": {"type": "string"}},
                "processes": {"type": "array", "items": {"type": "string"}},
                "properties": {"type": "object"},
                "notes": {"type": "string"},
            }
        tool(
            f"library.register_{kind}",
            f"Register {kind.title()}",
            register_desc,
            {"type": "object", "properties": register_props, "required": ["name", "family"]},
            register_,
        )
        tool(
            f"library.update_{kind}",
            f"Update {kind.title()}",
            (
                f"Change a {kind}: "
                + (
                    "title, machine_kinds, material_families, rules (replaced) or notes."
                    if kind == "process"
                    else "title, designation, forms, processes, properties (merged by "
                    "name) or notes."
                )
                + " The old version is kept. Held for the person's approval."
            ),
            {
                "type": "object",
                "properties": {"ref": {"type": "string"}, "changes": {"type": "object"}},
                "required": ["ref", "changes"],
            },
            update_,
        )

    async def limits(args: dict[str, Any]) -> dict[str, Any]:
        return await service.process_limits(str(args["ref"]), args.get("material") or None)

    tool(
        "library.process_limits",
        "Process Limits",
        (
            "A process's rules as check_manufacturability limits and a "
            "rank_manufacturing_processes candidate, each citing its source. With material "
            "(id or name), only the rules for that material's family apply, and a process "
            "not recorded as making that family is refused. needs_review names rules that "
            "are assumptions. A limit the process has no rule for is absent: DFM reads it as "
            "unknown."
        ),
        {
            "type": "object",
            "properties": {"ref": {"type": "string"}, "material": {"type": "string"}},
            "required": ["ref"],
        },
        limits,
    )
