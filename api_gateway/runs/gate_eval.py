"""Project-backed gate evaluator for the design flow (MET-10).

Answers "which work-product types has this project recorded since the phase
started?" by reading the same project store the dashboard reads — so a gate is
"ready" only when the deliverable is actually viewable in the twin, closing the
"CAD silently missing" gap.
"""

from __future__ import annotations

from datetime import datetime

import structlog

from api_gateway.projects.backend import ProjectBackend

logger = structlog.get_logger(__name__)


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
    """`GateEvaluator` backed by the gateway's project store."""

    def __init__(self, backend: ProjectBackend) -> None:
        self._backend = backend

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
            if ts is None or ts >= since_ts:
                wp_type = getattr(wp, "type", None)
                if wp_type is not None:
                    present.add(str(getattr(wp_type, "value", wp_type)))
        logger.info(
            "gate_eval_present_types",
            project_id=project_id,
            present=sorted(present),
            since_ts=since_ts,
        )
        return present
