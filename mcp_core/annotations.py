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
        # FORGE-409: the connection diagnostic. A tool with no entry here
        # inherits the destructive default -- so the first version of
        # `health.check` was held for approval, which is the FORGE-407 shape
        # again: a read refused because nothing had classified it.
        "health.check",
        # FORGE-400: the flow catalogue and a run's status. Reading a run
        # cannot change it -- and an agent following a run will call
        # flow.status repeatedly, so classifying it as a write would hold
        # every poll for a human.
        "flow.list",
        "flow.status",
        # FORGE-539: the lifecycle readers. Compiling an intent stores nothing,
        # and coverage, lifecycle and the completion verdict only read.
        "flow.compile_intent",
        "flow.capabilities",
        "flow.lifecycle",
        "flow.verify_completion",
        # Geometry and model inspection
        "cadquery.get_properties",
        "cadquery.validate_physics_stability",
        "freecad.describe_step_file",
        "freecad.get_properties",
        "freecad.list_named_faces",
        # FORGE-492: the read side of the stateful session tools. Each only
        # inspects a session object or the session's own object list.
        "freecad.describe_session",
        "freecad.describe_model",
        "freecad.list_joints",
        "freecad.measure",
        "omniverse_usd.describe_stage",
        "omniverse_usd.validate_usd_minimum",
        # Analysis that reads an existing result rather than producing one
        "calculix.check_mesh_convergence",
        "calculix.compute_joint_loads",
        "calculix.cross_check_cantilever_beam",
        "calculix.cross_check_cantilever_frequency",
        "calculix.cross_check_thermal_steady_state",
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
        # FORGE-343 follow-up: the per-distributor lookups. These are
        # catalog reads against an external API -- they change nothing
        # here. They were unclassified, not judged: the coverage guard
        # scans for `tool_id="..."` literals and this adapter builds its
        # ids with an f-string, so all twelve sat on the destructive
        # default. Harmless while nothing enforced it; the moment
        # FORGE-387 made the write gate real over HTTP, a price check
        # started asking a human for approval.
        "digikey.search",
        "digikey.get_product",
        "digikey.get_pricing",
        "digikey.get_availability",
        "mouser.search",
        "mouser.get_product",
        "mouser.get_pricing",
        "mouser.get_availability",
        "nexar.search",
        "nexar.get_product",
        "nexar.get_pricing",
        "nexar.get_availability",
        "knowledge.search",
        "memory.list_insights",
        "memory.retrieve_similar_experience",
        "web.fetch",
        "web.search",
        # FORGE-544: arithmetic over the arguments; reads and writes nothing.
        "power.check_budget",
        # Twin reads
        "constraint.validate",
        "project.get",
        "project.list",
        # FORGE-335. Deliberate, and the reasoning matters because the
        # instinct is to call it a write: it persists nothing, changes only
        # this session's own view of which project it is in, is undone by
        # calling it again, and cannot touch another session. Classifying it
        # as a write would put project *selection* behind a human approval —
        # so the guardrail's cost would fall on the one action that makes
        # every later call more correctly scoped, and the pressure would be
        # to switch gating off. What it does change is which project a later
        # write lands in, so the approval prompt names the effective project
        # (see mcp_core.elicitation.approval_request).
        "project.open",
        "run.get_status",
        "twin.analyze_engineering_change",
        "twin.compute_hierarchy_rollup",
        "twin.constraint_violations",
        "twin.evaluate_metric",
        "twin.evaluate_thermal_metric",
        "twin.evaluate_overhang_metric",
        "twin.find_by_property",
        "twin.get_design_loop",
        # FORGE-275: pure computation over a work product's existing
        # assembly.joints metadata -- reads only, writes nothing.
        "twin.get_harness_estimate",
        "twin.get_node",
        # FORGE-523: an item's revision history, read from REVISION_OF edges.
        "twin.item_history",
        "twin.rank_sensitivity",
        "twin.thread_for",
        # FORGE-357: named thread questions. Both walk the graph and write
        # nothing.
        "twin.what_verifies",
        "twin.where_used",
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
        # FORGE-492: the stateful FreeCAD session authoring tools. Every one
        # works on the session's own scratch document, held in memory by the
        # adapter (or its per-session worker subprocess) and gone when the
        # session closes. Nothing here writes to the twin, MinIO or a path
        # the caller names; persistence is a separate, explicit step
        # (`twin.commit_geometry`, which is classified above). A few edit an
        # object already in that scratch document (transform_object,
        # set_expression, the dress-up and pattern tools); that is still the
        # session's own throwaway state, not persisted data, so none carries
        # destructiveHint. `import_step` only reads the staged file it is
        # pointed at. `close_session` discards the scratch document itself,
        # which holds nothing that was not already disposable. Left on the
        # destructive default these were all refused for the design-flow
        # service caller, so no phase could author CAD at all.
        "freecad.open_session",
        "freecad.close_session",
        "freecad.create_primitive",
        "freecad.create_body",
        "freecad.import_step",
        "freecad.create_sketch",
        "freecad.pad_sketch",
        "freecad.pocket_sketch",
        "freecad.revolve_sketch",
        "freecad.loft_sketches",
        "freecad.sweep_sketch",
        "freecad.shell_solid",
        "freecad.transform_object",
        "freecad.fillet",
        "freecad.fillet_edges",
        "freecad.chamfer",
        "freecad.chamfer_edges",
        "freecad.boolean",
        "freecad.linear_pattern",
        "freecad.polar_pattern",
        "freecad.mirror_feature",
        "freecad.create_assembly",
        "freecad.add_part_to_assembly",
        "freecad.add_assembly_joint",
        "freecad.create_variable_set",
        "freecad.set_expression",
        "freecad.generate_enclosure",
        "freecad.generate_gear",
        "freecad.generate_ic_package",
        "freecad.generate_profile_part",
        "freecad.fastener_hole",
        "freecad.thread_insert",
        "freecad.lattice_perforation",
        # FORGE-400. `flow.propose` writes a flow version and an approval
        # entry; `flow.start_run` starts real work. Both write, so neither is
        # read-only. Neither is held at the call either (FORGE-471): the
        # approval is on the flow version, see `guardrails.DOWNSTREAM_APPROVED`.
        #
        # Additive rather than destructive: neither overwrites anything, and
        # marking them destructive would tell a reviewer this might remove
        # data, which is the kind of inaccurate warning that gets ignored.
        "flow.propose",
        "flow.start_run",
        # FORGE-539: proposing a patch writes a version and an approval;
        # applying one needs that approval. Same shape as the two above.
        "flow.patch",
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
        # FORGE-347: an imported URDF is recorded through the same
        # architecture recorder, so it is the same kind of write -- it
        # only ever adds a new reference-design node.
        "twin.import_urdf",
        "twin.commit_technical_drawing",
        # FORGE-299: a pure-append snapshot (release_package
        # EngineeringEntity) -- never overwrites an existing node, only
        # reads the project's current state and the gate check it calls
        # (evaluate_g8_release) is itself read-only.
        "twin.create_release_package",
        # FORGE-298: for each matching requirement, records one new
        # verification_case entity -- a pure append, never overwrites an
        # existing Constraint or entity.
        "twin.generate_test_plan",
        # FORGE-295: records one new bringup_checklist entity derived from
        # a work product's existing assembly.joints metadata -- a pure
        # append, never overwrites the source work product or its joints.
        "twin.create_bringup_checklist",
        # FORGE-276: records a new PINMAP + FIRMWARE_SOURCE work product
        # pair derived from a work product's existing assembly.joints
        # metadata -- a pure append, never overwrites the source work
        # product or its joints.
        "twin.create_firmware_scaffold",
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
        # FORGE-287: composes twin.optimize_parameter (already ADDITIVE
        # above) unchanged, then persists every candidate it evaluates as a
        # new DesignLoopIteration node + edges -- pure append, nothing
        # existing is overwritten.
        "twin.start_design_loop",
        # FORGE-288: same family as twin.start_design_loop -- a second real
        # parameter (height_mm) over the same append-only persistence.
        "twin.start_tube_height_design_loop",
        # FORGE-262: records a Decision (a twin.record_decision call,
        # already ADDITIVE above) plus one GENERATED_FROM edge to the
        # selected concept_option -- pure append, nothing existing is
        # overwritten or removed.
        "twin.select_concept",
        # FORGE-265: records a Decision (a twin.record_decision call,
        # already ADDITIVE above) plus the selected candidate as a new
        # BOMItem (twin.record_component_selection, already ADDITIVE
        # above) plus one GENERATED_FROM edge -- pure append, nothing
        # existing is overwritten or removed.
        "twin.select_component",
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
        # FORGE-492: returns the STEP bytes in the response and writes
        # nothing (its own docstring: "NOT persisted anywhere").
        "freecad.export_model",
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
        # FORGE-492: deliberately NOT classified with the session tools above.
        # The sandbox is source-level only (blocked names `open`, `os`, ...),
        # and the namespace still hands the script `Import` and `Part`, whose
        # `Import.export(objs, path)` and `Shape.exportStep(path)` write to any
        # path the script names. The handler's own docstring says the real
        # isolation boundary is the container. A script that can overwrite a
        # file outside the session is destructive, so the service caller
        # keeps being refused it and the model is told so as an observation.
        "freecad.execute_code",
        "freecad.export_geometry",
        "kicad.export_bom",
        "kicad.export_gerber",
        "kicad.export_netlist",
        "omniverse_usd.convert_glb_to_usd",
        "project.delete",
        "project.update",
        "twin.approve_engineering_change",
        "twin.approve_engineering_entity",
        # Same human-authority-approval family as the two lines above: it
        # records a person's approval of the design loop's converged
        # winner, moving project state forward.
        "twin.approve_design_loop",
        # A maturity-gate promotion is the same family as the approvals
        # above: a human-authority decision that moves project state
        # forward (concept -> sim_validated -> ... -> released). It refuses
        # rather than warns, and it persists the attempt either way.
        "twin.attempt_promotion",
        # FORGE-405: revises every existing Constraint/EngineeringEntity
        # currently recorded for the project, advancing authority to
        # 'baselined' -- the same "mutates existing nodes' state" family as
        # twin.approve_engineering_entity above, not a pure append (it also
        # creates one new Baseline node, but that's incidental to the real
        # effect: every member entity's authority moves forward).
        "twin.create_baseline",
        "twin.execute_revalidation_plan",
        "twin.mark_engineering_change_rolled_back",
        "twin.reject_engineering_change",
        # FORGE-266: unlike twin.record_hierarchy_node (ADDITIVE above, sets
        # REALIZED_BY/INSTANCE_OF only once at creation), this REPLACES a
        # node's existing REALIZED_BY/INSTANCE_OF edge -- the prior target is
        # removed, not just added alongside. "Replace placeholder with part"
        # is exactly the kind of state-forward, prior-state-losing decision
        # this category exists for.
        "twin.realize_hierarchy_node",
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
        # Same tools, also open-world: they reach a third-party API whose
        # answer changes without us.
        "digikey.search",
        "digikey.get_product",
        "digikey.get_pricing",
        "digikey.get_availability",
        "mouser.search",
        "mouser.get_product",
        "mouser.get_pricing",
        "mouser.get_availability",
        "nexar.search",
        "nexar.get_product",
        "nexar.get_pricing",
        "nexar.get_availability",
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
