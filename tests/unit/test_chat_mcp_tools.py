"""Chat harness can drive MCP tools via the bridge (MET-548). Network-free."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_gateway.chat.harness_backend import mcp_tools_from_bridge, run_chat_turn
from orchestrator.harness.providers import CredentialStore, ProviderSpec
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
async def test_chat_harness_invokes_mcp_tool(tmp_path: Path) -> None:
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
