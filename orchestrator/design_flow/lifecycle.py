"""Where a design run stands, as separate answers rather than one status (FORGE-539).

A run's status used to be a single word ("running", "completed", ...) and a
phase's a single word too. One word cannot say "the simulation ran, its
result is valid, and the design it checked fails the requirement", which is
exactly the case that matters most: an engine that only knows "completed"
reports success.

So each phase gets four independent answers:

* ``execution_status``: did the work run (PENDING, RUNNING, WAITING,
  SUCCEEDED, FAILED_RETRYABLE, FAILED_NONRETRYABLE, SKIPPED)
* ``eligibility``: can it run now (ELIGIBLE, WAITING_FOR_DEPENDENCY,
  WAITING_FOR_APPROVAL, BLOCKED)
* ``validity``: is its result still good (UNKNOWN, VALID, POTENTIALLY_INVALID,
  STALE): a phase whose recorded item has been superseded in the twin is
  STALE, and everything downstream of it POTENTIALLY_INVALID
* ``objective_status``: did it meet its objective (NOT_EVALUATED,
  SATISFIED, UNSATISFIED, INCONCLUSIVE), from its gate

and the run gets a completion verdict against the project's requirements:
COMPLETED_VERIFIED only when every mandatory requirement passes with
current evidence, no produced result is stale and no blocking gap remains.
A run whose phases all finished while a requirement still fails is
PARTIALLY_COMPLETED, never verified, which is the spec's test 7.

Pure functions over plain inputs. Whoever calls this gathers the run state,
the stale item keys and the requirement statuses; nothing here reads a
database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from orchestrator.design_flow.graph import build_graph

__all__ = [
    "CompletionClass",
    "CompletionVerdict",
    "Eligibility",
    "ExecutionStatus",
    "LifecycleView",
    "NodeView",
    "ObjectiveStatus",
    "RequirementStatus",
    "Validity",
    "lifecycle_view",
    "normalize_requirement_status",
]


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_NONRETRYABLE = "FAILED_NONRETRYABLE"
    SKIPPED = "SKIPPED"


class Eligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    WAITING_FOR_DEPENDENCY = "WAITING_FOR_DEPENDENCY"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    BLOCKED = "BLOCKED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Validity(StrEnum):
    UNKNOWN = "UNKNOWN"
    VALID = "VALID"
    POTENTIALLY_INVALID = "POTENTIALLY_INVALID"
    STALE = "STALE"


class ObjectiveStatus(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    INCONCLUSIVE = "INCONCLUSIVE"


class RequirementStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_EVALUATED = "NOT_EVALUATED"
    STALE = "STALE"
    WAIVED = "WAIVED"


class CompletionClass(StrEnum):
    COMPLETED_VERIFIED = "COMPLETED_VERIFIED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    BLOCKED = "BLOCKED"
    IN_PROGRESS = "IN_PROGRESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: The requirement matrix's own words (pass / uncertain / fail / no_data /
#: stale) mapped onto the lifecycle contract's. ``no_data`` is NOT_EVALUATED:
#: absence of evidence is never a pass.
_MATRIX_STATUS: dict[str, RequirementStatus] = {
    "pass": RequirementStatus.PASS,
    "passed": RequirementStatus.PASS,
    "fail": RequirementStatus.FAIL,
    "failed": RequirementStatus.FAIL,
    "uncertain": RequirementStatus.INCONCLUSIVE,
    "inconclusive": RequirementStatus.INCONCLUSIVE,
    "no_data": RequirementStatus.NOT_EVALUATED,
    "not_evaluated": RequirementStatus.NOT_EVALUATED,
    "stale": RequirementStatus.STALE,
    "waived": RequirementStatus.WAIVED,
}


def normalize_requirement_status(raw: str | None) -> RequirementStatus:
    """A matrix status as the contract's. Anything unknown is NOT_EVALUATED."""
    return _MATRIX_STATUS.get(str(raw or "").strip().lower(), RequirementStatus.NOT_EVALUATED)


@dataclass(frozen=True)
class NodeView:
    id: str
    execution_status: ExecutionStatus
    eligibility: Eligibility
    validity: Validity
    objective_status: ObjectiveStatus
    item_keys: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "execution_status": self.execution_status.value,
            "eligibility": self.eligibility.value,
            "validity": self.validity.value,
            "objective_status": self.objective_status.value,
            "item_keys": list(self.item_keys),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class CompletionVerdict:
    classification: CompletionClass
    #: Why the classification is what it is, one line each.
    reasons: tuple[str, ...] = ()
    #: Mandatory requirements that are not passing, with their status.
    unmet_requirements: tuple[dict[str, str], ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        return self.classification is CompletionClass.COMPLETED_VERIFIED

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "verified": self.verified,
            "reasons": list(self.reasons),
            "unmet_requirements": [dict(r) for r in self.unmet_requirements],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class LifecycleView:
    run_status: str
    mode: str
    nodes: tuple[NodeView, ...]
    completion: CompletionVerdict
    requirements: tuple[dict[str, str], ...] = ()
    gaps: tuple[dict[str, Any], ...] = ()
    stale_item_keys: tuple[str, ...] = field(default_factory=tuple)

    def node(self, node_id: str) -> NodeView:
        for candidate in self.nodes:
            if candidate.id == node_id:
                return candidate
        raise KeyError(node_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_status": self.run_status,
            "mode": self.mode,
            "nodes": [n.as_dict() for n in self.nodes],
            "completion": self.completion.as_dict(),
            "requirements": [dict(r) for r in self.requirements],
            "gaps": [dict(g) for g in self.gaps],
            "stale_item_keys": list(self.stale_item_keys),
        }


_TERMINAL_FAILED = {"failed"}
_TERMINAL_STOPPED = {"rejected", "canceled", "cancelled"}


def _item_keys(phase: Any) -> tuple[str, ...]:
    """The twin items ``phase`` writes. Declared slots, else derived ones."""
    try:
        from orchestrator.design_flow.slots import effective_slots

        return tuple(s.item_key for s in effective_slots(phase) if s.item_key)
    except Exception:  # noqa: BLE001 - a frozen phase without slot support: no keys
        return tuple(str(getattr(s, "item_key", "") or "") for s in getattr(phase, "slots", ()))


def lifecycle_view(
    phases: Sequence[Any],
    state: Mapping[str, Any],
    *,
    stale_item_keys: Iterable[str] = (),
    requirements: Iterable[Mapping[str, Any]] = (),
    gaps: Iterable[Mapping[str, Any]] = (),
) -> LifecycleView:
    """The full lifecycle picture of one run.

    ``state`` is the engine's own state (the Temporal ``state`` query, or the
    in-process run record shaped the same way): ``status``, ``completed``
    entries (``{phase, status}``), ``skipped``, ``running``, ``current_phase``,
    ``awaiting_gate`` and ``gate_ready``. ``requirements`` are matrix rows
    (``id``, ``status``, optional ``mandatory``); ``gaps`` are capability-gap
    rows with a ``blocking`` flag.
    """
    graph = build_graph(phases)
    status = str(state.get("status") or "queued").lower()
    completed = {
        str(c.get("phase")): str(c.get("status") or "completed")
        for c in state.get("completed") or []
        if c.get("phase")
    }
    skipped = {str(s) for s in state.get("skipped") or []}
    running = {str(r) for r in state.get("running") or []}
    current = state.get("current_phase")
    if status == "running" and current and current not in completed and not running:
        running = {str(current)}
    gate_open = state.get("awaiting_gate")
    gate_phase = str(current) if gate_open and current else None
    gate_ready = bool(state.get("gate_ready", True))
    stale = {str(k) for k in stale_item_keys}

    # Validity first: stale outputs, then everything downstream of them.
    validity: dict[str, Validity] = {}
    keys_by_phase = {p.id: _item_keys(p) for p in phases}
    for phase in phases:
        if phase.id not in completed:
            validity[phase.id] = Validity.UNKNOWN
        elif stale & set(keys_by_phase[phase.id]):
            validity[phase.id] = Validity.STALE
        else:
            validity[phase.id] = Validity.VALID
    for phase_id in graph.order:
        if validity[phase_id] is Validity.VALID and any(
            validity[u] in (Validity.STALE, Validity.POTENTIALLY_INVALID)
            for u in graph.upstream(phase_id)
        ):
            validity[phase_id] = Validity.POTENTIALLY_INVALID

    nodes: list[NodeView] = []
    approved: set[str] = set()
    for phase in phases:
        pid = phase.id
        reasons: list[str] = []
        entry_status = completed.get(pid)
        has_gate = getattr(phase, "gate", None) is not None and not getattr(
            phase.gate, "auto_approve", False
        )
        # Execution
        if pid in skipped:
            execution = ExecutionStatus.SKIPPED
            reasons.append(f"condition not met: {getattr(phase, 'condition', '') or '?'}")
        elif pid == gate_phase:
            execution = ExecutionStatus.WAITING
        elif pid in running:
            execution = ExecutionStatus.RUNNING
        elif entry_status is None:
            execution = (
                ExecutionStatus.FAILED_NONRETRYABLE
                if status in _TERMINAL_FAILED and pid == current
                else ExecutionStatus.PENDING
            )
        elif entry_status == "failed":
            execution = ExecutionStatus.FAILED_RETRYABLE
            reasons.append("its gate found the phase not ready")
        elif entry_status == "exhausted":
            execution = ExecutionStatus.FAILED_RETRYABLE
            reasons.append("the agent ran out of steps")
        else:
            execution = ExecutionStatus.SUCCEEDED

        # Objective, from the gate
        if execution is ExecutionStatus.SKIPPED:
            objective = ObjectiveStatus.NOT_EVALUATED
        elif pid == gate_phase:
            objective = ObjectiveStatus.NOT_EVALUATED if gate_ready else ObjectiveStatus.UNSATISFIED
            reasons.append(
                "waiting for a person at its gate"
                if gate_ready
                else "its gate is not ready: retry or rework"
            )
        elif entry_status == "ungrounded":
            objective = ObjectiveStatus.UNSATISFIED
            reasons.append("its reply claimed work no tool call performed")
        elif execution is ExecutionStatus.SUCCEEDED and has_gate:
            objective = ObjectiveStatus.SATISFIED
            approved.add(pid)
        elif execution is ExecutionStatus.SUCCEEDED:
            objective = ObjectiveStatus.INCONCLUSIVE
            reasons.append("no gate reviews this phase, so its objective was not assessed")
            approved.add(pid)
        elif execution is ExecutionStatus.FAILED_RETRYABLE:
            objective = ObjectiveStatus.UNSATISFIED
        else:
            objective = ObjectiveStatus.NOT_EVALUATED

        if validity[pid] is Validity.STALE:
            reasons.append("an item it recorded has a newer revision in the twin")
        elif validity[pid] is Validity.POTENTIALLY_INVALID:
            reasons.append("a phase it depends on produced a result that is now stale")

        nodes.append(
            NodeView(
                id=pid,
                execution_status=execution,
                eligibility=Eligibility.NOT_APPLICABLE,  # filled below
                validity=validity[pid],
                objective_status=objective,
                item_keys=keys_by_phase[pid],
                reasons=tuple(reasons),
            )
        )

    # Eligibility: settled parents make a pending phase eligible.
    terminal = status in _TERMINAL_FAILED | _TERMINAL_STOPPED
    ready = set(graph.ready(done=approved, skipped=skipped, running=running))
    # FORGE-572: a blocking capability gap blocks the phase it belongs to. It
    # used to show only in the completion verdict, so a phase nothing could
    # run read as ELIGIBLE right up until it failed.
    gap_blocked: dict[str, list[str]] = {}
    for g in gaps:
        if g.get("blocking") and g.get("phase"):
            gap_blocked.setdefault(str(g["phase"]), []).append(
                str(g.get("capability") or "a required capability")
            )
    resolved: list[NodeView] = []
    for node in nodes:
        reasons_out = node.reasons
        if node.execution_status is ExecutionStatus.WAITING:
            eligibility = Eligibility.WAITING_FOR_APPROVAL
        elif node.execution_status is not ExecutionStatus.PENDING:
            eligibility = Eligibility.NOT_APPLICABLE
        elif terminal:
            eligibility = Eligibility.BLOCKED
        elif node.id in gap_blocked:
            eligibility = Eligibility.BLOCKED
            reasons_out = (
                *node.reasons,
                "no available tool produces " + ", ".join(gap_blocked[node.id]),
            )
        elif node.id in ready:
            eligibility = Eligibility.ELIGIBLE
        else:
            eligibility = Eligibility.WAITING_FOR_DEPENDENCY
        resolved.append(
            NodeView(
                id=node.id,
                execution_status=node.execution_status,
                eligibility=eligibility,
                validity=node.validity,
                objective_status=node.objective_status,
                item_keys=node.item_keys,
                reasons=reasons_out,
            )
        )

    reqs = tuple(
        {
            "id": str(r.get("id") or r.get("requirement_id") or r.get("name") or "?"),
            "status": normalize_requirement_status(r.get("status")).value,
            "mandatory": "false" if r.get("mandatory") is False else "true",
            **({"name": str(r["name"])} if r.get("name") else {}),
        }
        for r in requirements
    )
    gap_rows = tuple(dict(g) for g in gaps)
    completion = _completion(
        status=status,
        nodes=resolved,
        requirements=reqs,
        gaps=gap_rows,
        gate_ready=gate_ready,
        gate_open=bool(gate_open),
        error=str(state.get("error") or ""),
    )
    return LifecycleView(
        run_status=status,
        mode=str(state.get("mode") or "linear"),
        nodes=tuple(resolved),
        completion=completion,
        requirements=reqs,
        gaps=gap_rows,
        stale_item_keys=tuple(sorted(stale)),
    )


def _completion(
    *,
    status: str,
    nodes: Sequence[NodeView],
    requirements: Sequence[dict[str, str]],
    gaps: Sequence[dict[str, Any]],
    gate_ready: bool,
    gate_open: bool,
    error: str,
) -> CompletionVerdict:
    """The intent-satisfaction verdict. Only a fully evidenced finish is verified."""
    if status in _TERMINAL_FAILED:
        return CompletionVerdict(CompletionClass.FAILED, reasons=(error or "the run failed",))
    if status in _TERMINAL_STOPPED:
        return CompletionVerdict(
            CompletionClass.CANCELLED,
            reasons=(error or f"the run was {status} by a reviewer",),
        )
    if status != "completed":
        if gate_open and not gate_ready:
            return CompletionVerdict(
                CompletionClass.BLOCKED,
                reasons=("a gate found its phase not ready; it needs a retry, rework or reject",),
            )
        return CompletionVerdict(CompletionClass.IN_PROGRESS, reasons=(f"the run is {status}",))

    reasons: list[str] = []
    warnings: list[str] = []
    mandatory = [r for r in requirements if r.get("mandatory") != "false"]
    unmet = tuple(
        {"id": r["id"], "status": r["status"], **({"name": r["name"]} if "name" in r else {})}
        for r in mandatory
        if r["status"] not in (RequirementStatus.PASS.value, RequirementStatus.WAIVED.value)
    )
    if unmet:
        reasons.append(
            f"{len(unmet)} mandatory requirement(s) not passing with current evidence: "
            + ", ".join(f"{u['id']} ({u['status']})" for u in unmet)
        )
    stale_nodes = [n.id for n in nodes if n.validity is Validity.STALE]
    if stale_nodes:
        reasons.append("results superseded since they were produced: " + ", ".join(stale_nodes))
    unsatisfied = [n.id for n in nodes if n.objective_status is ObjectiveStatus.UNSATISFIED]
    if unsatisfied:
        reasons.append("phases whose objective was not met: " + ", ".join(unsatisfied))
    blocking = [g for g in gaps if g.get("blocking")]
    if blocking:
        reasons.append(
            "blocking capability gaps: "
            + ", ".join(str(g.get("capability") or g.get("phase") or "?") for g in blocking)
        )
    if reasons:
        return CompletionVerdict(
            CompletionClass.PARTIALLY_COMPLETED,
            reasons=tuple(reasons),
            unmet_requirements=unmet,
        )

    if not mandatory:
        warnings.append("no requirements were recorded, so nothing was verified against them")
    waived = [r["id"] for r in mandatory if r["status"] == RequirementStatus.WAIVED.value]
    if waived:
        warnings.append("passing only by waiver: " + ", ".join(waived))
    skipped = [n.id for n in nodes if n.execution_status is ExecutionStatus.SKIPPED]
    if skipped:
        warnings.append("skipped by condition: " + ", ".join(skipped))
    potentially = [n.id for n in nodes if n.validity is Validity.POTENTIALLY_INVALID]
    if potentially:
        warnings.append("depend on superseded results: " + ", ".join(potentially))
    inconclusive = [n.id for n in nodes if n.objective_status is ObjectiveStatus.INCONCLUSIVE]
    if inconclusive:
        warnings.append("ran without a gate reviewing them: " + ", ".join(inconclusive))
    nonblocking = [g for g in gaps if not g.get("blocking")]
    if nonblocking:
        warnings.append(f"{len(nonblocking)} non-blocking capability gap(s)")
    if warnings:
        return CompletionVerdict(
            CompletionClass.COMPLETED_WITH_WARNINGS,
            reasons=("every mandatory requirement passes",) if mandatory else (),
            warnings=tuple(warnings),
        )
    return CompletionVerdict(
        CompletionClass.COMPLETED_VERIFIED,
        reasons=(
            "every mandatory requirement passes with current evidence, no result is "
            "stale, every phase's objective was met and no blocking gap remains",
        ),
    )
