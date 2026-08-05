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


# --------------------------------------------------------------------------
# Constraint-set recording (MET-582)
# --------------------------------------------------------------------------

from api_gateway.runs.req_handlers import constraint_entries_from_spec  # noqa: E402


def test_quantified_limits_become_evaluable_error_constraints() -> None:
    entries = constraint_entries_from_spec(
        [
            {"param": "mass", "limit": "<= 60", "unit": "g", "verify": "scale"},
            {"param": "safety factor", "limit": ">= 2.0", "unit": "", "verify": "FEA"},
        ]
    )
    by_name = {e["name"]: e for e in entries}
    mass = by_name["mass"]
    assert mass["severity"] == "error"
    assert "mass_g" in mass["expression"] and "<= 60" in mass["expression"]
    compile(mass["expression"], "<c>", "eval")  # must be evaluable
    sf = by_name["safety_factor"]
    # >= limits default ABSENT metadata to the limit itself (vacuous pass) —
    # a project with no data yet must not fail its gate.
    assert "get('safety_factor', 2.0)" in sf["expression"]
    compile(sf["expression"], "<c>", "eval")


def test_non_quantified_limits_become_info_only() -> None:
    entries = constraint_entries_from_spec(
        [{"param": "board size", "limit": "per the outline", "unit": "mm", "verify": "inspect"}]
    )
    assert entries[0]["severity"] == "info"
    assert entries[0]["expression"] == "True"
    assert "per the outline" in entries[0]["message"]
    assert "inspect" in entries[0]["message"]


def test_duplicate_params_get_unique_names() -> None:
    entries = constraint_entries_from_spec(
        [
            {"param": "mass", "limit": "<= 60", "unit": "g"},
            {"param": "mass", "limit": "<= 10", "unit": "g"},
        ]
    )
    assert len({e["name"] for e in entries}) == 2


@pytest.mark.asyncio
async def test_handler_records_constraint_set(monkeypatch) -> None:
    async def doc_recorder(**kwargs):
        return {"node_id": "prd1"}

    async def fake_extract(goal, prior, *, provider, model):
        return _normalize_req_spec(
            {"constraints": [{"param": "mass", "limit": "<= 10", "unit": "g", "verify": "scale"}]},
            goal,
        )

    bridge = _Bridge()
    handler = GoalDrivenRequirementsHandler(bridge, doc_recorder, extract=fake_extract)
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "requirements")
    ctx = FlowContext(goal="a widget", project_id="p1", completed=[])
    outcome = await handler.run_phase(goal=ctx.goal, phase=phase, context=ctx)

    tools = [t for t, _ in bridge.calls]
    assert "twin.record_constraint_set" in tools
    args = next(a for t, a in bridge.calls if t == "twin.record_constraint_set")
    assert args["project_id"] == "p1"
    assert any(c["name"] == "mass" and c["severity"] == "error" for c in args["constraints"])
    assert any(a.startswith("constraint_set:") for a in outcome.artifacts)


@pytest.mark.asyncio
async def test_constraint_set_failure_does_not_mask_phase(monkeypatch) -> None:
    """Recording failure logs and continues — the REQUIRED deliverable check
    at the gate is what surfaces the miss, with a readable message."""

    async def doc_recorder(**kwargs):
        return {"node_id": "prd1"}

    async def fake_extract(goal, prior, *, provider, model):
        return _normalize_req_spec({}, goal)

    class _BoomBridge(_Bridge):
        async def invoke(self, tool: str, args: dict) -> dict:
            if tool == "twin.record_constraint_set":
                raise RuntimeError("tool not registered")
            return await super().invoke(tool, args)

    handler = GoalDrivenRequirementsHandler(_BoomBridge(), doc_recorder, extract=fake_extract)
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "requirements")
    outcome = await handler.run_phase(
        goal="g", phase=phase, context=FlowContext(goal="g", project_id="p1", completed=[])
    )
    assert outcome.status == "completed"
    assert "constraint_set:None" in outcome.artifacts
