"""Test plan generation from requirements (FORGE-298, gap G-I2).

**What this is.** `twin.generate_test_plan` mechanically derives one
`verification_case` EngineeringEntity (FORGE-45's generic node) per
Constraint (requirement) on a project whose `verification_method == "test"`.
A Constraint already carries everything a bench-test step needs as real
structured fields (FORGE-258/259/312): `metric`, `operator`, `limit`,
`unit`, `target_node_type`. Deriving a test step is therefore string
formatting over existing data, not new authoring/synthesis logic --
``"Measure {metric} on {target_node_type}; acceptance: {metric} {operator}
{limit}{unit}"``.

**`verification_case`'s new metadata shape.** Unlike `release_package`/
`concept_option`/`waiver`, `verification_case` had no defined metadata
convention before this ticket. This module establishes it:
``{"step": str, "acceptance_value": str, "requirement_id": str}`` -- see
``docs/twin_schema.md`` for the documented shape.

**Deliberately out of scope**:
- **FMEA, HALT/HASS, reliability modeling.** MetaForge-Planner's
  `FRAMEWORK_MAPPING.md` lists these alongside test plans under discipline
  #9 ("Testing & Reliability"), but only marks test plans as the Phase-1-
  relevant slice. Separate, later-phase tickets.
- **Bench-equipment/procedure authoring.** This derives the measurement +
  acceptance criteria only, never lab setup instructions, fixture specs, or
  equipment lists.
- **Wiring generated test cases into `evaluate_g8_release`'s verification-
  complete check.** A real, separable follow-up -- generating a
  `verification_case` doesn't by itself attach real Evidence to satisfy
  that check, which is a distinct capability.
- **Hardcoding to payload/reach/repeatability.** Those are the ticket's own
  illustrative examples, not a fixed category list -- this generates a test
  plan entry for whatever real requirements a project actually has with
  `verification_method == "test"`, generically.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.test_plan")


def _derive_step(constraint: Any) -> str:
    metric = constraint.metric or constraint.name
    target = f" on {constraint.target_node_type}" if constraint.target_node_type else ""
    acceptance = f"{metric} {constraint.operator} {constraint.limit}{constraint.unit}"
    return f"Measure {metric}{target}; acceptance: {acceptance}"


def _derive_acceptance_value(constraint: Any) -> str:
    return f"{constraint.limit}{constraint.unit}"


def make_test_plan_generator(
    twin: Any,
    *,
    engineering_entity_recorder: Any,
) -> Any:
    """Return an async ``generate(*, project_id) -> dict`` bound to a twin +
    engineering_entity_recorder. For every Constraint on the project with
    ``verification_method == "test"``, records one new ``verification_case``
    entity whose metadata carries the mechanically-derived
    ``{step, acceptance_value, requirement_id}``. Idempotent-by-intent is
    NOT guaranteed -- calling this twice creates two verification_case
    entities per matching requirement (mirrors release_package's own
    create-a-new-snapshot-each-call semantics; callers wanting "only the
    latest" should read the most recently created one per requirement_id)."""

    async def generate(*, project_id: str) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.generate_test_plan") as span:
            pid = UUID(project_id)
            span.set_attribute("test_plan.project_id", project_id)

            constraints = await twin.list_constraints(project_id=pid)
            test_constraints = [c for c in constraints if c.verification_method == "test"]
            span.set_attribute("test_plan.requirement_count", len(test_constraints))

            entries: list[dict[str, Any]] = []
            for constraint in test_constraints:
                step = _derive_step(constraint)
                acceptance_value = _derive_acceptance_value(constraint)
                recorded = await engineering_entity_recorder(
                    entity_type="verification_case",
                    statement=step,
                    title=f"Test plan: {constraint.name}",
                    extra={
                        "step": step,
                        "acceptance_value": acceptance_value,
                        "requirement_id": str(constraint.id),
                    },
                    # Default relation ("derives_from"): no EdgeType member
                    # cleanly expresses "verification_case verifies
                    # constraint" in the child->parent direction the
                    # recorder always uses (VERIFIED_BY reads backwards
                    # here), so this reuses the same generic derivation
                    # relation every other entity recorder defaults to.
                    parent_refs=[str(constraint.id)],
                    project_id=project_id,
                )
                entries.append(
                    {
                        "node_id": recorded["node_id"],
                        "requirement_id": str(constraint.id),
                        "requirement_name": constraint.name,
                        "step": step,
                        "acceptance_value": acceptance_value,
                    }
                )

            logger.info(
                "test_plan_generated",
                project_id=project_id,
                requirement_count=len(test_constraints),
                entry_count=len(entries),
            )
            return {"project_id": project_id, "entries": entries}

    return generate


def make_test_plan_lister(twin: Any) -> Any:
    """Return an async ``list_entries(*, project_id) -> list[dict]``, oldest
    first, reading real ``verification_case`` entities back from the graph."""

    async def list_entries(*, project_id: str) -> list[dict[str, Any]]:
        pid = UUID(project_id)
        entities = await twin.list_engineering_entities(
            project_id=pid, entity_type="verification_case"
        )
        entities_sorted = sorted(entities, key=lambda e: e.created_at)
        return [
            {
                "node_id": str(e.id),
                "requirement_id": e.metadata.get("requirement_id", ""),
                "step": e.metadata.get("step", e.statement or ""),
                "acceptance_value": e.metadata.get("acceptance_value", ""),
                "created_at": e.created_at.isoformat(),
            }
            for e in entities_sorted
        ]

    return list_entries
