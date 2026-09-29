"""Sensitivity-analysis orchestration for twin.rank_sensitivity (FORGE-317).

Wires ``twin_core.prediction.sensitivity``'s pure finite-difference math to
real graph data (a CAD WorkProduct's own recorded bounding box, the same
``bounding_box_extents_mm`` read ``api_gateway/twin/metric_evaluator.py``
already uses) and real material properties (``tool_registry.tools.cadquery.
materials``, FORGE-234's own density/elastic-modulus table -- reused via
its existing ``resolve_density_kg_m3``/``resolve_elastic_properties``
lookups, not a second material table). Records the ranking as Evidence,
``valid_against`` the work product (same staleness-pinning convention
``metric_evaluator.py`` established, FORGE-314).

``wall_thickness_mm`` has no home anywhere in the recorded twin data today
(the real arm's own geometry is a solid-beam model, not a hollow tube) --
the caller must supply it explicitly, same "resolve before construct,
never guess" discipline every recorder in this codebase already follows.
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
)
from twin_core.prediction.sensitivity import (
    rank_deflection_sensitivity,
    rank_mass_sensitivity,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.sensitivity")

_METRICS = frozenset({"tip_deflection", "mass"})

# FORGE-317's own acceptance criterion names these two materials as the
# comparison set for the mass-margin ranking -- not every material in
# FORGE-234's table (a sensitivity ranking with 16 candidate materials is
# noise; the ticket asks "ranked by wall thickness and material", i.e.
# material as ONE more ranked axis, not "compare against everything").
_DEFAULT_CANDIDATE_MATERIALS = ("aluminum_6061", "steel", "titanium", "carbon_fiber")


def make_sensitivity_ranker(twin: Any, *, evidence_recorder: Any = None) -> Any:
    """Return an async ``rank(...)`` bound to a twin + (optional) evidence
    recorder."""

    async def rank(
        *,
        metric: str,
        work_product_id: str,
        wall_thickness_mm: float,
        project_id: str | None = None,
        material: str = "aluminum_6061",
        load_n: float | None = None,
        deflection_limit_mm: float | None = None,
        mass_limit_kg: float | None = None,
        candidate_materials: list[str] | None = None,
        delta_fraction: float = 0.1,
    ) -> dict[str, Any]:
        if metric not in _METRICS:
            raise ValueError(f"twin.rank_sensitivity: 'metric' must be one of {sorted(_METRICS)}")

        wp_id = UUID(work_product_id)
        wp = await twin.get_work_product(wp_id)
        if wp is None:
            raise ValueError(f"twin.rank_sensitivity: no work_product {work_product_id!r}")
        length_mm, width_mm, height_mm = bounding_box_extents_mm(wp.metadata)

        if metric == "tip_deflection":
            if load_n is None or deflection_limit_mm is None:
                raise ValueError(
                    "twin.rank_sensitivity: metric='tip_deflection' requires 'load_n' and "
                    "'deflection_limit_mm'"
                )
            youngs_modulus_mpa, _poissons_ratio = resolve_elastic_properties(material)
            ranking = rank_deflection_sensitivity(
                length_mm=length_mm,
                width_mm=width_mm,
                height_mm=height_mm,
                wall_thickness_mm=wall_thickness_mm,
                load_n=load_n,
                youngs_modulus_mpa=youngs_modulus_mpa,
                limit_mm=deflection_limit_mm,
                delta_fraction=delta_fraction,
            )
        else:
            if mass_limit_kg is None:
                raise ValueError("twin.rank_sensitivity: metric='mass' requires 'mass_limit_kg'")
            density_kg_m3 = resolve_density_kg_m3(material)
            candidates = candidate_materials or list(_DEFAULT_CANDIDATE_MATERIALS)
            candidate_densities = {name: resolve_density_kg_m3(name) for name in candidates}
            ranking = rank_mass_sensitivity(
                length_mm=length_mm,
                width_mm=width_mm,
                height_mm=height_mm,
                wall_thickness_mm=wall_thickness_mm,
                density_kg_m3=density_kg_m3,
                limit_kg=mass_limit_kg,
                material_name=material,
                candidate_material_densities=candidate_densities,
                delta_fraction=delta_fraction,
            )

        out = ranking.model_dump()

        if evidence_recorder is not None:
            ev = await evidence_recorder(
                evidence_type="calculation",
                producer={"tool": "twin_core.prediction.sensitivity"},
                inputs={
                    "work_product_id": work_product_id,
                    "metric": metric,
                    "wall_thickness_mm": wall_thickness_mm,
                    "material": material,
                    "length_mm": length_mm,
                    "width_mm": width_mm,
                    "height_mm": height_mm,
                },
                result=out,
                statement=(
                    f"sensitivity ranking for {metric}: "
                    f"{', '.join(e['parameter'] for e in out['rankings'])}"
                ),
                valid_against=[{"ref": work_product_id, "entity_kind": "work_product"}],
                project_id=project_id,
            )
            out["evidence_node_id"] = ev["node_id"]

        logger.info(
            "sensitivity_ranked",
            work_product_id=work_product_id,
            metric=metric,
            top_parameter=out["rankings"][0]["parameter"] if out["rankings"] else None,
        )
        return out

    return rank
