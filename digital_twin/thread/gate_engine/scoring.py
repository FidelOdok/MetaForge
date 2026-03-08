"""Gate scoring functions for EVT/DVT/PVT readiness evaluation.

Each function computes a score from 0-100 for a specific gate criterion
by querying the Digital Twin graph state.
"""

from __future__ import annotations

import structlog

from observability.tracing import get_tracer
from twin_core.api import TwinAPI
from twin_core.constraint_engine.models import ConstraintEvaluationResult
from twin_core.models.enums import ArtifactType

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.gate_engine")


async def calculate_requirement_coverage(twin: TwinAPI, branch: str) -> float:
    """Calculate the percentage of requirements with linked test evidence.

    Queries all PRD artifacts (requirements) and checks how many have
    associated edges with test_evidence metadata. Returns 0-100.
    """
    with tracer.start_as_current_span("gate.scoring.requirement_coverage") as span:
        span.set_attribute("branch", branch)

        requirements = await twin.list_artifacts(
            branch=branch, artifact_type=ArtifactType.PRD
        )
        if not requirements:
            logger.info("requirement_coverage_no_requirements", branch=branch)
            return 100.0  # No requirements = fully covered (vacuously true)

        covered = 0
        for req in requirements:
            edges = await twin.get_edges(req.id, direction="outgoing")
            has_evidence = any(
                edge.metadata.get("type") == "test_evidence"
                for edge in edges
            )
            if has_evidence:
                covered += 1

        score = (covered / len(requirements)) * 100.0
        span.set_attribute("gate.requirements_total", len(requirements))
        span.set_attribute("gate.requirements_covered", covered)
        span.set_attribute("gate.score", score)

        logger.info(
            "requirement_coverage_calculated",
            branch=branch,
            total=len(requirements),
            covered=covered,
            score=score,
        )
        return score


async def calculate_bom_risk(twin: TwinAPI, branch: str) -> float:
    """Calculate BOM risk score as inverse of average risk across BOM items.

    Queries all components and averages their risk_score metadata.
    Returns 100 - avg_risk (so lower risk = higher score). Range: 0-100.
    """
    with tracer.start_as_current_span("gate.scoring.bom_risk") as span:
        span.set_attribute("branch", branch)

        components = await twin.find_components({})
        if not components:
            logger.info("bom_risk_no_components", branch=branch)
            return 100.0  # No components = no risk

        total_risk = 0.0
        counted = 0
        for comp in components:
            risk = getattr(comp, "risk_score", None)
            if risk is None and hasattr(comp, "specs"):
                risk = comp.specs.get("risk_score")
            if risk is not None:
                total_risk += float(risk)
                counted += 1

        if counted == 0:
            score = 100.0  # No risk data = assume low risk
        else:
            avg_risk = total_risk / counted
            score = max(0.0, min(100.0, 100.0 - avg_risk))

        span.set_attribute("gate.components_total", len(components))
        span.set_attribute("gate.components_with_risk", counted)
        span.set_attribute("gate.score", score)

        logger.info(
            "bom_risk_calculated",
            branch=branch,
            total_components=len(components),
            with_risk=counted,
            score=score,
        )
        return score


async def calculate_constraint_compliance(
    constraint_result: ConstraintEvaluationResult,
) -> float:
    """Calculate constraint compliance as percentage of passing constraints.

    Uses the evaluated_count and violations from a ConstraintEvaluationResult.
    Returns 0-100.
    """
    with tracer.start_as_current_span("gate.scoring.constraint_compliance") as span:
        total = constraint_result.evaluated_count
        if total == 0:
            return 100.0  # No constraints = fully compliant

        failing = len(constraint_result.violations)
        passing = total - failing
        score = (passing / total) * 100.0

        span.set_attribute("gate.constraints_total", total)
        span.set_attribute("gate.constraints_passing", passing)
        span.set_attribute("gate.constraints_failing", failing)
        span.set_attribute("gate.score", score)

        logger.info(
            "constraint_compliance_calculated",
            total=total,
            passing=passing,
            failing=failing,
            score=score,
        )
        return score


async def calculate_test_evidence(twin: TwinAPI, branch: str) -> float:
    """Calculate the percentage of test plans with linked results.

    Queries TEST_PLAN artifacts and checks how many have associated
    edges with test_result metadata. Returns 0-100.
    """
    with tracer.start_as_current_span("gate.scoring.test_evidence") as span:
        span.set_attribute("branch", branch)

        test_plans = await twin.list_artifacts(
            branch=branch, artifact_type=ArtifactType.TEST_PLAN
        )
        if not test_plans:
            logger.info("test_evidence_no_test_plans", branch=branch)
            return 100.0  # No test plans = vacuously true

        with_results = 0
        for tp in test_plans:
            edges = await twin.get_edges(tp.id, direction="outgoing")
            has_result = any(
                edge.metadata.get("type") == "test_result"
                for edge in edges
            )
            if has_result:
                with_results += 1

        score = (with_results / len(test_plans)) * 100.0
        span.set_attribute("gate.test_plans_total", len(test_plans))
        span.set_attribute("gate.test_plans_with_results", with_results)
        span.set_attribute("gate.score", score)

        logger.info(
            "test_evidence_calculated",
            branch=branch,
            total=len(test_plans),
            with_results=with_results,
            score=score,
        )
        return score


async def calculate_design_review(twin: TwinAPI, branch: str) -> float:
    """Calculate the percentage of artifacts with review approval.

    Queries all artifacts and checks their metadata for review_status == 'approved'.
    Returns 0-100.
    """
    with tracer.start_as_current_span("gate.scoring.design_review") as span:
        span.set_attribute("branch", branch)

        artifacts = await twin.list_artifacts(branch=branch)
        if not artifacts:
            logger.info("design_review_no_artifacts", branch=branch)
            return 100.0  # No artifacts = nothing to review

        approved = 0
        for art in artifacts:
            review_status = art.metadata.get("review_status") if art.metadata else None
            if review_status == "approved":
                approved += 1

        score = (approved / len(artifacts)) * 100.0
        span.set_attribute("gate.artifacts_total", len(artifacts))
        span.set_attribute("gate.artifacts_approved", approved)
        span.set_attribute("gate.score", score)

        logger.info(
            "design_review_calculated",
            branch=branch,
            total=len(artifacts),
            approved=approved,
            score=score,
        )
        return score
