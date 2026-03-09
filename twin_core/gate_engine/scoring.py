"""Scoring algorithm for gate readiness evaluation."""

from __future__ import annotations

from datetime import UTC, datetime

from twin_core.gate_engine.models import (
    GateDefinition,
    ReadinessScore,
)


def compute_readiness_score(gate: GateDefinition) -> ReadinessScore:
    """Compute the weighted readiness score for a gate definition.

    The score is a weighted average of all criteria scores. Required criteria
    that are not fully met (score < 1.0) are recorded as blockers regardless
    of the overall weighted score.

    Returns a ReadinessScore with the computed score, pass/fail status,
    and any blockers.
    """
    if not gate.criteria:
        return ReadinessScore(
            gate_id=gate.id,
            phase=gate.phase,
            weighted_score=0.0,
            threshold=gate.threshold,
            passed=False,
            blockers=["No criteria defined"],
            criteria_scores={},
            computed_at=datetime.now(UTC),
        )

    total_weight = sum(c.weight for c in gate.criteria)
    if total_weight == 0:
        return ReadinessScore(
            gate_id=gate.id,
            phase=gate.phase,
            weighted_score=0.0,
            threshold=gate.threshold,
            passed=False,
            blockers=["All criteria have zero weight"],
            criteria_scores={c.name: c.score for c in gate.criteria},
            computed_at=datetime.now(UTC),
        )

    weighted_sum = sum(c.weight * c.score for c in gate.criteria)
    weighted_score = weighted_sum / total_weight

    blockers: list[str] = []
    criteria_scores: dict[str, float] = {}

    for criterion in gate.criteria:
        criteria_scores[criterion.name] = criterion.score
        if criterion.required and criterion.score < 1.0:
            blockers.append(
                f"Required criterion '{criterion.name}' not fully met "
                f"(score: {criterion.score:.2f})"
            )

    # Gate passes if weighted score meets threshold AND no required blockers
    passed = weighted_score >= gate.threshold and len(blockers) == 0

    # Also add threshold blocker if score is below
    if weighted_score < gate.threshold:
        blockers.insert(
            0,
            f"Weighted score {weighted_score:.2f} below threshold {gate.threshold:.2f}",
        )

    return ReadinessScore(
        gate_id=gate.id,
        phase=gate.phase,
        weighted_score=round(weighted_score, 4),
        threshold=gate.threshold,
        passed=passed,
        blockers=blockers,
        criteria_scores=criteria_scores,
        computed_at=datetime.now(UTC),
    )
