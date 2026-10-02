"""FORGE-490: a design-flow phase turn forwards approval-tier tool calls to the
sidecar instead of parking them in an in-process store nobody can answer."""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from api_gateway.runs import flow_brain
from api_gateway.runs.flow_brain import ReActPhaseBrain
from observability.metrics import MetricsRegistry
from orchestrator.design_flow.executor import FlowContext
from orchestrator.design_flow.spec import get_flow
from orchestrator.harness.runs import InMemoryRunStore
from orchestrator.harness.runtime import HarnessRuntime
from orchestrator.harness.tools import ApprovalDeniedError, ToolRegistry


def _registry(calls: list[dict[str, object]]) -> ToolRegistry:
    tools = ToolRegistry()

    async def _record(args: dict[str, object]) -> dict[str, object]:
        calls.append(args)
        return {"done": True}

    tools.register_native(
        "twin_record_decision",
        description="d",
        input_schema={},
        handler=_record,
        requires_approval=True,
    )
    return tools


@pytest.mark.asyncio
async def test_forward_mode_creates_no_hold_for_mcp_tool() -> None:
    calls: list[dict[str, object]] = []
    runs = InMemoryRunStore()
    tools = ToolRegistry()

    async def _h(args: dict[str, object]) -> dict[str, object]:
        calls.append(args)
        return {"done": True}

    tools.register_mcp(
        "metaforge", "t", description="d", input_schema={}, handler=_h, requires_approval=True
    )
    rt = HarnessRuntime.build(
        tools=tools, runs=runs, approval_mode="forward", approver_reachable=False
    )
    assert await rt.call_tool("mcp_metaforge_t", {"x": 1}) == {"done": True}
    assert calls == [{"x": 1}]
    assert runs.list() == []


@pytest.mark.asyncio
async def test_forward_mode_forwards_mcp_tool() -> None:
    calls: list[dict[str, object]] = []
    tools = ToolRegistry()

    async def _h(args: dict[str, object]) -> dict[str, object]:
        calls.append(args)
        return {"done": True}

    tools.register_mcp(
        "metaforge", "twin_record_decision", description="d", input_schema={},
        handler=_h, requires_approval=True,
    )
    rt = HarnessRuntime.build(tools=tools, approval_mode="forward")
    assert await rt.call_tool("mcp_metaforge_twin_record_decision", {"x": 1}) == {"done": True}
    assert calls == [{"x": 1}]


@pytest.mark.asyncio
async def test_forward_mode_refuses_native_approval_tool_without_hold_or_run() -> None:
    calls: list[dict[str, object]] = []
    runs = InMemoryRunStore()
    rt = HarnessRuntime.build(tools=_registry(calls), runs=runs, approval_mode="forward")
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(ApprovalDeniedError, match="unattended design-flow turn"):
            await rt.call_tool("twin_record_decision", {"x": 1})
    assert calls == []
    assert runs.list() == []
    assert [e for e in logs if e["event"] == "approval_tool_refused_unattended"]


@pytest.mark.asyncio
async def test_hold_mode_still_parks_the_call_like_chat() -> None:
    calls: list[dict[str, object]] = []
    runs = InMemoryRunStore()

    async def instant_sleep(seconds: float) -> None:
        return None

    rt = HarnessRuntime.build(
        tools=_registry(calls),
        runs=runs,
        approval_timeout_seconds=0.0,
        approval_sleep=instant_sleep,
    )
    with pytest.raises(ApprovalDeniedError):
        await rt.call_tool("twin_record_decision", {"x": 1})
    assert calls == []
    assert len(runs.list()) == 1


class _Metrics:
    def __init__(self) -> None:
        self.unreachable: list[str] = []

    def record_unreachable_approval_hold(self, tool: str) -> None:
        self.unreachable.append(tool)


@pytest.mark.asyncio
async def test_unreachable_hold_raises_alarm_and_metric() -> None:
    metrics = _Metrics()

    async def instant_sleep(seconds: float) -> None:
        return None

    rt = HarnessRuntime.build(
        tools=_registry([]),
        runs=InMemoryRunStore(),
        approval_timeout_seconds=0.0,
        approval_sleep=instant_sleep,
        approver_reachable=False,
        metrics=metrics,  # type: ignore[arg-type]
    )
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(ApprovalDeniedError):
            await rt.call_tool("twin_record_decision", {})
    errors = [e for e in logs if e["event"] == "approval_hold_unreachable"]
    assert errors and errors[0]["log_level"] == "error"
    assert metrics.unreachable == ["twin_record_decision"]


@pytest.mark.asyncio
async def test_reachable_hold_does_not_alarm() -> None:
    metrics = _Metrics()

    async def instant_sleep(seconds: float) -> None:
        return None

    rt = HarnessRuntime.build(
        tools=_registry([]),
        runs=InMemoryRunStore(),
        approval_timeout_seconds=0.0,
        approval_sleep=instant_sleep,
        metrics=metrics,  # type: ignore[arg-type]
    )
    with structlog.testing.capture_logs() as logs:
        with pytest.raises(ApprovalDeniedError):
            await rt.call_tool("twin_record_decision", {})
    assert not [e for e in logs if e["event"] == "approval_hold_unreachable"]
    assert metrics.unreachable == []


def test_metric_is_registered() -> None:
    names = [m.name for m in MetricsRegistry.harness_metrics()]
    assert "metaforge_unreachable_approval_hold_total" in names


@pytest.mark.asyncio
async def test_phase_turn_passes_forward_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_turn(prompt: str, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "done"

    async def no_backstop(self: Any, *a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(flow_brain, "run_chat_turn", fake_turn)
    monkeypatch.setattr(ReActPhaseBrain, "_backstop_decision", no_backstop)
    phase = next(p for p in get_flow("hardware_v1").phases if p.id == "intent")
    ctx = FlowContext(goal="g", project_id="p1", completed=[])
    await ReActPhaseBrain(mcp_bridge=None).run_phase(goal="g", phase=phase, context=ctx)
    assert seen["approval_mode"] == "forward"
