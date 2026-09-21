"""RequirementQualityRecord (FORGE-55, spec section 32 Requirement Quality
Record).

"The harness may calculate summary metrics for dashboards, but shall NOT
treat a score as proof of requirement validity" -- this module has no
single score field anywhere, deliberately: it's a set of independent
pass/fail diagnostics, not a number.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from api_gateway.requirement_intelligence.linter import LintCategory, LintFinding

PassFail = Literal["pass", "fail"]

# Which lint categories each diagnostic axis cares about. A finding in any
# of an axis's categories fails that axis; everything else stays "pass".
_CLARITY_CATEGORIES = {
    LintCategory.AMBIGUOUS,
    LintCategory.WEAK_MODAL,
    LintCategory.WEAK_WORD,
    LintCategory.UNVERIFIABLE,
    LintCategory.UNDEFINED_PRONOUN,
}
_ATOMICITY_CATEGORIES = {LintCategory.COMPOUND}
_QUANTIFIED_CATEGORIES = {LintCategory.MISSING_THRESHOLD, LintCategory.MISSING_UNIT}
_VERIFICATION_READY_CATEGORIES = (
    _CLARITY_CATEGORIES | _QUANTIFIED_CATEGORIES | {LintCategory.MISSING_CONDITION}
)


class RequirementQualityRecord(BaseModel):
    """Diagnostics, not a score (spec section 32)."""

    clarity: PassFail = "pass"
    atomicity: PassFail = "pass"
    quantified: PassFail = "pass"
    # `traceability` needs graph context (does this requirement have a
    # parent?) the linter can't see from text alone -- caller-supplied, not
    # derived here. `None` means "not evaluated" (distinct from a pass/fail
    # this module has no basis to claim).
    traceability: PassFail | None = None
    verification_ready: PassFail = "pass"
    conflicts: list[str] = Field(default_factory=list)


def build_quality_record(
    findings: list[LintFinding],
    *,
    has_parent: bool | None = None,
    conflicts: list[str] | None = None,
) -> RequirementQualityRecord:
    """Derive a quality record from lint findings. `has_parent` (when given)
    sets `traceability` directly -- this function doesn't infer it."""
    categories = {f.category for f in findings}
    return RequirementQualityRecord(
        clarity="fail" if categories & _CLARITY_CATEGORIES else "pass",
        atomicity="fail" if categories & _ATOMICITY_CATEGORIES else "pass",
        quantified="fail" if categories & _QUANTIFIED_CATEGORIES else "pass",
        traceability=None if has_parent is None else ("pass" if has_parent else "fail"),
        verification_ready="fail" if categories & _VERIFICATION_READY_CATEGORIES else "pass",
        conflicts=list(conflicts or []),
    )
