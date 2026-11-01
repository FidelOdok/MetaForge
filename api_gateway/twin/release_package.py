"""Release package: a versioned snapshot of a project's hierarchy, BOM,
evidence, and decisions (FORGE-299, gap G-I3).

**What this is.** `twin.create_release_package` bundles the current state
of four already-real data sources -- the hierarchy tree
(`twin.list_hierarchy_nodes`), the BOM (`twin.list_bom_items`), Evidence
entities (`twin.list_engineering_entities(entity_type="evidence")`), and
Decision records (`twin.list_work_products(work_product_type=
WorkProductType.DESIGN_DECISION)`) -- into one new `release_package`
EngineeringEntity (FORGE-45's generic node), recorded via the existing
`engineering_entity_recorder` closure every other entity type already goes
through. The snapshot holds ONLY the ids of the items it references, never
a deep copy of their content -- a release package is a pointer-in-time
index, not a duplicate archive.

**The real gate this ticket reuses, not duplicates.** `release_approval`
(FORGE-73) and G8's `evaluate_g8_release` (`twin_core.consistency.gates`)
already existed before this ticket, real and comprehensive (baseline fixed,
no stale evidence, verification coverage complete, waivers approved, a
`release_approval` entity approved) -- but purely *informational*: nothing
in this codebase had ever called it as anything but an advisory display
value (`api_gateway/runs/gate_eval.py`'s `TwinConsistencyGateChecker`, which
itself never blocks a gate transition on the result). This module gives G8
its first real *consumer with teeth*: `create_release_package` calls
`evaluate_g8_release` itself and refuses to create a package unless it
returns `GateStatus.PASSED`. This is additive -- it does not change
`gate_eval.py`'s non-blocking posture anywhere else in the system; G8
remains purely advisory everywhere except this one new creation-time check.

**"Diff against previous release."** Deliberately a simple, real count
delta (hierarchy/BOM/evidence/decision counts, current minus the
immediately-prior package for the same project) -- not a structural diff.
No diff/comparison utility exists anywhere in `twin_core` to build a real
structural diff on top of, and inventing one is out of scope for this
ticket.

**Deliberately out of scope**:
- **Drawings.** FORGE-293 ("2D technical drawings... GD&T") has not
  shipped -- zero drawing-generation capability exists anywhere in this
  codebase (confirmed during FORGE-294's own scoping investigation, which
  hit the identical gap for CNC manufacturing outputs). A release package's
  snapshot carries an always-empty `drawing_ids` list rather than fake
  drawing content or a silently-missing field.
- **A full structural diff** between two releases' hierarchy/BOM/evidence
  content -- the count-delta described above ships instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.consistency.gates import GateStatus, evaluate_g8_release
from twin_core.models.enums import WorkProductType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.release_package")

_SNAPSHOT_LIST_KEYS = (
    "hierarchy_node_ids",
    "bom_item_ids",
    "evidence_ids",
    "decision_ids",
    "drawing_ids",
)


async def _snapshot(twin: Any, project_id: UUID) -> dict[str, Any]:
    hierarchy_nodes = await twin.list_hierarchy_nodes(project_id=project_id)
    bom_items = await twin.list_bom_items(project_id=project_id)
    evidence = await twin.list_engineering_entities(project_id=project_id, entity_type="evidence")
    decisions = await twin.list_work_products(
        project_id=project_id, work_product_type=WorkProductType.DESIGN_DECISION
    )
    return {
        "hierarchy_node_ids": [str(n.id) for n in hierarchy_nodes],
        "bom_item_ids": [str(i.id) for i in bom_items],
        "evidence_ids": [str(e.id) for e in evidence],
        "decision_ids": [str(d.id) for d in decisions],
        # FORGE-293 (2D technical drawings) hasn't shipped -- always empty,
        # never fabricated, until that capability exists to snapshot.
        "drawing_ids": [],
    }


def _count_diff(prior: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    if prior is None:
        return {
            "compared_to": None,
            "hierarchy_delta": len(current["hierarchy_node_ids"]),
            "bom_delta": len(current["bom_item_ids"]),
            "evidence_delta": len(current["evidence_ids"]),
            "decision_delta": len(current["decision_ids"]),
        }
    return {
        "compared_to": prior["node_id"],
        "hierarchy_delta": len(current["hierarchy_node_ids"]) - len(prior["hierarchy_node_ids"]),
        "bom_delta": len(current["bom_item_ids"]) - len(prior["bom_item_ids"]),
        "evidence_delta": len(current["evidence_ids"]) - len(prior["evidence_ids"]),
        "decision_delta": len(current["decision_ids"]) - len(prior["decision_ids"]),
    }


def make_release_package_creator(
    twin: Any,
    *,
    engineering_entity_recorder: Any,
    traceability_coverage: Any = None,
) -> Any:
    """Return an async ``create(*, project_id, notes=None) -> dict`` bound
    to a twin + engineering_entity_recorder (+ optional
    ``traceability_coverage`` accessor, a ``Callable[[UUID], Awaitable]``
    matching ``twin_core.consistency.gates.TraceabilityCoverageAccessor``,
    so G8's 'verification complete' check can actually evaluate instead of
    reporting NOT_EVALUATED)."""

    async def create(*, project_id: str, notes: str | None = None) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.create_release_package") as span:
            pid = UUID(project_id)
            span.set_attribute("release.project_id", project_id)

            gate = await evaluate_g8_release(twin, pid, traceability_coverage=traceability_coverage)
            span.set_attribute("release.gate_status", gate.status.value)
            if gate.status != GateStatus.PASSED:
                failing = "; ".join(
                    f"{c.label}: {c.detail}" for c in gate.checks if c.status.value != "pass"
                )
                raise ValueError(
                    "twin.create_release_package: G8 release gate is "
                    f"{gate.status.value}, not passed -- {failing}"
                )

            snapshot = await _snapshot(twin, pid)

            existing = await twin.list_engineering_entities(
                project_id=pid, entity_type="release_package"
            )
            prior: dict[str, Any] | None = None
            if existing:
                prior_entity = max(existing, key=lambda e: e.created_at)
                prior = {
                    "node_id": str(prior_entity.id),
                    **{k: prior_entity.metadata.get(k, []) for k in _SNAPSHOT_LIST_KEYS},
                }

            diff = _count_diff(prior, snapshot)

            statement = (
                f"Release package: {len(snapshot['hierarchy_node_ids'])} hierarchy nodes, "
                f"{len(snapshot['bom_item_ids'])} BOM items, "
                f"{len(snapshot['evidence_ids'])} evidence, "
                f"{len(snapshot['decision_ids'])} decisions"
            )
            created_at = datetime.now(UTC)
            title = notes or f"Release package {created_at.isoformat()}"

            recorded = await engineering_entity_recorder(
                entity_type="release_package",
                statement=statement,
                title=title,
                extra={
                    **snapshot,
                    "diff_from_previous": diff,
                    "gate_status": gate.status.value,
                },
                project_id=project_id,
            )

            logger.info(
                "release_package_created",
                project_id=project_id,
                node_id=recorded["node_id"],
                hierarchy_count=len(snapshot["hierarchy_node_ids"]),
                bom_count=len(snapshot["bom_item_ids"]),
                evidence_count=len(snapshot["evidence_ids"]),
                decision_count=len(snapshot["decision_ids"]),
            )

            return {
                **recorded,
                "title": title,
                "statement": statement,
                "created_at": created_at.isoformat(),
                "snapshot": snapshot,
                "diff_from_previous": diff,
                "gate_status": gate.status.value,
            }

    return create


def make_release_package_lister(twin: Any) -> Any:
    """Return an async ``list_packages(*, project_id) -> list[dict]``,
    oldest first, each carrying its own already-computed
    ``diff_from_previous`` (computed once at creation time, not recomputed
    on every list call)."""

    async def list_packages(*, project_id: str) -> list[dict[str, Any]]:
        pid = UUID(project_id)
        entities = await twin.list_engineering_entities(
            project_id=pid, entity_type="release_package"
        )
        entities_sorted = sorted(entities, key=lambda e: e.created_at)
        return [
            {
                "node_id": str(e.id),
                "created_at": e.created_at.isoformat(),
                "title": e.title,
                "statement": e.statement,
                "snapshot": {k: e.metadata.get(k, []) for k in _SNAPSHOT_LIST_KEYS},
                "diff_from_previous": e.metadata.get("diff_from_previous"),
                "gate_status": e.metadata.get("gate_status"),
            }
            for e in entities_sorted
        ]

    return list_packages
