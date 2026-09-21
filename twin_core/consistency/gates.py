"""G3 Preliminary Feasibility Gate evaluation (FORGE-60, spec sections 22-23,
Phase 5 of epic FORGE-35).

The design-flow ``Gate`` (``orchestrator/design_flow/spec.py``) is a
declarative checkpoint -- a name plus advisory criteria the human reviewer
reads. This module is what FORGE-60's own ticket asked for: making G3's
checks "actually... evaluate... not just show them as prose", by composing
FORGE-57's already-real ``InvariantEngine``/``BudgetEngine`` plus a direct
read of the project's ``"risk"`` ``EngineeringEntity`` nodes.

Scope, stated plainly -- same discipline as every other Phase 4/5 sub-task
this epic has landed. This evaluates checks a caller can already express
with ``Budget``/``Invariant`` objects and risk metadata that exist TODAY. It
deliberately does NOT:

- auto-derive a project's mass/cost/power ``Budget``/``Invariant``
  declarations -- there is still no per-project "the mass budget for this
  quadruped is 5kg, allocated legs=2kg/body=2kg/head=1kg" persistence
  anywhere in the codebase. A caller (an agent, a future MCP tool) supplies
  the ``Budget``/``Invariant`` objects it already knows about, same as
  ``InvariantEngine``/``BudgetEngine``'s own callers do.
- evaluate structural feasibility, actuator sizing, thermal plausibility,
  geometry feasibility, or technology availability -- those need real
  simulation/CAD outputs (Phase 6, FORGE-41, Evidence Integration, not yet
  built). They come back as ``NOT_EVALUATED``, never a faked PASS.
- wire itself into ``orchestrator/design_flow/executor.py``'s automatic
  gate-blocking -- the executor's ``Gate``/``Phase`` model has no evaluator
  hook yet; adding one is real, separate integration work.

Risk scoring reuses the ONE existing severity/likelihood/mitigation
convention in the codebase (``api_gateway/twin/structured_document_recorder.
py``'s hazard-analysis document, ``severity * likelihood`` against the same
``[(20,"critical"),(12,"high"),(6,"medium"),(0,"low")]`` thresholds) rather
than inventing a second, competing one -- even though that convention lives
on a different node type (a hazard-analysis work product, not a ``"risk"``
``EngineeringEntity``).
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from twin_core.api import TwinAPI
from twin_core.consistency.budgets import BudgetEngine
from twin_core.consistency.invariants import InvariantEngine
from twin_core.consistency.models import Budget, Invariant


class GateCheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"


class GateStatus(StrEnum):
    """Spec section 22's gate state machine."""

    NOT_READY = "not_ready"
    READY_FOR_REVIEW = "ready_for_review"
    PASSED = "passed"
    PASSED_WITH_DEVIATION = "passed_with_deviation"
    FAILED = "failed"
    STALE = "stale"
    WAIVED = "waived"


class GateCheck(BaseModel):
    id: str
    label: str
    status: GateCheckStatus
    detail: str


class GateEvaluation(BaseModel):
    gate_id: str
    status: GateStatus
    checks: list[GateCheck] = Field(default_factory=list)


def _status_from_checks(checks: list[GateCheck]) -> GateStatus:
    """FAIL beats NOT_EVALUATED beats PASS. A gate with any unevaluated
    check can be reviewed by a human (READY_FOR_REVIEW) but never
    auto-reported PASSED -- "a gate shall NOT be a manually set boolean"
    cuts both ways: it also must not be silently marked done."""
    if any(c.status == GateCheckStatus.FAIL for c in checks):
        return GateStatus.FAILED
    if any(c.status == GateCheckStatus.NOT_EVALUATED for c in checks):
        return GateStatus.READY_FOR_REVIEW
    return GateStatus.PASSED


_RISK_LEVELS = [(20, "critical"), (12, "high"), (6, "medium"), (0, "low")]


def _risk_level(score: int) -> str:
    for threshold, level in _RISK_LEVELS:
        if score >= threshold:
            return level
    return "low"  # pragma: no cover -- thresholds bottom out at 0


async def _evaluate_budget_check(engine: BudgetEngine, budget: Budget) -> GateCheck:
    status = await engine.compute_status(budget)
    return GateCheck(
        id=f"budget:{budget.id}",
        label=f"{budget.metric.title()} budget ({budget.unit})",
        status=GateCheckStatus.FAIL if status.violation else GateCheckStatus.PASS,
        detail=(
            f"actual={status.actual:.3g}{budget.unit}, "
            f"system_total={status.system_total:.3g}{budget.unit}, "
            f"margin={status.margin:.3g}{budget.unit}"
        ),
    )


async def _evaluate_invariant_check(
    engine: InvariantEngine, project_id: UUID, invariant: Invariant
) -> GateCheck:
    (result,) = await engine.evaluate(project_id, [invariant])
    return GateCheck(
        id=f"invariant:{invariant.id}",
        label=invariant.id,
        status=GateCheckStatus.FAIL if result.violated else GateCheckStatus.PASS,
        detail=(
            f"current={result.current:.3g}{invariant.unit} "
            f"{invariant.comparison.value} {invariant.limit:.3g}{invariant.unit}"
        ),
    )


async def _evaluate_risk_checks(twin: TwinAPI, project_id: UUID) -> list[GateCheck]:
    """One check per recorded 'risk' EngineeringEntity: FAIL if it scores
    critical (severity*likelihood) with no mitigation recorded;
    NOT_EVALUATED if the risk hasn't been scored yet (metadata missing
    severity/likelihood) -- never a silent PASS for an un-assessed risk. No
    risks recorded at all also surfaces as one NOT_EVALUATED check, not an
    empty, invisible pass.
    """
    entities = await twin.list_engineering_entities(project_id=project_id)
    risks = [e for e in entities if e.entity_type == "risk"]
    if not risks:
        return [
            GateCheck(
                id="risks:none-recorded",
                label="Major risks identified",
                status=GateCheckStatus.NOT_EVALUATED,
                detail="no 'risk' entities recorded for this project yet",
            )
        ]

    checks: list[GateCheck] = []
    for risk in risks:
        severity = risk.metadata.get("severity")
        likelihood = risk.metadata.get("likelihood")
        label = risk.title or risk.statement or str(risk.id)
        if severity is None or likelihood is None:
            checks.append(
                GateCheck(
                    id=f"risk:{risk.id}",
                    label=f"Risk assessed: {label}",
                    status=GateCheckStatus.NOT_EVALUATED,
                    detail="no severity/likelihood recorded in metadata yet",
                )
            )
            continue
        score = int(severity) * int(likelihood)
        level = _risk_level(score)
        mitigated = bool(str(risk.metadata.get("mitigation", "")).strip())
        is_critical_unmitigated = level == "critical" and not mitigated
        checks.append(
            GateCheck(
                id=f"risk:{risk.id}",
                label=f"Risk mitigated: {label}",
                status=GateCheckStatus.FAIL if is_critical_unmitigated else GateCheckStatus.PASS,
                detail=(
                    f"severity={severity}, likelihood={likelihood}, "
                    f"level={level}, mitigated={mitigated}"
                ),
            )
        )
    return checks


_G3_NOT_EVALUATED_CHECKS = (
    ("structural_feasibility", "First-order structural feasibility"),
    ("actuator_sizing", "Actuator sizing"),
    ("thermal_plausibility", "Thermal plausibility"),
    ("geometry_feasibility", "Geometry feasibility"),
    ("technology_availability", "Technology availability"),
)


async def evaluate_g3_feasibility(
    twin: TwinAPI,
    project_id: UUID,
    *,
    budgets: list[Budget] | None = None,
    invariants: list[Invariant] | None = None,
) -> GateEvaluation:
    """Evaluate the G3 Preliminary Feasibility Gate (spec section 23) for
    `project_id`. `budgets`/`invariants` are the caller-supplied
    declarations for this project (mass/cost/power/... whichever apply) --
    see this module's docstring for why there's no auto-derivation yet.
    """
    budget_engine = BudgetEngine(twin.graph)
    invariant_engine = InvariantEngine(twin.graph)

    checks: list[GateCheck] = []
    for budget in budgets or []:
        checks.append(await _evaluate_budget_check(budget_engine, budget))
    for invariant in invariants or []:
        checks.append(await _evaluate_invariant_check(invariant_engine, project_id, invariant))
    checks.extend(await _evaluate_risk_checks(twin, project_id))
    for check_id, label in _G3_NOT_EVALUATED_CHECKS:
        checks.append(
            GateCheck(
                id=check_id,
                label=label,
                status=GateCheckStatus.NOT_EVALUATED,
                detail="requires simulation/CAD evidence (Phase 6, FORGE-41) not yet wired",
            )
        )

    return GateEvaluation(gate_id="G3", status=_status_from_checks(checks), checks=checks)
