"""HITLEngine (FORGE-53, spec sections 24 Human-in-the-Loop Model, 63
Safety-Critical Policy, 14 Harness Execution Contract step 11).

Classification is deterministic, derived from the Patch's own operations
(FORGE-50) plus caller-supplied authority context (FORGE-51) -- not guessed
from field names or free text. Two inputs the doc's illustrative examples
can't be reduced to without inventing semantics this engine has no business
inventing:

- Which specific REVISE/ADD/etc. counts as "low-risk derived metadata"
  (NOTIFY) versus "new derived requirement" (REVIEW) can't be told apart
  from a PatchOperation's shape alone -- both are just a `revise`/`add` with
  some `fields`/`entity` payload. Rather than guess, this engine classifies
  every ADD/plain REVISE at REVIEW (the doc's baseline for "something new
  entered the graph"), and gives the caller `state["risk_hint"]` to name a
  lower level explicitly when it has real information the patch doesn't
  carry (e.g. a skill that TRULY only normalized a unit).
- Whether a change counts as "critical" depends on whether the entity being
  touched is currently baselined -- the Patch itself doesn't carry that (a
  PatchOperation only has `expected_revision`, not the target's authority).
  Callers pass ``state["baselined_entity_ids"]`` (computed via
  TwinAPI.get_constraint/get_engineering_entity before calling this, same
  as PolicyEngine's flat `state` context pattern from FORGE-52).

``impact`` (spec section 21's impact graph) is accepted for interface
compatibility with the spec's exact signature but not yet acted on -- real
impact analysis is Phase 4/7 scope (Engineering Consistency / Advanced
Change Management), neither of which exists yet. Not invented here.
"""

from __future__ import annotations

from typing import Any

from twin_core.hitl.models import ApprovalRequest, HITLLevel
from twin_core.models.patch import Patch, PatchOp, PatchOperation

# spec section 63: category -> (level floor, extra note, requires an
# independent approver). `None` floor means the category adds a note/
# independence requirement without itself forcing a specific level.
_SAFETY_CRITICAL_FLOORS: dict[str, tuple[HITLLevel | None, str | None, bool]] = {
    "creation": (HITLLevel.REVIEW, None, False),
    "change": (HITLLevel.EXPLICIT_APPROVAL, None, False),
    "waiver": (HITLLevel.MANDATORY_AUTHORITY, None, True),
    "verification": (None, "Independent evidence required (spec section 63: verification).", True),
    "release": (None, "No stale evidence permitted (spec section 63: release).", False),
}

_RISK_HINTS: dict[str, HITLLevel] = {level.name.lower(): level for level in HITLLevel}


class IndependenceViolation(Exception):
    """Raised by ``HITLEngine.validate_approver`` when the proposed approver
    is the same actor who authored the patch, but independence is required."""

    def __init__(self, patch_id: Any, approver: str) -> None:
        self.patch_id = patch_id
        self.approver = approver
        super().__init__(
            f"Approval of patch {patch_id} requires an independent approver -- "
            f"{approver!r} authored it and cannot also approve it."
        )


class HITLEngine:
    """Classifies a Patch into a risk-based approval level."""

    async def required_approval(
        self,
        patch: Patch,
        impact: dict[str, Any] | None,
        state: dict[str, Any],
    ) -> ApprovalRequest:
        baselined_ids = {str(x) for x in state.get("baselined_entity_ids", [])}
        risk_hint = self._risk_hint(state)

        per_op = [self._classify_operation(op, baselined_ids, risk_hint) for op in patch.operations]
        level = max((lvl for lvl, _ in per_op), default=HITLLevel.AUTONOMOUS)
        reasons = sorted({why for _, why in per_op})

        notes: list[str] = []
        require_independent = False
        category = state.get("category")
        if state.get("safety_critical") and isinstance(category, str):
            floor, note, independent = _SAFETY_CRITICAL_FLOORS.get(category, (None, None, False))
            if floor is not None and floor > level:
                level = floor
                reasons.append(f"safety-critical category {category!r} floors at {floor.name}")
            if note:
                notes.append(note)
            require_independent = require_independent or independent

        return ApprovalRequest(
            patch_id=patch.id,
            level=level,
            required=level >= HITLLevel.REVIEW,
            notify=level >= HITLLevel.NOTIFY,
            reason="; ".join(reasons) if reasons else "no operations require oversight",
            require_independent_approver=require_independent,
            notes=notes,
        )

    def validate_approver(self, approval: ApprovalRequest, patch: Patch, approver: str) -> None:
        """Raise ``IndependenceViolation`` if `approver` may not approve `patch`."""
        if (
            approval.require_independent_approver
            and approver
            and patch.created_by
            and approver == patch.created_by
        ):
            raise IndependenceViolation(patch.id, approver)

    @staticmethod
    def _risk_hint(state: dict[str, Any]) -> HITLLevel | None:
        hint = state.get("risk_hint")
        if hint is None:
            return None
        if isinstance(hint, HITLLevel):
            return hint
        return _RISK_HINTS.get(str(hint).lower())

    @staticmethod
    def _classify_operation(
        op: PatchOperation, baselined_ids: set[str], risk_hint: HITLLevel | None
    ) -> tuple[HITLLevel, str]:
        if op.op in (PatchOp.LINK, PatchOp.UNLINK):
            return HITLLevel.AUTONOMOUS, f"{op.op} is trace generation (AUTONOMOUS)"

        if op.op in (PatchOp.DEPRECATE, PatchOp.INVALIDATE):
            return HITLLevel.EXPLICIT_APPROVAL, f"{op.op} is deletion-class (EXPLICIT_APPROVAL)"

        if op.op == PatchOp.ADD:
            level = risk_hint if risk_hint is not None else HITLLevel.REVIEW
            return level, "add is a new derived requirement (REVIEW)" if risk_hint is None else (
                f"add hinted at {level.name} by caller"
            )

        if op.op in (PatchOp.REVISE, PatchOp.SUPERSEDE):
            target_id = str(op.entity_id)
            if op.op == PatchOp.REVISE and op.fields and op.fields.get("authority") == "baselined":
                return HITLLevel.EXPLICIT_APPROVAL, "revise creates a baseline (EXPLICIT_APPROVAL)"
            if target_id in baselined_ids:
                return (
                    HITLLevel.EXPLICIT_APPROVAL,
                    f"{op.op} targets an already-baselined entity (EXPLICIT_APPROVAL)",
                )
            level = risk_hint if risk_hint is not None else HITLLevel.REVIEW
            return level, f"{op.op} on a proposed entity ({level.name})"

        # Conservative default for any future PatchOp this engine doesn't
        # yet know about -- never silently classify an unknown op as safe.
        return (
            HITLLevel.EXPLICIT_APPROVAL,
            f"unrecognized op {op.op!r} defaults to EXPLICIT_APPROVAL",
        )
