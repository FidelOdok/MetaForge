"""G3-G8 gate evaluation (FORGE-60/61/62/63, spec sections 22-23, Phase 5 of
epic FORGE-35): Preliminary Feasibility (G3), Architecture (G4), Concept
Selection (G5), Preliminary Design / Design Sketch (G6), Verification
Readiness (G7), Release (G8).

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

**G4 (Architecture)**: "architecture satisfies major constraints" is real --
``TwinAPI.evaluate_constraints()`` (the same engine
``orchestrator/design_flow/spec.py``'s ``Gate(enforce_constraints=True)``
already gates V&V sign-off with), scoped to the project's own Constraint ids
so an unrelated project's violation can't fail this gate. Note the engine
only evaluates a Constraint reachable via a CONSTRAINED_BY edge from a
WorkProduct (how ``twin.record_constraint_set`` always creates one) -- a
bare ``twin.create_constraint()`` node with no binding is invisible to it,
same as everywhere else this engine is used. "Critical
requirements allocated" (``EdgeType.ALLOCATED_TO`` has zero creators anywhere
in the codebase today), "subsystem boundaries defined", "interfaces
identified", and "no unowned safety-critical requirement" (no subsystem/
interface node type, no safety-critical/owner metadata convention -- grepped,
confirmed pure white space) come back ``NOT_EVALUATED``.

**G5 (Concept Selection)**: reads the project's ``design_decision`` work
products (``twin.record_decision``). FORGE-61 extended that recorder with
``parent_refs``/``relation`` (default ``satisfies``) so "selected concept
linked to requirements/objectives" is a real graph edge, not metadata prose --
the first real use of the previously-declared-but-unused ``EdgeType.
SATISFIES``. "Trade study performed" is a per-decision PASS when
``alternatives`` is non-empty; the spec's own "where applicable" hedge means
an empty list is ``NOT_EVALUATED``, not a blind FAIL -- this module can't
tell "no trade study needed" from "one was skipped". "Rationale captured" is
real (``rationale`` is a required field on every recorded decision).
Deliberately NOT built in this pass: the "Decision Agent" (spec section
26.12) that would generate alternatives/run a trade study/select one
automatically -- this module only evaluates decisions a caller already
recorded, same posture as G3 evaluating budgets a caller already declared.

**G6 (Preliminary Design / Design Sketch)**: FORGE-62's own ticket asked to
formalize the EXISTING ``design_sketch``/approve-sketch mechanism
(``api_gateway/twin/design_sketch_recorder.py``, the
``decide_sketch_needed``/``author_design_sketch`` mechanical skills, the
``POST /v1/twin/nodes/{id}/approve-sketch`` route) rather than build a
parallel one -- there is no ``orchestrator/design_flow`` phase for it in any
flow today (a sketch is a "cheap reference checkpoint before CAD" the
existing skills decide is or isn't needed; none of the three built-in flows
gates on it), so this evaluator, like G5, has no phase to attach to yet.
"Geometry/layout" reads whether a ``design_sketch`` work product exists and
its real ``metadata["approved"]`` flag -- PASS when approved, FAIL when one
exists but hasn't been approved (a real, actionable gap), NOT_EVALUATED when
none exists at all (``decide_sketch_needed``'s own rules mean a sketch is
sometimes legitimately not needed -- e.g. a single-part, non-novel,
non-revision design -- so absence isn't automatically a failure). "Components"
and "major interfaces" reuse ``SYSTEM_ARCHITECTURE`` work product metadata
(``component_count``/``interface_count``/``dangling_interfaces``, written by
``make_system_architecture_recorder``) when one has been recorded for the
project. "Unresolved risks" reuses ``_evaluate_risk_checks`` verbatim (same
convention as G3). "Mass estimate", "power estimate", and "manufacturability
concerns" come back ``NOT_EVALUATED``: grepped the codebase and confirmed no
``design_sketch``/``cad_model`` creation path ever writes a mass or power
field (``cross_domain_rules.py`` reads ``weight_grams``/
``power_dissipation_w`` but nothing writes them), and the tolerance/DFM skill
(``check_tolerance``) computes manufacturability in-session but never
persists it to the Twin, so nothing survives for a gate to query afterward.
"Requirement coverage" also comes back ``NOT_EVALUATED``: ``TraceabilityAgent``
(FORGE-56) computes a real, structured ``TraceabilityCoverage`` internally
but only returns it stringified inside ``AgentResult.evidence`` -- exposing
it as a reusable accessor is a real, small, separate refactor of tested
Phase-3 code, deliberately not risked in this pass.

**G7 (Verification Readiness)**: per critical (``ConstraintSeverity.ERROR``)
requirement in the project, two real per-requirement checks reusing
``TraceabilityAgent``'s own established conventions rather than inventing
new ones -- "verification method defined" (``Constraint.metadata
["verification_method"]``, the exact field ``RequirementAuthorAgent``
(FORGE-55) already writes and ``TraceabilityAgent`` already reads) and
"ownership defined" (``Constraint.source`` -- confirmed the closest real
owner field this codebase has; ``TraceabilityAgent``'s own docstring says
so explicitly). Both are a real FAIL when missing on a critical requirement,
not a hedge -- unlike G5's alternatives, there's no legitimate "not
applicable" case for a critical requirement lacking a verification method or
an owner. "Acceptance criteria defined", "measurement method defined", and
"expected evidence defined" come back ``NOT_EVALUATED``: grepped the whole
repo for ``acceptance_criteria``/``expected_evidence``/``measurement_method``
as metadata keys -- zero hits anywhere. ``entity_type="verification_case"``
is a real, usable ``EngineeringEntity`` literal with a fully generic
recorder (``engineering_entity_recorder.py``) but no dedicated field
convention of its own, same situation G4 found for "subsystem"/"interface".

**G8 (Release)**: "configuration baseline fixed" is real --
``TwinAPI.list_baselines(project_id=...)`` (FORGE-51) already exists and is
project-scoped; FAIL when the project has none (a release without any
baseline is a real, actionable gap, not a vacuous pass -- unlike G4's
"no constraints recorded" case, a baseline is an unconditionally required
release step, so its absence isn't hedged as NOT_EVALUATED). "Stale evidence
resolved" is real -- reads ``"evidence"`` ``EngineeringEntity`` nodes'
``metadata["staleness"]`` (FORGE-59's ``StalenessEngine`` convention,
default ``current`` when unset) directly, no ``propagate()`` call needed;
FAIL if any is ``stale``/``invalid``. When the project has recorded NO
evidence at all, this comes back ``NOT_EVALUATED`` rather than a vacuous
PASS -- a release gate silently reporting "no stale evidence" when nothing
was ever verified would be actively misleading, the exact vacuous-pass
failure mode this codebase already guards against elsewhere (MET-582/583's
constraint-as-gate-criteria rules). "Required verification complete" comes
back ``NOT_EVALUATED`` for the same reason as G6's "requirement coverage" --
``TraceabilityCoverage.verification_to_evidence`` is computed internally by
``TraceabilityAgent`` but not exposed as a reusable accessor, and
``EdgeType.VALIDATES`` has zero real creators anywhere in production code.
"Waivers approved" and "build/manufacturing release approved" come back
``NOT_EVALUATED``: grepped for "waiver" -- the only real hit is a transient
classification string ``HITLEngine`` accepts as an approval-request
category (never persisted to the graph, no ``list_waivers``/project scoping
possible), confirmed pure white space.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from twin_core.api import TwinAPI
from twin_core.consistency.budgets import BudgetEngine
from twin_core.consistency.invariants import InvariantEngine
from twin_core.consistency.models import Budget, Invariant
from twin_core.models.enums import ConstraintSeverity, WorkProductType


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


_G4_NOT_EVALUATED_CHECKS = (
    ("critical_requirements_allocated", "Critical requirements allocated"),
    ("subsystem_boundaries_defined", "Subsystem boundaries defined"),
    ("interfaces_identified", "Interfaces identified"),
    ("no_unowned_safety_critical_requirement", "No unowned safety-critical requirement"),
)


async def evaluate_g4_architecture(twin: TwinAPI, project_id: UUID) -> GateEvaluation:
    """Evaluate the G4 Architecture Gate (spec section 23) for `project_id`.
    See this module's docstring for exactly which checks are real today.
    """
    project_constraint_ids = {c.id for c in await twin.list_constraints(project_id=project_id)}
    result = await twin.evaluate_constraints()
    project_violations = [v for v in result.violations if v.constraint_id in project_constraint_ids]
    checks: list[GateCheck] = [
        GateCheck(
            id="architecture_satisfies_major_constraints",
            label="Architecture satisfies major constraints",
            status=GateCheckStatus.FAIL if project_violations else GateCheckStatus.PASS,
            detail=(
                f"{len(project_violations)} ERROR-severity violation(s) among "
                f"{len(project_constraint_ids)} project constraint(s): "
                + ", ".join(v.constraint_name for v in project_violations)
                if project_violations
                else f"0 violations among {len(project_constraint_ids)} project constraint(s)"
            ),
        )
    ]
    for check_id, label in _G4_NOT_EVALUATED_CHECKS:
        checks.append(
            GateCheck(
                id=check_id,
                label=label,
                status=GateCheckStatus.NOT_EVALUATED,
                detail="no subsystem/interface graph concept or safety-critical/owner "
                "metadata convention exists yet",
            )
        )
    return GateEvaluation(gate_id="G4", status=_status_from_checks(checks), checks=checks)


async def _evaluate_decision_checks(twin: TwinAPI, project_id: UUID) -> list[GateCheck]:
    decisions = await twin.list_work_products(
        work_product_type=WorkProductType.DESIGN_DECISION, project_id=project_id
    )
    if not decisions:
        return [
            GateCheck(
                id="decisions:none-recorded",
                label="Viable concept(s) selected",
                status=GateCheckStatus.NOT_EVALUATED,
                detail="no design_decision recorded for this project yet",
            )
        ]

    checks: list[GateCheck] = []
    for decision in decisions:
        label = decision.name or str(decision.id)
        alternatives = decision.metadata.get("alternatives") or []
        rationale = str(decision.metadata.get("rationale") or "").strip()
        parent_refs = decision.metadata.get("parent_refs") or []

        checks.append(
            GateCheck(
                id=f"decision:{decision.id}:trade_study",
                label=f"Trade study performed: {label}",
                status=GateCheckStatus.PASS if alternatives else GateCheckStatus.NOT_EVALUATED,
                detail=(
                    f"{len(alternatives)} alternative(s) recorded"
                    if alternatives
                    else "no alternatives recorded -- may be not applicable, not evaluated"
                ),
            )
        )
        checks.append(
            GateCheck(
                id=f"decision:{decision.id}:rationale",
                label=f"Rationale captured: {label}",
                status=GateCheckStatus.PASS if rationale else GateCheckStatus.FAIL,
                detail="rationale present" if rationale else "no rationale recorded",
            )
        )
        checks.append(
            GateCheck(
                id=f"decision:{decision.id}:linked",
                label=f"Selected concept linked to requirements/objectives: {label}",
                status=GateCheckStatus.PASS if parent_refs else GateCheckStatus.NOT_EVALUATED,
                detail=(
                    f"linked to {len(parent_refs)} requirement(s)/objective(s)"
                    if parent_refs
                    else "no parent_refs recorded on this decision"
                ),
            )
        )
    return checks


async def evaluate_g5_concept_selection(twin: TwinAPI, project_id: UUID) -> GateEvaluation:
    """Evaluate the G5 Concept Selection Gate (spec section 23) for
    `project_id`. See this module's docstring for exactly which checks are
    real today and what's deliberately out of scope (the Decision Agent).
    """
    checks = await _evaluate_decision_checks(twin, project_id)
    return GateEvaluation(gate_id="G5", status=_status_from_checks(checks), checks=checks)


async def _evaluate_design_sketch_check(twin: TwinAPI, project_id: UUID) -> GateCheck:
    sketches = await twin.list_work_products(
        work_product_type=WorkProductType.DESIGN_SKETCH, project_id=project_id
    )
    if not sketches:
        return GateCheck(
            id="geometry_layout",
            label="Geometry/layout sketch approved",
            status=GateCheckStatus.NOT_EVALUATED,
            detail="no design_sketch recorded -- may not be needed per decide_sketch_needed",
        )
    unapproved = [s for s in sketches if not s.metadata.get("approved")]
    return GateCheck(
        id="geometry_layout",
        label="Geometry/layout sketch approved",
        status=GateCheckStatus.FAIL if unapproved else GateCheckStatus.PASS,
        detail=(
            f"{len(unapproved)} of {len(sketches)} sketch(es) awaiting approval"
            if unapproved
            else f"all {len(sketches)} sketch(es) approved"
        ),
    )


async def _evaluate_architecture_checks(twin: TwinAPI, project_id: UUID) -> list[GateCheck]:
    architectures = await twin.list_work_products(
        work_product_type=WorkProductType.SYSTEM_ARCHITECTURE, project_id=project_id
    )
    if not architectures:
        return [
            GateCheck(
                id="components",
                label="Components identified",
                status=GateCheckStatus.NOT_EVALUATED,
                detail="no system_architecture recorded for this project yet",
            ),
            GateCheck(
                id="major_interfaces",
                label="Major interfaces identified",
                status=GateCheckStatus.NOT_EVALUATED,
                detail="no system_architecture recorded for this project yet",
            ),
        ]

    component_count = sum(int(a.metadata.get("component_count") or 0) for a in architectures)
    interface_count = sum(int(a.metadata.get("interface_count") or 0) for a in architectures)
    dangling: list[str] = []
    for a in architectures:
        dangling.extend(a.metadata.get("dangling_interfaces") or [])

    return [
        GateCheck(
            id="components",
            label="Components identified",
            status=GateCheckStatus.PASS if component_count > 0 else GateCheckStatus.FAIL,
            detail=f"{component_count} component(s) across {len(architectures)} architecture(s)",
        ),
        GateCheck(
            id="major_interfaces",
            label="Major interfaces identified",
            status=GateCheckStatus.FAIL if dangling else GateCheckStatus.PASS,
            detail=(
                f"{interface_count} interface(s), {len(dangling)} dangling"
                if dangling
                else f"{interface_count} interface(s), none dangling"
            ),
        ),
    ]


_G6_NOT_EVALUATED_CHECKS = (
    ("mass_estimate", "Mass estimate"),
    ("power_estimate", "Power estimate"),
    ("requirement_coverage", "Requirement coverage"),
    ("manufacturability_concerns", "Manufacturability concerns"),
)


async def evaluate_g6_design_sketch(twin: TwinAPI, project_id: UUID) -> GateEvaluation:
    """Evaluate the G6 Preliminary Design / Design Sketch Gate (spec section
    23) for `project_id`. See this module's docstring for exactly which
    checks are real today, and how this formalizes the existing
    design_sketch/approve-sketch mechanism rather than replacing it.
    """
    checks: list[GateCheck] = [await _evaluate_design_sketch_check(twin, project_id)]
    checks.extend(await _evaluate_architecture_checks(twin, project_id))
    checks.extend(await _evaluate_risk_checks(twin, project_id))
    for check_id, label in _G6_NOT_EVALUATED_CHECKS:
        checks.append(
            GateCheck(
                id=check_id,
                label=label,
                status=GateCheckStatus.NOT_EVALUATED,
                detail="no data source exists yet -- see this module's docstring",
            )
        )
    return GateEvaluation(gate_id="G6", status=_status_from_checks(checks), checks=checks)


_G7_NOT_EVALUATED_CHECKS = (
    ("acceptance_criteria_defined", "Acceptance criteria defined"),
    ("measurement_method_defined", "Measurement method defined"),
    ("expected_evidence_defined", "Expected evidence defined"),
)


async def _evaluate_critical_requirement_checks(twin: TwinAPI, project_id: UUID) -> list[GateCheck]:
    constraints = await twin.list_constraints(project_id=project_id)
    critical = [c for c in constraints if c.severity == ConstraintSeverity.ERROR]
    if not critical:
        return [
            GateCheck(
                id="requirements:none-critical",
                label="Critical requirements have verification method + ownership",
                status=GateCheckStatus.NOT_EVALUATED,
                detail="no ERROR-severity (critical) requirements recorded for this project yet",
            )
        ]

    checks: list[GateCheck] = []
    for req in critical:
        has_verification = bool(req.metadata.get("verification_method"))
        checks.append(
            GateCheck(
                id=f"requirement:{req.id}:verification_method",
                label=f"Verification method defined: {req.name}",
                status=GateCheckStatus.PASS if has_verification else GateCheckStatus.FAIL,
                detail=(
                    str(req.metadata.get("verification_method"))
                    if has_verification
                    else "no verification_method recorded on this critical requirement"
                ),
            )
        )
        has_owner = bool(req.source)
        checks.append(
            GateCheck(
                id=f"requirement:{req.id}:ownership",
                label=f"Verification ownership defined: {req.name}",
                status=GateCheckStatus.PASS if has_owner else GateCheckStatus.FAIL,
                detail=(
                    f"source={req.source}"
                    if has_owner
                    else "no source recorded on this critical requirement"
                ),
            )
        )
    return checks


async def evaluate_g7_verification_readiness(twin: TwinAPI, project_id: UUID) -> GateEvaluation:
    """Evaluate the G7 Verification Readiness Gate (spec section 23) for
    `project_id`. See this module's docstring for exactly which checks are
    real today.
    """
    checks = await _evaluate_critical_requirement_checks(twin, project_id)
    for check_id, label in _G7_NOT_EVALUATED_CHECKS:
        checks.append(
            GateCheck(
                id=check_id,
                label=label,
                status=GateCheckStatus.NOT_EVALUATED,
                detail="no verification_case metadata convention exists yet -- see this "
                "module's docstring",
            )
        )
    return GateEvaluation(gate_id="G7", status=_status_from_checks(checks), checks=checks)


_G8_NOT_EVALUATED_CHECKS = (
    ("required_verification_complete", "Required verification complete"),
    ("waivers_approved", "Waivers approved"),
    ("release_approved", "Build/manufacturing release approved"),
)


async def _evaluate_baseline_check(twin: TwinAPI, project_id: UUID) -> GateCheck:
    baselines = await twin.list_baselines(project_id=project_id)
    return GateCheck(
        id="configuration_baseline_fixed",
        label="Configuration baseline fixed",
        status=GateCheckStatus.PASS if baselines else GateCheckStatus.FAIL,
        detail=(
            f"{len(baselines)} baseline(s) recorded"
            if baselines
            else "no baseline recorded for this project -- release requires one"
        ),
    )


async def _evaluate_stale_evidence_check(twin: TwinAPI, project_id: UUID) -> GateCheck:
    entities = await twin.list_engineering_entities(project_id=project_id)
    evidence = [e for e in entities if e.entity_type == "evidence"]
    if not evidence:
        return GateCheck(
            id="stale_evidence_resolved",
            label="Stale evidence resolved",
            status=GateCheckStatus.NOT_EVALUATED,
            detail="no 'evidence' entities recorded for this project yet",
        )
    stale = [e for e in evidence if e.metadata.get("staleness", "current") in ("stale", "invalid")]
    return GateCheck(
        id="stale_evidence_resolved",
        label="Stale evidence resolved",
        status=GateCheckStatus.FAIL if stale else GateCheckStatus.PASS,
        detail=(
            f"{len(stale)} of {len(evidence)} evidence entit(ies) stale/invalid"
            if stale
            else f"all {len(evidence)} evidence entit(ies) current"
        ),
    )


async def evaluate_g8_release(twin: TwinAPI, project_id: UUID) -> GateEvaluation:
    """Evaluate the G8 Release Gate (spec section 23) for `project_id`. See
    this module's docstring for exactly which checks are real today.
    """
    checks: list[GateCheck] = [
        await _evaluate_baseline_check(twin, project_id),
        await _evaluate_stale_evidence_check(twin, project_id),
    ]
    for check_id, label in _G8_NOT_EVALUATED_CHECKS:
        checks.append(
            GateCheck(
                id=check_id,
                label=label,
                status=GateCheckStatus.NOT_EVALUATED,
                detail="no data source exists yet -- see this module's docstring",
            )
        )
    return GateEvaluation(gate_id="G8", status=_status_from_checks(checks), checks=checks)
