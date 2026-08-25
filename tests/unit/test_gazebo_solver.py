"""Unit tests for Gazebo solver and result parser (MET-633).

No real `gz` binary required -- subprocess calls are not reached by these
tests (validation errors occur first) or are exercised indirectly via the
adapter-level mocks in test_gazebo_adapter.py.
"""

from __future__ import annotations

import json

import pytest

from tool_registry.tools.gazebo.result_parser import (
    StatsParseError,
    parse_stats_file,
)
from tool_registry.tools.gazebo.solver import (
    MAX_SIM_TIMEOUT,
    SolverError,
    SolverTimeoutError,
)

# ---------------------------------------------------------------------------
# 1. Solver error classes
# ---------------------------------------------------------------------------


class TestSolverErrors:
    """Solver error hierarchy."""

    def test_solver_error_fields(self) -> None:
        err = SolverError("failed", returncode=1, stderr="bad world file")
        assert err.returncode == 1
        assert err.stderr == "bad world file"
        assert "failed" in str(err)

    def test_timeout_is_solver_error(self) -> None:
        err = SolverTimeoutError("timed out")
        assert isinstance(err, SolverError)

    def test_max_timeout_constant(self) -> None:
        assert MAX_SIM_TIMEOUT == 300


# ---------------------------------------------------------------------------
# 2. run_simulation validation (without running subprocess)
# ---------------------------------------------------------------------------


class TestRunSimulationValidation:
    """Input validation for run_simulation (without running subprocess)."""

    async def test_missing_world_file(self) -> None:
        from tool_registry.tools.gazebo.solver import run_simulation

        with pytest.raises(FileNotFoundError, match="World file not found"):
            await run_simulation("/nonexistent/world.sdf", 1.0)

    async def test_wrong_extension(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from tool_registry.tools.gazebo.solver import run_simulation

        bad_file = tmp_path / "world.txt"
        bad_file.write_text("dummy")
        with pytest.raises(ValueError, match="World file must be one of"):
            await run_simulation(str(bad_file), 1.0)

    async def test_non_positive_duration(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from tool_registry.tools.gazebo.solver import run_simulation

        world_file = tmp_path / "world.sdf"
        world_file.write_text("<sdf><world name='w'></world></sdf>")
        with pytest.raises(ValueError, match="duration_s must be positive"):
            await run_simulation(str(world_file), 0.0)


# ---------------------------------------------------------------------------
# 3. Stats file parsing
# ---------------------------------------------------------------------------


class TestParseStatsFile:
    def test_valid_stats_file(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        stats_path = tmp_path / "world.stats.json"
        stats_path.write_text(
            json.dumps(
                {
                    "sim_time_s": 1.0,
                    "real_time_s": 1.05,
                    "iterations": 1000,
                    "model_poses": {"box": [0.0, 0.0, 0.5, 0.0, 0.0, 0.0]},
                }
            )
        )
        result = parse_stats_file(str(stats_path))
        assert result["sim_time_s"] == 1.0
        assert result["iterations"] == 1000
        assert result["model_poses"]["box"] == [0.0, 0.0, 0.5, 0.0, 0.0, 0.0]

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            parse_stats_file("/nonexistent/world.stats.json")

    def test_invalid_json_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        stats_path = tmp_path / "world.stats.json"
        stats_path.write_text("not valid json{{{")
        with pytest.raises(StatsParseError, match="Invalid JSON"):
            parse_stats_file(str(stats_path))

    def test_missing_required_keys_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        stats_path = tmp_path / "world.stats.json"
        stats_path.write_text(json.dumps({"sim_time_s": 1.0}))
        with pytest.raises(StatsParseError, match="missing required keys"):
            parse_stats_file(str(stats_path))

    def test_defaults_model_poses_to_empty_dict(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        stats_path = tmp_path / "world.stats.json"
        stats_path.write_text(
            json.dumps({"sim_time_s": 1.0, "real_time_s": 1.0, "iterations": 100})
        )
        result = parse_stats_file(str(stats_path))
        assert result["model_poses"] == {}
