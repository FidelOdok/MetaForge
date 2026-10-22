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
COMPONENT_FAMILIES = [
    "screw",
    "nut",
    "washer",
    "bearing",
    "bushing",
    "standoff",
    "pin",
    "retaining_ring",
    "shaft_collar",
    "linear_rail",
    "linear_bearing",
    "profile_extrusion",
    "hinge",
    "slide",
    "gear",
    "pulley",
    "belt",
    "spring",
    "actuator",
    "motor",
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

#: FORGE-631: an appearance is filtered by its finish.
APPEARANCE_FINISHES = [
    "as_made",
    "painted",
    "powder_coated",
    "anodised",
    "plated",
    "moulded_in_colour",
    "printed",
    "textured",
    "polished",
    "brushed",
    "bead_blasted",
    "other",
]
#: FORGE-635: an ergonomic limit is filtered by the feature kind it bounds.
ERGONOMIC_KINDS = [
    "grip_diameter",
    "grip_width",
    "handle_length",
    "finger_clearance",
    "button_size",
    "actuation_force",
    "operating_height",
    "reach_distance",
    "product_mass",
]
_ERGONOMIC = (
    "limit_kind is the feature it bounds, each in one unit: grip_diameter, grip_width, "
    "handle_length, finger_clearance, button_size, operating_height, reach_distance (mm), "
    "actuation_force (N), product_mass (kg). Give min, max or both, applies_to (what kind "
    "of control or object, e.g. 'finger-operated push button'), population (who the "
    "figure is for, as the source states it) and the source (source_kind, source_ref: the "
    "document and its section or table). "
)
_APPEARANCE = (
    "colour is {system (ral, pantone, ncs, supplier or custom), code (e.g. RAL 9005; not "
    "for custom), name, srgb_hex (#RRGGBB, the on-screen value), source_kind, source_ref}: "
    "a screen sRGB of a RAL or Pantone code is an approximation, so source it like any "
    "value. properties is an object keyed by name: gloss_gu_60 (GU, ISO 2813 at 60 deg), "
    "coating_thickness_um, surface_roughness_ra_um (um), render_roughness and "
    "render_metallic (0..1, how a renderer draws it: usually user or assumption). "
)

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
_SPECS = (
    "specs is an object keyed by name, each {value, unit, source_kind, source_ref}: "
    "nominal_diameter_mm, thread_pitch_mm, length_mm, head_diameter_mm, head_height_mm, "
    "socket_size_mm, socket_depth_mm, width_across_flats_mm, height_mm, inner_diameter_mm, "
    "outer_diameter_mm, thickness_mm, bore_mm, width_mm, travel_mm (mm); dynamic_load_kn, "
    "static_load_kn, fatigue_load_limit_kn (kN); reference_speed_rpm, limiting_speed_rpm "
    "(rpm); mass_kg (kg); force_n (N); torque_nm (N m); voltage_v (V). "
)
_PROPERTIES = (
    "properties is an object keyed by name: density_kg_m3 (kg/m3), youngs_modulus_mpa, "
    "youngs_modulus_across_mpa, yield_strength_mpa, yield_strength_across_mpa, "
    "ultimate_strength_mpa, fatigue_strength_mpa, bending_strength_mpa, "
    "bending_strength_across_mpa, tensile_strength_across_mpa (MPa; woods and panels have no "
    "yield point, so give bending strength, never yield), poisson_ratio (-), elongation_pct "
    "(%), thermal_conductivity_w_mk (W/mK), cte_um_mk (um/mK), max_service_temp_c, "
    "melting_point_c (C), cost_per_kg (unit with currency, e.g. GBP/kg). "
)


def catalog_tool_ids() -> set[str]:
    """Every tool id this module registers.

    The ids are assembled per kind, which a literal-id source scan cannot
    see, so coverage checks import this (the same as ``distributor_tool_ids``).
    """
    ids = {"library.process_limits", "library.standard_part"}
    for kind, plural in (
        ("process", "processes"),
        ("material", "materials"),
        ("component", "components"),
        ("appearance", "appearances"),
        ("ergonomic_limit", "ergonomic_limits"),
    ):
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

    async def standard_part(
        self, family: str, size: str, length_mm: float | None
    ) -> dict[str, Any]: ...


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
        ("component", "components", COMPONENT_FAMILIES),
        ("appearance", "appearances", APPEARANCE_FINISHES),
        ("ergonomic_limit", "ergonomic_limits", ERGONOMIC_KINDS),
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
                f"{'(the finish) ' if kind == 'appearance' else ''}"
                f"{'(the feature kind) ' if kind == 'ergonomic_limit' else ''}"
                f"narrows the list. Read this before choosing a {kind} so the choice rests "
                "on recorded figures."
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
        required = ["name", "family"]
        register_props: dict[str, Any]
        if kind == "ergonomic_limit":
            register_desc = (
                "Add an ergonomic design limit: name (identifier, e.g. "
                "mil1472h_pushbutton_finger_diameter), title, limit_kind, min and/or max, "
                "applies_to, population and its source. "
                + _ERGONOMIC
                + _SOURCES
                + " Held for the person's approval."
            )
            register_props = {
                "name": {"type": "string"},
                "title": {"type": "string"},
                "limit_kind": {"type": "string", "enum": families},
                "min": {"type": "number"},
                "max": {"type": "number"},
                "unit": {"type": "string"},
                "applies_to": {"type": "string"},
                "population": {"type": "string"},
                "source_kind": {"type": "string"},
                "source_ref": {"type": "string"},
                "notes": {"type": "string"},
            }
            required = ["name", "limit_kind", "population", "source_kind"]
        elif kind == "appearance":
            register_desc = (
                "Add an appearance (colour, material and finish): name (identifier, e.g. "
                "black_anodised_6061), title, finish, colour, texture (a texture standard "
                "reference, e.g. VDI 3400 Ref 27), properties, materials and processes "
                "(library names it applies to) and suppliers [{supplier, sku, url}]. "
                + _APPEARANCE
                + _SOURCES
                + " Held for the person's approval."
            )
            register_props = {
                "name": {"type": "string"},
                "title": {"type": "string"},
                "finish": {"type": "string", "enum": families},
                "colour": {"type": "object"},
                "texture": {"type": "string"},
                "properties": {"type": "object"},
                "materials": {"type": "array", "items": {"type": "string"}},
                "processes": {"type": "array", "items": {"type": "string"}},
                "suppliers": {"type": "array", "items": {"type": "object"}},
                "notes": {"type": "string"},
            }
            required = ["name", "finish", "colour"]
        elif kind == "component":
            register_desc = (
                "Add a mechanical component: name (identifier), title, family, origin "
                "(standard: dimensions from a standard's table, standard required; purchased: "
                "a supplier's part), size, specs, geometry ({generator, params} for a "
                "standard part, or {step_url, licence} / {step_work_product_id} for a "
                "purchased one), material (a library material name), and suppliers "
                "[{supplier, sku, url}]. " + _SPECS + _SOURCES + " For a standard part, "
                "build the entry with library.standard_part and register what it returns. "
                "Held for the person's approval."
            )
            register_props = {
                "name": {"type": "string"},
                "title": {"type": "string"},
                "family": {"type": "string", "enum": families},
                "origin": {"type": "string", "enum": ["standard", "purchased"]},
                "standard": {"type": "string"},
                "size": {"type": "string"},
                "specs": {"type": "object"},
                "geometry": {"type": "object"},
                "material": {"type": "string"},
                "suppliers": {"type": "array", "items": {"type": "object"}},
                "notes": {"type": "string"},
            }
        elif kind == "process":
            register_desc = (
                "Add a manufacturing process: name (identifier, e.g. cnc_milling), title, "
                "family, machine_kinds (the machine library's kinds that run it), "
                "material_families it can make, and rules. "
                + _RULES
                + _SOURCES
                + " Held for the person's approval."
            )
            register_props = {
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
            {"type": "object", "properties": register_props, "required": required},
            register_,
        )
        tool(
            f"library.update_{kind}",
            f"Update {kind.title()}",
            (
                f"Change a {kind}: "
                + {
                    "process": "title, machine_kinds, material_families, rules (replaced) or "
                    "notes.",
                    "material": "title, designation, forms, processes, properties (merged by "
                    "name) or notes.",
                    "component": "title, specs (merged by name), geometry, material, "
                    "suppliers (replaced) or notes.",
                    "appearance": "title, finish, colour (replaced), texture, properties "
                    "(merged by name), materials, processes, suppliers (replaced) or notes.",
                    "ergonomic_limit": "title, applies_to, min, max, population, "
                    "source_kind, source_ref or notes.",
                }[kind]
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

    async def standard(args: dict[str, Any]) -> dict[str, Any]:
        length = args.get("length_mm")
        return await service.standard_part(
            str(args["family"]), str(args["size"]), float(length) if length is not None else None
        )

    tool(
        "library.standard_part",
        "Build Standard Part",
        (
            "Build a standard mechanical part from its standard's dimension table, without "
            "storing it: family iso4762 (socket head cap screw; give size like M4 and "
            "length_mm), iso4032 (hex nut), iso7089 (plain washer) or deep_groove_bearing "
            "(size is the designation, e.g. 608). Every dimension cites the table row. A "
            "size or length the standard does not have is refused. Pass the result to "
            "library.register_component to add it."
        ),
        {
            "type": "object",
            "properties": {
                "family": {
                    "type": "string",
                    "enum": ["iso4762", "iso4032", "iso7089", "deep_groove_bearing"],
                },
                "size": {"type": "string"},
                "length_mm": {"type": "number"},
            },
            "required": ["family", "size"],
        },
        standard,
    )

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
