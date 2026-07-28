"""Goal-driven firmware handler: pinmap + scaffold + concrete decision (MET-10)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from api_gateway.runs.fw_handlers import (
    GoalDrivenFirmwareHandler,
    _normalize_fw_spec,
    firmware_scaffold_c,
    pinmap_csv,
)
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.spec import get_flow

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))
from firmware_rubric import evaluate_firmware, firmware_score  # noqa: E402


def test_normalize_fills_defaults_and_coerces() -> None:
    s = _normalize_fw_spec({}, "an I2C IMU breakout board")
    assert s["bus"] == "I2C" and s["registers"] and s["pins"]
    assert s["sample_rate_hz"] > 0
    assert s["device_addr"].startswith("0x")
    # coercion: bare hex gets 0x, bad sample rate defaults, malformed reg dropped
    s2 = _normalize_fw_spec(
        {
            "bus": "i2c",
            "device_addr": "69",
            "sample_rate_hz": "0",
            "registers": [{"name": "who am i", "addr": "75"}, {"junk": 1}],
        },
        "imu",
    )
    assert s2["device_addr"] == "0x69"
    assert s2["sample_rate_hz"] == 100.0
    assert s2["registers"][0]["name"] == "WHO_AM_I" and s2["registers"][0]["addr"] == "0x75"


def test_scaffold_and_pinmap_have_concrete_tokens() -> None:
    spec = _normalize_fw_spec({}, "imu board")
    c = firmware_scaffold_c(spec).lower()
    assert "i2c" in c and "who_am_i" in c and "0x75" in c and "read" in c
    csv = pinmap_csv(spec["pins"])
    assert csv.splitlines()[0] == "signal,pin,direction"


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def invoke(self, tool: str, args: dict) -> dict:
        self.calls.append(tool)
        return {"status": "ok", "data": {"id": "d1"}}


@pytest.mark.asyncio
async def test_handler_emits_pinmap_scaffold_and_decision() -> None:
    recorded: list[dict] = []

    async def doc_recorder(**kwargs):
        recorded.append(kwargs)
        return {"node_id": f"n{len(recorded)}"}

    async def fake_extract(goal, prior, *, provider, model):
        return _normalize_fw_spec({"device": "MPU-6050", "device_addr": "0x68"}, goal)

    bridge = _Bridge()
    handler = GoalDrivenFirmwareHandler(bridge, doc_recorder, extract=fake_extract)
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "firmware")
    ctx = FlowContext(goal="an I2C IMU breakout board", project_id="p1", completed=[])

    outcome = await handler.run_phase(goal=ctx.goal, phase=phase, context=ctx)

    assert outcome.status == "completed"
    # both artifacts + the decision landed
    kinds = {r["wp_type"].value for r in recorded}
    assert kinds == {"pinmap", "firmware_source"}
    assert any(a.startswith("pinmap:") for a in outcome.artifacts)
    assert any(a.startswith("firmware_source:") for a in outcome.artifacts)
    assert bridge.calls == ["twin.record_decision"]


@pytest.mark.asyncio
async def test_handler_output_satisfies_firmware_rubric() -> None:
    """The handler's own decision text + artifact types should score ~1.0."""

    async def doc_recorder(**kwargs):
        return {"node_id": "n"}

    captured: dict = {}

    class _CapBridge:
        async def invoke(self, tool: str, args: dict) -> dict:
            captured.update(args)
            return {"status": "ok", "data": {}}

    async def fake_extract(goal, prior, *, provider, model):
        return _normalize_fw_spec({"device": "MPU-6050"}, goal)

    handler = GoalDrivenFirmwareHandler(_CapBridge(), doc_recorder, extract=fake_extract)
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "firmware")
    ctx = FlowContext(goal="I2C IMU breakout", project_id="p1", completed=[])
    await handler.run_phase(goal=ctx.goal, phase=phase, context=ctx)

    checks = evaluate_firmware(
        decision_text=captured["rationale"],
        artifact_types={"pinmap", "firmware_source"},
        goal=ctx.goal,
    )
    assert firmware_score(checks) == 1.0, checks
