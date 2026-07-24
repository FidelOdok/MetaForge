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
    ``expected_artifacts`` documents the work-product types the phase should
    record into the twin — advisory context for the brain, not enforced here.
    """

    id: str
    title: str
    objective: str
    expected_artifacts: tuple[str, ...] = ()
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


FLOWS: dict[str, FlowDefinition] = {DESIGN_V1.id: DESIGN_V1}

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
