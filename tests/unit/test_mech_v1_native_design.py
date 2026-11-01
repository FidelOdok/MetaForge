"""mech_v1 design phase: native brain first, scripted backstop, honest gate (FORGE-496).

Live evidence: run_7e54c80824f5403d committed a 800 x 250 x 25 mm 'pine wood' box
for a flow whose stock was 18 mm birch plywood, and G6 reported 0 violations.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from api_gateway.runs.geometry_constraints import check_geometry_constraints
from api_gateway.runs.mech_handlers import (
    GoalDrivenMechanicalHandler,
    NativeMechanicalDesignHandler,
    _normalize_spec,
)
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import get_flow
from twin_core.constraint_engine.models import ConstraintEvaluationResult

FLOW_CONTEXT = "18 mm birch plywood offcut 800 x 300, PETG, X1C 256 mm envelope"


def _constraint(metric: str, limit: float | None, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(
        name=kw.pop("name", metric),
        metric=metric,
        operator=kw.pop("operator", "<="),
        limit=limit,
        unit=kw.pop("unit", "mm"),
        severity=SimpleNamespace(value=kw.pop("severity", "error")),
        metadata=kw.pop("metadata", {}),
        acceptance_criteria=kw.pop("acceptance_criteria", ""),
        message=kw.pop("message", ""),
    )


STOCK_CONSTRAINTS = [
    _constraint("envelope_length", 800),
    _constraint("envelope_width", 300),
    _constraint("stock_thickness", 18),
    _constraint("material", None, acceptance_criteria="18 mm birch plywood"),
]

LIVE_MODEL = {"dimensions_mm": {"x": 800, "y": 250, "z": 25}, "material": "pine wood"}
GOOD_MODEL = {"dimensions_mm": {"x": 800, "y": 300, "z": 18}, "material": "Birch plywood 18 mm"}


# --- the gate comparison ---------------------------------------------------


def test_live_example_geometry_violates_thickness_and_material() -> None:
    out = check_geometry_constraints(STOCK_CONSTRAINTS, [("Shelf", LIVE_MODEL)])
    assert any("stock_thickness" in v and "25" in v for v in out.violations)
    assert any("material" in v and "pine wood" in v for v in out.violations)
    assert not any("envelope" in v for v in out.violations)  # 800 x 250 fits 800 x 300


def test_design_within_stock_passes() -> None:
    out = check_geometry_constraints(STOCK_CONSTRAINTS, [("Shelf", GOOD_MODEL)])
    assert out.violations == []
    assert out.evaluated == 4


def test_envelope_overrun_and_missing_material_are_violations() -> None:
    meta = {"bbox_mm": {"min": [0, 0, 0], "max": [900, 300, 18]}}
    out = check_geometry_constraints(STOCK_CONSTRAINTS, [("Shelf", meta)])
    assert any("envelope_length" in v for v in out.violations)
    assert any("records no material" in v for v in out.violations)


def test_printed_part_limit_is_not_applied_to_the_whole_bounding_box() -> None:
    out = check_geometry_constraints(
        [_constraint("printed_part_max_dimension", 256)], [("Shelf", GOOD_MODEL)]
    )
    assert out.violations == [] and out.evaluated == 0


def test_no_cad_model_is_not_evaluated_not_passing() -> None:
    out = check_geometry_constraints(STOCK_CONSTRAINTS, [])
    assert out.evaluated == 0 and len(out.not_evaluated) == 4


def test_assembly_thickness_is_checked_per_part_or_skipped() -> None:
    parts = [
        {"name": "Board", "dimensions_mm": {"x": 800, "y": 300, "z": 18}},
        {"name": "Cleat", "dimensions_mm": {"x": 700, "y": 40, "z": 30}},
    ]
    meta = {"assembly": {"parts": parts}, "dimensions_mm": {"x": 800, "y": 300, "z": 60}}
    out = check_geometry_constraints([_constraint("stock_thickness", 18)], [("A", meta)])
    assert len(out.violations) == 1 and "Cleat" in out.violations[0]
    bare = {"assembly": {"parts": [{"name": "a"}, {"name": "b"}]}, **GOOD_MODEL}
    skipped = check_geometry_constraints([_constraint("stock_thickness", 18)], [("A", bare)])
    assert skipped.violations == [] and skipped.not_evaluated


@pytest.mark.asyncio
async def test_gate_checker_fails_the_live_design() -> None:
    from api_gateway.runs.gate_eval import TwinConstraintChecker

    wp_id = uuid4()

    class Twin:
        async def evaluate_constraints(self, branch: str = "main") -> ConstraintEvaluationResult:
            return ConstraintEvaluationResult(passed=True, evaluated_count=164)

        async def list_constraints(self, project_id: Any = None) -> list[Any]:
            return STOCK_CONSTRAINTS

        async def get_work_product(self, wp: Any) -> Any:
            return SimpleNamespace(metadata=LIVE_MODEL)

    class Backend:
        async def get_project(self, project_id: str) -> Any:
            wp = SimpleNamespace(
                id=wp_id,
                name="Shelf",
                type=SimpleNamespace(value="cad_model"),
                updated_at=datetime.now(UTC),
            )
            return SimpleNamespace(work_products=[wp])

    report = await TwinConstraintChecker(Twin(), Backend()).check(str(uuid4()))
    assert report.checked and not report.passed
    assert report.evaluated_count > 164
    assert any("stock_thickness" in v for v in report.violations)
    assert any("material" in v for v in report.violations)


# --- mech_v1 routing -------------------------------------------------------


class _Native:
    def __init__(self) -> None:
        self.goal = ""

    async def run_phase(self, *, goal: str, phase: Any, context: Any) -> PhaseOutcome:
        self.goal = goal
        return PhaseOutcome(summary="native design done")


class _Fallback(GoalDrivenMechanicalHandler):
    def __init__(self) -> None:
        self.ran_with: FlowContext | None = None

    async def run_phase(self, *, goal: str, phase: Any, context: Any) -> PhaseOutcome:
        self.ran_with = context
        return PhaseOutcome(summary="single box", artifacts=["cad_model:n1"])


def _probe(result: bool) -> Any:
    async def probe(project_id: str | None, since_ts: float) -> bool:
        return result

    return probe


_PHASE = get_flow("mech_v1").phases[3]
_CTX = FlowContext(goal="shelf", project_id="p1", flow_context=FLOW_CONTEXT)


@pytest.mark.asyncio
async def test_native_design_is_used_when_it_commits_a_cad_model() -> None:
    native, fallback = _Native(), _Fallback()
    handler = NativeMechanicalDesignHandler(native, fallback, _probe(True))
    out = await handler.run_phase(goal="a shelf", phase=_PHASE, context=_CTX)
    assert out.summary == "native design done"
    assert fallback.ran_with is None
    assert "extra_metadata" in native.goal and "meaningful name" in native.goal


@pytest.mark.asyncio
async def test_backstop_runs_with_the_context_and_is_marked_as_fallback() -> None:
    native, fallback = _Native(), _Fallback()
    handler = NativeMechanicalDesignHandler(native, fallback, _probe(False))
    out = await handler.run_phase(goal="a shelf", phase=_PHASE, context=_CTX)
    assert out.summary.startswith("FALLBACK:")
    assert out.artifacts == ["cad_model:n1"]
    assert fallback.ran_with is _CTX and fallback.ran_with.flow_context == FLOW_CONTEXT


@pytest.mark.asyncio
async def test_native_failure_also_reaches_the_backstop() -> None:
    class Boom:
        async def run_phase(self, **kw: Any) -> PhaseOutcome:
            raise RuntimeError("model exploded")

    fallback = _Fallback()
    out = await NativeMechanicalDesignHandler(Boom(), fallback, _probe(True)).run_phase(
        goal="g", phase=_PHASE, context=_CTX
    )
    assert out.summary.startswith("FALLBACK:") and "model exploded" in out.summary


class _Bridge:
    async def invoke(self, tool: str, args: dict) -> dict:
        return {
            "freecad.open_session": {"status": "ok", "data": {"session_id": "s1"}},
            "freecad.create_primitive": {"status": "ok", "data": {"obj_id": "o1"}},
            "freecad.export_model": {"status": "ok", "data": {"step_base64": "U1RFUA=="}},
            "twin.record_decision": {"status": "ok", "data": {"id": "d1"}},
        }[tool]


@pytest.mark.asyncio
async def test_goal_driven_handler_gets_context_and_constraints_and_retries_a_bad_spec() -> None:
    prompts: list[str] = []
    recorded: dict = {}

    async def recorder(**kwargs: Any) -> dict:
        recorded.update(kwargs)
        return {"node_id": "n1"}

    async def extract(goal: str, prior: str, *, provider: Any, model: Any) -> dict:
        prompts.append(prior)
        if len(prompts) == 1:  # the live failure: 25 mm pine
            return _normalize_spec(
                {
                    "name": "Shelf",
                    "parameters": {"length": 800, "width": 250, "height": 25},
                    "material": "pine wood",
                },
                goal,
            )
        return _normalize_spec(
            {
                "name": "Shelf",
                "parameters": {"length": 800, "width": 300, "height": 18},
                "material": "birch plywood",
            },
            goal,
        )

    async def load(project_id: str | None) -> list[Any]:
        return STOCK_CONSTRAINTS

    handler = GoalDrivenMechanicalHandler(
        _Bridge(), recorder, extract=extract, constraints_loader=load
    )
    await handler.run_phase(goal="a shelf", phase=_PHASE, context=_CTX)

    assert FLOW_CONTEXT in prompts[0] and "stock_thickness" in prompts[0]
    assert "previous spec broke" in prompts[1]
    assert recorded["extra_metadata"]["material"] == "birch plywood"
    assert recorded["extra_metadata"]["dimensions_mm"] == {"x": 800.0, "y": 300.0, "z": 18.0}


@pytest.mark.asyncio
async def test_build_phase_brain_routes_only_mech_v1_design_to_the_native_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_gateway.runs import routes

    monkeypatch.setattr("api_gateway.chat.routes.get_mcp_bridge", lambda: _Bridge())
    monkeypatch.setattr("api_gateway.runs.routes.ensure_usage_store", lambda: None)
    monkeypatch.setattr("api_gateway.projects.routes.get_project_backend", lambda: object())
    monkeypatch.setattr("api_gateway.twin.routes.get_twin", lambda: object())

    mech = await routes.build_phase_brain("r1", "mech_v1")
    assert isinstance(mech._handlers["design"], NativeMechanicalDesignHandler)
    assert set(mech._handlers) == {"design"}

    hw = await routes.build_phase_brain("r1", "hardware_v1")
    assert type(hw._handlers["design"]) is GoalDrivenMechanicalHandler
    d1 = await routes.build_phase_brain("r1", "design_v1")
    assert type(d1._handlers["design"]).__name__ == "MechanicalDesignHandler"
