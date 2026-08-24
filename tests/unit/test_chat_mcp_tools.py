"""Chat harness can drive MCP tools via the bridge (MET-548). Network-free."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_gateway.chat.harness_backend import _build_context, mcp_tools_from_bridge, run_chat_turn
from api_gateway.chat.tool_approvals import get_approval_store, reset_approval_store
from orchestrator.harness.providers import CredentialStore, ProviderSpec
from orchestrator.harness.runs import ApprovalDecision
from orchestrator.harness.tools import ApprovalDeniedError
from skill_registry.mcp_bridge import InMemoryMcpBridge


@pytest.mark.asyncio
async def test_mcp_tools_from_bridge_builds_defs() -> None:
    bridge = InMemoryMcpBridge()
    bridge.register_tool("calculix.run_fea", capability="fea", name="Run FEA")
    defs = await mcp_tools_from_bridge(bridge)
    assert len(defs) == 1
    server, td = defs[0]
    assert server == "calculix"  # split from tool_id
    assert td.name == "run_fea"
    assert "calculix.run_fea" in td.description


@pytest.mark.asyncio
async def test_mcp_tools_from_bridge_tags_the_starter_approval_tier() -> None:
    """Production-harness audit follow-up: a conservative starter set of
    persistent-write tool ids require approval; everything else is auto-allow."""
    bridge = InMemoryMcpBridge()
    bridge.register_tool("twin.commit_geometry", capability="twin_write")
    bridge.register_tool("twin.record_decision", capability="twin_write")
    bridge.register_tool("project.create", capability="project_write")
    bridge.register_tool("project.update", capability="project_write")
    bridge.register_tool("project.delete", capability="project_write")
    bridge.register_tool("project.get", capability="project_read")
    bridge.register_tool("freecad.pad_sketch", capability="cad_author")
    defs = {td.name: td for _server, td in await mcp_tools_from_bridge(bridge)}

    for gated in ("commit_geometry", "record_decision"):
        assert defs[gated].requires_approval is True
    for gated in ("create", "update", "delete"):
        assert defs[gated].requires_approval is True
    assert defs["get"].requires_approval is False
    assert defs["pad_sketch"].requires_approval is False


@pytest.mark.asyncio
async def test_mcp_tools_from_bridge_surfaces_input_schema() -> None:
    """The tool's real parameter schema reaches the model (MET-548 fix).

    Previously every tool advertised a bare ``{"type": "object"}``, so the
    model never learned which arguments were required and calls to tools with
    required params (session.start, freecad.*, calculix.*) failed.
    """
    schema = {
        "type": "object",
        "properties": {
            "agent_code": {"type": "string", "minLength": 1},
            "task_type": {"type": "string", "minLength": 1},
        },
        "required": ["agent_code", "task_type"],
    }
    bridge = InMemoryMcpBridge()
    bridge.register_tool(
        "session.start", capability="session_capture", name="Start", input_schema=schema
    )
    defs = await mcp_tools_from_bridge(bridge)
    _, td = defs[0]
    assert td.input_schema == schema
    assert td.input_schema["required"] == ["agent_code", "task_type"]


@pytest.mark.asyncio
async def test_mcp_tools_from_bridge_falls_back_when_no_schema() -> None:
    """Tools without a usable object schema keep the permissive fallback."""
    bridge = InMemoryMcpBridge()
    bridge.register_tool("twin.get_node", capability="twin_inspect", name="Get Node")
    defs = await mcp_tools_from_bridge(bridge)
    _, td = defs[0]
    assert td.input_schema == {"type": "object"}


@pytest.mark.asyncio
async def test_chat_harness_invokes_mcp_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This test scripts ReAct-protocol replies — pin the ReAct path
    # explicitly (MET-575: the path now follows the resolved provider,
    # and the all-defaults resolution is anthropic → native).
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")
    bridge = InMemoryMcpBridge()
    bridge.register_tool("twin.query_node", capability="twin", name="Query Node")
    bridge.register_tool_response("twin.query_node", {"node": "N1", "mass_g": 42})

    # Scripted model: first call requests the tool, second returns a final answer.
    calls = {"n": 0}

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            payload = {
                "thought": "look it up",
                "tool": "mcp_twin_query_node",
                "arguments": {"id": "N1"},
            }
            return {"text": json.dumps(payload), "model": spec.model}
        return {"text": '{"thought": "done", "final": "Mass is 42 g"}', "model": spec.model}

    out = await run_chat_turn(
        "What is the mass of N1?",
        invoke=invoke,
        max_steps=3,
        credentials=CredentialStore(tmp_path / "c.json"),
        mcp_bridge=bridge,
    )
    assert out == "Mass is 42 g"
    assert calls["n"] == 2  # tool step + final step — the tool was actually driven


class TestChatHarnessApprovalWiring:
    """Production-harness audit follow-up: `_build_context` shares the SAME
    process-level approval store `get_approval_store()` returns, so a
    separate request (simulated here, the real one is the REST endpoint in
    `tool_approvals.py`) can resolve a tool call this turn paused on."""

    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        reset_approval_store()
        yield
        reset_approval_store()

    @pytest.mark.asyncio
    async def test_requires_approval_tool_resolves_via_the_shared_store(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("twin.commit_geometry", capability="twin_write")
        bridge.register_tool_response("twin.commit_geometry", {"committed": True})

        ctx = await _build_context("thread-1", CredentialStore(), bridge)
        # Speed the poll up for the test — no real wall-clock wait needed to
        # prove the wiring, same seam HarnessRuntime's own tests use.
        approved_ids: list[str] = []

        async def fast_sleep(seconds: float) -> None:
            if not approved_ids:
                run = get_approval_store().list()[0]
                get_approval_store().submit_approval(run.id, ApprovalDecision.APPROVE)
                approved_ids.append(run.id)

        ctx.runtime.approval_sleep = fast_sleep
        result = await ctx.runtime.call_tool("mcp_twin_commit_geometry", {})
        assert result == {"committed": True}
        # The SAME store `_build_context` wired in is the one that resolved it.
        assert get_approval_store().list()[0].status.value == "running"

    @pytest.mark.asyncio
    async def test_requires_approval_tool_denied_via_the_shared_store(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("twin.commit_geometry", capability="twin_write")
        bridge.register_tool_response("twin.commit_geometry", {"committed": True})

        ctx = await _build_context("thread-1", CredentialStore(), bridge)

        async def fast_sleep(seconds: float) -> None:
            run = get_approval_store().list()[0]
            get_approval_store().submit_approval(run.id, ApprovalDecision.REJECT)

        ctx.runtime.approval_sleep = fast_sleep
        with pytest.raises(ApprovalDeniedError, match="rejected"):
            await ctx.runtime.call_tool("mcp_twin_commit_geometry", {})
