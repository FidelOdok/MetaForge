"""Declarative phase/gate model for the design-flow harness (MET-10).

A :class:`FlowDefinition` is an ordered list of :class:`Phase` objects. Each
phase names an *objective* (what the brain must produce) and optionally carries
a :class:`Gate` — a checkpoint that pauses the run for human approval before
the next phase starts. The model is deliberately product-agnostic: the same
flow drives a drone, a bracket, or a quadruped; the phase objectives are
prompts, not hardcoded engineering.

Phase 1 ships one built-in flow, ``design_v1`` — a thin vertical
(Requirements -> Design -> Simulation), each phase gated. Later slices add the
full lifecycle (architecture, digital-twin consolidation, release) and
per-discipline fan-out; adding a phase is a data change here, not new control
flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Gate:
    """A checkpoint at a phase boundary.

    ``name`` is the human-facing gate label (e.g. "Requirements sign-off").
    ``auto_approve`` skips the human pause (useful for tests / unattended
    runs). ``criteria`` are advisory readiness checks surfaced to the reviewer;
    the weighted evaluation lands with the gate_engine wiring in a later slice.
    """

    name: str
    auto_approve: bool = False
    criteria: tuple[str, ...] = ()


@dataclass(frozen=True)
class Phase:
    """One step of the design lifecycle.

    ``objective`` is handed to the :class:`PhaseBrain` as the phase goal.
    ``required_deliverables`` are the work-product *types* (``WorkProductType``
    values, e.g. ``"cad_model"``) the phase must record into the twin before its
    gate. When ``enforce_deliverables`` is set, a phase whose required
    deliverables are absent fails its gate instead of silently passing — this is
    the "no work product is missing" guarantee. ``expected_artifacts`` is the
    softer, advisory list surfaced to the brain as guidance.
    """

    id: str
    title: str
    objective: str
    expected_artifacts: tuple[str, ...] = ()
    required_deliverables: tuple[str, ...] = ()
    enforce_deliverables: bool = True
    gate: Gate | None = None


@dataclass(frozen=True)
class FlowDefinition:
    """An ordered, named sequence of phases."""

    id: str
    name: str
    phases: tuple[Phase, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------
# Built-in flows
# --------------------------------------------------------------------------

DESIGN_V1 = FlowDefinition(
    id="design_v1",
    name="Design vertical (Requirements -> Design -> Simulation)",
    phases=(
        Phase(
            id="requirements",
            title="Requirements",
            objective=(
                "Establish the engineering requirements for the product from the stated "
                "goal. Capture functional requirements, key constraints (mass, envelope, "
                "load, power, cost as applicable), and the primary load/use cases. Record "
                "the requirements and the top design decisions into the digital twin "
                "(use the record-decision tool), scoped to the project."
            ),
            expected_artifacts=("prd", "constraint_set", "design_decision"),
            # Producible via twin.record_decision today; prd/constraint_set gain
            # dedicated creation tools in a later slice.
            required_deliverables=("design_decision",),
            gate=Gate(
                name="Requirements sign-off",
                criteria=(
                    "Functional requirements enumerated",
                    "Key constraints quantified",
                    "Primary load/use case defined",
                ),
            ),
        ),
        Phase(
            id="design",
            title="Detailed Design",
            objective=(
                "Produce the detailed design that satisfies the approved requirements. "
                "Author the primary geometry / schematic for the load-bearing or "
                "functionally-critical subsystem using the available CAD/EDA tools, name "
                "every part meaningfully, and record the design rationale (material, "
                "dimensions, safety factor target) into the twin."
            ),
            expected_artifacts=("cad_model", "schematic", "design_decision"),
            # The load-bearing geometry MUST be committed to the twin (via
            # twin.commit_geometry) — the gate cannot pass without a viewable
            # cad_model, so the CAD can never be silently missing.
            required_deliverables=("cad_model",),
            gate=Gate(
                name="Design review",
                criteria=(
                    "Critical subsystem geometry/schematic authored",
                    "Material + key dimensions chosen with rationale",
                    "Design traces to a requirement",
                ),
            ),
        ),
        Phase(
            id="simulation",
            title="Simulation & V&V",
            objective=(
                "Validate the design against its requirements. Run the appropriate "
                "analysis (FEA stress for mechanical, ERC/DRC for electronics) on the "
                "critical subsystem, extract the key result (max stress / safety factor / "
                "violation count), and record a pass/fail verdict against the requirement "
                "into the twin."
            ),
            expected_artifacts=("simulation_result", "test_result", "design_decision"),
            # The V&V verdict is recorded as a decision today; a typed
            # simulation_result/test_result work product follows with its tool.
            required_deliverables=("design_decision",),
            gate=Gate(
                name="V&V sign-off",
                criteria=(
                    "Analysis executed on the critical subsystem",
                    "Key result extracted",
                    "Verdict recorded against requirement",
                ),
            ),
        ),
    ),
)


# --------------------------------------------------------------------------
# Full hardware / robotics lifecycle
# --------------------------------------------------------------------------
#
# ``hardware_v1`` takes a hardware or robotics product from intent to a
# manufacturable, verified design across the Phase-1 disciplines, gated at every
# boundary. It reuses the ``requirements``/``design``/``simulation`` phase ids so
# the deterministic mechanical handlers still apply when driving the mechanical
# vertical, and adds architecture / electronics / firmware / manufacturing
# phases that the native tool-calling brain drives. Objectives are
# product-agnostic prompts (drone, arm, quadruped, bracket) with robotics-aware
# guidance (DOF, actuation, kinematics) folded in.
#
# Required deliverables are only the types producible today (``design_decision``
# via twin.record_decision, ``cad_model`` via twin.commit_geometry) so gates stay
# passable; the richer typed products (schematic/bom/firmware_source/…) are
# advisory ``expected_artifacts`` until their creation tools land.

HARDWARE_V1 = FlowDefinition(
    id="hardware_v1",
    name="Hardware & robotics lifecycle (Requirements → Architecture → Mechanical → "
    "Electronics → Firmware → V&V → Manufacturing)",
    phases=(
        Phase(
            id="requirements",
            title="Requirements",
            objective=(
                "Establish the engineering requirements for the product from the stated "
                "goal. Capture functional requirements, the operating environment, and the "
                "key quantified constraints (mass, envelope, payload, degrees of freedom, "
                "power/energy budget, runtime, cost target). For a robot, capture the "
                "motion/task envelope: degrees of freedom, reach, speed, and payload. "
                "Record the requirements and the top decisions into the digital twin "
                "(record-decision tool), scoped to the project."
            ),
            expected_artifacts=("prd", "constraint_set", "design_decision"),
            required_deliverables=("design_decision",),
            gate=Gate(
                name="Requirements sign-off",
                criteria=(
                    "Functional requirements enumerated",
                    "Key constraints quantified (mass/power/DOF/cost as applicable)",
                    "Primary use / motion case defined",
                ),
            ),
        ),
        Phase(
            id="architecture",
            title="System Architecture",
            objective=(
                "Define the system architecture that satisfies the requirements. Decompose "
                "the product into subsystems (structure, actuation, sensing, compute, "
                "power), define their interfaces, and allocate budgets (mass, power, "
                "compute, cost) across them. Select the actuator class, sensor suite, "
                "compute platform, and power source with rationale, checking each against "
                "its budget. Record the architecture and the selection decisions into the "
                "twin."
            ),
            expected_artifacts=("design_decision",),
            required_deliverables=("design_decision",),
            gate=Gate(
                name="Architecture review",
                criteria=(
                    "Subsystems and interfaces defined",
                    "Mass / power / compute / cost budgets allocated",
                    "Actuation, sensing, compute, power selected with rationale",
                ),
            ),
        ),
        Phase(
            id="design",
            title="Mechanical Design",
            objective=(
                "Produce the mechanical design for the load-bearing / motion-critical "
                "structure (chassis and mounts; for a robot, a representative link, joint, "
                "or leg). Author the geometry with the CAD tools, name every part "
                "meaningfully, choose material and key dimensions against a safety-factor "
                "target, and PERSIST the geometry with the commit-geometry tool so it "
                "becomes a viewable cad_model in the twin — a described-but-uncommitted "
                "model does NOT count. Record the design rationale."
            ),
            expected_artifacts=("cad_model", "design_decision"),
            required_deliverables=("cad_model",),
            gate=Gate(
                name="Mechanical design review",
                criteria=(
                    "Critical geometry authored and committed to the twin",
                    "Material and key dimensions chosen with rationale",
                    "Design traces to a requirement",
                ),
            ),
        ),
        Phase(
            id="electronics",
            title="Electronics Design",
            objective=(
                "Design the electronics. Close the power budget (sources, rails, worst-case "
                "draw), define the schematic topology for the main board (compute, motor "
                "drivers, sensors, power), and select the key components. Run ERC on any "
                "available schematic. Record the power budget, topology, and part decisions "
                "into the twin. (Phase-1 KiCad is read-only, so capture the schematic plan "
                "as decisions.)"
            ),
            expected_artifacts=("schematic", "pcb_layout", "bom", "design_decision"),
            required_deliverables=("design_decision",),
            gate=Gate(
                name="Electronics review",
                criteria=(
                    "Power budget closed",
                    "Schematic topology defined for compute / drivers / sensors / power",
                    "Key components selected; ERC clean where applicable",
                ),
            ),
        ),
        Phase(
            id="firmware",
            title="Firmware & Control",
            objective=(
                "Define the firmware and control architecture. Specify the control loop "
                "(for a robot: actuator control and state-estimation approach), the "
                "task/RTOS structure, and the pin map / driver list tying the compute "
                "platform to the actuators and sensors. Record the firmware architecture "
                "and pinmap decisions into the twin."
            ),
            expected_artifacts=("firmware_source", "pinmap", "design_decision"),
            required_deliverables=("design_decision",),
            gate=Gate(
                name="Firmware review",
                criteria=(
                    "Control approach defined",
                    "Task structure and pin map specified",
                    "Drivers for each actuator / sensor identified",
                ),
            ),
        ),
        Phase(
            id="simulation",
            title="Simulation & V&V",
            objective=(
                "Validate the design against its requirements. Run structural FEA on the "
                "load-bearing part (max stress / safety factor); for a robot, sanity-check "
                "the kinematics — reach, and joint torque against the motion requirements; "
                "run ERC/DRC/thermal where applicable. Record a pass/fail verdict against "
                "each key requirement into the twin."
            ),
            expected_artifacts=("simulation_result", "test_result", "design_decision"),
            required_deliverables=("design_decision",),
            gate=Gate(
                name="V&V sign-off",
                criteria=(
                    "Analysis executed on the critical subsystem",
                    "Key results extracted (stress / safety factor / torque / violations)",
                    "Pass/fail verdict recorded against each requirement",
                ),
            ),
        ),
        Phase(
            id="manufacturing",
            title="Manufacturing Prep",
            objective=(
                "Prepare the design for manufacture. Consolidate the BOM with sourceable "
                "parts and a cost roll-up, identify the fabrication outputs (gerbers and "
                "pick-and-place for the PCB, STEP/drawings for machined or printed parts), "
                "and write the assembly and bring-up plan. Record the manufacturing plan "
                "and BOM decisions into the twin. (Phase-1 exports read from existing EDA, "
                "so capture the plan and BOM as decisions.)"
            ),
            expected_artifacts=(
                "bom",
                "gerber",
                "pick_and_place",
                "manufacturing_file",
                "test_plan",
                "design_decision",
            ),
            required_deliverables=("design_decision",),
            gate=Gate(
                name="Manufacturing readiness",
                criteria=(
                    "BOM complete and costed",
                    "Fabrication outputs identified",
                    "Assembly and bring-up plan written",
                ),
            ),
        ),
    ),
)


FLOWS: dict[str, FlowDefinition] = {DESIGN_V1.id: DESIGN_V1, HARDWARE_V1.id: HARDWARE_V1}

DEFAULT_FLOW_ID = DESIGN_V1.id


def get_flow(flow_id: str | None) -> FlowDefinition:
    """Resolve a flow by id, falling back to the default flow.

    Raises ``KeyError`` for an unknown non-empty id so a bad request surfaces
    cleanly rather than silently running the wrong lifecycle.
    """
    if not flow_id:
        return FLOWS[DEFAULT_FLOW_ID]
    try:
        return FLOWS[flow_id]
    except KeyError as exc:
        raise KeyError(f"unknown flow '{flow_id}'; known flows: {sorted(FLOWS)}") from exc
