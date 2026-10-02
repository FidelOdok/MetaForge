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
from typing import Any
from uuid import UUID

import structlog

from api_gateway.projects.backend import ProjectBackend
from api_gateway.runs.analysis_constraints import (
    AnalysisCheck,
    SimResult,
    check_analysis_constraints,
)
from api_gateway.runs.geometry_constraints import GeometryCheck, check_geometry_constraints
from orchestrator.design_flow.executor import ConsistencyGateReport, ConstraintReport
from twin_core.consistency import (
    evaluate_g3_feasibility,
    evaluate_g4_architecture,
    evaluate_g5_concept_selection,
    evaluate_g6_design_sketch,
    evaluate_g7_verification_readiness,
    evaluate_g8_release,
)
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)


async def _is_loadable(twin: object, wp_id: object) -> bool:
    """Whether the twin has a retrievable blob for this work product.

    Fail-open when we can't check (no twin / no id) so tests and non-twin setups
    keep their prior behaviour; only a wired twin tightens the gate.

    A lookup that *raises* stays fail-closed -- an unverifiable model must not
    satisfy a gate -- but it is logged distinctly (MET-728). "The blob is
    genuinely missing" and "the twin was unreachable" both produced the same
    ``gate_eval_cad_not_loadable`` line, and they want opposite responses:
    regenerate the model, versus retry.
    """
    if twin is None or wp_id is None:
        return True
    getter = getattr(twin, "get_work_product", None)
    if getter is None:
        return True
    try:
        wp = await getter(UUID(str(wp_id)))
    except Exception as exc:  # noqa: BLE001 - unverifiable must not pass a gate
        logger.warning(
            "gate_eval_loadability_unknown",
            wp_id=str(wp_id),
            error=str(exc),
            consequence="treated as not loadable; the gate will fail closed",
        )
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
        except Exception as exc:  # noqa: BLE001 - scoping is best-effort
            # MET-728: None means "unscopable", and unscopable means EVERY
            # violation counts as in-scope -- so the gate gets stricter. Safe
            # direction, but a design flow could fail its constraint gate
            # because a project lookup blipped, and nothing recorded that
            # scoping was even attempted.
            logger.warning(
                "gate_eval_project_scoping_failed",
                project_id=project_id,
                error=str(exc),
                consequence="all violations counted as in-scope; the gate fails closed",
            )
            return None
        if project is None:
            return None
        return {str(getattr(wp, "id", "")) for wp in project.work_products}

    async def _current_cad_models(self, project_id: str) -> list[tuple[str, dict[str, Any]]]:
        """The latest cad_model per name for the project, as ``(name, metadata)``."""
        getter = getattr(self._twin, "get_work_product", None)
        if self._backend is None or getter is None:
            return []
        project = await self._backend.get_project(project_id)
        if project is None:
            return []
        latest: dict[str, tuple[float, Any]] = {}
        for wp in project.work_products:
            wp_type = getattr(wp, "type", None)
            if str(getattr(wp_type, "value", wp_type)) != "cad_model":
                continue
            ts = _to_epoch(getattr(wp, "updated_at", None)) or 0.0
            name = str(getattr(wp, "name", "") or getattr(wp, "id", ""))
            if name not in latest or ts >= latest[name][0]:
                latest[name] = (ts, wp)
        models: list[tuple[str, dict[str, Any]]] = []
        for name, (_, wp) in latest.items():
            try:
                node = await getter(UUID(str(getattr(wp, "id", ""))))
            except Exception as exc:  # noqa: BLE001 - one unreadable model must not crash the gate
                logger.warning("gate_eval_cad_read_failed", name=name, error=str(exc))
                continue
            if node is not None:
                models.append((name, dict(getattr(node, "metadata", None) or {})))
        return models

    async def _geometry_check(self, project_id: str | None) -> GeometryCheck:
        lister = getattr(self._twin, "list_constraints", None)
        if lister is None or not project_id:
            return GeometryCheck()
        try:
            constraints = await lister(project_id=UUID(project_id))
            models = await self._current_cad_models(project_id)
        except Exception as exc:  # noqa: BLE001 - geometry comparison is best-effort
            logger.warning(
                "gate_eval_geometry_constraints_failed",
                project_id=project_id,
                error=str(exc),
                consequence="geometry constraints not compared at this gate",
            )
            return GeometryCheck()
        outcome = check_geometry_constraints(constraints, models)
        logger.info(
            "gate_eval_geometry_constraints",
            project_id=project_id,
            evaluated=outcome.evaluated,
            violations=len(outcome.violations),
            not_evaluated=outcome.not_evaluated,
        )
        return outcome

    async def _analysis_check(self, project_id: str | None) -> AnalysisCheck:
        """FORGE-498: analysis constraints vs the latest simulation_result per cad_model."""
        lister = getattr(self._twin, "list_constraints", None)
        getter = getattr(self._twin, "get_work_product", None)
        if lister is None or getter is None or self._backend is None or not project_id:
            return AnalysisCheck()
        try:
            constraints = await lister(project_id=UUID(project_id))
            project = await self._backend.get_project(project_id)
            if project is None:
                return AnalysisCheck()
            latest: dict[str, tuple[float, str]] = {}
            sim_wps: list[Any] = []
            for wp in project.work_products:
                wp_type = getattr(wp, "type", None)
                type_str = str(getattr(wp_type, "value", wp_type))
                if type_str == "simulation_result":
                    sim_wps.append(wp)
                elif type_str == "cad_model":
                    ts = _to_epoch(getattr(wp, "updated_at", None)) or 0.0
                    name = str(getattr(wp, "name", "") or getattr(wp, "id", ""))
                    if name not in latest or ts >= latest[name][0]:
                        latest[name] = (ts, str(getattr(wp, "id", "")))
            sims = [await self._read_sim(wp, getter) for wp in sim_wps]
        except Exception as exc:  # noqa: BLE001 - analysis comparison is best-effort
            logger.warning(
                "gate_eval_analysis_constraints_failed",
                project_id=project_id,
                error=str(exc),
                consequence="analysis constraints not compared at this gate",
            )
            return AnalysisCheck()
        models = [(cad_id, name) for name, (_, cad_id) in latest.items()]
        outcome = check_analysis_constraints(constraints, models, [s for s in sims if s])
        logger.info(
            "gate_eval_analysis_constraints",
            project_id=project_id,
            evaluated=outcome.evaluated,
            violations=len(outcome.violations),
            not_evaluated=outcome.not_evaluated,
        )
        return outcome

    async def _read_sim(self, wp: Any, getter: Any) -> SimResult | None:
        """A simulation_result with the cad_models it derives from, or None if unreadable."""
        sim_id = str(getattr(wp, "id", ""))
        try:
            node = await getter(UUID(sim_id))
        except Exception as exc:  # noqa: BLE001 - one unreadable result must not crash the gate
            logger.warning("gate_eval_sim_read_failed", sim_id=sim_id, error=str(exc))
            return None
        if node is None:
            return None
        meta = dict(getattr(node, "metadata", None) or {})
        cad_ids: set[str] = set()
        if meta.get("source_cad_model_id"):
            cad_ids.add(str(meta["source_cad_model_id"]))
        edges = getattr(self._twin, "get_edges", None)
        if edges is not None:
            try:
                for edge in await edges(UUID(sim_id), "outgoing", EdgeType.DERIVES_FROM):
                    cad_ids.add(str(edge.target_id))
            except Exception as exc:  # noqa: BLE001 - metadata link may still resolve
                logger.warning("gate_eval_sim_edges_failed", sim_id=sim_id, error=str(exc))
        ts = _to_epoch(getattr(wp, "updated_at", None)) or 0.0
        name = str(getattr(wp, "name", "") or sim_id)
        return SimResult(id=sim_id, name=name, updated_at=ts, metadata=meta, cad_ids=cad_ids)

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
        # FORGE-496: structured metric/limit constraints carry a placeholder
        # expression the engine can never fail, so compare them to the
        # committed cad_model here.
        geometry = await self._geometry_check(project_id)
        violations += geometry.violations
        warnings += geometry.warnings
        analysis = await self._analysis_check(project_id)
        violations += analysis.violations
        warnings += analysis.warnings
        report = ConstraintReport(
            checked=True,
            passed=not violations,
            evaluated_count=int(getattr(result, "evaluated_count", 0))
            + geometry.evaluated
            + analysis.evaluated,
            violations=violations,
            warnings=warnings,
            satisfied=analysis.satisfied,
            not_evaluated=geometry.not_evaluated + analysis.not_evaluated,
            assumptions=analysis.assumptions,
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


class TwinConsistencyGateChecker:
    """`ConsistencyGateChecker` backed by `twin_core.consistency.gates`
    (FORGE-73/91). Six ``gate_id``s (G3-G8) are mapped; G0-G2 have no
    dedicated evaluator module yet. Purely informational: the executor
    never fails a gate on this checker's result (no ``enforce_*`` flag
    exists for it). G6/G7 don't inject a ``traceability_coverage``
    accessor -- same posture as every other gate here, none do -- so their
    requirement-coverage check stays ``NOT_EVALUATED`` until that's wired.
    """

    def __init__(self, twin: Any) -> None:
        self._twin = twin

    async def check(self, gate_id: str, project_id: str | None) -> ConsistencyGateReport:
        if not project_id:
            return ConsistencyGateReport(checked=False)
        try:
            pid = UUID(project_id)
        except ValueError:
            return ConsistencyGateReport(checked=False)

        if gate_id == "G3":
            evaluation = await evaluate_g3_feasibility(self._twin, pid)
        elif gate_id == "G4":
            evaluation = await evaluate_g4_architecture(self._twin, pid)
        elif gate_id == "G5":
            evaluation = await evaluate_g5_concept_selection(self._twin, pid)
        elif gate_id == "G6":
            evaluation = await evaluate_g6_design_sketch(self._twin, pid)
        elif gate_id == "G7":
            evaluation = await evaluate_g7_verification_readiness(self._twin, pid)
        elif gate_id == "G8":
            evaluation = await evaluate_g8_release(self._twin, pid)
        else:
            return ConsistencyGateReport(checked=False)

        logger.info(
            "gate_eval_consistency",
            project_id=project_id,
            gate_id=gate_id,
            status=evaluation.status.value,
        )
        return ConsistencyGateReport(checked=True, evaluation=evaluation)
