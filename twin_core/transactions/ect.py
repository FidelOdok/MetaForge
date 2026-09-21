"""Engineering Change Transaction lifecycle (FORGE-66, spec section 13,
Phase 7 of epic FORGE-35).

Composes three already-real engines rather than reinventing any of them:

- ``TransactionEngine`` (FORGE-50) does the actual atomic commit.
- ``HITLEngine`` (FORGE-53) classifies the underlying Patch into an
  approval level -- ``required_approval``'s own ``impact`` parameter,
  accepted since FORGE-53 "for interface compatibility... not yet acted
  on", is exactly where this module's ``analyze()`` output plugs in.
- FORGE-67 (next sub-task) will supply a REAL transitive impact graph.
  Until it exists, ``analyze()`` here reports the patch's own DIRECTLY
  targeted entities as ``affected_objects`` -- real, not guessed, but
  deliberately not the full "servo -> gearbox -> frame -> battery -> ..."
  blast radius the spec's own worked example describes (that needs a
  pre-commit dependency-graph walk `StalenessEngine.propagate` doesn't do
  today -- it's strictly post-commit, confirmed by reading it: it compares
  a dependent's pinned revision against the CURRENT revision of an
  already-changed entity). ``impact`` severity is derived honestly from the
  real ``HITLLevel`` classification (AUTONOMOUS/NOTIFY -> low, REVIEW ->
  medium, EXPLICIT_APPROVAL/MANDATORY_AUTHORITY -> high) rather than
  invented as a second, competing scoring system.

State machine (spec section 13): PROPOSED -> ANALYZING -> READY_FOR_REVIEW
-> APPROVED | REJECTED -> COMMITTED | ROLLED_BACK. Every transition here
checks the ECT's current status and raises ``ECTStateError`` rather than
silently proceeding from the wrong state -- the same discipline
``PolicyEngine`` (FORGE-52) already applies to precondition checks.

``ROLLED_BACK`` is NOT an automatic transactional rollback --
``TransactionEngine`` itself documents that no such capability exists (no
multi-write database transaction under ``GraphEngine``, stated plainly in
its own module docstring). ``mark_rolled_back`` records that a committed
change was later reverted by some compensating action outside this
module's scope -- a status label for bookkeeping, never a promise that
this module can undo a commit by itself.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from twin_core.api import TwinAPI
from twin_core.hitl.engine import HITLEngine
from twin_core.hitl.models import HITLLevel
from twin_core.models.engineering_change_transaction import (
    ChangeTrigger,
    ECTStatus,
    EngineeringChangeTransaction,
    ImpactSeverity,
)
from twin_core.models.patch import Patch
from twin_core.transactions.engine import TransactionEngine


class ECTStateError(Exception):
    """Raised when an ECT lifecycle function is called from the wrong
    status -- never a silent no-op or a proceed-anyway."""

    def __init__(self, ect_id: UUID, expected: tuple[ECTStatus, ...], actual: ECTStatus) -> None:
        self.ect_id = ect_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"ECT {ect_id} is {actual.value!r}, expected one of {[s.value for s in expected]}"
        )


_IMPACT_BY_LEVEL: dict[HITLLevel, ImpactSeverity] = {
    HITLLevel.AUTONOMOUS: "low",
    HITLLevel.NOTIFY: "low",
    HITLLevel.REVIEW: "medium",
    HITLLevel.EXPLICIT_APPROVAL: "high",
    HITLLevel.MANDATORY_AUTHORITY: "high",
}


def _require_status(ect: EngineeringChangeTransaction, *allowed: ECTStatus) -> None:
    if ect.status not in allowed:
        raise ECTStateError(ect.id, allowed, ect.status)


async def _get(twin: TwinAPI, ect_id: UUID) -> EngineeringChangeTransaction:
    ect = await twin.get_ect(ect_id)
    if ect is None:
        raise KeyError(f"EngineeringChangeTransaction {ect_id} not found")
    return ect


async def propose_change(
    twin: TwinAPI,
    *,
    trigger: ChangeTrigger,
    observation: str,
    patch: Patch,
    created_by: str = "",
    project_id: UUID | None = None,
) -> EngineeringChangeTransaction:
    """Open a new ECT in PROPOSED status. Nothing is analyzed or written to
    the graph yet -- see ``analyze``/``approve``/``commit``."""
    ect = EngineeringChangeTransaction(
        trigger=trigger,
        observation=observation,
        patch=patch,
        created_by=created_by,
        project_id=project_id,
        status=ECTStatus.PROPOSED,
    )
    return await twin.create_ect(ect)


async def analyze(
    twin: TwinAPI,
    ect_id: UUID,
    *,
    hitl: HITLEngine | None = None,
    state: dict[str, Any] | None = None,
) -> EngineeringChangeTransaction:
    """PROPOSED -> ANALYZING -> READY_FOR_REVIEW. Computes ``affected_objects``
    (direct patch targets -- see module docstring), ``impact``, and
    ``approval_required`` via the real ``HITLEngine`` classification.
    """
    ect = await _get(twin, ect_id)
    _require_status(ect, ECTStatus.PROPOSED)
    await twin.update_ect(ect_id, {"status": ECTStatus.ANALYZING})

    engine = hitl or HITLEngine()
    affected = sorted(
        {str(op.entity_id) for op in ect.patch.operations if op.entity_id is not None}
        | {str(op.target_id) for op in ect.patch.operations if op.target_id is not None}
    )
    approval = await engine.required_approval(ect.patch, None, state or {})

    return await twin.update_ect(
        ect_id,
        {
            "status": ECTStatus.READY_FOR_REVIEW,
            "affected_objects": affected,
            "impact": _IMPACT_BY_LEVEL[approval.level],
            "approval_required": approval.required,
        },
    )


async def approve(
    twin: TwinAPI,
    ect_id: UUID,
    *,
    approver: str,
    hitl: HITLEngine | None = None,
    state: dict[str, Any] | None = None,
) -> EngineeringChangeTransaction:
    """READY_FOR_REVIEW -> APPROVED. Re-derives the same approval
    classification ``analyze`` computed (never trusts a possibly-stale
    stored ``approval_required`` for the independence check) and raises
    ``IndependenceViolation`` if this ECT's category requires an approver
    other than whoever authored the patch.
    """
    ect = await _get(twin, ect_id)
    _require_status(ect, ECTStatus.READY_FOR_REVIEW)
    engine = hitl or HITLEngine()
    approval = await engine.required_approval(ect.patch, None, state or {})
    engine.validate_approver(approval, ect.patch, approver)
    return await twin.update_ect(ect_id, {"status": ECTStatus.APPROVED, "decided_by": approver})


async def reject(
    twin: TwinAPI, ect_id: UUID, *, reason: str, decided_by: str = ""
) -> EngineeringChangeTransaction:
    """READY_FOR_REVIEW -> REJECTED (terminal; never committed)."""
    ect = await _get(twin, ect_id)
    _require_status(ect, ECTStatus.READY_FOR_REVIEW)
    if not reason or not reason.strip():
        raise ValueError("reject: 'reason' is required")
    return await twin.update_ect(
        ect_id,
        {"status": ECTStatus.REJECTED, "decision_reason": reason, "decided_by": decided_by},
    )


async def commit(
    twin: TwinAPI, ect_id: UUID, *, engine: TransactionEngine | None = None
) -> EngineeringChangeTransaction:
    """APPROVED -> COMMITTED. Delegates to ``TransactionEngine.commit`` --
    on a conflict (another change landed on the same entities since this
    ECT was analyzed), the ECT stays APPROVED with the conflict recorded in
    ``committed_patch_result`` rather than being silently marked COMMITTED.
    """
    ect = await _get(twin, ect_id)
    _require_status(ect, ECTStatus.APPROVED)
    txn = engine or TransactionEngine(twin)
    result = await txn.commit(ect.patch)

    updates: dict[str, Any] = {"committed_patch_result": result.model_dump(mode="json")}
    if result.status == "committed":
        updates["status"] = ECTStatus.COMMITTED
    return await twin.update_ect(ect_id, updates)


async def mark_rolled_back(
    twin: TwinAPI, ect_id: UUID, *, reason: str
) -> EngineeringChangeTransaction:
    """COMMITTED -> ROLLED_BACK. A bookkeeping label, not an automatic undo
    -- see module docstring."""
    ect = await _get(twin, ect_id)
    _require_status(ect, ECTStatus.COMMITTED)
    if not reason or not reason.strip():
        raise ValueError("mark_rolled_back: 'reason' is required")
    return await twin.update_ect(
        ect_id, {"status": ECTStatus.ROLLED_BACK, "decision_reason": reason}
    )
