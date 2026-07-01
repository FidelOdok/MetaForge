"""Unit tests for the harness runtime composition root (MET-547)."""

from __future__ import annotations

import pytest

from orchestrator.harness import HarnessRuntime
from orchestrator.harness.providers import ProviderSpec, load_provider_config
from orchestrator.harness.runs import RunStatus
from orchestrator.harness.tools import GateBlockedError, ToolRegistry

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
