"""Unit tests for Isaac Sim container dispatch (MET-635/636).

Uses tool_registry.container_runtime.InMemoryRuntime -- the repo's
documented convention ("Use InMemoryRuntime in unit tests -- never
require Docker", tool_registry/CLAUDE.md) -- exercised here for real,
since calculix/gazebo don't go through ContainerRuntime at all.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tool_registry.compute_providers import RemoteVolumesUnsupportedError
from tool_registry.container_runtime import ExecutionResult, InMemoryRuntime
from tool_registry.tools.isaac_sim.dispatch import (
    IsaacSimDispatchError,
    render_scene,
    run_physics,
)

# ---------------------------------------------------------------------------
# 1. Validation (no runtime dispatch reached)
# ---------------------------------------------------------------------------


class TestRunPhysicsValidation:
    async def test_missing_command_raises(self) -> None:
        with pytest.raises(ValueError, match="command is required"):
            await run_physics(command=[], accept_eula=True)

    async def test_eula_not_accepted_raises(self) -> None:
        with pytest.raises(IsaacSimDispatchError, match="accept_eula must be explicitly"):
            await run_physics(command=["./python.sh", "run.py"], accept_eula=False)

    async def test_missing_usd_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="USD file not found"):
            await run_physics(
                command=["./python.sh", "run.py"],
                usd_path="/nonexistent/scene.usd",
                accept_eula=True,
            )


class TestRenderSceneValidation:
    async def test_missing_command_raises(self) -> None:
        with pytest.raises(ValueError, match="command is required"):
            await render_scene(command=[], accept_eula=True)

    async def test_eula_not_accepted_raises(self) -> None:
        with pytest.raises(IsaacSimDispatchError, match="accept_eula must be explicitly"):
            await render_scene(command=["./python.sh", "render.py"], accept_eula=False)


# ---------------------------------------------------------------------------
# 2. Real dispatch via InMemoryRuntime
# ---------------------------------------------------------------------------


class TestRunPhysicsDispatch:
    async def test_success_via_in_memory_runtime(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        runtime = InMemoryRuntime()
        runtime.register_result(
            "nvcr.io/nvidia/isaac-sim",
            ExecutionResult(
                success=True, exit_code=0, stdout='{"steps": 1000}', duration_seconds=1.5
            ),
        )

        usd_file = tmp_path / "scene.usd"
        usd_file.write_text("#usda 1.0")

        with patch("tool_registry.tools.isaac_sim.dispatch.resolve_runtime", return_value=runtime):
            result = await run_physics(
                command=["./python.sh", "run_physics.py", "--usd", "/workspace/input/scene.usd"],
                usd_path=str(usd_file),
                accept_eula=True,
            )

        assert result["success"] is True
        assert result["exit_code"] == 0
        assert "steps" in result["stdout"]

        # Verify the volume mount was actually constructed correctly.
        config, command = runtime._run_history[0]
        assert config.volumes == {str(usd_file.parent): "/workspace/input"}
        assert config.env == {"ACCEPT_EULA": "Y"}
        assert command == [
            "./python.sh",
            "run_physics.py",
            "--usd",
            "/workspace/input/scene.usd",
        ]

    async def test_no_usd_path_means_no_volumes(self) -> None:
        runtime = InMemoryRuntime()

        with patch("tool_registry.tools.isaac_sim.dispatch.resolve_runtime", return_value=runtime):
            await run_physics(command=["./python.sh", "run_physics.py"], accept_eula=True)

        config, _ = runtime._run_history[0]
        assert config.volumes == {}

    async def test_failure_propagates(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        runtime = InMemoryRuntime()
        runtime.register_result(
            "nvcr.io/nvidia/isaac-sim",
            ExecutionResult(success=False, exit_code=1, stderr="CUDA error", duration_seconds=0.5),
        )

        with patch("tool_registry.tools.isaac_sim.dispatch.resolve_runtime", return_value=runtime):
            result = await run_physics(command=["./python.sh", "run_physics.py"], accept_eula=True)

        assert result["success"] is False
        assert "CUDA error" in result["stderr"]


class TestRemoteProviderVolumesUnsupported:
    """Verify the architectural claim in the module docstring for real,
    against the actual RunPodRuntime -- not mocked -- since the volume
    check fires before any HTTP call is made.
    """

    async def test_remote_provider_with_usd_path_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        usd_file = tmp_path / "scene.usd"
        usd_file.write_text("#usda 1.0")

        with pytest.raises(RemoteVolumesUnsupportedError):
            await run_physics(
                command=["./python.sh", "run_physics.py"],
                usd_path=str(usd_file),
                compute_provider="runpod",
                accept_eula=True,
            )


class TestRenderSceneDispatch:
    async def test_success_via_in_memory_runtime(self) -> None:
        runtime = InMemoryRuntime()
        runtime.register_result(
            "nvcr.io/nvidia/isaac-sim",
            ExecutionResult(success=True, exit_code=0, duration_seconds=3.2),
        )

        with patch("tool_registry.tools.isaac_sim.dispatch.resolve_runtime", return_value=runtime):
            result = await render_scene(command=["./python.sh", "render.py"], accept_eula=True)

        assert result["success"] is True
        assert result["duration_seconds"] == 3.2
