"""Tests for the Gazebo MCP tool adapter (MET-633)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tool_registry.tools.gazebo.adapter import GazeboServer
from tool_registry.tools.gazebo.config import GazeboConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server() -> GazeboServer:
    """Bare Gazebo server (no mocks on solver methods)."""
    return GazeboServer()


@pytest.fixture()
def server_with_mocks() -> GazeboServer:
    """Server with mocked solver methods for testing."""
    s = GazeboServer()
    s._execute_solver = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "sim_time_s": 1.0,
            "wall_time_s": 1.05,
            "iterations": 1000,
            "result_files": ["/tmp/gazebo/world.stats.json"],
            "results": {
                "sim_time_s": 1.0,
                "real_time_s": 1.05,
                "iterations": 1000,
                "model_poses": {"box": [0.0, 0.0, 0.5, 0.0, 0.0, 0.0]},
            },
        }
    )
    s._validate_world_file = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "valid": True,
            "model_count": 2,
            "issues": [],
        }
    )
    return s


# ---------------------------------------------------------------------------
# TestGazeboConfig
# ---------------------------------------------------------------------------


class TestGazeboConfig:
    def test_default_config(self) -> None:
        cfg = GazeboConfig()
        assert cfg.gz_binary == "gz"
        assert cfg.work_dir == "/tmp/gazebo"
        assert cfg.max_sim_time == 300
        assert cfg.headless is True

    def test_custom_config(self) -> None:
        cfg = GazeboConfig(
            gz_binary="/usr/local/bin/gz",
            work_dir="/data/gazebo",
            max_sim_time=120,
            headless=False,
        )
        assert cfg.gz_binary == "/usr/local/bin/gz"
        assert cfg.work_dir == "/data/gazebo"
        assert cfg.max_sim_time == 120
        assert cfg.headless is False


# ---------------------------------------------------------------------------
# TestGazeboServer
# ---------------------------------------------------------------------------


class TestGazeboServer:
    def test_server_registers_three_tools(self, server: GazeboServer) -> None:
        assert len(server.tool_ids) == 3

    def test_tool_ids(self, server: GazeboServer) -> None:
        expected = {
            "gazebo.run_simulation",
            "gazebo.validate_world",
            "gazebo.extract_results",
        }
        assert set(server.tool_ids) == expected

    def test_adapter_id_and_version(self, server: GazeboServer) -> None:
        assert server.adapter_id == "gazebo"
        assert server.version == "0.1.0"

    def test_custom_config_propagated(self) -> None:
        cfg = GazeboConfig(max_sim_time=60)
        s = GazeboServer(config=cfg)
        assert s.config.max_sim_time == 60


# ---------------------------------------------------------------------------
# TestRunSimulation
# ---------------------------------------------------------------------------


class TestRunSimulation:
    async def test_run_simulation_success(self, server_with_mocks: GazeboServer) -> None:
        result = await server_with_mocks.run_simulation(
            {"world_file": "/worlds/cube_drop.sdf", "duration_s": 1.0}
        )
        assert result["sim_time_s"] == 1.0
        assert result["iterations"] == 1000
        assert result["results"]["model_poses"]["box"][2] == 0.5

    async def test_run_simulation_missing_world_file_raises(
        self, server_with_mocks: GazeboServer
    ) -> None:
        with pytest.raises(ValueError, match="world_file is required"):
            await server_with_mocks.run_simulation({"world_file": "", "duration_s": 1.0})

    async def test_run_simulation_missing_duration_raises(
        self, server_with_mocks: GazeboServer
    ) -> None:
        with pytest.raises(ValueError, match="duration_s is required"):
            await server_with_mocks.run_simulation({"world_file": "/worlds/cube_drop.sdf"})


# ---------------------------------------------------------------------------
# TestValidateWorld
# ---------------------------------------------------------------------------


class TestValidateWorld:
    async def test_validate_world_success(self, server_with_mocks: GazeboServer) -> None:
        result = await server_with_mocks.validate_world({"world_file": "/worlds/cube_drop.sdf"})
        assert result["valid"] is True
        assert result["model_count"] == 2
        assert result["issues"] == []

    async def test_validate_world_missing_file_raises(
        self, server_with_mocks: GazeboServer
    ) -> None:
        with pytest.raises(ValueError, match="world_file is required"):
            await server_with_mocks.validate_world({"world_file": ""})


# ---------------------------------------------------------------------------
# TestUnmockedSolverRaisesOnMissingFiles
# ---------------------------------------------------------------------------


class TestUnmockedSolverRaisesOnMissingFiles:
    """Verify that calling solver methods without mocks raises on missing files."""

    async def test_execute_solver_raises(self, server: GazeboServer) -> None:
        with pytest.raises(FileNotFoundError):
            await server._execute_solver("/worlds/does_not_exist.sdf", 1.0)

    async def test_validate_world_file_raises(self, server: GazeboServer) -> None:
        with pytest.raises(FileNotFoundError):
            await server._validate_world_file("/worlds/does_not_exist.sdf")
