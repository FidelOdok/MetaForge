"""Unit tests for agent-runtime assembly — tools + skills (MET-548)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.harness import AgentContext, NativeToolDef, build_agent_runtime
from orchestrator.harness.providers import load_provider_config
from orchestrator.harness.tools import GateBlockedError

CONFIG = load_provider_config(
    {"roles": {"generator": [{"provider": "anthropic", "model": "claude-opus-4-8"}]}}
)


async def _search(args: dict[str, object]) -> dict[str, object]:
    return {"hits": [f"result for {args.get('query')}"]}


def test_empty_build() -> None:
    ctx = build_agent_runtime(CONFIG)
    assert isinstance(ctx, AgentContext)
    assert ctx.runtime.tools.all_tools() == []
    assert ctx.skills.names() == []


@pytest.mark.asyncio
async def test_registers_native_tools() -> None:
    ctx = build_agent_runtime(
        CONFIG,
        native_tools=[NativeToolDef("twin_search", "search the twin", {"type": "object"}, _search)],
    )
    assert [t.name for t in ctx.runtime.tools.all_tools()] == ["twin_search"]
    out = await ctx.runtime.call_tool("twin_search", {"query": "bolt"})
    assert out == {"hits": ["result for bolt"]}


def test_registers_mcp_tools_namespaced() -> None:
    ctx = build_agent_runtime(
        CONFIG,
        mcp_tools=[("calculix", NativeToolDef("run_fea", "run FEA", {}, _search))],
    )
    assert ctx.runtime.tools.get("mcp_calculix_run_fea").origin == "calculix"


@pytest.mark.asyncio
async def test_gate_check_flows_into_runtime() -> None:
    ctx = build_agent_runtime(
        CONFIG,
        native_tools=[
            NativeToolDef("cut", "destructive", {}, _search, required_gates=("approval",))
        ],
        gate_check=lambda g: False,
    )
    with pytest.raises(GateBlockedError):
        await ctx.runtime.call_tool("cut", {})


def test_loads_skills_from_dir(tmp_path: Path) -> None:
    (tmp_path / "enclosure").mkdir()
    (tmp_path / "enclosure" / "SKILL.md").write_text(
        "---\nname: enclosure\ntools: [twin_search]\n---\nDo the thing.",
        encoding="utf-8",
    )
    ctx = build_agent_runtime(CONFIG, skills_dir=tmp_path)
    assert ctx.skills.names() == ["enclosure"]
    assert ctx.skills.get("enclosure").tools == ("twin_search",)


def test_missing_skills_dir_is_noop(tmp_path: Path) -> None:
    ctx = build_agent_runtime(CONFIG, skills_dir=tmp_path / "does-not-exist")
    assert ctx.skills.names() == []
