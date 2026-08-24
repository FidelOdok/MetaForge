"""Unit tests for domain_agents.shared.cad_backend.resolve_cad_backend (MET-630 follow-up)."""

from __future__ import annotations

import pytest

from domain_agents.shared.cad_backend import resolve_cad_backend
from skill_registry.mcp_bridge import InMemoryMcpBridge


async def test_resolves_preferred_backend_when_available():
    mcp = InMemoryMcpBridge()
    mcp.register_tool("cadquery.execute_script", capability="cad_scripting")
    mcp.register_tool("freecad.execute_code", capability="cad_scripting")

    backend, tool_id = await resolve_cad_backend(mcp, "cad_scripting", "freecad")

    assert backend == "freecad"
    assert tool_id == "freecad.execute_code"


async def test_falls_back_when_preferred_unavailable():
    mcp = InMemoryMcpBridge()
    mcp.register_tool("freecad.execute_code", capability="cad_scripting")

    backend, tool_id = await resolve_cad_backend(mcp, "cad_scripting", "cadquery")

    assert backend == "freecad"
    assert tool_id == "freecad.execute_code"


async def test_raises_when_no_backend_registered():
    mcp = InMemoryMcpBridge()

    with pytest.raises(RuntimeError, match="No CAD backend available for capability"):
        await resolve_cad_backend(mcp, "cad_scripting", "cadquery")


async def test_raises_when_capability_registered_under_different_tag():
    mcp = InMemoryMcpBridge()
    mcp.register_tool("cadquery.execute_script", capability="cad_generation")

    with pytest.raises(RuntimeError, match="cad_scripting"):
        await resolve_cad_backend(mcp, "cad_scripting", "cadquery")


async def test_third_backend_picked_up_with_no_code_change():
    """A hypothetical third kernel registering under the same tag is a valid candidate."""
    mcp = InMemoryMcpBridge()
    mcp.register_tool("build123d.execute_script", capability="cad_scripting")

    backend, tool_id = await resolve_cad_backend(mcp, "cad_scripting", "cadquery")

    assert backend == "build123d"
    assert tool_id == "build123d.execute_script"
