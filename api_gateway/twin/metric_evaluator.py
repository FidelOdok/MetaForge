"""Tiered-evaluator orchestration for twin.evaluate_metric (FORGE-315).

Wires ``twin_core.prediction.evaluator``'s pure tier-0 math to real graph
data (a CAD WorkProduct's own recorded bounding box, MET-630) and, on
escalation, to a real tier-2 ``calculix.run_fea`` call through an injected
McpBridge -- the same gateway-layer cross-tool-invocation pattern
``api_gateway/twin/regenerate_geometry.py`` already uses in production
(``bridge.invoke(...)``), not a new mechanism. See ``api_gateway/server.py``
for how the bridge is threaded in (the same lazy-bridge-binding seam
``_LazyBridgeMeasure``/``_LazyBridgeAssemblyInfo`` already use, generalized
to a plain ``invoke`` passthrough).

Tier-2 escalation requires the caller to supply an already-generated mesh
(``mesh_file``) plus the node sets/material/load a real ``calculix.run_fea``
call needs. This module does NOT attempt to autonomously derive those --
picking the right mesh element sets for a part it's never seen a load case
for is a real, currently-unsolved gap in this codebase (confirmed during
FORGE-315's own scoping: FORGE-278/239/277 together get a human to
point-and-click node sets into a reusable LOAD_CASE work product, but there
is no programmatic path from "a design changed" to "correct boundary
conditions" for a genuinely novel part). When escalation triggers with no
``tier2`` args supplied, this returns a clear, honest "tier-2 not attempted"
result rather than guessing at node-set names -- the same "resolve before
construct, never silently guess" discipline every recorder in this package
already follows for ref resolution.

FORGE-316: every Evidence this module records also carries a ``replay``
payload (``{tool_id: "twin.evaluate_metric", args: {...}}``) -- literally
enough to call this same function again. ``twin.execute_revalidation_plan``
uses this to automatically re-run exactly the checks a committed change
actually marked stale, rather than requiring a human to redo them by hand.
An optional ``supersedes`` (the id of the stale tier-0 evidence being
replayed) threads through to ``evidence_recorder.py``'s own FORGE-65
revalidation flow -- a rerun is a new evidence entity, never a mutation of
the old one.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from twin_core.prediction.evaluator import evaluate_tip_deflection_tier0

logger = structlog.get_logger(__name__)


def bounding_box_extents_mm(metadata: dict[str, Any]) -> tuple[float, float, float]:
    """(length, width, height) mm, sorted descending -- ``length`` is
    treated as the beam's own axis (the longest bounding-box extent, the
    cantilever-arm-link case FORGE-315's acceptance criterion targets).
    Public (FORGE-317): also used by ``api_gateway/twin/sensitivity.py``
    for the same geometry read, rather than a second implementation.
    Reads the exact shape ``geometry_recorder.py``/MET-630 already writes:
    ``metadata["geometry_features"]["properties"]["bounding_box"]`` ==
    ``{min_x, min_y, min_z, max_x, max_y, max_z}``.

    The two cross-section extents are ALSO sorted descending (the larger
    becomes ``width``, the smaller ``height``), deliberately, not just as a
    side effect of one ``sorted()`` call: ``I = width * height**3 / 12``, so
    assigning the smaller extent to ``height`` minimizes I and therefore
    MAXIMIZES the predicted deflection -- the conservative (worst-case)
    orientation when the real load axis relative to the cross-section isn't
    known. A caller that knows the true orientation should compute its own
    tier-0 estimate directly via ``twin_core.prediction.evaluator`` instead.
    """
    features = metadata.get("geometry_features") or {}
    bbox = (features.get("properties") or {}).get("bounding_box")
    if not bbox:
        raise ValueError(
            "twin.evaluate_metric: this work product has no recorded "
            "geometry_features.properties.bounding_box (MET-630) -- cannot "
            "compute a tier-0 estimate without real geometry dimensions"
        )
    extents = sorted(
        (
            bbox["max_x"] - bbox["min_x"],
            bbox["max_y"] - bbox["min_y"],
            bbox["max_z"] - bbox["min_z"],
        ),
        reverse=True,
    )
    return extents[0], extents[1], extents[2]


def make_metric_evaluator(
    twin: Any, *, evidence_recorder: Any = None, mcp_bridge: Any = None
) -> Any:
    """Return an async ``evaluate_tip_deflection(...)`` bound to a twin +
    (optional) evidence recorder + (optional) mcp_bridge for tier-2
    escalation."""

    async def evaluate_tip_deflection(
        *,
        work_product_id: str,
        project_id: str | None = None,
        load_n: float,
        youngs_modulus_mpa: float,
        limit_mm: float | None = None,
        band_fraction: float = 0.2,
        escalation_k: float = 1.0,
        tier2: dict[str, Any] | None = None,
        supersedes: str | None = None,
    ) -> dict[str, Any]:
        wp_id = UUID(work_product_id)
        wp = await twin.get_work_product(wp_id)
        if wp is None:
            raise ValueError(f"twin.evaluate_metric: no work_product {work_product_id!r}")
        length_mm, width_mm, height_mm = bounding_box_extents_mm(wp.metadata)

        tier0 = evaluate_tip_deflection_tier0(
            length_mm=length_mm,
            width_mm=width_mm,
            height_mm=height_mm,
            load_n=load_n,
            youngs_modulus_mpa=youngs_modulus_mpa,
            limit_mm=limit_mm,
            band_fraction=band_fraction,
            escalation_k=escalation_k,
        )

        out: dict[str, Any] = {
            "metric": tier0.metric,
            "tier": tier0.tier,
            "value_mm": tier0.value_mm,
            "band_mm": tier0.band_mm,
            "limit_mm": tier0.limit_mm,
            "margin_mm": tier0.margin_mm,
            "escalated": tier0.escalate,
        }

        # FORGE-316: the exact kwargs needed to call this same tool again --
        # stored on the resulting Evidence so an automatic revalidation
        # (twin.execute_revalidation_plan) can literally re-run this check
        # rather than only knowing it's stale. Deliberately excludes
        # 'tier2' (a fresh mesh/node-set reference goes stale itself; a
        # replay re-derives tier0 fresh and only re-escalates if the new
        # margin still calls for it) and 'supersedes' (set fresh per call).
        replay_args = {
            "work_product_id": work_product_id,
            "project_id": project_id,
            "load_n": load_n,
            "youngs_modulus_mpa": youngs_modulus_mpa,
            "limit_mm": limit_mm,
            "band_fraction": band_fraction,
            "escalation_k": escalation_k,
        }

        if evidence_recorder is not None:
            ev = await evidence_recorder(
                evidence_type="calculation",
                producer={"tool": "twin_core.prediction.evaluator.cantilever_tip_deflection_mm"},
                inputs={
                    "work_product_id": work_product_id,
                    "length_mm": length_mm,
                    "width_mm": width_mm,
                    "height_mm": height_mm,
                    "load_n": load_n,
                    "youngs_modulus_mpa": youngs_modulus_mpa,
                },
                result=out,
                statement=(
                    f"tier-0 cantilever beam estimate: {tier0.metric}={tier0.value_mm:.4g}mm"
                ),
                valid_against=[{"ref": work_product_id, "entity_kind": "work_product"}],
                supersedes=supersedes,
                replay={"tool_id": "twin.evaluate_metric", "args": replay_args},
                project_id=project_id,
            )
            out["evidence_node_id"] = ev["node_id"]

        if not tier0.escalate:
            logger.info(
                "evaluator_tier0_within_band",
                work_product_id=work_product_id,
                value_mm=tier0.value_mm,
                band_mm=tier0.band_mm,
                margin_mm=tier0.margin_mm,
            )
            return out

        logger.info(
            "evaluator_escalating_to_tier2",
            work_product_id=work_product_id,
            margin_mm=tier0.margin_mm,
            band_mm=tier0.band_mm,
        )

        if not tier2 or mcp_bridge is None:
            out["tier2"] = {
                "attempted": False,
                "reason": (
                    "escalation triggered (|margin| < k*band) but no tier-2 FEA "
                    "inputs were supplied (need 'tier2': {mesh_file, "
                    "fixed_node_set, load_node_set, material, load_force_n}), "
                    "or no mcp_bridge is wired -- tier-0 result recorded as "
                    "Evidence, tier-2 not attempted. Generate a mesh for this "
                    "part (freecad.generate_mesh) and pass its node sets to "
                    "run tier-2."
                ),
            }
            return out

        fea_args = {
            "mesh_file": tier2["mesh_file"],
            "load_case": tier2.get("load_case", f"{tier0.metric}_tier2_escalation"),
            "analysis_type": "static_stress",
            "material": tier2["material"],
            "fixed_node_set": tier2["fixed_node_set"],
            "load_node_set": tier2["load_node_set"],
            "load_force_n": tier2["load_force_n"],
        }
        try:
            fea_result = await mcp_bridge.invoke("calculix.run_fea", fea_args)
        except Exception as exc:  # noqa: BLE001 -- a tier-2 failure must not lose the tier-0 result
            logger.warning(
                "evaluator_tier2_fea_failed", work_product_id=work_product_id, error=str(exc)
            )
            out["tier2"] = {"attempted": True, "error": str(exc)}
            return out

        out["tier2"] = {"attempted": True, "result": fea_result}

        if evidence_recorder is not None:
            ev2 = await evidence_recorder(
                evidence_type="simulation",
                producer={"tool": "calculix.run_fea"},
                inputs=fea_args,
                result=fea_result,
                statement=(
                    f"tier-2 FEA escalation for {tier0.metric} (tier-0 margin "
                    f"{tier0.margin_mm:.4g}mm < band {tier0.band_mm:.4g}mm)"
                ),
                valid_against=[{"ref": work_product_id, "entity_kind": "work_product"}],
                # A tier-2 replay re-invokes the SAME tool with the SAME
                # tier-0 args, not a fixed replay of this exact FEA call --
                # it re-derives whether tier-2 is still needed fresh (this
                # module never re-uses a mesh_file/node-set reference that
                # may itself be stale). No 'supersedes' threaded here: a
                # revalidation replay supersedes the tier-0 evidence (the
                # thing that was actually pinned as a dependency), not this
                # tier-2 sub-record.
                replay={"tool_id": "twin.evaluate_metric", "args": replay_args},
                project_id=project_id,
            )
            out["tier2"]["evidence_node_id"] = ev2["node_id"]

        return out

    return evaluate_tip_deflection
