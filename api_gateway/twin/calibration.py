"""Calibration residual recording + band lookup orchestration (FORGE-321).

A calibration residual is persisted the same way every other tier-0/tier-2
result in this epic is -- as Evidence (``evidence_type="inspection"``,
reusing FORGE-64's mechanism rather than inventing a new node type), never
a bespoke store outside the graph. This matters for the ticket's own
acceptance wording ("a new project's prediction uses the calibrated
band"): a calibration prior has to survive past the session/process that
recorded it, and outlive the specific project it was measured on -- a
plain in-memory cache would satisfy neither. ``lookup`` deliberately does
NOT filter by ``project_id``: calibration is a prior that informs the
NEXT project, not a per-project cache.

``digital_twin/calibration/store.py`` is the pure math (residual list ->
:class:`CalibratedBand`); this module is only the real-graph plumbing
around it.
"""

from __future__ import annotations

from typing import Any

import structlog

from digital_twin.calibration.store import CalibratedBand, compute_calibrated_band
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.calibration")

#: Marks a recorded Evidence entity as calibration-residual data rather
#: than a tier-0/tier-2 prediction record -- both share the same
#: ``entity_type="evidence"`` node, distinguished by this key the way
#: FORGE-318's matrix already distinguishes evidence shapes by their own
#: result keys (``value_mm``/``margin_mm`` vs. ``baseline_value``/...).
_RESIDUAL_KIND = "calibration_residual"


def make_calibration_recorder(twin: Any, *, evidence_recorder: Any = None) -> Any:
    """Return an async ``record_residual(...)`` bound to a twin + (optional)
    evidence recorder."""

    async def record_residual(
        *,
        metric: str,
        tier: int,
        predicted: float,
        measured: float,
        unit: str = "mm",
        project_id: str | None = None,
        valid_against: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        residual = predicted - measured
        result = {
            "kind": _RESIDUAL_KIND,
            "metric": metric,
            "tier": tier,
            "unit": unit,
            "predicted": predicted,
            "measured": measured,
            "residual": residual,
        }
        with tracer.start_as_current_span("calibration.record_residual") as span:
            span.set_attribute("calibration.metric", metric)
            span.set_attribute("calibration.tier", tier)
            span.set_attribute("calibration.residual", residual)

            if evidence_recorder is None:
                return {"residual": residual}

            ev = await evidence_recorder(
                evidence_type="inspection",
                producer={"tool": "digital_twin.calibration"},
                inputs={
                    "metric": metric,
                    "tier": tier,
                    "predicted": predicted,
                    "measured": measured,
                },
                result=result,
                statement=(
                    f"calibration residual for {metric} tier {tier}: predicted "
                    f"{predicted:.4g}{unit} vs measured {measured:.4g}{unit} "
                    f"(residual {residual:.4g}{unit})"
                ),
                valid_against=valid_against,
                project_id=project_id,
            )
            logger.info(
                "calibration_residual_recorded",
                node_id=ev["node_id"],
                metric=metric,
                tier=tier,
                residual=residual,
            )
            return {"residual": residual, "evidence_node_id": ev["node_id"]}

    return record_residual


def make_calibrated_band_lookup(twin: Any) -> Any:
    """Return an async ``lookup(metric, tier) -> CalibratedBand | None``
    bound to a twin, scanning every calibration-residual Evidence entity
    recorded for that ``(metric, tier)`` -- across every project, on
    purpose (see module docstring)."""

    async def lookup(*, metric: str, tier: int) -> CalibratedBand | None:
        entities = await twin.list_engineering_entities(entity_type="evidence")
        residuals: list[float] = []
        for e in entities:
            result = e.metadata.get("result") or {}
            if (
                result.get("kind") == _RESIDUAL_KIND
                and result.get("metric") == metric
                and result.get("tier") == tier
            ):
                residuals.append(result["residual"])
        return compute_calibrated_band(residuals, metric=metric, tier=tier)

    return lookup
