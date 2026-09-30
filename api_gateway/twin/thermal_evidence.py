"""Thermal analysis evidence recorder (FORGE-297, gap G-I1).

Wires a real ``calculix.run_thermal`` call (FORGE-282) to a real Evidence
entity recorded against a work product -- the same "run a real tool, record
its real structured output as graph-checkable Evidence" pattern
``api_gateway/twin/metric_evaluator.py`` established for tip_deflection,
generalized to a second analysis type. This is what closes the gap
FORGE-297 found: FORGE-281/282/283 added real new CalculiX analysis types,
but none of them fed the requirements matrix/coverage numbers -- calling
them produced a real result that never became a graph-checkable fact.

Deliberately single-tier (unlike ``metric_evaluator.py``'s tier-0-hand-calc-
then-escalate-to-tier-2-FEA ladder): thermal analysis has no equivalent
cheap closed-form estimate to gate on before running the real solver.
FORGE-282's ``cross_check_thermal_steady_state`` is a hand-calc, but it is
offered here as an optional cross-check attached to the recorded evidence,
not a gating tier a caller pays for only sometimes -- every call runs the
real ``calculix.run_thermal`` solver and records its real output.

Wiring the remaining two new analysis types (FORGE-281 modal, FORGE-283
joint-loads) into this same evidence-recording pattern is explicitly
deferred to their own future tickets -- this module demonstrates the
pattern for one (thermal), not all three, matching FORGE-297's own honest
scope note.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


def make_thermal_evidence_recorder(
    twin: Any,
    *,
    evidence_recorder: Any,
    mcp_bridge: Any,
) -> Any:
    """Return an async ``evaluate_thermal(...)`` bound to a twin + evidence
    recorder + mcp_bridge (real ``calculix.run_thermal``/
    ``calculix.cross_check_thermal_steady_state`` calls)."""

    async def evaluate_thermal(
        *,
        work_product_id: str,
        project_id: str | None = None,
        mesh_file: str,
        material: dict[str, Any],
        heat_source_node_set: str,
        power_dissipation_w: float,
        sink_node_set: str,
        sink_temp_c: float,
        rated_max_temp_c: float | None = None,
        cross_check: dict[str, Any] | None = None,
        supersedes: str | None = None,
    ) -> dict[str, Any]:
        wp_id = UUID(work_product_id)
        wp = await twin.get_work_product(wp_id)
        if wp is None:
            raise ValueError(f"twin.evaluate_thermal_metric: no work_product {work_product_id!r}")

        thermal_args = {
            "mesh_file": mesh_file,
            "material": material,
            "heat_source_node_set": heat_source_node_set,
            "power_dissipation_w": power_dissipation_w,
            "sink_node_set": sink_node_set,
            "sink_temp_c": sink_temp_c,
        }
        fea_result = await mcp_bridge.invoke("calculix.run_thermal", thermal_args)

        out: dict[str, Any] = {"result": fea_result}
        peak_temp_c = fea_result.get("max_temperature_c")

        if cross_check is not None:
            cross_check_args = {
                **cross_check,
                "power_dissipation_w": power_dissipation_w,
                "sink_temp_c": sink_temp_c,
            }
            if peak_temp_c is not None:
                cross_check_args["fea_peak_temp_c"] = peak_temp_c
            cross_check_result = await mcp_bridge.invoke(
                "calculix.cross_check_thermal_steady_state", cross_check_args
            )
            out["cross_check"] = cross_check_result

        if rated_max_temp_c is not None and peak_temp_c is not None:
            out["rated_max_temp_c"] = rated_max_temp_c
            out["within_rating"] = peak_temp_c <= rated_max_temp_c

        replay_args = {
            "work_product_id": work_product_id,
            "project_id": project_id,
            "mesh_file": mesh_file,
            "material": material,
            "heat_source_node_set": heat_source_node_set,
            "power_dissipation_w": power_dissipation_w,
            "sink_node_set": sink_node_set,
            "sink_temp_c": sink_temp_c,
            "rated_max_temp_c": rated_max_temp_c,
        }

        statement = (
            f"steady-state thermal analysis: peak {peak_temp_c:.4g}C"
            if peak_temp_c is not None
            else "steady-state thermal analysis"
        )

        ev = await evidence_recorder(
            evidence_type="simulation",
            producer={"tool": "calculix.run_thermal"},
            inputs=thermal_args,
            result={"tier": 1, "metric": "peak_temperature_c", **out},
            statement=statement,
            valid_against=[{"ref": work_product_id, "entity_kind": "work_product"}],
            supersedes=supersedes,
            replay={"tool_id": "twin.evaluate_thermal_metric", "args": replay_args},
            project_id=project_id,
        )
        out["evidence_node_id"] = ev["node_id"]
        out["peak_temperature_c"] = peak_temp_c
        return out

    return evaluate_thermal
