"""Analysis constraints vs the latest simulation_result (FORGE-498).

Uses the live shelf-run numbers: simulation_result acd578d0 recorded
max_displacement_mm 12.07 at the 490 N factored load (service load 245 N) and
verdict pass, against front_edge_service_deflection <= 5 mm.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api_gateway.runs.analysis_constraints import SimResult, check_analysis_constraints
from api_gateway.runs.gate_eval import TwinConstraintChecker
from orchestrator.design_flow.executor import ConstraintReport, _constraint_details
from twin_core.constraint_engine.models import ConstraintEvaluationResult
from twin_core.models.enums import EdgeType

LIVE = {
    "max_displacement_mm": 12.07,
    "max_von_mises_mpa": 8.19,
    "factored_load_n": 490,
    "service_load_n": 245,
    "computed_petg_sf_at_factored_load": 6.11,
    "verdict": "pass",
}


def _c(
    metric: str, limit: float, op: str = "<=", unit: str = "mm", **kw: object
) -> SimpleNamespace:
    return SimpleNamespace(
        name=kw.pop("name", metric), metric=metric, limit=limit, operator=op, unit=unit, **kw
    )


def _sim(meta: dict, cad_id: str = "cad1", ts: float = 1.0, name: str = "fea") -> SimResult:
    return SimResult(
        id=f"sim-{name}-{ts}", name=name, updated_at=ts, metadata=meta, cad_ids={cad_id}
    )


MODELS = [("cad1", "shelf")]


def test_live_service_deflection_is_violated_with_scaling_stated() -> None:
    c = _c("front_edge_service_deflection", 5)
    out = check_analysis_constraints([c], MODELS, [_sim(LIVE)])
    assert len(out.violations) == 1
    msg = out.violations[0]
    assert "6.03 mm" in msg or "6.04 mm" in msg
    assert "requirement is <= 5 mm" in msg
    assert "recorded 12.07" in msg and "scaled linearly from 490 N" in msg
    assert "service load 245 N" in msg
    assert not out.satisfied and out.evaluated == 1


def test_service_deflection_within_limit_passes() -> None:
    out = check_analysis_constraints([_c("service_deflection", 7)], MODELS, [_sim(LIVE)])
    assert not out.violations and len(out.satisfied) == 1


def test_unknown_load_basis_is_not_evaluated_never_passed() -> None:
    out = check_analysis_constraints([_c("max_deflection", 20)], MODELS, [_sim(LIVE)])
    assert not out.violations and not out.satisfied
    assert len(out.not_evaluated) == 1 and "load basis unknown" in out.not_evaluated[0]


def test_service_limit_without_recorded_loads_is_not_evaluated() -> None:
    meta = {"max_displacement_mm": 1.0}
    out = check_analysis_constraints([_c("service_deflection", 5)], MODELS, [_sim(meta)])
    assert not out.satisfied and "cannot be scaled" in out.not_evaluated[0]


def test_single_load_result_compared_as_recorded() -> None:
    meta = {"max_von_mises_mpa": 8.19}
    out = check_analysis_constraints([_c("max_stress", 5, unit="MPa")], MODELS, [_sim(meta)])
    assert len(out.violations) == 1 and "8.19 MPa" in out.violations[0]


def test_stress_scales_and_unit_converts() -> None:
    # 8.19 MPa at 490 N -> 4.095 MPa at 245 N; limit 5000 kPa = 5 MPa -> passes.
    c = _c("service_von_mises_stress", 5000, unit="kPa")
    out = check_analysis_constraints([c], MODELS, [_sim(LIVE)])
    assert not out.violations and len(out.satisfied) == 1


def test_safety_factor_scales_inversely() -> None:
    # SF 6.11 at 490 N is 3.055 at 245 N... but a service SF limit scales DOWN the
    # other way: capacity is fixed, so SF at the smaller service load is larger.
    c = _c("service_safety_factor", 10, op=">=", unit="")
    out = check_analysis_constraints([c], MODELS, [_sim(LIVE)])
    assert not out.violations  # 6.11 * 490 / 245 = 12.22 >= 10
    c2 = _c("service_safety_factor", 13, op=">=", unit="")
    out2 = check_analysis_constraints([c2], MODELS, [_sim(LIVE)])
    assert len(out2.violations) == 1 and "12.22" in out2.violations[0]


def test_factored_constraint_uses_factored_load_unscaled() -> None:
    c = _c("factored_deflection", 10)
    out = check_analysis_constraints([c], MODELS, [_sim(LIVE)])
    assert len(out.violations) == 1 and "12.07 mm" in out.violations[0]


def test_no_linked_result_is_not_evaluated() -> None:
    out = check_analysis_constraints([_c("service_deflection", 5)], MODELS, [])
    assert "no simulation_result is linked" in out.not_evaluated[0]


def test_result_for_superseded_cad_model_is_not_used() -> None:
    old = _sim(LIVE, cad_id="old-cad")
    out = check_analysis_constraints([_c("service_deflection", 5)], MODELS, [old])
    assert not out.violations and out.not_evaluated


def test_latest_result_wins() -> None:
    stale = _sim(LIVE, ts=1.0, name="v1")
    fresh = _sim({**LIVE, "max_displacement_mm": 6.0}, ts=2.0, name="v2")
    out = check_analysis_constraints([_c("service_deflection", 5)], MODELS, [stale, fresh])
    assert not out.violations and "'v2'" in out.satisfied[0]


def test_result_without_the_value_is_not_evaluated() -> None:
    out = check_analysis_constraints(
        [_c("service_deflection", 5)], MODELS, [_sim({"service_load_n": 245})]
    )
    assert "records no deflection" in out.not_evaluated[0]


def test_modelling_assumptions_are_surfaced() -> None:
    meta = {**LIVE, "modelling_assumptions": ["board and arm fused as one bonded body"]}
    out = check_analysis_constraints([_c("service_deflection", 7)], MODELS, [_sim(meta)])
    assert out.assumptions == ["fea: board and arm fused as one bonded body"]
    report = ConstraintReport(checked=True, assumptions=out.assumptions)
    assert "Modelling assumptions: fea: board and arm fused" in _constraint_details(report)


def test_warning_severity_goes_to_warnings() -> None:
    sev = SimpleNamespace(value="warning")
    c = _c("service_deflection", 5, severity=sev)
    out = check_analysis_constraints([c], MODELS, [_sim(LIVE)])
    assert not out.violations and len(out.warnings) == 1


def test_non_analysis_constraints_are_ignored() -> None:
    out = check_analysis_constraints([_c("envelope_length", 5)], MODELS, [_sim(LIVE)])
    assert out.evaluated == 0 and not out.not_evaluated


# --- through the gate checker ------------------------------------------------


class _Twin:
    def __init__(self, constraints: list, nodes: dict, edges: dict) -> None:
        self._constraints, self._nodes, self._edges = constraints, nodes, edges

    async def evaluate_constraints(self, branch: str = "main") -> ConstraintEvaluationResult:
        return ConstraintEvaluationResult(passed=True, evaluated_count=0)

    async def list_constraints(self, project_id: object = None) -> list:
        return self._constraints

    async def get_work_product(self, wp_id: object) -> object:
        return self._nodes.get(str(wp_id))

    async def get_edges(self, node_id: object, direction: str, edge_type: EdgeType) -> list:
        assert edge_type is EdgeType.DERIVES_FROM
        return self._edges.get(str(node_id), [])


def _wp(wp_id: object, wp_type: str, name: str, ts: str = "2026-10-01T00:00:00+00:00") -> object:
    return SimpleNamespace(
        id=wp_id,
        type=SimpleNamespace(value=wp_type),
        name=name,
        updated_at=datetime.fromisoformat(ts),
    )


@pytest.mark.asyncio
async def test_gate_blocks_on_live_violation_via_derives_from_edge() -> None:
    cad, sim = uuid4(), uuid4()
    backend = SimpleNamespace()

    async def get_project(_pid: str) -> object:
        return SimpleNamespace(
            work_products=[_wp(cad, "cad_model", "shelf"), _wp(sim, "simulation_result", "fea")]
        )

    backend.get_project = get_project
    twin = _Twin(
        [_c("front_edge_service_deflection", 5)],
        {str(sim): SimpleNamespace(metadata=LIVE), str(cad): SimpleNamespace(metadata={})},
        {str(sim): [SimpleNamespace(target_id=cad)]},
    )
    report = await TwinConstraintChecker(twin, backend).check(str(uuid4()))
    assert report.checked and not report.passed
    assert "front_edge_service_deflection" in report.violations[0]
    assert report.evaluated_count == 1


@pytest.mark.asyncio
async def test_gate_links_via_source_cad_model_id_metadata() -> None:
    cad, sim = uuid4(), uuid4()

    async def get_project(_pid: str) -> object:
        return SimpleNamespace(
            work_products=[_wp(cad, "cad_model", "shelf"), _wp(sim, "simulation_result", "fea")]
        )

    twin = _Twin(
        [_c("service_deflection", 5)],
        {str(sim): SimpleNamespace(metadata={**LIVE, "source_cad_model_id": str(cad)})},
        {},
    )
    backend = SimpleNamespace(get_project=get_project)
    report = await TwinConstraintChecker(twin, backend).check(str(uuid4()))
    assert not report.passed


@pytest.mark.asyncio
async def test_gate_reports_not_evaluated_without_blocking() -> None:
    cad = uuid4()

    async def get_project(_pid: str) -> object:
        return SimpleNamespace(work_products=[_wp(cad, "cad_model", "shelf")])

    twin = _Twin([_c("service_deflection", 5)], {}, {})
    report = await TwinConstraintChecker(twin, SimpleNamespace(get_project=get_project)).check(
        str(uuid4())
    )
    assert report.passed and report.not_evaluated
    assert "Not evaluated (1)" in _constraint_details(report)
