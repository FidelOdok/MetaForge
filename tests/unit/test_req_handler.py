"""Verifiable requirements handler: quantified + acceptance criteria + PRD (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from api_gateway.runs.req_handlers import (
    GoalDrivenRequirementsHandler,
    _normalize_req_spec,
    prd_md,
)
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.spec import get_flow

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))
from requirements_rubric import evaluate_requirements, requirements_score  # noqa: E402


def test_normalize_fills_defaults_and_verification() -> None:
    s = _normalize_req_spec({}, "an I2C IMU breakout board")
    assert s["functional"] and s["constraints"] and s["interfaces"]
    # every constraint carries a verification method
    assert all(c["verify"] for c in s["constraints"])


def test_prd_has_verification_column() -> None:
    s = _normalize_req_spec(
        {"constraints": [{"param": "mass", "limit": "<= 10", "unit": "g", "verify": "scale"}]},
        "board",
    )
    md = prd_md(s, "board").lower()
    assert "verification" in md and "| mass |" in md


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        return {"status": "ok", "data": {}}


@pytest.mark.asyncio
async def test_handler_output_scores_full_on_requirements_rubric() -> None:
    async def doc_recorder(**kwargs):
        return {"node_id": "prd1"}

    async def fake_extract(goal, prior, *, provider, model):
        return _normalize_req_spec(
            {
                "functional": [
                    "expose an MPU-6050 on a 0.1-inch header",
                    "regulate 3.3 V from USB",
                ],
                "constraints": [
                    {
                        "param": "mass",
                        "limit": "<= 10",
                        "unit": "g",
                        "verify": "measured on a scale",
                    },
                    {
                        "param": "power",
                        "limit": "<= 0.1",
                        "unit": "W",
                        "verify": "measured at 3.3 V",
                    },
                ],
                "interfaces": ["I2C", "USB"],
                "environment": "USB powered, -40 to 85 C",
            },
            goal,
        )

    bridge = _Bridge()
    handler = GoalDrivenRequirementsHandler(bridge, doc_recorder, extract=fake_extract)
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "requirements")
    ctx = FlowContext(goal="an I2C IMU breakout board", project_id="p1", completed=[])

    outcome = await handler.run_phase(goal=ctx.goal, phase=phase, context=ctx)

    assert outcome.status == "completed"
    assert any(a.startswith("prd:") for a in outcome.artifacts)
    rationale = bridge.calls[0][1]["rationale"]
    checks = evaluate_requirements(decision_text=rationale, artifact_types={"prd"}, goal=ctx.goal)
    assert checks["verification_criteria"], "each constraint must carry an acceptance method"
    assert requirements_score(checks) == 1.0, checks
