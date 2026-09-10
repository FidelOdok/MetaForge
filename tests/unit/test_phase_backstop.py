"""Native phase brain guarantees its design_decision deliverable (MET-10)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api_gateway.runs.flow_brain import ReActPhaseBrain
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.spec import get_flow


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, tool: str, args: dict) -> dict:
        self.calls.append((tool, args))
        return {"status": "ok", "data": {}}


@pytest.mark.asyncio
async def test_backstop_records_decision_when_phase_requires_one() -> None:
    bridge = _Bridge()
    brain = ReActPhaseBrain(mcp_bridge=bridge)
    # hardware_v1's electronics phase requires a design_decision.
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "electronics")
    ctx = FlowContext(goal="a breakout board", project_id="p1", completed=[])

    await brain._backstop_decision(phase, ctx, "Designed 3.3V LDO + I2C IMU topology.")

    assert bridge.calls, "a decision should be recorded"
    tool, args = bridge.calls[0]
    assert tool == "twin.record_decision"
    assert args["rationale"] == "Designed 3.3V LDO + I2C IMU topology."
    assert args["project_id"] == "p1"


@pytest.mark.asyncio
async def test_backstop_skips_when_phase_does_not_require_a_decision() -> None:
    bridge = _Bridge()
    brain = ReActPhaseBrain(mcp_bridge=bridge)
    # mech_v1's design phase requires a cad_model, not a design_decision.
    phase = next(p for p in get_flow("mech_v1").phases if p.id == "design")
    ctx = FlowContext(goal="a bracket", project_id="p1", completed=[])

    await brain._backstop_decision(phase, ctx, "authored the bracket")
    assert bridge.calls == []


@pytest.mark.asyncio
async def test_backstop_never_raises_on_bridge_error() -> None:
    class _BadBridge:
        async def invoke(self, tool: str, args: dict) -> dict:
            raise RuntimeError("boom")

    brain = ReActPhaseBrain(mcp_bridge=_BadBridge())
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "firmware")
    ctx = FlowContext(goal="g", project_id=None, completed=[])
    await brain._backstop_decision(phase, ctx, "summary")  # must not raise


@pytest.mark.asyncio
@patch("api_gateway.runs.flow_brain.get_metrics")
@patch("api_gateway.runs.flow_brain.run_chat_turn", new_callable=AsyncMock)
async def test_run_phase_threads_the_configured_metrics_collector(
    mock_run_chat_turn: AsyncMock, mock_get_metrics: MagicMock
) -> None:
    """MET-659 follow-up: run_phase is the design-flow-phase equivalent of
    the interactive chat harness's run_chat_turn_streaming call, which had
    the same gap (init_metrics() set a collector nothing ever read)."""
    sentinel_collector = object()
    mock_get_metrics.return_value = sentinel_collector
    mock_run_chat_turn.return_value = "Designed the thing."

    brain = ReActPhaseBrain(mcp_bridge=_Bridge())
    phase = next(p for p in get_flow("mech_v1").phases if p.id == "design")
    ctx = FlowContext(goal="a bracket", project_id="p1", completed=[])

    await brain.run_phase(goal="a bracket", phase=phase, context=ctx)

    assert mock_run_chat_turn.call_args.kwargs["metrics"] is sentinel_collector
