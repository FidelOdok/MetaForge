"""Tailoring a template to a project (FORGE-398).

The model does **not** write a flow. It proposes operations from a closed set,
and the server applies them to a versioned template.

That distinction is the design. Asking a model for a whole flow and validating
the result afterwards puts the invariants in a position where they have to
catch everything, and "the validator will catch it" is the reasoning that ends
with a release gate quietly missing because somebody added a rule later than
the flow that broke it. Here, removing a gate or switching enforcement off are
not things the model can express — there is no operation for them. The
validator (FORGE-397) still runs, as a backstop rather than the only line.

What a model *can* do:

* **drop a phase** that does not apply — a kitchen cabinet has no firmware
* **require more of a phase** — add a deliverable, so a gate demands evidence
  it would otherwise accept the absence of
* **assign disciplines** — the branches a phase fans out into
* **declare items**: the parts a phase writes, by name (FORGE-524), so two
  brackets are two items with keys fixed before the run, not whatever the
  model happens to call them on the day

Every operation carries a rationale, because a flow that differs from its
template and cannot say why is a flow nobody can review.

Note the asymmetry: deliverables can be **added** and never removed. Tailoring
is allowed to make a flow stricter and never laxer. A model that decides a
simulation is unnecessary can drop the whole phase — visible in the diff, and
answerable by the human approving it — but cannot quietly keep the phase and
stop checking its output.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

import structlog

from orchestrator.design_flow.context import ClarifyingQuestion, FlowContext, ManufacturingRoute
from orchestrator.design_flow.invariants import ValidationResult, validate_flow
from orchestrator.design_flow.spec import DeliverableSlot, FlowDefinition, Gate, Phase

logger = structlog.get_logger(__name__)

__all__ = [
    "MODEL_OPERATIONS",
    "ROUTE_SELECTION_PHASE_ID",
    "CallerProvenance",
    "FlowProposal",
    "ModelProvenance",
    "Operation",
    "OperationKind",
    "TailoringError",
    "apply_operations",
    "build_proposal",
    "parse_caller_operations",
    "parse_operations",
]


class OperationKind(StrEnum):
    DROP_PHASE = "drop_phase"
    ADD_DELIVERABLE = "add_deliverable"
    SET_DISCIPLINES = "set_disciplines"
    #: Input I5 (FORGE-477): run one phase on a named "provider:model".
    SET_MODEL = "set_model"
    #: FORGE-524: name the items (parts, requirement sets) a phase writes.
    #: Each becomes a deliverable slot with a fixed item key.
    DECLARE_ITEMS = "declare_items"
    #: FORGE-539: the phases a phase needs (value: list of phase ids). Lets
    #: independent work run in parallel and keeps rework local.
    SET_DEPENDENCIES = "set_dependencies"
    #: FORGE-539: run the phase only when a condition over the flow's facts
    #: holds (value: e.g. "route == undecided"). A false condition skips it.
    SET_CONDITION = "set_condition"
    #: FORGE-539: the intermediate outcome the phase establishes (value: one line).
    SET_OUTCOME = "set_outcome"
    #: Server-only (FORGE-463): inserted when the manufacturing route is
    #: "undecided", so the route becomes a gated decision rather than a guess.
    #: Not in :data:`MODEL_OPERATIONS` -- a model cannot add phases.
    ADD_ROUTE_SELECTION = "add_route_selection"


#: What a model may ask for. Everything else is the server's.
MODEL_OPERATIONS: frozenset[OperationKind] = frozenset(
    {
        OperationKind.DROP_PHASE,
        OperationKind.ADD_DELIVERABLE,
        OperationKind.SET_DISCIPLINES,
        OperationKind.SET_MODEL,
        OperationKind.DECLARE_ITEMS,
        OperationKind.SET_DEPENDENCIES,
        OperationKind.SET_CONDITION,
        OperationKind.SET_OUTCOME,
    }
)

ROUTE_SELECTION_PHASE_ID = "route_selection"

#: Template disciplines a deliverable depends on (FORGE-497). ``set_disciplines``
#: names the disciplines a phase fans out into; it must widen or retarget the
#: phase, never strip the one its own deliverables need (a simulation phase
#: tailored to ``['mechanical']`` lost the FEA tools and could not pass its
#: gate). The generator merges rather than replaces: these template
#: disciplines are kept and the model's list is added to them.
DELIVERABLE_CORE_DISCIPLINES: dict[str, str] = {
    "simulation_result": "simulation",
    "load_case": "simulation",
    "verification_report": "simulation",
    "cad_model": "mechanical",
    "robot_description": "robotics",
}


def merge_disciplines(phase: Phase, assigned: list[str]) -> tuple[str, ...]:
    """``assigned`` plus the template disciplines the phase's deliverables need."""
    wanted = {
        DELIVERABLE_CORE_DISCIPLINES[a]
        for a in (*phase.required_deliverables, *phase.expected_artifacts)
        if a in DELIVERABLE_CORE_DISCIPLINES
    }
    kept = [d for d in phase.disciplines if d.lower() in wanted]
    merged = list(dict.fromkeys([*kept, *assigned]))
    if len(merged) != len(dict.fromkeys(assigned)):
        logger.info(
            "flow_generator_core_disciplines_kept",
            phase=phase.id,
            kept=kept,
            assigned=assigned,
        )
    return tuple(merged)


def _route_selection_phase() -> Phase:
    # The gate name deliberately avoids the release markers ("sign-off" etc.):
    # this decision must not be what satisfies release-gate-exists.
    return Phase(
        id=ROUTE_SELECTION_PHASE_ID,
        title="Manufacturing Route Selection",
        objective=(
            "The manufacturing route is undecided. Before any detailed design, compare "
            "making it in-house against having a vendor make it: what processes, machines "
            "and stock each route offers, lead time, cost at the stated quantity, and what "
            "each route constrains in the design. Record the selected route, the process "
            "and the material as a decision (record-decision tool) with the alternatives "
            "considered, scoped to the project."
        ),
        expected_artifacts=("design_decision",),
        required_deliverables=("design_decision",),
        gate=Gate(
            name="Manufacturing route decision",
            criteria=(
                "In-house and vendor routes compared",
                "Selected process and material follow from the chosen route's capabilities",
                "Alternatives and rationale recorded",
            ),
        ),
    )


@dataclass(frozen=True)
class Operation:
    """One tailoring change, with the reason it was made."""

    kind: OperationKind
    phase_id: str
    rationale: str
    #: For ``add_deliverable``: the artifact type. For ``set_disciplines``:
    #: the discipline list.
    value: Any = None
    #: The stated manufacturing capabilities the change was made under
    #: (FORGE-463). Server-recorded, so the review shows what the generator
    #: was told whether or not the model cited it.
    basis: str = ""

    def full_rationale(self) -> str:
        return f"{self.rationale} [given: {self.basis}]" if self.basis else self.rationale

    def describe(self) -> str:
        if self.kind is OperationKind.ADD_ROUTE_SELECTION:
            return f"add phase '{self.phase_id}' (manufacturing route decision)"
        if self.kind is OperationKind.DROP_PHASE:
            return f"drop phase '{self.phase_id}'"
        if self.kind is OperationKind.ADD_DELIVERABLE:
            return f"require '{self.value}' from phase '{self.phase_id}'"
        if self.kind is OperationKind.SET_MODEL:
            return f"run phase '{self.phase_id}' on model {self.value}"
        if self.kind is OperationKind.DECLARE_ITEMS:
            names = ", ".join(f"{t} '{n}'" for t, n in _declared_items(self.value, ()))
            return f"phase '{self.phase_id}' declares {names}"
        if self.kind is OperationKind.SET_DEPENDENCIES:
            needs = ", ".join(str(v) for v in self.value or []) or "nothing (a root)"
            return f"phase '{self.phase_id}' depends on {needs}"
        if self.kind is OperationKind.SET_CONDITION:
            return f"run phase '{self.phase_id}' only when {self.value}"
        if self.kind is OperationKind.SET_OUTCOME:
            return f"phase '{self.phase_id}' establishes: {self.value}"
        return f"assign {self.value} to phase '{self.phase_id}'"


class TailoringError(ValueError):
    """An operation could not be applied, and the flow was not changed."""


@dataclass(frozen=True)
class ModelProvenance:
    """Which provider and model actually produced a proposal (FORGE-468).

    Recorded because the configured model and the one that answered can
    differ: a primary that cannot serve the request falls back to another
    provider, and a reviewer approving a tailoring should know whose it was.
    """

    provider: str
    model: str
    #: ``"<provider>:<model>"`` of the primary when a fallback answered.
    fell_back_from: str | None = None


@dataclass(frozen=True)
class CallerProvenance:
    """Who proposed a tailoring that no server-side model produced (FORGE-481).

    The caller's own model wrote the operations; the server only applied them.
    Recorded so a reviewer knows the tailoring was a client's, and which one.
    """

    client: str | None = None
    model: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"proposed_by": "caller", "client": self.client, "model": self.model}


@dataclass
class FlowProposal:
    """A tailored flow, its provenance, and whether it is startable."""

    base_template_id: str
    base_version: str
    definition: FlowDefinition
    operations: list[Operation] = field(default_factory=list)
    validation: ValidationResult = field(default_factory=ValidationResult)
    intent: str = ""
    context: FlowContext | None = None
    #: What the proposal took as given without being told. Listed, because an
    #: unstated assumption reads exactly like a stated fact in a flow.
    assumptions: list[str] = field(default_factory=list)
    #: Product-specific questions the model raised that did not block the
    #: proposal. Answering them may change it.
    open_questions: list[ClarifyingQuestion] = field(default_factory=list)
    #: The provider/model that produced the tailoring, when known.
    generated_by: ModelProvenance | None = None
    #: Set instead of ``generated_by`` when the caller supplied the operations.
    proposed_by: CallerProvenance | None = None

    @property
    def valid(self) -> bool:
        return self.validation.ok

    @property
    def requirements_pending(self) -> bool:
        return self.context is None or self.context.requirements_pending

    def diff(self) -> list[str]:
        """What changed from the template, in one line each."""
        return [f"{op.describe()} — {op.full_rationale()}" for op in self.operations]


def parse_operations(raw: Any) -> list[Operation]:
    """Turn a model's JSON into operations, dropping anything unrecognised.

    Unknown operation kinds are ignored rather than rejected, which is the one
    place this module is lenient and the reason is narrow: a model inventing
    ``remove_gate`` should have that request *disappear*, not fail the whole
    proposal. Failing would teach it to retry with different wording; silence
    teaches it the operation does not exist. Everything it asked for that is
    real still applies, and the diff shows exactly what was kept.
    """
    if not isinstance(raw, list):
        return []
    operations: list[Operation] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind_raw = str(entry.get("op") or entry.get("kind") or "").strip()
        try:
            kind = OperationKind(kind_raw)
        except ValueError:
            kind = None
        if kind is None or kind not in MODEL_OPERATIONS:
            # Server-only kinds disappear the same way invented ones do.
            logger.info("flow_generator_unknown_operation", op=kind_raw)
            continue
        phase_id = str(entry.get("phase") or entry.get("phase_id") or "").strip()
        if not phase_id:
            continue
        rationale = str(entry.get("rationale") or entry.get("reason") or "").strip()
        if not rationale:
            # A change nobody can review is not a change worth keeping.
            logger.info("flow_generator_operation_without_rationale", op=kind_raw, phase=phase_id)
            continue
        operations.append(
            Operation(
                kind=kind,
                phase_id=phase_id,
                rationale=rationale,
                value=entry.get("value"),
            )
        )
    return operations


def parse_caller_operations(raw: Any, base: FlowDefinition) -> list[Operation]:
    """Operations a caller supplied, checked strictly against ``base`` (FORGE-481).

    The opposite stance to :func:`parse_operations`. That one is lenient
    because a server-side model's stray request should vanish; here a caller
    can read the refusal and fix its call, so silently dropping an operation
    would hand back a proposal that is not the one it asked for. Anything
    unknown, unrecognised, unexplained or aimed at a phase the template does
    not have raises :class:`TailoringError` naming the offending operation.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TailoringError("operations must be a list")
    phase_ids = {phase.id for phase in base.phases}
    allowed = ", ".join(sorted(k.value for k in MODEL_OPERATIONS))
    operations: list[Operation] = []
    for index, entry in enumerate(raw):
        where = f"operation {index + 1}"
        if not isinstance(entry, dict):
            raise TailoringError(f"{where} must be an object with op, phase and rationale")
        kind_raw = str(entry.get("op") or entry.get("kind") or "").strip()
        try:
            kind = OperationKind(kind_raw)
        except ValueError:
            kind = None
        if kind is None or kind not in MODEL_OPERATIONS:
            raise TailoringError(f"{where}: unknown operation '{kind_raw}'; allowed: {allowed}")
        phase_id = str(entry.get("phase") or entry.get("phase_id") or "").strip()
        if phase_id not in phase_ids:
            raise TailoringError(
                f"{where} ({kind.value}): unknown phase '{phase_id}' in template "
                f"'{base.id}'; phases: {', '.join(sorted(phase_ids))}"
            )
        rationale = str(entry.get("rationale") or entry.get("reason") or "").strip()
        if not rationale:
            raise TailoringError(f"{where} ({kind.value} {phase_id}): a rationale is required")
        value = entry.get("value")
        if kind is OperationKind.ADD_DELIVERABLE and not str(value or "").strip():
            raise TailoringError(f"{where}: add_deliverable needs a 'value' (artifact type)")
        if kind is OperationKind.SET_DISCIPLINES and not (
            isinstance(value, list) and any(str(v).strip() for v in value)
        ):
            raise TailoringError(f"{where}: set_disciplines needs a non-empty list 'value'")
        if kind is OperationKind.DECLARE_ITEMS:
            phase = next(p for p in base.phases if p.id == phase_id)
            if not _declared_items(value, _phase_definition_types(phase)):
                raise TailoringError(
                    f"{where}: declare_items needs a 'value' list of item names, or of "
                    "{type, name} objects; a bare name needs the phase to produce exactly "
                    f"one definition type (phase '{phase_id}' produces "
                    f"{_phase_definition_types(phase) or 'none'})"
                )
        if kind is OperationKind.SET_MODEL and not _model_ref_ok(str(value or "").strip()):
            raise TailoringError(
                f"{where}: set_model value '{value}' is not a usable provider:model"
            )
        if kind is OperationKind.SET_DEPENDENCIES:
            if not isinstance(value, list):
                raise TailoringError(f"{where}: set_dependencies needs a list of phase ids")
            unknown = [str(v) for v in value if str(v) not in phase_ids]
            if unknown or phase_id in [str(v) for v in value]:
                raise TailoringError(
                    f"{where}: set_dependencies names unknown or self phase(s) "
                    f"{unknown or [phase_id]}; phases: {', '.join(sorted(phase_ids))}"
                )
        if kind is OperationKind.SET_CONDITION:
            from orchestrator.design_flow.graph import ConditionError, parse_condition

            try:
                parse_condition(str(value or ""))
            except ConditionError as exc:
                raise TailoringError(f"{where}: {exc}") from exc
        if kind is OperationKind.SET_OUTCOME and not str(value or "").strip():
            raise TailoringError(f"{where}: set_outcome needs a one-line 'value'")
        operations.append(Operation(kind=kind, phase_id=phase_id, rationale=rationale, value=value))
    return operations


def _phase_definition_types(phase: Phase) -> tuple[str, ...]:
    from orchestrator.design_flow.slots import definition_deliverables

    return tuple(definition_deliverables(phase))


def _declared_items(value: Any, phase_types: tuple[str, ...]) -> list[tuple[str, str]]:
    """``(item_type, name)`` pairs from a ``declare_items`` value.

    Entries are ``{"type": ..., "name": ...}`` objects, or bare names when the
    phase produces exactly one definition type (``["left bracket", "right
    bracket"]`` on a design phase that commits cad_models). Anything else is
    dropped; duplicates collapse.
    """
    from twin_core.items.registry import is_definition

    entries = value if isinstance(value, list) else [value] if value else []
    out: list[tuple[str, str]] = []
    for entry in entries:
        if isinstance(entry, dict):
            item_type = str(entry.get("type") or entry.get("item_type") or "").strip()
            name = str(entry.get("name") or "").strip()
        else:
            item_type = phase_types[0] if len(phase_types) == 1 else ""
            name = str(entry or "").strip()
        if not item_type and len(phase_types) == 1:
            item_type = phase_types[0]
        if item_type and name and is_definition(item_type) and (item_type, name) not in out:
            out.append((item_type, name))
    return out


def _model_ref_ok(ref: str) -> bool:
    from orchestrator.harness.providers.routing import (
        RoutingConfigError,
        parse_route_ref,
        validate_route_ref,
    )

    try:
        validate_route_ref(parse_route_ref(ref, "model"), "model")
    except RoutingConfigError:
        return False
    return True


def apply_operations(
    base: FlowDefinition, operations: list[Operation]
) -> tuple[FlowDefinition, list[Operation]]:
    """Apply what can be applied. Returns the flow and the operations used.

    An operation naming a phase that does not exist is skipped, not fatal:
    a model that misremembers one phase id should not lose the four changes it
    got right. Skipped operations are absent from the returned list, so the
    diff a human reads describes what actually happened.
    """
    by_id = {phase.id: phase for phase in base.phases}
    dropped: set[str] = set()
    extra_deliverables: dict[str, list[str]] = {}
    disciplines: dict[str, list[str]] = {}
    models: dict[str, str] = {}
    declared: dict[str, list[DeliverableSlot]] = {}
    dependencies: dict[str, tuple[str, ...]] = {}
    conditions: dict[str, str] = {}
    outcomes: dict[str, str] = {}
    applied: list[Operation] = []
    add_route_selection = False

    for op in operations:
        if op.kind is OperationKind.ADD_ROUTE_SELECTION:
            if ROUTE_SELECTION_PHASE_ID not in by_id and not add_route_selection:
                add_route_selection = True
                applied.append(op)
            continue
        if op.phase_id not in by_id:
            logger.info("flow_generator_unknown_phase", phase=op.phase_id, flow=base.id)
            continue
        if op.kind is OperationKind.DROP_PHASE:
            dropped.add(op.phase_id)
            applied.append(op)
        elif op.kind is OperationKind.ADD_DELIVERABLE:
            artifact = str(op.value or "").strip()
            if not artifact:
                continue
            if artifact in by_id[op.phase_id].required_deliverables:
                # Already required. Reporting it as a change would put a line
                # in the diff that describes nothing.
                continue
            extra_deliverables.setdefault(op.phase_id, []).append(artifact)
            applied.append(op)
        elif op.kind is OperationKind.SET_MODEL:
            ref = str(op.value or "").strip()
            if not _model_ref_ok(ref):
                # Same leniency as an unknown phase: a model that names a pair
                # that cannot work loses that one change, not the proposal.
                logger.info("flow_generator_bad_phase_model", phase=op.phase_id, model=ref)
                continue
            models[op.phase_id] = ref
            applied.append(op)
        elif op.kind is OperationKind.DECLARE_ITEMS:
            items = _declared_items(op.value, _phase_definition_types(by_id[op.phase_id]))
            if not items:
                logger.info("flow_generator_bad_declared_items", phase=op.phase_id)
                continue
            slots = declared.setdefault(op.phase_id, [])
            for item_type, name in items:
                if all((s.item_type, s.name) != (item_type, name) for s in slots):
                    slots.append(DeliverableSlot(item_type=item_type, name=name))
            applied.append(op)
        elif op.kind is OperationKind.SET_DISCIPLINES:
            value = op.value if isinstance(op.value, list) else []
            names = [str(v).strip() for v in value if str(v).strip()]
            if not names:
                continue
            disciplines[op.phase_id] = names
            applied.append(op)
        elif op.kind is OperationKind.SET_DEPENDENCIES:
            needs = tuple(
                str(v)
                for v in (op.value if isinstance(op.value, list) else [])
                if str(v) in by_id and str(v) != op.phase_id
            )
            dependencies[op.phase_id] = tuple(dict.fromkeys(needs))
            applied.append(op)
        elif op.kind is OperationKind.SET_CONDITION:
            from orchestrator.design_flow.graph import ConditionError, parse_condition

            text = str(op.value or "").strip()
            try:
                parse_condition(text)
            except ConditionError:
                logger.info("flow_generator_bad_condition", phase=op.phase_id, condition=text)
                continue
            conditions[op.phase_id] = text
            applied.append(op)
        elif op.kind is OperationKind.SET_OUTCOME:
            text = " ".join(str(op.value or "").split())
            if not text:
                continue
            outcomes[op.phase_id] = text
            applied.append(op)

    # FORGE-539: a dropped phase hands its own dependencies to whatever
    # depended on it, so the graph keeps its ordering instead of pointing at a
    # phase that no longer exists. Implicit (sequential) phases need nothing:
    # "the phase before me" moves along by itself.
    effective_deps = {pid: dependencies.get(pid, by_id[pid].depends_on) for pid in by_id}

    def _resolve(needs: tuple[str, ...]) -> tuple[str, ...]:
        out: list[str] = []
        for need in needs:
            if need in dropped:
                inherited = effective_deps.get(need)
                if inherited is None:
                    index = [p.id for p in base.phases].index(need)
                    inherited = (base.phases[index - 1].id,) if index else ()
                out.extend(_resolve(tuple(inherited)))
            else:
                out.append(need)
        return tuple(dict.fromkeys(out))

    phases: list[Phase] = []
    for phase in base.phases:
        if phase.id in dropped:
            continue
        explicit = effective_deps[phase.id]
        if explicit is not None:
            phase = replace(phase, depends_on=_resolve(tuple(explicit)))
        if phase.id in conditions:
            phase = replace(phase, condition=conditions[phase.id])
        if phase.id in outcomes:
            phase = replace(phase, outcome=outcomes[phase.id])
        if add_route_selection and "cad_model" in phase.expected_artifacts:
            # Before the first phase that commits geometry: the route decides
            # what that geometry may assume.
            route_phase = _route_selection_phase()
            if phase.depends_on is not None:
                # FORGE-539: in a graph the geometry phase must explicitly
                # wait for the decision, or the two would run side by side.
                route_phase = replace(route_phase, depends_on=phase.depends_on)
                phase = replace(phase, depends_on=(*phase.depends_on, ROUTE_SELECTION_PHASE_ID))
            phases.append(route_phase)
            add_route_selection = False
        added = extra_deliverables.get(phase.id, [])
        assigned = disciplines.get(phase.id)
        if phase.id in models:
            phase = replace(phase, model=models[phase.id])
        if phase.id in declared:
            # Keys are bound when the version is saved (slots.bind_slots).
            new_slots = [
                s
                for s in declared[phase.id]
                if all((o.item_type, o.name) != (s.item_type, s.name) for o in phase.slots)
            ]
            phase = replace(
                phase,
                slots=(*phase.slots, *new_slots),
                expected_artifacts=tuple(
                    dict.fromkeys([*phase.expected_artifacts, *(s.item_type for s in new_slots)])
                ),
            )
        if not added and assigned is None:
            phases.append(phase)
            continue
        # ``replace`` rather than a rebuilt Phase: every field this does not
        # name (gate, enforcement, model, slots, and FORGE-539's graph fields)
        # is carried through untouched. There is no operation that can switch
        # enforcement off, and copying it is what makes that true rather than
        # a rule somebody has to remember.
        phases.append(
            replace(
                phase,
                # A phase now required to deliver something is a phase asked to
                # produce it; otherwise deliverable-is-producible rejects the
                # very tightening the operation exists for (e.g. a test_plan
                # standing in for a dropped simulation).
                expected_artifacts=tuple(
                    [
                        *phase.expected_artifacts,
                        *(a for a in added if a not in phase.expected_artifacts),
                    ]
                ),
                required_deliverables=tuple([*phase.required_deliverables, *added]),
                disciplines=(
                    merge_disciplines(phase, assigned)
                    if assigned is not None
                    else phase.disciplines
                ),
            )
        )

    if add_route_selection:
        # No geometry phase to precede: the decision goes last but one, still
        # ahead of whatever verifies and releases.
        route_phase = _route_selection_phase()
        if phases and phases[-1].depends_on is not None:
            last = phases[-1]
            route_phase = replace(route_phase, depends_on=last.depends_on)
            phases[-1] = replace(
                last, depends_on=(*(last.depends_on or ()), ROUTE_SELECTION_PHASE_ID)
            )
        phases.insert(max(len(phases) - 1, 0), route_phase)

    tailored = FlowDefinition(id=base.id, name=base.name, phases=tuple(phases))
    return tailored, applied


#: Evidence that shows a physical part carries its stated loads.
ANALYSIS_EVIDENCE = "simulation_result"


def _require_analysis_evidence(
    base: FlowDefinition, ops: list[Operation], basis: str
) -> list[Operation]:
    """Require the analysis result at the gate that only expected it (FORGE-570).

    A template's V&V phase *expects* a ``simulation_result`` but its gate
    *requires* only a decision, so a run could sign off a factor-of-safety
    requirement with no analysis at all. When the generator is told the
    loads, and the flow designs a physical part, the analysis becomes
    required at that gate: a server change in the diff, with its reason, like
    the route-selection step. Unknown loads leave it alone: there is no load
    case to analyse yet, which the physical-verification invariant reports.
    """
    if not any("cad_model" in p.expected_artifacts for p in base.phases):
        return []
    # A phase the caller already made require it, or dropped (choosing a
    # required test_plan instead, which the invariants check), is left alone.
    already = {
        op.phase_id
        for op in ops
        if op.kind is OperationKind.DROP_PHASE
        or (op.kind is OperationKind.ADD_DELIVERABLE and op.value == ANALYSIS_EVIDENCE)
    }
    return [
        Operation(
            OperationKind.ADD_DELIVERABLE,
            phase.id,
            "the loads are stated and the flow designs a physical part, so the analysis that "
            "shows it carries them is required at this gate, not merely expected",
            value=ANALYSIS_EVIDENCE,
            basis=basis,
        )
        for phase in base.phases
        if phase.gate is not None
        and ANALYSIS_EVIDENCE in phase.expected_artifacts
        and ANALYSIS_EVIDENCE not in phase.required_deliverables
        and phase.id not in already
    ]


def build_proposal(
    base: FlowDefinition,
    *,
    base_version: str,
    operations: list[Operation],
    intent: str = "",
    context: FlowContext | None = None,
    assumptions: list[str] | None = None,
    open_questions: list[ClarifyingQuestion] | None = None,
    generated_by: ModelProvenance | None = None,
    proposed_by: CallerProvenance | None = None,
) -> FlowProposal:
    """Tailor ``base`` and report whether the result can be started.

    With a ``context`` (FORGE-463): an undecided manufacturing route adds the
    route-selection phase, every change records the capabilities it was made
    under, and the invariants that depend on the loads read them.
    """
    ops = list(operations)
    if context is not None:
        basis = context.capability_basis()
        ops = [replace(op, basis=op.basis or basis) for op in ops]
        if context.loads_known:
            ops.extend(_require_analysis_evidence(base, ops, basis))
        if context.route is ManufacturingRoute.UNDECIDED:
            ops.append(
                Operation(
                    OperationKind.ADD_ROUTE_SELECTION,
                    ROUTE_SELECTION_PHASE_ID,
                    "the manufacturing route is undecided, so choosing it is a gated "
                    "decision in the flow rather than a guess made by the generator",
                    basis=basis,
                )
            )
    tailored, applied = apply_operations(base, ops)
    validation = validate_flow(tailored, context=context)
    notes = list(assumptions or [])
    if context is None or context.requirements_pending:
        notes.insert(
            0,
            "requirements pending: none were recorded when this flow was proposed, so its "
            "verification phases are kept for the requirements phase to fill",
        )
    if context is not None and not (context.budget or "").strip():
        notes.append("budget not stated: no cost ceiling was applied")
    proposal = FlowProposal(
        base_template_id=base.id,
        base_version=base_version,
        definition=tailored,
        operations=applied,
        validation=validation,
        intent=intent,
        context=context,
        assumptions=notes,
        open_questions=list(open_questions or []),
        generated_by=generated_by,
        proposed_by=proposed_by,
    )
    logger.info(
        "flow_proposal_built",
        template=base.id,
        version=base_version,
        requested=len(operations),
        applied=len(applied),
        phases=len(tailored.phases),
        valid=proposal.valid,
    )
    return proposal
