"""Goal-driven electronics handler: guaranteed BOM + closed power budget (MET-10)."""

from __future__ import annotations

import pytest

from api_gateway.runs.elec_handlers import GoalDrivenElectronicsHandler, _normalize_elec_spec
from api_gateway.twin.bom_recorder import bom_csv
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.spec import get_flow


def test_normalize_fills_defaults_and_coerces() -> None:
    s = _normalize_elec_spec({}, "an I2C IMU breakout board")
    assert s["components"] and s["rails"] and s["interfaces"]
    assert s["power_budget_mw"] > 0  # always a closed numeric budget
    assert s["name"]
    # coercion: drop malformed component, keep the good one, coerce qty
    s2 = _normalize_elec_spec(
        {
            "components": [
                {"part": "MPU-6050", "ref": "U1", "qty": "1", "function": "IMU"},
                {"nonsense": True},
            ],
            "rails": [{"name": "3V3", "voltage_v": "3.3"}],
            "interfaces": ["I2C"],
            "power_budget_mw": "45",
        },
        "imu board",
    )
    assert len(s2["components"]) == 1
    assert s2["components"][0]["qty"] == 1
    assert s2["rails"][0]["voltage_v"] == 3.3
    assert s2["power_budget_mw"] == 45.0


def test_bom_csv_is_stable_and_escaped() -> None:
    csv = bom_csv([{"ref": "U1", "part": "LDO regulator", "value": "3.3V", "qty": 1}])
    assert csv.splitlines()[0] == "ref,part,value,qty,function"
    assert '"LDO regulator"' in csv  # space triggers quoting


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def invoke(self, tool: str, args: dict) -> dict:
        self.calls.append(tool)
        return {"status": "ok", "data": {"id": "d1"}}


@pytest.mark.asyncio
async def test_handler_records_bom_and_power_budget() -> None:
    recorded: dict = {}

    async def bom_recorder(**kwargs):
        recorded.update(kwargs)
        return {"node_id": "bom1"}

    async def fake_extract(goal, prior, *, provider, model):
        return _normalize_elec_spec(
            {
                "name": "IMU Breakout",
                "components": [
                    {"ref": "U1", "part": "MPU-6050", "value": "", "qty": 1, "function": "IMU"},
                    {"ref": "U2", "part": "LDO", "value": "3.3V", "qty": 1, "function": "power"},
                ],
                "rails": [{"name": "3V3", "voltage_v": 3.3}],
                "interfaces": ["I2C"],
                "power_budget_mw": 90,
            },
            goal,
        )

    bridge = _Bridge()
    handler = GoalDrivenElectronicsHandler(bridge, bom_recorder, extract=fake_extract)
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "electronics")
    ctx = FlowContext(goal="an I2C IMU breakout board", project_id="p1", completed=[])

    outcome = await handler.run_phase(goal=ctx.goal, phase=phase, context=ctx)

    assert outcome.status == "completed"
    assert any(a.startswith("bom:bom1") for a in outcome.artifacts)
    assert "design_decision:electronics" in outcome.artifacts
    # the BOM went through the recorder with the real components
    assert recorded["rows"][0]["part"] == "MPU-6050"
    assert recorded["name"] == "IMU Breakout BOM"
    # a decision with the closed numeric budget was recorded
    assert bridge.calls == ["twin.record_decision"]
    assert "90 mW" in outcome.summary
