"""Tool profiles — a working set small enough for a client to reason about.

The MCP server exposes 97 tools. Handing all of them to a harness is not
free: every client has a limit on how many tools it will carry, and the ones
that do not error simply stop showing the overflow. The model then behaves as
though the missing capability does not exist, which is indistinguishable from
it genuinely not existing.

So a profile is a named subset, sized between ``MIN_TOOLS`` and ``MAX_TOOLS``
(FORGE-339). The bounds are enforced by a test rather than by trimming at
runtime: a profile that outgrows its ceiling is a decision someone has to
make about what to drop, not something this module should quietly do on their
behalf. Silent truncation is the failure this whole feature exists to prevent
— doing it here would be the joke writing itself.

Layer-1 module: stdlib only.
"""

from __future__ import annotations

import difflib

MIN_TOOLS = 20
MAX_TOOLS = 40


class UnknownProfileError(ValueError):
    """Asked for a profile that does not exist.

    Names the ones that do, for the same reason ``ToolNotFoundError`` names
    real tools (FORGE-343): a bare rejection leaves the caller unable to tell
    a typo from a server that has no profiles at all.
    """

    def __init__(self, requested: str, available: list[str]) -> None:
        self.requested = requested
        self.available = available
        suggestion = difflib.get_close_matches(requested, available, n=1)
        message = f"Unknown tool profile: {requested!r}. Available: {', '.join(available)}"
        if suggestion:
            message += f". Did you mean {suggestion[0]!r}?"
        super().__init__(message)


# Shared by every profile. Deliberately small — it is the tax each domain
# profile pays before it gets any of its own tools, so every entry here has
# to earn its place in all five.
_BASE: frozenset[str] = frozenset(
    {
        # FORGE-418: the entry point. `/metaforge:use` returns the project
        # brief by calling this, and a profile without it left the default
        # plugin install unable to reach the brief at all -- the agent
        # rebuilt status from `project.get`'s work-product list instead,
        # which is the long way round to a worse answer. It also binds the
        # session's project scope, so omitting it cost resources/list too.
        "project.open",
        "project.get",
        "project.list",
        "session.start",
        "session.log_event",
        "session.complete",
        "twin.get_node",
        "twin.find_by_property",
        "twin.thread_for",
        "twin.record_decision",
        "twin.record_evidence",
        "twin.constraint_violations",
        "knowledge.search",
        # FORGE-410/409: the connection diagnostic. A profile that omits it
        # takes `/metaforge:doctor` away from exactly the connection most
        # likely to need it -- a capped one, on a harness that truncates. It
        # is one read-only tool, so the tax it adds to all five is the
        # smallest in this set.
        "health.check",
    }
)

PROFILES: dict[str, frozenset[str]] = {
    # Everything an engineer needs before picking a discipline: project and
    # session handling, twin reads and the records that are not domain
    # specific.
    "core": _BASE
    | {
        "project.create",
        "project.update",
        "constraint.validate",
        "knowledge.ingest",
        "memory.list_insights",
        "memory.retrieve_similar_experience",
        # FORGE-462: starting a gated design flow is a core action. The run.*
        # pair were listed here while no sidecar registered them; flow.* is
        # the approved-version path a person signs off on first.
        "flow.list",
        "flow.propose",
        "flow.start_run",
        "flow.status",
        "run.get_status",
        "run.start_design_flow",
        "twin.compute_hierarchy_rollup",
        "twin.evaluate_metric",
        "twin.propose_change",
        "twin.query_cypher",
        "twin.record_claim",
        "twin.record_constraint_set",
        "twin.record_document",
        "twin.record_hierarchy_node",
        "web.fetch",
        "web.search",
    },
    # CAD authoring. Both kernels, because a part often starts in one and is
    # inspected in the other.
    "mechanical": _BASE
    | {
        "cadquery.boolean_operation",
        "cadquery.create_assembly",
        "cadquery.create_parametric",
        "cadquery.execute_script",
        "cadquery.export_geometry",
        "cadquery.generate_enclosure",
        "cadquery.get_properties",
        "cadquery.validate_physics_stability",
        "freecad.boolean_operation",
        "freecad.create_parametric",
        "freecad.describe_step_file",
        "freecad.export_geometry",
        "freecad.generate_mesh",
        "freecad.get_properties",
        "freecad.list_named_faces",
        "twin.commit_geometry",
        "twin.stage_work_product_file",
    },
    # Prediction and its evidence. Includes the mesh and geometry-property
    # tools because a load case is set up against real geometry, not against
    # a description of it.
    "simulation": _BASE
    | {
        "calculix.check_mesh_convergence",
        "calculix.cross_check_cantilever_beam",
        "calculix.extract_results",
        "calculix.run_fea",
        "calculix.run_thermal",
        "calculix.validate_mesh",
        "cadquery.get_properties",
        "freecad.generate_mesh",
        "freecad.list_named_faces",
        "gazebo.extract_results",
        "gazebo.run_simulation",
        "gazebo.validate_world",
        "twin.evaluate_metric",
        "twin.execute_revalidation_plan",
        "twin.rank_sensitivity",
        "twin.record_claim",
        "twin.stage_work_product_file",
    },
    # Schematic and board work, plus the sourcing that decides what goes on
    # it — margins against a part you cannot buy are not margins.
    "electronics": _BASE
    | {
        "component.search_intent",
        "component.search_parametric",
        "constraint.validate",
        "distributors.resolve_offers",
        "kicad.export_bom",
        "kicad.export_gerber",
        "kicad.export_netlist",
        "kicad.get_pin_mapping",
        "kicad.run_drc",
        "kicad.run_erc",
        "knowledge.populate_bom",
        "twin.commit_procurement_record",
        "twin.record_component_selection",
    },
    # Assemblies and robot descriptions, through to a simulator.
    "robotics": _BASE
    | {
        "cadquery.create_assembly",
        "cadquery.export_sdf",
        "cadquery.export_sdf_assembly",
        "cadquery.export_urdf",
        "cadquery.export_urdf_assembly",
        "cadquery.export_usd",
        "cadquery.export_usd_assembly",
        "cadquery.generate_ros2_launch",
        "freecad.create_parametric",
        "gazebo.run_simulation",
        "gazebo.validate_world",
        "isaac_sim.render_scene",
        "isaac_sim.run_physics",
        "omniverse_usd.convert_glb_to_usd",
        "omniverse_usd.describe_stage",
        "omniverse_usd.validate_usd_minimum",
        "twin.commit_geometry",
        "twin.stage_work_product_file",
    },
}

DEFAULT_PROFILE = "core"


def profile_names() -> list[str]:
    return sorted(PROFILES)


def tools_for_profile(name: str) -> list[str]:
    """Tool ids in ``name``, sorted. Raises rather than falling back."""
    try:
        return sorted(PROFILES[name])
    except KeyError:
        raise UnknownProfileError(name, profile_names()) from None
