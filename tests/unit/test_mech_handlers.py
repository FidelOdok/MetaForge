"""Deterministic mechanical phase handlers (MET-10). Network-free.

Fakes the MCP bridge + geometry recorder so the scripted sequences are verified
without a live gateway: each handler must produce its required twin deliverable.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.runs.mech_handlers import (
    HybridBrain,
    MechanicalDesignHandler,
    RequirementsHandler,
    SimulationHandler,
)
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import Phase


class FakeBridge:
    """Records tool invocations and returns canned ``ok`` envelopes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(self, tool_id: str, params: dict[str, Any], timeout: int | None = None):
        self.calls.append((tool_id, params))
        if tool_id == "freecad.open_session":
            return {"status": "ok", "data": {"session_id": "s1"}}
        if tool_id == "freecad.create_primitive":
            return {"status": "ok", "data": {"obj_id": "primitive_1"}}
        if tool_id == "freecad.export_model":
            return {"status": "ok", "data": {"step_base64": "U1RFUC1EQVRB"}}  # "STEP-DATA"
        if tool_id == "twin.record_decision":
            return {"status": "ok", "data": {"node_id": "dec-1"}}
        return {"status": "ok", "data": {}}

    async def is_available(self, tool_id: str) -> bool:
        return True

    async def list_tools(self, capability: str | None = None) -> list[dict[str, Any]]:
        return []

    def tools_called(self) -> list[str]:
        return [c[0] for c in self.calls]


class FakeRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.records.append(kwargs)
        return {"node_id": "cad-node-1", "content_hash": "abc"}


_PHASE = Phase(id="x", title="X", objective="o")
_CTX = FlowContext(goal="quadruped leg", project_id="proj-1", session_id="sess-1")


@pytest.mark.asyncio
async def test_requirements_handler_records_decision() -> None:
    bridge = FakeBridge()
    out = await RequirementsHandler(bridge).run_phase(goal=_CTX.goal, phase=_PHASE, context=_CTX)
    assert isinstance(out, PhaseOutcome)
    assert "twin.record_decision" in bridge.tools_called()
    tool, params = next(c for c in bridge.calls if c[0] == "twin.record_decision")
    assert params["project_id"] == "proj-1"
    assert "design_decision:requirements" in out.artifacts


@pytest.mark.asyncio
async def test_design_handler_authors_and_commits_cad_model() -> None:
    bridge = FakeBridge()
    recorder = FakeRecorder()
    out = await MechanicalDesignHandler(bridge, recorder).run_phase(
        goal=_CTX.goal, phase=_PHASE, context=_CTX
    )
    # Authored via the session and exported, then committed via the recorder.
    assert bridge.tools_called()[:3] == [
        "freecad.open_session",
        "freecad.create_primitive",
        "freecad.export_model",
    ]
    assert len(recorder.records) == 1
    rec = recorder.records[0]
    assert rec["step_base64"] == "U1RFUC1EQVRB"
    assert rec["name"] == "HipBracket_FL"
    assert rec["project_id"] == "proj-1"
    assert any(a.startswith("cad_model:") for a in out.artifacts)


@pytest.mark.asyncio
async def test_design_handler_raises_without_step_bytes() -> None:
    class NoStepBridge(FakeBridge):
        async def invoke(self, tool_id, params, timeout=None):
            if tool_id == "freecad.export_model":
                return {"status": "ok", "data": {}}  # no base64
            return await super().invoke(tool_id, params, timeout)

    with pytest.raises(RuntimeError, match="no base64 STEP"):
        await MechanicalDesignHandler(NoStepBridge(), FakeRecorder()).run_phase(
            goal=_CTX.goal, phase=_PHASE, context=_CTX
        )


@pytest.mark.asyncio
async def test_simulation_handler_computes_verdict() -> None:
    bridge = FakeBridge()
    out = await SimulationHandler(bridge).run_phase(goal=_CTX.goal, phase=_PHASE, context=_CTX)
    tool, params = next(c for c in bridge.calls if c[0] == "twin.record_decision")
    # 100 N * 40 mm / (30*8^2/6 mm^3) = 4000 / 320 = 12.5 MPa -> SF 276/12.5 ~= 22 -> PASS
    assert "PASS" in params["title"]
    assert "design_decision:vv" in out.artifacts


@pytest.mark.asyncio
async def test_bridge_error_envelope_raises() -> None:
    class ErrBridge(FakeBridge):
        async def invoke(self, tool_id, params, timeout=None):
            if tool_id == "twin.record_decision":
                return {"status": "error", "error": "boom"}
            return await super().invoke(tool_id, params, timeout)

    with pytest.raises(RuntimeError, match="boom"):
        await RequirementsHandler(ErrBridge()).run_phase(goal=_CTX.goal, phase=_PHASE, context=_CTX)


@pytest.mark.asyncio
async def test_hybrid_brain_routes_to_handler_else_fallback() -> None:
    class FallbackBrain:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
            self.seen.append(phase.id)
            return PhaseOutcome(summary="fallback", status="completed")

    class Handler:
        async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
            return PhaseOutcome(summary="handled", status="completed")

    fallback = FallbackBrain()
    hybrid = HybridBrain(handlers={"design": Handler()}, fallback=fallback)

    handled = await hybrid.run_phase(
        goal="g", phase=Phase(id="design", title="D", objective="o"), context=_CTX
    )
    assert handled.summary == "handled"

    fell = await hybrid.run_phase(
        goal="g", phase=Phase(id="requirements", title="R", objective="o"), context=_CTX
    )
    assert fell.summary == "fallback"
    assert fallback.seen == ["requirements"]
