"""Honest manufacturing handler: real package + readiness, no false ready (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from api_gateway.runs.mfg_handlers import (
    GoalDrivenManufacturingHandler,
    _normalize_mfg_spec,
    mfg_package_md,
)
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.spec import get_flow

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))
from manufacturing_rubric import evaluate_manufacturing, manufacturing_score  # noqa: E402


def test_normalize_fills_defaults_and_coerces() -> None:
    s = _normalize_mfg_spec({}, "an I2C IMU breakout board")
    assert s["layers"] == 2 and s["processes"] and s["assembly"] and s["deferred"]
    assert s["name"]
    s2 = _normalize_mfg_spec({"pcb": {"layers": "4"}, "processes": []}, "board")
    assert s2["layers"] == 4 and s2["processes"]  # bad/empty -> defaults


def test_package_md_has_fab_assembly_and_deferral() -> None:
    s = _normalize_mfg_spec({"pcb": {"layers": 2, "size": "25 x 20 mm"}}, "board")
    md = mfg_package_md(s).lower()
    assert "fabrication" in md and "assembly" in md and "deferred to phase 2" in md


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        return {"status": "ok", "data": {}}


@pytest.mark.asyncio
async def test_handler_output_is_earned_and_not_false_ready() -> None:
    async def doc_recorder(**kwargs):
        return {"node_id": "mf1"}

    async def fake_extract(goal, prior, *, provider, model):
        return _normalize_mfg_spec({"pcb": {"layers": 2, "size": "25 x 20 mm"}}, goal)

    bridge = _Bridge()
    handler = GoalDrivenManufacturingHandler(bridge, doc_recorder, extract=fake_extract)
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "manufacturing")
    ctx = FlowContext(goal="an I2C IMU breakout board", project_id="p1", completed=[])

    outcome = await handler.run_phase(goal=ctx.goal, phase=phase, context=ctx)

    assert outcome.status == "completed"
    assert any(a.startswith("manufacturing_file:") for a in outcome.artifacts)
    rationale = bridge.calls[0][1]["rationale"]
    checks = evaluate_manufacturing(
        decision_text=rationale, artifact_types={"bom", "manufacturing_file"}, goal=ctx.goal
    )
    assert checks["not_false_ready"], "honest deferral must not read as false readiness"
    assert checks["mfg_artifact_present"] and checks["assembly_or_bringup_noted"]
    assert manufacturing_score(checks) == 1.0, checks
