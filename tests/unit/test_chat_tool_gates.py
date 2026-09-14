"""Gate declarations for chat-driven MCP tools (MET-569).

The harness has had gate plumbing since MET-547 and nothing ever declared a
gate, so ``required_gates`` was dead code and a chat turn could always write to
the twin. These tests pin both halves of the fix: the declarations, and the
evaluator that has to exist alongside them (a gated tool with no evaluator
never runs at all).
"""

from __future__ import annotations

import pytest

from api_gateway.chat.harness_backend import (
    GATE_PROJECT_WRITE,
    GATE_TWIN_WRITE,
    _build_context,
    chat_gate_check,
    mcp_tools_from_bridge,
)
from orchestrator.harness.providers import CredentialStore
from orchestrator.harness.tools import GateBlockedError
from skill_registry.mcp_bridge import InMemoryMcpBridge


def _bridge() -> InMemoryMcpBridge:
    bridge = InMemoryMcpBridge()
    for tool_id in (
        "twin.commit_geometry",
        "twin.record_decision",
        "twin.record_constraint_set",
        "twin.propose_change",
        "twin.stage_work_product_file",
        "twin.get_node",
        "project.create",
        "project.update",
        "project.delete",
        "project.get",
        "freecad.pad_sketch",
    ):
        bridge.register_tool(tool_id, capability="x")
        bridge.register_tool_response(tool_id, {"ok": True})
    return bridge


@pytest.mark.asyncio
async def test_persistent_twin_and_project_writes_declare_gates():
    defs = {td.name: td for _server, td in await mcp_tools_from_bridge(_bridge())}

    assert defs["commit_geometry"].required_gates == (GATE_TWIN_WRITE,)
    assert defs["record_decision"].required_gates == (GATE_TWIN_WRITE,)
    assert defs["record_constraint_set"].required_gates == (GATE_TWIN_WRITE,)
    assert defs["propose_change"].required_gates == (GATE_TWIN_WRITE,)
    assert defs["stage_work_product_file"].required_gates == (GATE_TWIN_WRITE,)
    assert defs["create"].required_gates == (GATE_PROJECT_WRITE,)
    assert defs["update"].required_gates == (GATE_PROJECT_WRITE,)
    assert defs["delete"].required_gates == (GATE_PROJECT_WRITE,)


@pytest.mark.asyncio
async def test_reads_and_ephemeral_cad_work_stay_ungated():
    # Gating a read would make the agent useless; gating freecad.* would gate
    # work that only ever touches the adapter's temporary workspace.
    defs = {td.name: td for _server, td in await mcp_tools_from_bridge(_bridge())}

    assert defs["get_node"].required_gates == ()
    assert defs["get"].required_gates == ()
    assert defs["pad_sketch"].required_gates == ()


def test_gates_default_to_satisfied(monkeypatch: pytest.MonkeyPatch):
    # Declaring the gates must not change what an existing deployment can do.
    monkeypatch.delenv("METAFORGE_CHAT_TWIN_WRITES", raising=False)
    monkeypatch.delenv("METAFORGE_CHAT_PROJECT_WRITES", raising=False)

    assert chat_gate_check(GATE_TWIN_WRITE) is True
    assert chat_gate_check(GATE_PROJECT_WRITE) is True


def test_an_operator_can_lock_writes_down(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("METAFORGE_CHAT_TWIN_WRITES", "off")
    monkeypatch.setenv("METAFORGE_CHAT_PROJECT_WRITES", "0")

    assert chat_gate_check(GATE_TWIN_WRITE) is False
    assert chat_gate_check(GATE_PROJECT_WRITE) is False


def test_an_unknown_gate_is_never_satisfied():
    # Fail safe: a tool declaring a gate nobody evaluates must not run.
    assert chat_gate_check("some_future_gate") is False


class TestRuntimeEnforcement:
    @pytest.mark.asyncio
    async def test_a_gated_write_runs_when_the_gate_is_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("METAFORGE_CHAT_TWIN_WRITES", raising=False)
        ctx = await _build_context("thread-1", CredentialStore(), _bridge())

        assert await ctx.runtime.call_tool("mcp_twin_stage_work_product_file", {}) == {"ok": True}

    @pytest.mark.asyncio
    async def test_a_gated_write_is_blocked_when_the_gate_is_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_CHAT_TWIN_WRITES", "false")
        ctx = await _build_context("thread-1", CredentialStore(), _bridge())

        with pytest.raises(GateBlockedError, match="twin_write"):
            await ctx.runtime.call_tool("mcp_twin_stage_work_product_file", {})

    @pytest.mark.asyncio
    async def test_reads_are_unaffected_by_a_closed_write_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_CHAT_TWIN_WRITES", "false")
        monkeypatch.setenv("METAFORGE_CHAT_PROJECT_WRITES", "false")
        ctx = await _build_context("thread-1", CredentialStore(), _bridge())

        assert await ctx.runtime.call_tool("mcp_twin_get_node", {}) == {"ok": True}
        assert await ctx.runtime.call_tool("mcp_project_get", {}) == {"ok": True}
