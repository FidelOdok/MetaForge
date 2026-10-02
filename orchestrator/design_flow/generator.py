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
from orchestrator.design_flow.spec import FlowDefinition, Gate, Phase

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
    }
)

ROUTE_SELECTION_PHASE_ID = "route_selection"


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
        if kind is OperationKind.SET_MODEL and not _model_ref_ok(str(value or "").strip()):
            raise TailoringError(
                f"{where}: set_model value '{value}' is not a usable provider:model"
            )
        operations.append(Operation(kind=kind, phase_id=phase_id, rationale=rationale, value=value))
    return operations


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
        elif op.kind is OperationKind.SET_DISCIPLINES:
            value = op.value if isinstance(op.value, list) else []
            names = [str(v).strip() for v in value if str(v).strip()]
            if not names:
                continue
            disciplines[op.phase_id] = names
            applied.append(op)

    phases: list[Phase] = []
    for phase in base.phases:
        if phase.id in dropped:
            continue
        if add_route_selection and "cad_model" in phase.expected_artifacts:
            # Before the first phase that commits geometry: the route decides
            # what that geometry may assume.
            phases.append(_route_selection_phase())
            add_route_selection = False
        added = extra_deliverables.get(phase.id, [])
        assigned = disciplines.get(phase.id)
        if phase.id in models:
            phase = replace(phase, model=models[phase.id])
        if not added and assigned is None:
            phases.append(phase)
            continue
        phases.append(
            Phase(
                id=phase.id,
                title=phase.title,
                objective=phase.objective,
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
                # Deliberately carried through untouched: there is no
                # operation that can switch enforcement off, and copying the
                # original value is what makes that true rather than a rule
                # somebody has to remember.
                enforce_deliverables=phase.enforce_deliverables,
                gate=phase.gate,
                disciplines=tuple(assigned) if assigned is not None else phase.disciplines,
                model=phase.model,
            )
        )

    if add_route_selection:
        # No geometry phase to precede: the decision goes last but one, still
        # ahead of whatever verifies and releases.
        phases.insert(max(len(phases) - 1, 0), _route_selection_phase())

    tailored = FlowDefinition(id=base.id, name=base.name, phases=tuple(phases))
    return tailored, applied


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
