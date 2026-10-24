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

Layer-1 module: stdlib only (plus ``mcp_core.guardrails``, a sibling).
"""

from __future__ import annotations

import difflib

MIN_TOOLS = 20
MAX_TOOLS = 40

#: FORGE-588: the name that asks for every registered tool, on purpose. It is
#: not in :data:`PROFILES` because it has no cap: it is for a client that
#: loads tools lazily (Claude Code defers them behind its tool search), where
#: a capped set costs more than it saves. A client-mode run needs phase,
#: gate, CAD, simulation and value-sourcing tools at once, which no 40-tool
#: profile can hold. Asking for it is explicit, unlike a missing or unknown
#: name, which still never widens to everything.
FULL_PROFILE = "all"


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
# to earn its place in every profile.
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
        # FORGE-539: intent, coverage, the lifecycle (which carries the
        # completion verdict, so flow.verify_completion stays off this capped
        # profile), and patching a running flow.
        "flow.compile_intent",
        "flow.capabilities",
        "flow.lifecycle",
        "flow.patch",
        # FORGE-630: run.get_status gave its place to twin.render_work_product;
        # flow.status reports a run's state, and run.get_status stays in `all`.
        "run.start_design_flow",
        "twin.compute_hierarchy_rollup",
        "twin.evaluate_metric",
        "twin.propose_change",
        "twin.query_cypher",
        "twin.record_claim",
        "twin.record_constraint_set",
        "twin.record_document",
        "twin.record_hierarchy_node",
        # FORGE-630: see a part. The Codex plugin connects with `core`, so
        # without it there a Codex user could not render anything.
        "twin.render_work_product",
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
        # FORGE-630: see what was modelled.
        "twin.render_work_product",
        "twin.stage_work_product_file",
    },
    # A mechanical product end to end (FORGE-479): the stateful FreeCAD session
    # surface (open, sketch, pad/pocket, features, assembly, export, close),
    # geometry commit, component selection and the records a design phase
    # leaves behind. `core` and `mechanical` cover neither: the shelf run found
    # the FreeCAD session tools, component selection and flow.* in no profile.
    # `twin.attempt_promotion` is deliberately absent: promotion is a human
    # authority, and an agent profile that carried it would let a run promote
    # its own work.
    "mechanical_product": _BASE
    | {
        "component.search_parametric",
        "freecad.add_assembly_joint",
        "freecad.add_part_to_assembly",
        "freecad.boolean",
        "freecad.chamfer",
        "freecad.close_session",
        "freecad.create_body",
        "freecad.create_primitive",
        "freecad.create_sketch",
        "freecad.describe_session",
        # FORGE-494: freecad.execute_code is out of this profile. The design-flow
        # service caller is refused it (FORGE-492, mcp_core/annotations.py keeps
        # it destructive), so it was a dead slot, and the slot makes room for
        # twin.record_document in PHASE_COMMON at PHASE_MCP_BUDGET.
        "freecad.export_model",
        "freecad.fillet",
        "freecad.measure",
        "freecad.open_session",
        "freecad.pad_sketch",
        "freecad.pocket_sketch",
        "freecad.transform_object",
        "twin.commit_geometry",
        "twin.record_component_selection",
        "twin.record_constraint_set",
        "twin.record_engineering_entity",
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
        # FORGE-557: steady internal flow (pressure drop, flow rate).
        "openfoam.run_internal_flow",
        # Composable flows: ask-and-tell search over one design variable,
        # evaluated with the analysis tools above.
        "optimize.suggest",
        # FORGE-542: circuit simulation is prediction too.
        "spice.run_simulation",
        "twin.evaluate_metric",
        "twin.execute_revalidation_plan",
        "twin.rank_sensitivity",
        "twin.record_claim",
        # FORGE-533: the recorder for a simulation_result and a load_case.
        # Phases got it through PHASE_COMMON (FORGE-494); a plugin client on
        # this profile did not, so it could run an analysis and then had no
        # way to record the answer.
        "twin.record_document",
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
        # FORGE-674/677: compile the firmware the skills generate, and run it
        # on an emulated board, so "it builds" is a result rather than a claim.
        "firmware.build",
        "firmware.run_emulated",
        # FORGE-675: cppcheck / clang-tidy findings on the same sources.
        "firmware.static_check",
        "kicad.export_bom",
        "kicad.export_gerber",
        # FORGE-680: drill and pick-and-place complete the fabrication set.
        "kicad.export_drill",
        "kicad.export_pos",
        "kicad.export_netlist",
        "kicad.get_pin_mapping",
        "kicad.run_drc",
        "kicad.run_erc",
        "knowledge.populate_bom",
        # FORGE-544: the per-rail budget, computed rather than hand-summed.
        "power.check_budget",
        # FORGE-542: simulate the netlist kicad.export_netlist writes.
        "spice.run_simulation",
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
        # FORGE-671: a mobile robot's drive and kinematics, computed rather
        # than hand-estimated.
        "robot.diff_drive_kinematics",
        "robot.size_drive",
        "twin.commit_geometry",
        "twin.stage_work_product_file",
    },
    # FORGE-588: driving a client-mode run on a harness with a hard tool cap.
    # Start and watch the run, take and hand back phases, answer gates in the
    # chat, and source engineering values (FORGE-586) and record them. CAD
    # authoring and solvers do not fit beside these; a run whose phases need
    # them belongs on a client that can take the full set (`all`).
    # FORGE-611: choosing the process before design and checking the design
    # against it after. Sourcing (web, knowledge), the geometry reads a DFM
    # check can stand on, component search, and the records both phases
    # write. No CAD authoring: these phases read the design, not change it.
    "manufacturing": _BASE
    | {
        "web.search",
        "web.fetch",
        "knowledge.extract",
        "memory.retrieve_similar_experience",
        "component.search_intent",
        "component.search_parametric",
        "freecad.measure",
        "freecad.describe_model",
        "freecad.describe_session",
        "freecad.list_named_faces",
        "twin.evaluate_overhang_metric",
        "twin.query_cypher",
        "twin.record_engineering_entity",
        "twin.record_constraint_set",
        "twin.record_claim",
        "twin.record_document",
        # FORGE-451: which machines exist and what each can do.
        "library.list_machines",
        "library.get_machine",
        "library.machine_limits",
        # FORGE-451 / FORGE-446: the flow context already lists the chosen
        # processes' rules and materials' properties; this turns a process
        # (for a material) into DFM limits. The list tools stay in `all`:
        # adding them put the phase over PHASE_MCP_BUDGET, which drops web.*.
        "library.process_limits",
        # FORGE-427: a vendor's real price as a ranking input (the quote is
        # recorded, so the call is held; ordering is not in any capped set).
        "vendor.list_connectors",
        "vendor.quote",
        # FORGE-614: where to send it when a vendor has no API.
        "vendor.handoffs",
        # FORGE-615: a rough cost before any quote exists.
        "cost.estimate_manufacturing",
    },
    # FORGE-633: industrial design (the Planner's discipline #2): form factor,
    # ergonomics and DFM-aware finishes. Renders a design in its library
    # appearances, reads materials, processes and machines for what a finish
    # or a wall can be, measures the model, and sources ergonomic limits. No
    # CAD authoring: the envelope it fixes is what mechanical design draws.
    "industrial_design": _BASE
    | {
        "twin.render_work_product",
        "library.list_appearances",
        "library.get_appearance",
        "library.register_appearance",
        "library.update_appearance",
        # FORGE-635: the sourced limits check_ergonomics reads.
        "library.list_ergonomic_limits",
        "library.get_ergonomic_limit",
        "library.list_materials",
        "library.get_material",
        "library.process_limits",
        "library.machine_limits",
        "freecad.measure",
        "freecad.describe_model",
        "freecad.describe_step_file",
        "knowledge.extract",
        "memory.retrieve_similar_experience",
        "web.fetch",
        "web.search",
        "twin.record_claim",
        "twin.query_cypher",
        "twin.record_constraint_set",
        "twin.record_document",
        "twin.record_engineering_entity",
    },
    "client_run": _BASE
    | {
        "flow.list",
        "flow.start_run",
        "flow.status",
        "flow.lifecycle",
        "flow.await_gate",
        "phase.list_tasks",
        "phase.claim",
        "phase.submit",
        "knowledge.extract",
        "knowledge.ingest",
        "memory.retrieve_similar_experience",
        "web.search",
        "web.fetch",
        "component.search_intent",
        "component.search_parametric",
        "twin.record_component_selection",
        "twin.record_constraint_set",
        "twin.record_engineering_entity",
        "twin.record_document",
        "twin.record_claim",
        "twin.evaluate_metric",
        "twin.query_cypher",
        "library.list_machines",
        "library.machine_limits",
    },
}

DEFAULT_PROFILE = "core"

#: Tools every design-flow phase carries: the shared base plus the records the
#: early phases (intent, needs, requirements) exist to produce, which no
#: discipline profile includes.
PHASE_COMMON: frozenset[str] = _BASE | {
    "twin.query_cypher",
    "twin.record_constraint_set",
    # FORGE-494: records prd, simulation_result and load_case, which gates
    # require in phases whose profile otherwise lacks a document recorder.
    "twin.record_document",
    "twin.record_engineering_entity",
}

#: The profile whose own tools a discipline adds to :data:`PHASE_COMMON`.
#: A discipline with no entry adds nothing: its phase records through the
#: common tools.
DISCIPLINE_PROFILES: dict[str, str] = {
    "mechanical": "mechanical_product",
    "simulation": "simulation",
    "electronics": "electronics",
    # FORGE-676: the firmware tools live on the electronics profile.
    "firmware": "electronics",
    "supply_chain": "electronics",
    "robotics": "robotics",
    "manufacturing": "manufacturing",
    "industrial_design": "industrial_design",
}

#: Slots a phase turn keeps back for the harness's own native tools
#: (``search_tools`` and ``read_tool_result``), so the whole array stays
#: within :data:`MAX_TOOLS`.
PHASE_NATIVE_RESERVE = 2
PHASE_MCP_BUDGET = MAX_TOOLS - PHASE_NATIVE_RESERVE

#: The stateful FreeCAD session surface that authors a cad_model.
_CAD_AUTHORING: frozenset[str] = frozenset(
    {
        "freecad.open_session",
        "freecad.create_body",
        "freecad.create_sketch",
        "freecad.create_primitive",
        "freecad.pad_sketch",
        "freecad.pocket_sketch",
        "freecad.boolean",
        "freecad.fillet",
        "freecad.transform_object",
        "freecad.create_assembly",
        "freecad.add_part_to_assembly",
        "freecad.add_assembly_joint",
        "freecad.describe_session",
        "freecad.measure",
        "freecad.export_model",
        "freecad.close_session",
        "twin.commit_geometry",
        "twin.stage_work_product_file",
        # FORGE-647: shows committed parts coming together in the viewer.
        "twin.place_part",
    }
)

_RECORD_DOCUMENT: frozenset[str] = frozenset({"twin.record_document"})

#: Work-product type -> the MCP tools that produce it (FORGE-497).
#:
#: A phase's tool set follows from what it must deliver, not only from the
#: disciplines it names: tailoring can replace a phase's disciplines, and a
#: simulation phase that lost ``simulation`` also lost the only tools that can
#: produce a ``simulation_result``. Keys are spelled as ``WorkProductType``
#: values (what ``deliverable_hints`` and the gate evaluator use). A type with
#: no entry has no model-callable recorder (the platform handler produces it).
DELIVERABLE_TOOLS: dict[str, frozenset[str]] = {
    "design_decision": frozenset({"twin.record_decision"}),
    "intent": frozenset({"twin.record_engineering_entity"}),
    "stakeholder_need": frozenset({"twin.record_engineering_entity"}),
    "prd": _RECORD_DOCUMENT,
    "load_case": _RECORD_DOCUMENT,
    "documentation": _RECORD_DOCUMENT,
    "constraint_set": frozenset({"twin.record_constraint_set"}),
    "cad_model": _CAD_AUTHORING,
    "simulation_result": frozenset(
        {
            "freecad.generate_mesh",
            "calculix.run_fea",
            "calculix.extract_results",
            "calculix.check_mesh_convergence",
            "calculix.validate_mesh",
            "twin.record_document",
            # FORGE-504: a committed cad_model lives in the blob store, not on
            # disk; staging it is how the mesh tool gets a STEP path to load.
            "twin.stage_work_product_file",
        }
    ),
    "robot_description": frozenset(
        {"cadquery.export_urdf", "cadquery.export_sdf", "twin.record_document"}
    ),
    "bom": frozenset(
        {
            "component.search_intent",
            "component.search_parametric",
            "twin.record_component_selection",
        }
    ),
    # FORGE-630: studio renders of a model, kept in the twin.
    "render": frozenset({"twin.render_work_product"}),
    "pinmap": frozenset({"twin.create_firmware_scaffold"}),
    "firmware_source": frozenset({"twin.create_firmware_scaffold"}),
}

#: Families dropped first when a discipline mix overflows the budget: niche
#: simulators and exporters a phase can live without.
_LOW_PRIORITY_PREFIXES: tuple[str, ...] = ("gazebo.", "isaac_sim.", "omniverse_usd.", "cadquery.")


class PhaseToolBudgetError(ValueError):
    """The common tools plus the deliverable tools alone exceed the phase budget."""

    def __init__(self, deliverables: list[str], size: int) -> None:
        super().__init__(
            f"phase deliverables {deliverables} need {size} always-kept tools, over the "
            f"{PHASE_MCP_BUDGET}-tool phase budget; split the phase or trim DELIVERABLE_TOOLS"
        )


def _service_refused(tool_id: str) -> bool:
    """Is this tool one the design-flow service caller is always refused?"""
    from mcp_core.guardrails import ARGUMENT_CLASSIFIED, Caller, decide

    if tool_id in ARGUMENT_CLASSIFIED:
        return False
    return decide(tool_id, caller=Caller.SERVICE, twin_mutations_enabled=True).refused


def deliverable_tools(deliverables: tuple[str, ...] | list[str]) -> frozenset[str]:
    """The tools the named deliverable types need, minus service-refused ones."""
    wanted: set[str] = set()
    for d in deliverables:
        wanted |= DELIVERABLE_TOOLS.get(d, frozenset())
    return frozenset(t for t in wanted if not _service_refused(t))


def _drop_rank(tool_id: str) -> tuple[int, str]:
    """Sort key: tools earlier in this order are kept first when over budget."""
    low = tool_id.startswith(_LOW_PRIORITY_PREFIXES)
    return (1 if low else 0, tool_id)


def _phase_plan(
    disciplines: tuple[str, ...] | list[str],
    deliverables: tuple[str, ...] | list[str],
    required: tuple[str, ...] | list[str] = (),
) -> tuple[frozenset[str], frozenset[str]]:
    """(tools kept, tools dropped) for a phase."""
    # A composed task names the tools its objective is written for
    # (FORGE-672): kept like deliverable tools, never dropped.
    named = frozenset(t for t in required if not _service_refused(t))
    must = PHASE_COMMON | deliverable_tools(deliverables) | named
    if len(must) > PHASE_MCP_BUDGET:
        # Never dropped, so the array exceeds the budget: say so loudly rather
        # than silently truncating a tool the gate needs.
        raise PhaseToolBudgetError(sorted(deliverables), len(must))
    extra: set[str] = set()
    for d in disciplines:
        profile = DISCIPLINE_PROFILES.get(d.lower())
        if profile is not None:
            extra |= PROFILES[profile]
    # A service-refused tool is a dead slot; never spend budget on it.
    extra = {t for t in extra if not _service_refused(t)} - must
    keep = set(must)
    dropped: list[str] = []
    for tool in sorted(extra, key=_drop_rank):
        if len(keep) >= PHASE_MCP_BUDGET:
            dropped.append(tool)
        else:
            keep.add(tool)
    return frozenset(keep), frozenset(dropped)


def tools_for_phase(
    disciplines: tuple[str, ...] | list[str],
    deliverables: tuple[str, ...] | list[str] = (),
    required: tuple[str, ...] | list[str] = (),
) -> frozenset[str]:
    """The MCP tool ids a design-flow phase carries (FORGE-479, FORGE-497).

    :data:`PHASE_COMMON`, plus the tools for every required and expected
    deliverable (:data:`DELIVERABLE_TOOLS`, always kept), plus the profile of
    each discipline the phase names. When that exceeds
    :data:`PHASE_MCP_BUDGET`, discipline-profile tools are dropped first
    (niche families before the rest, then alphabetically last); a deliverable
    tool or a common tool is never dropped. The drop is reported by
    :func:`phase_overflow`, not silent.

    ``required`` are tools a composed task names in its ``capabilities``
    (FORGE-672): its objective is written for them, so they are kept like
    deliverable tools whatever the discipline profiles hold.
    """
    return _phase_plan(disciplines, deliverables, required)[0]


def tools_for_disciplines(disciplines: tuple[str, ...] | list[str]) -> frozenset[str]:
    """Back-compat wrapper: a phase with no deliverables (see :func:`tools_for_phase`)."""
    return tools_for_phase(disciplines)


def phase_overflow(
    disciplines: tuple[str, ...] | list[str],
    deliverables: tuple[str, ...] | list[str] = (),
    required: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """Tools :func:`tools_for_phase` had to drop for this phase (usually none)."""
    return sorted(_phase_plan(disciplines, deliverables, required)[1])


def profile_names() -> list[str]:
    return sorted(PROFILES)


def tools_for_profile(name: str) -> list[str]:
    """Tool ids in ``name``, sorted. Raises rather than falling back."""
    try:
        return sorted(PROFILES[name])
    except KeyError:
        raise UnknownProfileError(name, [*profile_names(), FULL_PROFILE]) from None


def resolve_profile(name: str | None) -> str | None:
    """The capped profile to serve for ``name``; ``None`` means every tool.

    ``None``, empty and :data:`FULL_PROFILE` serve everything. Any other name
    must be a real profile: an unknown one raises rather than widening.
    """
    if not name or name == FULL_PROFILE:
        return None
    tools_for_profile(name)  # raises UnknownProfileError
    return name


def unmapped_disciplines(disciplines: tuple[str, ...] | list[str]) -> list[str]:
    """Disciplines with no entry in :data:`DISCIPLINE_PROFILES` (common set only)."""
    return [d for d in disciplines if d.lower() not in DISCIPLINE_PROFILES]
