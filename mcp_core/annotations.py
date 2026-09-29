"""MCP tool annotations — the hints a client uses to decide what to ask about.

The MCP spec lets a server tag each tool with ``readOnlyHint``,
``destructiveHint``, ``idempotentHint`` and ``openWorldHint``. Clients use
them to decide whether a call needs a human in the loop: a tool marked
read-only is one a harness may run without asking.

That makes a wrong annotation a safety problem rather than a cosmetic one,
and it fails silently — the tool runs, the write lands, nobody was asked. So
two rules hold here:

**Nothing is inferred from a tool's name.** ``twin.record_claim`` and
``twin.reject_engineering_change`` both read like queries and neither is one.
A tool is annotated read-only only by appearing in ``READ_ONLY`` below, which
means someone looked at its handler.

**The conservative value is the default.** The MCP defaults —
``readOnlyHint: false``, ``destructiveHint: true`` — are already the safe
ones, so a tool nobody has classified is treated as a destructive write. A
new adapter that forgets to update this module is over-guarded, not
under-guarded.

Layer-1 module: stdlib only, like ``mcp_core.errors``.
"""

from __future__ import annotations

from typing import Any

# Tools that change nothing: no twin write, no file written anywhere the
# caller can observe, no external side effect.
READ_ONLY: frozenset[str] = frozenset(
    {
        # Geometry and model inspection
        "cadquery.get_properties",
        "cadquery.validate_physics_stability",
        "freecad.describe_step_file",
        "freecad.get_properties",
        "freecad.list_named_faces",
        "omniverse_usd.describe_stage",
        "omniverse_usd.validate_usd_minimum",
        # Analysis that reads an existing result rather than producing one
        "calculix.check_mesh_convergence",
        "calculix.cross_check_cantilever_beam",
        "calculix.extract_results",
        "calculix.validate_mesh",
        "gazebo.extract_results",
        "gazebo.validate_world",
        # Electronics checks — they report on the design, they do not edit it
        "kicad.get_pin_mapping",
        "kicad.run_drc",
        "kicad.run_erc",
        # Catalogue and knowledge reads
        "component.search_intent",
        "component.search_parametric",
        "distributors.resolve_offers",
        "knowledge.search",
        "memory.list_insights",
        "memory.retrieve_similar_experience",
        "web.fetch",
        "web.search",
        # Twin reads
        "constraint.validate",
        "project.get",
        "project.list",
        "run.get_status",
        "twin.analyze_engineering_change",
        "twin.compute_hierarchy_rollup",
        "twin.constraint_violations",
        "twin.evaluate_metric",
        "twin.find_by_property",
        "twin.get_node",
        "twin.rank_sensitivity",
        "twin.thread_for",
    }
)

# Tools whose read-only-ness is a runtime setting, not a property of the
# tool. Annotating these statically is how a client ends up told that a
# mutating call is safe.
#
# ``twin.query_cypher`` is the whole reason this category exists: it rejects
# mutating Cypher *by default*, but the adapter takes a ``twin_allow_mutations``
# flag (``--allow-twin-mutations``) that lets writes through, and that flag is
# on in the dev deployment.
CONDITIONALLY_READ_ONLY: frozenset[str] = frozenset({"twin.query_cypher"})

# Writes that only ever add. They need approval like any write, but a client
# can tell a reviewer "this appends a decision" rather than "this may destroy
# data", which is the difference ``destructiveHint`` exists to carry.
ADDITIVE: frozenset[str] = frozenset(
    {
        "knowledge.extract",
        "knowledge.ingest",
        "knowledge.populate_bom",
        "project.create",
        "run.start_design_flow",
        "session.complete",
        "session.log_event",
        "session.start",
        "twin.commit_compliance_checklist",
        "twin.commit_design_sketch",
        "twin.commit_engineering_change",
        "twin.commit_geometry",
        "twin.commit_hazard_analysis",
        "twin.commit_procurement_record",
        "twin.commit_system_architecture",
        "twin.commit_technical_drawing",
        "twin.optimize_parameter",
        "twin.propose_change",
        "twin.propose_engineering_change",
        "twin.record_claim",
        "twin.record_component_selection",
        "twin.record_constraint_set",
        "twin.record_decision",
        "twin.record_document",
        "twin.record_engineering_entity",
        "twin.record_evidence",
        "twin.record_hierarchy_node",
        "twin.record_measurement",
        "twin.register_device_instance",
        "twin.stage_work_product_file",
    }
)

# Writes that produce a new artefact or result without altering anything
# that already exists. Same approval requirement as any write; the hint only
# tells a reviewer which kind of write they are being asked about.
PRODUCING: frozenset[str] = frozenset(
    {
        "cadquery.create_assembly",
        "cadquery.create_parametric",
        "cadquery.generate_enclosure",
        "cadquery.generate_ros2_launch",
        "freecad.create_parametric",
        "freecad.generate_mesh",
        "calculix.run_fea",
        "calculix.run_thermal",
        "gazebo.run_simulation",
        "isaac_sim.render_scene",
        "isaac_sim.run_physics",
    }
)

# Tools that can overwrite or remove something. Listed explicitly even
# though it matches the default, so that ``unclassified()`` can tell
# "nobody has looked at this" apart from "someone looked and it is
# dangerous" — the two have the same behaviour and very different meanings.
#
# Every ``export_*`` is here because it writes to a caller-supplied path and
# will happily overwrite what is already there. A harness asking before it
# does that is the correct amount of friction.
DESTRUCTIVE: frozenset[str] = frozenset(
    {
        "cadquery.boolean_operation",
        "cadquery.execute_script",
        "cadquery.export_geometry",
        "cadquery.export_sdf",
        "cadquery.export_sdf_assembly",
        "cadquery.export_urdf",
        "cadquery.export_urdf_assembly",
        "cadquery.export_usd",
        "cadquery.export_usd_assembly",
        "freecad.boolean_operation",
        "freecad.export_geometry",
        "kicad.export_bom",
        "kicad.export_gerber",
        "kicad.export_netlist",
        "omniverse_usd.convert_glb_to_usd",
        "project.delete",
        "project.update",
        "twin.approve_engineering_change",
        "twin.approve_engineering_entity",
        # A maturity-gate promotion is the same family as the approvals
        # above: a human-authority decision that moves project state
        # forward (concept -> sim_validated -> ... -> released). It refuses
        # rather than warns, and it persists the attempt either way.
        "twin.attempt_promotion",
        "twin.execute_revalidation_plan",
        "twin.mark_engineering_change_rolled_back",
        "twin.reject_engineering_change",
    }
)

# Tools that reach outside the twin — the network, a distributor API, the
# open web. ``openWorldHint`` defaults to true in the spec, so this set marks
# the tools for which it stays true; everything else is closed-world.
OPEN_WORLD: frozenset[str] = frozenset(
    {
        "component.search_intent",
        "component.search_parametric",
        "distributors.resolve_offers",
        "knowledge.ingest",
        "web.fetch",
        "web.search",
    }
)

# Repeating the call with the same arguments has no additional effect.
# Only claimed where the handler is genuinely idempotent.
IDEMPOTENT: frozenset[str] = frozenset(
    {
        "project.delete",
        "twin.mark_engineering_change_rolled_back",
    }
)


def annotations_for(
    tool_id: str,
    *,
    title: str | None = None,
    twin_mutations_enabled: bool = False,
) -> dict[str, Any]:
    """Build the ``annotations`` object for one tool.

    ``twin_mutations_enabled`` mirrors the adapter's ``twin_allow_mutations``
    setting. With it on, the conditionally-read-only tools lose the hint —
    the same server, the same tool id, a different honest answer.
    """
    read_only = tool_id in READ_ONLY or (
        tool_id in CONDITIONALLY_READ_ONLY and not twin_mutations_enabled
    )

    annotations: dict[str, Any] = {
        "readOnlyHint": read_only,
        # A read-only tool cannot be destructive. Past that, only an
        # explicitly additive tool gets the benefit of the doubt.
        "destructiveHint": not (read_only or tool_id in ADDITIVE or tool_id in PRODUCING),
        "idempotentHint": read_only or tool_id in IDEMPOTENT,
        "openWorldHint": tool_id in OPEN_WORLD,
    }
    if title:
        annotations["title"] = title
    return annotations


def unclassified(tool_ids: list[str]) -> list[str]:
    """Tool ids no set in this module mentions.

    Used by the test that keeps this module honest as adapters are added.
    An unclassified tool still behaves safely — it inherits the destructive
    default — but nobody has looked at it, and that is worth surfacing
    rather than letting the set quietly fall behind the registry.
    """
    known = READ_ONLY | CONDITIONALLY_READ_ONLY | ADDITIVE | PRODUCING | DESTRUCTIVE
    return sorted(t for t in tool_ids if t not in known)
