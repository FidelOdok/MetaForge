"""Tests for the Isaac Sim MCP tool adapter (MET-635/636)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tool_registry.tools.isaac_sim.adapter import IsaacSimServer
from tool_registry.tools.isaac_sim.config import IsaacSimConfig
from tool_registry.tools.isaac_sim.dispatch import IsaacSimDispatchError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server() -> IsaacSimServer:
    return IsaacSimServer()


# ---------------------------------------------------------------------------
# TestIsaacSimConfig
# ---------------------------------------------------------------------------


class TestIsaacSimConfig:
    def test_default_config(self) -> None:
        cfg = IsaacSimConfig()
        assert cfg.image == "nvcr.io/nvidia/isaac-sim"
        assert cfg.tag == "6.0.1"
        assert cfg.compute_provider is None
        assert cfg.accept_eula is False

    def test_custom_config(self) -> None:
        cfg = IsaacSimConfig(compute_provider="runpod", timeout_seconds=600)
        assert cfg.compute_provider == "runpod"
        assert cfg.timeout_seconds == 600


# ---------------------------------------------------------------------------
# TestIsaacSimServer
# ---------------------------------------------------------------------------


class TestIsaacSimServer:
    def test_server_registers_two_tools(self, server: IsaacSimServer) -> None:
        assert len(server.tool_ids) == 2

    def test_tool_ids(self, server: IsaacSimServer) -> None:
        assert set(server.tool_ids) == {"isaac_sim.run_physics", "isaac_sim.render_scene"}

    def test_adapter_id_and_version(self, server: IsaacSimServer) -> None:
        assert server.adapter_id == "isaac_sim"
        assert server.version == "0.1.0"


# ---------------------------------------------------------------------------
# TestRunPhysics
# ---------------------------------------------------------------------------


class TestRunPhysics:
    async def test_success(self, server: IsaacSimServer) -> None:
        with patch(
            "tool_registry.tools.isaac_sim.adapter.dispatch_run_physics",
            return_value={
                "success": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "duration_seconds": 1.0,
            },
        ) as mock_dispatch:
            result = await server.run_physics(
                {"command": ["./python.sh", "run.py"], "accept_eula": True}
            )
        assert result["success"] is True
        mock_dispatch.assert_called_once()

    async def test_missing_command_raises(self, server: IsaacSimServer) -> None:
        with pytest.raises(ValueError, match="command is required"):
            await server.run_physics({"accept_eula": True})

    async def test_missing_accept_eula_raises(self, server: IsaacSimServer) -> None:
        with pytest.raises(ValueError, match="accept_eula is required"):
            await server.run_physics({"command": ["./python.sh", "run.py"]})


# ---------------------------------------------------------------------------
# TestRenderScene
# ---------------------------------------------------------------------------


class TestRenderScene:
    async def test_success(self, server: IsaacSimServer) -> None:
        with patch(
            "tool_registry.tools.isaac_sim.adapter.dispatch_render_scene",
            return_value={
                "success": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "duration_seconds": 2.0,
            },
        ) as mock_dispatch:
            result = await server.render_scene(
                {"command": ["./python.sh", "render.py"], "accept_eula": True}
            )
        assert result["duration_seconds"] == 2.0
        mock_dispatch.assert_called_once()

    async def test_missing_command_raises(self, server: IsaacSimServer) -> None:
        with pytest.raises(ValueError, match="command is required"):
            await server.render_scene({"accept_eula": True})


# ---------------------------------------------------------------------------
# TestUnmockedDispatchRaisesOnEulaNotAccepted
# ---------------------------------------------------------------------------


class TestUnmockedDispatchRaisesOnEulaNotAccepted:
    """Verify that calling without mocks raises the real EULA guard."""

    async def test_run_physics_raises(self, server: IsaacSimServer) -> None:
        with pytest.raises(IsaacSimDispatchError):
            await server.run_physics({"command": ["./python.sh", "run.py"], "accept_eula": False})

    async def test_render_scene_raises(self, server: IsaacSimServer) -> None:
        with pytest.raises(IsaacSimDispatchError):
            await server.render_scene(
                {"command": ["./python.sh", "render.py"], "accept_eula": False}
            )
