"""Project-backed gate evaluator for the design flow (MET-10).

Answers "which work-product types has this project recorded since the phase
started?" by reading the same project store the dashboard reads. A ``cad_model``
counts only when it is actually **loadable** — has a retrievable blob
(minio_object_key / file_path / content_hash) in the twin — not a bare node, so a
described-but-uncommitted model cannot pass a gate (the eval flywheel showed the
native brain sometimes records a cad_model node with no blob).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog

from api_gateway.projects.backend import ProjectBackend
from orchestrator.design_flow.executor import ConstraintReport

logger = structlog.get_logger(__name__)


async def _is_loadable(twin: object, wp_id: object) -> bool:
    """Whether the twin has a retrievable blob for this work product.

    Fail-open when we can't check (no twin / no id) so tests and non-twin setups
    keep their prior behaviour; only a wired twin tightens the gate.
    """
    if twin is None or wp_id is None:
        return True
    getter = getattr(twin, "get_work_product", None)
    if getter is None:
        return True
    try:
        wp = await getter(UUID(str(wp_id)))
    except Exception:  # noqa: BLE001 - a lookup failure means "not loadable"
        return False
    if wp is None:
        return False
    meta = getattr(wp, "metadata", None) or {}
    return bool(
        meta.get("minio_object_key") or meta.get("file_path") or getattr(wp, "content_hash", None)
    )


def _to_epoch(value: object) -> float | None:
    """Best-effort parse of a work product's ``updated_at`` into epoch seconds."""
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value.timestamp()
    return None


class ProjectGateEvaluator:
    """`GateEvaluator` backed by the gateway's project store.

    ``twin`` (optional) lets the evaluator verify a ``cad_model`` is loadable
    before it counts toward a gate; without it, presence alone counts.
    """

    def __init__(self, backend: ProjectBackend, *, twin: object | None = None) -> None:
        self._backend = backend
        self._twin = twin

    async def present_types(self, project_id: str | None, since_ts: float) -> set[str]:
        if not project_id:
            return set()
        project = await self._backend.get_project(project_id)
        if project is None:
            return set()
        present: set[str] = set()
        for wp in project.work_products:
            ts = _to_epoch(getattr(wp, "updated_at", None))
            # Count a deliverable only if it was recorded in this phase's window;
            # if a timestamp can't be parsed, count it (fail-open on readiness).
            if ts is not None and ts < since_ts:
                continue
            wp_type = getattr(wp, "type", None)
            if wp_type is None:
                continue
            type_str = str(getattr(wp_type, "value", wp_type))
            # A cad_model only counts if it is actually loadable in the twin.
            if type_str == "cad_model" and not await _is_loadable(
                self._twin, getattr(wp, "id", None)
            ):
                logger.info(
                    "gate_eval_cad_not_loadable",
                    project_id=project_id,
                    wp_id=getattr(wp, "id", None),
                )
                continue
            present.add(type_str)
        logger.info(
            "gate_eval_present_types",
            project_id=project_id,
            present=sorted(present),
            since_ts=since_ts,
        )
        return present


class TwinConstraintChecker:
    """`ConstraintChecker` backed by the twin's constraint engine (MET-583).

    Evaluates all constraints on the main branch, then scopes violations to
    the run's project where the data allows it: a violation citing
    ``work_product_ids`` counts only if at least one of them belongs to the
    project; a violation citing none is global and always counts. (The engine
    itself is not project-scoped today — see MET-583.)
    """

    def __init__(self, twin: object, backend: ProjectBackend | None = None) -> None:
        self._twin = twin
        self._backend = backend

    async def _project_wp_ids(self, project_id: str | None) -> set[str] | None:
        """The project's work-product ids, or None when unscopable."""
        if not project_id or self._backend is None:
            return None
        try:
            project = await self._backend.get_project(project_id)
        except Exception:  # noqa: BLE001 - scoping is best-effort
            return None
        if project is None:
            return None
        return {str(getattr(wp, "id", "")) for wp in project.work_products}

    async def check(self, project_id: str | None) -> ConstraintReport:
        evaluate = getattr(self._twin, "evaluate_constraints", None)
        if evaluate is None:
            return ConstraintReport(checked=False)
        result = await evaluate(branch="main")
        scope = await self._project_wp_ids(project_id)

        def _in_scope(violation: object) -> bool:
            wp_ids = [str(w) for w in getattr(violation, "work_product_ids", []) or []]
            if not wp_ids or scope is None:
                return True  # global violation, or nothing to scope against
            return any(w in scope for w in wp_ids)

        def _fmt(violation: object) -> str:
            name = getattr(violation, "constraint_name", "?")
            message = getattr(violation, "message", "")
            return f"{name}: {message}" if message else str(name)

        violations = [_fmt(v) for v in result.violations if _in_scope(v)]
        warnings = [_fmt(v) for v in result.warnings if _in_scope(v)]
        report = ConstraintReport(
            checked=True,
            passed=not violations,
            evaluated_count=int(getattr(result, "evaluated_count", 0)),
            violations=violations,
            warnings=warnings,
        )
        logger.info(
            "gate_eval_constraints",
            project_id=project_id,
            passed=report.passed,
            violations=len(violations),
            warnings=len(warnings),
            evaluated=report.evaluated_count,
        )
        return report
