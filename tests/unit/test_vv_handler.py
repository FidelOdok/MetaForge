"""Honest V&V handler: earned verdict + test_plan, no false pass (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from api_gateway.runs.vv_handlers import (
    GoalDrivenVVHandler,
    _normalize_vv_spec,
    vv_test_plan_md,
)
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.spec import get_flow

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))
from vv_rubric import evaluate_vv, vv_score  # noqa: E402


def test_normalize_defaults_and_verdict() -> None:
    s = _normalize_vv_spec({}, "an I2C IMU breakout board")
    assert s["checks"] and s["deferred"]
    assert s["verdict"] in ("PASS", "PARTIAL")
    # a failing check flips the verdict to PARTIAL
    s2 = _normalize_vv_spec(
        {"constraint_checks": [{"name": "size", "requirement": "25mm", "status": "review"}]},
        "board",
    )
    assert s2["verdict"] == "PARTIAL"


def test_test_plan_has_matrix_and_deferral() -> None:
    s = _normalize_vv_spec(
        {
            "constraint_checks": [
                {
                    "name": "envelope",
                    "requirement": "25 x 20 mm",
                    "result": "24 x 18 mm",
                    "status": "pass",
                }
            ],
            "deferred_analyses": ["ERC", "FEA"],
        },
        "board",
    )
    md = vv_test_plan_md(s)
    assert "| envelope |" in md and "deferred to phase 2" in md.lower()


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        return {"status": "ok", "data": {}}


@pytest.mark.asyncio
async def test_handler_output_is_earned_and_not_a_false_pass() -> None:
    async def doc_recorder(**kwargs):
        return {"node_id": "tp1"}

    async def fake_extract(goal, prior, *, provider, model):
        return _normalize_vv_spec(
            {
                "constraint_checks": [
                    {
                        "name": "board envelope",
                        "requirement": "25 x 20 mm",
                        "result": "24 x 18 mm",
                        "status": "pass",
                    },
                    {
                        "name": "power budget",
                        "requirement": "100 mW limit",
                        "result": "90 mW",
                        "status": "pass",
                    },
                ],
                "deferred_analyses": ["ERC", "DRC", "FEA"],
            },
            goal,
        )

    bridge = _Bridge()
    handler = GoalDrivenVVHandler(bridge, doc_recorder, extract=fake_extract)
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "simulation")
    ctx = FlowContext(goal="an I2C IMU breakout board", project_id="p1", completed=[])

    outcome = await handler.run_phase(goal=ctx.goal, phase=phase, context=ctx)

    assert outcome.status == "completed"
    assert any(a.startswith("test_plan:") for a in outcome.artifacts)
    # the recorded verdict must score well AND not be a false pass
    rationale = bridge.calls[0][1]["rationale"]
    checks = evaluate_vv(decision_text=rationale, artifact_types={"test_plan"}, goal=ctx.goal)
    assert checks["not_false_pass"], "honest deferral must not read as a false pass"
    assert checks["verdict_recorded"] and checks["quantified_result"]
    assert checks["real_check_artifact"] and checks["traces_to_requirement"]
    assert vv_score(checks) == 1.0, checks
