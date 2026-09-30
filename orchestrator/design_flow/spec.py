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
    runs). ``criteria`` are advisory readiness checks surfaced to the reviewer.

    ``enforce_constraints`` (MET-583, constraint-as-gate-criteria): when set,
    the gate also evaluates the project's recorded constraints through the
    constraint engine, and any ERROR-severity violation makes the gate
    not-ready (fail-fast, same as a missing required deliverable). Gates
    without the flag still *surface* the constraint state in the gate reason
    so the human reviewer sees real data, not just prose. This keeps the gate
    skeleton hardcoded while the criteria come from the project itself.

    ``gate_id`` (FORGE-73/91): the spec's own G-number ("G3" .. "G8") when
    this gate corresponds to a real ``twin_core.consistency.gates`` evaluator
    -- ``None`` for gates that don't (G0-G2 have no dedicated evaluator
    module yet). Purely informational today: a mapped gate's real evaluation
    status is surfaced in the human-facing gate reason, same as
    ``enforce_constraints`` already does for constraint state, but nothing
    here makes it block a transition -- that's a deliberate, separate
    decision, not made here.
    """

    name: str
    auto_approve: bool = False
    criteria: tuple[str, ...] = ()
    enforce_constraints: bool = False
    gate_id: str | None = None


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
    # Skill ``domain``s whose SKILL.md procedures + tool scope the phase brain
    # loads into context (the "select" pillar). Empty = no discipline skills yet.
    disciplines: tuple[str, ...] = ()


@dataclass(frozen=True)
class FlowDefinition:
    """An ordered, named sequence of phases."""

    id: str
    name: str
    phases: tuple[Phase, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------
# Built-in flows
# --------------------------------------------------------------------------
#
# G0 (Intent) and G1 (Needs) -- Engineering Intent & Requirements Harness
# (FORGE-35/48) -- precede every flow's "requirements" phase (G2). Defined
# once and shared across flows: same objective, same gate, regardless of
# which lifecycle they precede. Producible today via
# twin.record_engineering_entity (FORGE-45/47); the richer multi-agent
# Intent Interpreter / Clarification Agent split (spec section 26) is
# FORGE-38 (Phase 3), not this pass -- the generic ReAct phase brain drives
# these two phases for now, same as any phase with no deterministic handler.

# The flow literals that lived here (530 lines of Phase/Gate definitions for
# design_v1, hardware_v1 and mech_v1) moved to templates/*.yaml in FORGE-397.
#
# They were generated from these definitions rather than retyped, and
# tests/unit/test_flow_templates.py holds the round-trip that proved the
# migration changed nothing. They are gone rather than kept alongside,
# because two sources of truth means somebody edits the Python, nothing
# happens, and the flow they think they changed keeps running as it was.


def _load_flows() -> dict[str, FlowDefinition]:
    """The built-in flows, read from their template files (FORGE-397).

    The Python literals above are gone: each flow now lives in
    ``templates/<id>.yaml`` with an explicit ``version``, because a flow that
    can be tailored has to be comparable with the template it came from, and
    a completed run has to be able to say which version it ran.

    Imported inside the function to keep ``templates.py`` free to import the
    dataclasses from this module without a cycle.
    """
    from orchestrator.design_flow.templates import load_templates

    return {flow_id: t.definition for flow_id, t in load_templates().items()}


FLOWS: dict[str, FlowDefinition] = _load_flows()

DEFAULT_FLOW_ID = "design_v1"

# Kept as names because tests and callers reach for them. They are views on
# the loaded registry, not a second definition.
DESIGN_V1 = FLOWS["design_v1"]
HARDWARE_V1 = FLOWS["hardware_v1"]
MECH_V1 = FLOWS["mech_v1"]


def flow_version(flow_id: str | None) -> str:
    """The template version a run of ``flow_id`` would use.

    Recorded on every run (FORGE-397) so "which flow did this run use" has an
    answer that survives the template being edited afterwards.
    """
    from orchestrator.design_flow.templates import load_templates

    return load_templates()[flow_id or DEFAULT_FLOW_ID].version


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
