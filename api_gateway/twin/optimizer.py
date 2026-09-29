"""Wall-thickness optimisation orchestration for twin.optimize_parameter
(FORGE-320).

Wires ``twin_core.prediction.optimizer``'s pure bisection search to real
graph data (a CAD WorkProduct's own recorded bounding box, the same
``bounding_box_extents_mm`` ``api_gateway/twin/metric_evaluator.py`` and
``sensitivity.py`` already use) and real material properties
(``tool_registry.tools.cadquery.materials`` -- density, elastic modulus,
and the new ``MATERIAL_YIELD_MPA`` table this ticket adds, reused via their
existing ``resolve_*`` lookups, not a second material table).

Records the search as Evidence (``valid_against`` the work product, same
staleness-pinning convention ``metric_evaluator.py``/``sensitivity.py``
established), and -- when a feasible winner is found -- the result as a
real Decision via the existing ``twin.record_decision`` mechanism (MET-495,
FORGE-61's own "alternatives" field): each rejected candidate the bisection
evaluated becomes one alternative, with the constraint that rejected it as
its ``reason_rejected``. No new "Decision with alternatives" node type was
built -- ``record_decision`` already is exactly that.

Deliberately out of scope (see ``twin_core/prediction/optimizer.py``'s own
docstring for the full rationale): proposing the winning wall thickness as
an actual geometry change via ECT (``ControlledEntityKind`` only supports
``constraint``/``engineering_entity`` today, not ``work_product`` -- a
nontrivial, separate widening of the transaction engine itself, not a quick
follow-up); regenerating/committing the optimised geometry (no
parametrized re-authoring script exists for the real arm part -- its
original ``cadquery.execute_script`` source was never persisted with a
substitutable ``wall_thickness_mm`` variable). This ticket ships the
numeric recommendation + Decision record, not an automatic geometry edit.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from api_gateway.twin.metric_evaluator import bounding_box_extents_mm
from observability.tracing import get_tracer
from tool_registry.tools.cadquery.materials import (
    resolve_density_kg_m3,
    resolve_elastic_properties,
    resolve_yield_mpa,
)
from twin_core.prediction.optimizer import optimize_tube_height, optimize_wall_thickness

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.optimizer")


def make_wall_thickness_optimizer(
    twin: Any, *, evidence_recorder: Any = None, decision_recorder: Any = None
) -> Any:
    """Return an async ``optimize(...)`` bound to a twin + (optional)
    evidence/decision recorders."""

    async def optimize(
        *,
        work_product_id: str,
        load_n: float,
        deflection_limit_mm: float,
        sf_limit: float = 2.0,
        material: str = "aluminum_6061",
        wall_min_mm: float = 0.5,
        wall_max_mm: float | None = None,
        project_id: str | None = None,
        requirement_ids: list[str] | None = None,
        record_decision: bool = True,
    ) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.optimize_parameter") as span:
            wp_id = UUID(work_product_id)
            wp = await twin.get_work_product(wp_id)
            if wp is None:
                raise ValueError(f"twin.optimize_parameter: no work_product {work_product_id!r}")
            length_mm, width_mm, height_mm = bounding_box_extents_mm(wp.metadata)

            youngs_modulus_mpa, _poissons_ratio = resolve_elastic_properties(material)
            density_kg_m3 = resolve_density_kg_m3(material)
            yield_mpa = resolve_yield_mpa(material)

            span.set_attribute("optimizer.work_product_id", work_product_id)
            span.set_attribute("optimizer.material", material)

            result = optimize_wall_thickness(
                length_mm=length_mm,
                width_mm=width_mm,
                height_mm=height_mm,
                load_n=load_n,
                youngs_modulus_mpa=youngs_modulus_mpa,
                density_kg_m3=density_kg_m3,
                yield_mpa=yield_mpa,
                deflection_limit_mm=deflection_limit_mm,
                sf_limit=sf_limit,
                wall_min_mm=wall_min_mm,
                wall_max_mm=wall_max_mm,
            )
            out: dict[str, Any] = result.model_dump()
            out["material"] = material
            span.set_attribute("optimizer.status", result.status)

            if evidence_recorder is not None:
                ev = await evidence_recorder(
                    evidence_type="calculation",
                    producer={"tool": "twin_core.prediction.optimizer"},
                    inputs={
                        "work_product_id": work_product_id,
                        "material": material,
                        "load_n": load_n,
                        "deflection_limit_mm": deflection_limit_mm,
                        "sf_limit": sf_limit,
                        "length_mm": length_mm,
                        "width_mm": width_mm,
                        "height_mm": height_mm,
                    },
                    result=out,
                    statement=f"wall-thickness optimisation ({result.status}): {result.detail}",
                    valid_against=[{"ref": work_product_id, "entity_kind": "work_product"}],
                    project_id=project_id,
                )
                out["evidence_node_id"] = ev["node_id"]

            if record_decision and decision_recorder is not None and result.winner is not None:
                rejected = [
                    c
                    for c in result.candidates
                    if not c.feasible and c.wall_thickness_mm != result.winner.wall_thickness_mm
                ]
                alternatives = [
                    {
                        "option": f"wall_thickness_mm={c.wall_thickness_mm:.4g}",
                        "reason_rejected": (
                            f"deflection_margin={c.deflection_margin_mm:.4g}mm, "
                            f"sf_margin={c.sf_margin:.4g}"
                        ),
                    }
                    for c in rejected
                ]
                decision = await decision_recorder(
                    title=(
                        f"Optimised wall thickness: {result.winner.wall_thickness_mm:.4g}mm "
                        f"({material})"
                    ),
                    rationale=(
                        f"Minimum wall thickness meeting deflection <= {deflection_limit_mm}mm "
                        f"and safety factor >= {sf_limit} for work product {work_product_id}, "
                        "found via bisection over the tier-0 hollow-tube hand-calc "
                        f"(twin_core.prediction.optimizer). Resulting mass: "
                        f"{result.winner.mass_kg:.4g}kg."
                    ),
                    alternatives=alternatives,
                    parent_refs=requirement_ids,
                    project_id=project_id,
                    domain="mechanical",
                )
                out["decision_node_id"] = decision["node_id"]

            logger.info(
                "wall_thickness_optimized",
                work_product_id=work_product_id,
                status=result.status,
                winner_wall_thickness_mm=(
                    result.winner.wall_thickness_mm if result.winner else None
                ),
            )
            return out

    return optimize


def make_tube_height_optimizer(
    twin: Any, *, evidence_recorder: Any = None, decision_recorder: Any = None
) -> Any:
    """Return an async ``optimize(...)`` bound to a twin + (optional)
    evidence/decision recorders -- same shape as
    ``make_wall_thickness_optimizer``, but sweeps ``height_mm`` with
    ``wall_thickness_mm`` held fixed (FORGE-288, gap G-G2). See
    ``twin_core.prediction.optimizer.optimize_tube_height``'s own docstring
    for why bisection over height is exactly as sound as over wall
    thickness."""

    async def optimize(
        *,
        work_product_id: str,
        wall_thickness_mm: float,
        load_n: float,
        deflection_limit_mm: float,
        sf_limit: float = 2.0,
        material: str = "aluminum_6061",
        height_min_mm: float = 1.0,
        height_max_mm: float | None = None,
        project_id: str | None = None,
        requirement_ids: list[str] | None = None,
        record_decision: bool = True,
    ) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.optimize_tube_height") as span:
            wp_id = UUID(work_product_id)
            wp = await twin.get_work_product(wp_id)
            if wp is None:
                raise ValueError(f"twin.optimize_tube_height: no work_product {work_product_id!r}")
            length_mm, width_mm, _height_mm = bounding_box_extents_mm(wp.metadata)

            youngs_modulus_mpa, _poissons_ratio = resolve_elastic_properties(material)
            density_kg_m3 = resolve_density_kg_m3(material)
            yield_mpa = resolve_yield_mpa(material)

            span.set_attribute("optimizer.work_product_id", work_product_id)
            span.set_attribute("optimizer.material", material)

            result = optimize_tube_height(
                length_mm=length_mm,
                width_mm=width_mm,
                wall_thickness_mm=wall_thickness_mm,
                load_n=load_n,
                youngs_modulus_mpa=youngs_modulus_mpa,
                density_kg_m3=density_kg_m3,
                yield_mpa=yield_mpa,
                deflection_limit_mm=deflection_limit_mm,
                sf_limit=sf_limit,
                height_min_mm=height_min_mm,
                height_max_mm=height_max_mm,
            )
            out: dict[str, Any] = result.model_dump()
            out["material"] = material
            span.set_attribute("optimizer.status", result.status)

            if evidence_recorder is not None:
                ev = await evidence_recorder(
                    evidence_type="calculation",
                    producer={"tool": "twin_core.prediction.optimizer"},
                    inputs={
                        "work_product_id": work_product_id,
                        "material": material,
                        "wall_thickness_mm": wall_thickness_mm,
                        "load_n": load_n,
                        "deflection_limit_mm": deflection_limit_mm,
                        "sf_limit": sf_limit,
                        "length_mm": length_mm,
                        "width_mm": width_mm,
                    },
                    result=out,
                    statement=f"tube-height optimisation ({result.status}): {result.detail}",
                    valid_against=[{"ref": work_product_id, "entity_kind": "work_product"}],
                    project_id=project_id,
                )
                out["evidence_node_id"] = ev["node_id"]

            if record_decision and decision_recorder is not None and result.winner is not None:
                rejected = [
                    c
                    for c in result.candidates
                    if not c.feasible and c.height_mm != result.winner.height_mm
                ]
                alternatives = [
                    {
                        "option": f"height_mm={c.height_mm:.4g}",
                        "reason_rejected": (
                            f"deflection_margin={c.deflection_margin_mm:.4g}mm, "
                            f"sf_margin={c.sf_margin:.4g}"
                        ),
                    }
                    for c in rejected
                ]
                decision = await decision_recorder(
                    title=f"Optimised tube height: {result.winner.height_mm:.4g}mm ({material})",
                    rationale=(
                        f"Minimum height (wall_thickness_mm={wall_thickness_mm:.4g} fixed) "
                        f"meeting deflection <= {deflection_limit_mm}mm and safety factor "
                        f">= {sf_limit} for work product {work_product_id}, found via "
                        "bisection over the tier-0 hollow-tube hand-calc "
                        f"(twin_core.prediction.optimizer). Resulting mass: "
                        f"{result.winner.mass_kg:.4g}kg."
                    ),
                    alternatives=alternatives,
                    parent_refs=requirement_ids,
                    project_id=project_id,
                    domain="mechanical",
                )
                out["decision_node_id"] = decision["node_id"]

            logger.info(
                "tube_height_optimized",
                work_product_id=work_product_id,
                status=result.status,
                winner_height_mm=(result.winner.height_mm if result.winner else None),
            )
            return out

    return optimize
