"""Architecture handler: quantified subsystem budgets + doc (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from api_gateway.runs.arch_handlers import (
    GoalDrivenArchitectureHandler,
    _normalize_arch_spec,
    arch_budget_md,
)
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.spec import get_flow

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))
from architecture_rubric import architecture_score, evaluate_architecture  # noqa: E402


def test_normalize_fills_defaults_and_totals() -> None:
    s = _normalize_arch_spec({}, "an I2C IMU breakout board")
    assert len(s["subsystems"]) >= 2 and s["interfaces"]
    # totals are the sum of the per-subsystem budgets
    assert s["totals"]["mass_g"] == round(sum(x["mass_g"] for x in s["subsystems"]), 3)


def test_budget_md_has_quantified_table() -> None:
    s = _normalize_arch_spec(
        {
            "subsystems": [
                {"name": "sensing", "component": "MPU-6050", "mass_g": 1.5, "power_mw": 4}
            ]
        },
        "board",
    )
    md = arch_budget_md(s, "board").lower()
    assert "mass (g)" in md and "| sensing |" in md and "total" in md


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        return {"status": "ok", "data": {}}


@pytest.mark.asyncio
async def test_handler_output_scores_full_on_architecture_rubric() -> None:
    async def doc_recorder(**kwargs):
        return {"node_id": "arch1"}

    async def fake_extract(goal, prior, *, provider, model):
        return _normalize_arch_spec(
            {
                "subsystems": [
                    {
                        "name": "sensing",
                        "component": "MPU-6050",
                        "mass_g": 1.5,
                        "power_mw": 4,
                        "cost_usd": 2.5,
                    },
                    {
                        "name": "compute",
                        "component": "microcontroller",
                        "mass_g": 1,
                        "power_mw": 30,
                        "cost_usd": 2,
                    },
                    {
                        "name": "power",
                        "component": "LDO",
                        "mass_g": 1,
                        "power_mw": 15,
                        "cost_usd": 1,
                    },
                ],
                "interfaces": ["I2C between sensing and compute", "3.3 V rail from power"],
            },
            goal,
        )

    bridge = _Bridge()
    handler = GoalDrivenArchitectureHandler(bridge, doc_recorder, extract=fake_extract)
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "architecture")
    ctx = FlowContext(goal="an I2C IMU breakout board", project_id="p1", completed=[])

    outcome = await handler.run_phase(goal=ctx.goal, phase=phase, context=ctx)

    assert outcome.status == "completed"
    assert any(a.startswith("documentation:") for a in outcome.artifacts)
    rationale = bridge.calls[0][1]["rationale"]
    checks = evaluate_architecture(decision_text=rationale, artifact_types=set(), goal=ctx.goal)
    assert checks["budget_allocated_quantified"], "per-subsystem numeric budgets required"
    assert architecture_score(checks) == 1.0, checks
