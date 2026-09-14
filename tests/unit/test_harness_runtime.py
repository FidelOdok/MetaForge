"""Unit tests for the harness runtime composition root (MET-547)."""

from __future__ import annotations

import pytest

from observability.metrics import MetricsCollector
from orchestrator.harness import HarnessRuntime
from orchestrator.harness.providers import ProviderSpec, load_provider_config
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus
from orchestrator.harness.tools import ApprovalDeniedError, GateBlockedError, ToolRegistry

CONFIG = load_provider_config(
    {"roles": {"generator": [{"provider": "anthropic", "model": "claude-opus-4-8"}]}}
)


async def _echo(args: dict[str, object]) -> dict[str, object]:
    return {"echo": args}


def test_build_without_config_has_empty_pipeline() -> None:
    rt = HarnessRuntime.build()
    assert isinstance(rt.tools, ToolRegistry)
    with pytest.raises(KeyError):
        rt.providers.resolve("generator")  # no slots configured


def test_build_from_config_resolves_roles() -> None:
    rt = HarnessRuntime.build(CONFIG)
    assert [s.model for s in rt.providers.resolve("generator")] == ["claude-opus-4-8"]


@pytest.mark.asyncio
async def test_complete_delegates_to_pipeline() -> None:
    rt = HarnessRuntime.build(CONFIG)

    async def invoke(spec: ProviderSpec, request: object) -> str:
        return f"ran:{spec.model}"

    assert await rt.complete("generator", {}, invoke) == "ran:claude-opus-4-8"


@pytest.mark.asyncio
async def test_call_tool_enforces_runtime_gate() -> None:
    tools = ToolRegistry()
    tools.register_native(
        "cut",
        description="destructive",
        input_schema={},
        handler=_echo,
        required_gates=["approval"],
    )
    # Runtime gate policy denies everything -> gated tool is blocked.
    rt = HarnessRuntime.build(tools=tools, gate_check=lambda g: False)
    with pytest.raises(GateBlockedError):
        await rt.call_tool("cut", {})


@pytest.mark.asyncio
async def test_call_tool_allows_when_gate_satisfied() -> None:
    tools = ToolRegistry()
    tools.register_native(
        "cut", description="d", input_schema={}, handler=_echo, required_gates=["approval"]
    )
    rt = HarnessRuntime.build(tools=tools, gate_check=lambda g: True)
    assert await rt.call_tool("cut", {"x": 1}) == {"echo": {"x": 1}}


def test_runs_store_is_wired() -> None:
    ticks = iter(range(1, 100))
    rt = HarnessRuntime.build(clock=lambda: float(next(ticks)))
    run = rt.runs.create({"goal": "x"}, run_id="r1")
    assert run.status is RunStatus.QUEUED
    assert rt.runs.start("r1").status is RunStatus.RUNNING


class _FakeMetrics(MetricsCollector):
    """Records calls without needing a real OTel meter (production-harness
    audit follow-up: `call_tool` previously had zero metrics of its own)."""

    def __init__(self) -> None:
        super().__init__()  # no meter -- the base class's own no-op instruments
        self.tool_calls: list[tuple[str, str]] = []

    def record_harness_tool_call(self, tool_name: str, status: str, duration: float) -> None:
        self.tool_calls.append((tool_name, status))


@pytest.mark.asyncio
async def test_call_tool_records_metrics_on_success() -> None:
    tools = ToolRegistry()
    tools.register_native("echo", description="d", input_schema={}, handler=_echo)
    metrics = _FakeMetrics()
    rt = HarnessRuntime.build(tools=tools, metrics=metrics)
    await rt.call_tool("echo", {"x": 1})
    assert metrics.tool_calls == [("echo", "ok")]


@pytest.mark.asyncio
async def test_call_tool_records_metrics_on_error_and_still_raises() -> None:
    async def _boom(args: dict[str, object]) -> None:
        raise ValueError("boom")

    tools = ToolRegistry()
    tools.register_native("boom", description="d", input_schema={}, handler=_boom)
    metrics = _FakeMetrics()
    rt = HarnessRuntime.build(tools=tools, metrics=metrics)
    with pytest.raises(ValueError, match="boom"):
        await rt.call_tool("boom", {})
    assert metrics.tool_calls == [("boom", "error")]


@pytest.mark.asyncio
async def test_call_tool_with_no_metrics_is_unaffected() -> None:
    """The historical, metrics-less behavior — every existing caller — must
    be untouched (metrics=None is the default)."""
    tools = ToolRegistry()
    tools.register_native("echo", description="d", input_schema={}, handler=_echo)
    rt = HarnessRuntime.build(tools=tools)
    assert await rt.call_tool("echo", {"x": 1}) == {"echo": {"x": 1}}


class TestThreeTierApproval:
    """Production-harness audit follow-up: the third permission tier, "ask" —
    a `requires_approval` tool pauses on `orchestrator.harness.runs`'s
    already-built AWAITING_APPROVAL state machine rather than a new one."""

    @pytest.mark.asyncio
    async def test_proceeds_when_approved(self) -> None:
        tools = ToolRegistry()
        calls = {"n": 0}

        async def _mutate(args: dict[str, object]) -> dict[str, object]:
            calls["n"] += 1
            return {"done": True}

        tools.register_native(
            "commit", description="d", input_schema={}, handler=_mutate, requires_approval=True
        )
        runs = InMemoryRunStore()
        notified: list[tuple[str, str, dict[str, object]]] = []

        async def on_request(run_id: str, tool: str, arguments: dict[str, object]) -> None:
            notified.append((run_id, tool, arguments))

        async def fake_sleep(seconds: float) -> None:
            runs.submit_approval(notified[0][0], ApprovalDecision.APPROVE)

        rt = HarnessRuntime.build(
            tools=tools, runs=runs, on_approval_request=on_request, approval_sleep=fake_sleep
        )
        result = await rt.call_tool("commit", {"x": 1})
        assert result == {"done": True}
        assert calls["n"] == 1
        assert notified == [(notified[0][0], "commit", {"x": 1})]

    @pytest.mark.asyncio
    async def test_raises_when_rejected_and_never_invokes_the_handler(self) -> None:
        tools = ToolRegistry()
        calls = {"n": 0}

        async def _mutate(args: dict[str, object]) -> dict[str, object]:
            calls["n"] += 1
            return {"done": True}

        tools.register_native(
            "commit", description="d", input_schema={}, handler=_mutate, requires_approval=True
        )
        runs = InMemoryRunStore()

        async def fake_sleep(seconds: float) -> None:
            runs.submit_approval(runs.list()[0].id, ApprovalDecision.REJECT)

        rt = HarnessRuntime.build(tools=tools, runs=runs, approval_sleep=fake_sleep)
        with pytest.raises(ApprovalDeniedError, match="rejected"):
            await rt.call_tool("commit", {})
        assert calls["n"] == 0

    @pytest.mark.asyncio
    async def test_denies_by_default_on_timeout_with_no_decision(self) -> None:
        tools = ToolRegistry()
        calls = {"n": 0}

        async def _mutate(args: dict[str, object]) -> dict[str, object]:
            calls["n"] += 1
            return {"done": True}

        tools.register_native(
            "commit", description="d", input_schema={}, handler=_mutate, requires_approval=True
        )
        runs = InMemoryRunStore()

        async def fake_sleep(seconds: float) -> None:
            raise AssertionError("an already-elapsed deadline should never poll")

        rt = HarnessRuntime.build(
            tools=tools, runs=runs, approval_sleep=fake_sleep, approval_timeout_seconds=0.0
        )
        with pytest.raises(ApprovalDeniedError, match="timed out"):
            await rt.call_tool("commit", {})
        assert calls["n"] == 0
        assert runs.list()[0].status is RunStatus.REJECTED  # denial is durably recorded

    @pytest.mark.asyncio
    async def test_fails_safe_with_no_evaluator_available(self) -> None:
        """No approval ever arrives and the deadline is already elapsed —
        same fail-safe discipline GateBlockedError already has for gates."""
        tools = ToolRegistry()
        tools.register_native(
            "commit", description="d", input_schema={}, handler=_echo, requires_approval=True
        )

        async def fake_sleep(seconds: float) -> None:
            raise AssertionError("should not need to poll")

        rt = HarnessRuntime.build(
            tools=tools, approval_timeout_seconds=0.0, approval_sleep=fake_sleep
        )
        with pytest.raises(ApprovalDeniedError):
            await rt.call_tool("commit", {"x": 1})

    @pytest.mark.asyncio
    async def test_broken_notify_callback_does_not_block_approval(self) -> None:
        tools = ToolRegistry()
        tools.register_native(
            "commit", description="d", input_schema={}, handler=_echo, requires_approval=True
        )
        runs = InMemoryRunStore()

        async def broken_notify(run_id: str, tool: str, arguments: dict[str, object]) -> None:
            raise RuntimeError("notifier is down")

        async def fake_sleep(seconds: float) -> None:
            runs.submit_approval(runs.list()[0].id, ApprovalDecision.APPROVE)

        rt = HarnessRuntime.build(
            tools=tools, runs=runs, on_approval_request=broken_notify, approval_sleep=fake_sleep
        )
        result = await rt.call_tool("commit", {"x": 1})
        assert result == {"echo": {"x": 1}}

    @pytest.mark.asyncio
    async def test_plain_tools_unaffected_by_the_approval_mechanism(self) -> None:
        """requires_approval defaults to False — every pre-existing tool
        registration and every pre-existing test must be untouched."""
        tools = ToolRegistry()
        tools.register_native("echo", description="d", input_schema={}, handler=_echo)
        rt = HarnessRuntime.build(tools=tools)
        assert await rt.call_tool("echo", {"x": 1}) == {"echo": {"x": 1}}
