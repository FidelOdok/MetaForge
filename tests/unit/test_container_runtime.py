"""Unit tests for container runtime (Docker and InMemory)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tool_registry.container_runtime import (
    ContainerConfig,
    ContainerExecutionEngine,
    DockerRuntime,
    ExecutionResult,
    InMemoryRuntime,
)

# --- ContainerConfig tests ---


class TestContainerConfig:
    """Tests for ContainerConfig model."""

    def test_defaults(self):
        config = ContainerConfig(image="metaforge/test")
        assert config.tag == "latest"
        assert config.memory_limit == "512m"
        assert config.cpu_limit == 1.0
        assert config.timeout_seconds == 300
        assert config.work_dir == "/workspace"
        assert config.env == {}
        assert config.volumes == {}

    def test_full_image(self):
        config = ContainerConfig(image="metaforge/cadquery-adapter", tag="0.1.0")
        assert config.full_image == "metaforge/cadquery-adapter:0.1.0"

    def test_full_image_default_tag(self):
        config = ContainerConfig(image="metaforge/test")
        assert config.full_image == "metaforge/test:latest"

    def test_custom_config(self):
        config = ContainerConfig(
            image="metaforge/freecad",
            tag="2.0",
            memory_limit="2g",
            cpu_limit=2.0,
            timeout_seconds=600,
            work_dir="/data",
            env={"FREECAD_BINARY": "/usr/bin/freecadcmd"},
            volumes={"/host/models": "/workspace/models"},
        )
        assert config.memory_limit == "2g"
        assert config.cpu_limit == 2.0
        assert config.env["FREECAD_BINARY"] == "/usr/bin/freecadcmd"
        assert config.volumes["/host/models"] == "/workspace/models"


class TestExecutionResult:
    """Tests for ExecutionResult model."""

    def test_success_result(self):
        result = ExecutionResult(success=True, exit_code=0, stdout="output")
        assert result.success is True
        assert result.exit_code == 0
        assert result.stderr == ""
        assert result.work_products == []

    def test_failure_result(self):
        result = ExecutionResult(
            success=False,
            exit_code=1,
            stderr="error occurred",
            duration_seconds=5.2,
        )
        assert result.success is False
        assert result.exit_code == 1


# --- InMemoryRuntime tests ---


class TestInMemoryRuntime:
    """Tests for InMemoryRuntime."""

    async def test_is_available(self):
        runtime = InMemoryRuntime()
        assert await runtime.is_available() is True

    async def test_default_result(self):
        runtime = InMemoryRuntime()
        config = ContainerConfig(image="any-image")
        result = await runtime.run(config, ["echo", "hello"])
        assert result.success is True
        assert result.exit_code == 0

    async def test_registered_result(self):
        runtime = InMemoryRuntime()
        expected = ExecutionResult(
            success=False, exit_code=1, stderr="segfault"
        )
        runtime.register_result("my-image", expected)

        config = ContainerConfig(image="my-image")
        result = await runtime.run(config, ["run"])
        assert result.success is False
        assert result.stderr == "segfault"

    async def test_run_history(self):
        runtime = InMemoryRuntime()
        config = ContainerConfig(image="test")
        await runtime.run(config, ["cmd1"])
        await runtime.run(config, ["cmd2"])
        assert len(runtime._run_history) == 2
        assert runtime._run_history[0][1] == ["cmd1"]
        assert runtime._run_history[1][1] == ["cmd2"]

    async def test_cleanup_tracking(self):
        runtime = InMemoryRuntime()
        await runtime.cleanup("container-abc")
        await runtime.cleanup("container-xyz")
        assert runtime._cleanup_calls == ["container-abc", "container-xyz"]


# --- DockerRuntime tests (mocked, no real Docker needed) ---


class TestDockerRuntime:
    """Tests for DockerRuntime with mocked Docker SDK."""

    async def test_is_available_no_sdk(self):
        runtime = DockerRuntime()
        with patch("tool_registry.container_runtime.HAS_DOCKER_SDK", False):
            assert await runtime.is_available() is False

    async def test_is_available_with_sdk(self):
        runtime = DockerRuntime()
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        runtime._client = mock_client

        with patch("tool_registry.container_runtime.HAS_DOCKER_SDK", True):
            result = await runtime.is_available()
        assert result is True

    async def test_is_available_daemon_down(self):
        runtime = DockerRuntime()
        mock_client = MagicMock()
        mock_client.ping.side_effect = Exception("connection refused")
        runtime._client = mock_client

        with patch("tool_registry.container_runtime.HAS_DOCKER_SDK", True):
            result = await runtime.is_available()
        assert result is False

    async def test_run_no_sdk_raises(self):
        runtime = DockerRuntime()
        config = ContainerConfig(image="test")

        with patch("tool_registry.container_runtime.HAS_DOCKER_SDK", False):
            with pytest.raises(RuntimeError, match="Docker SDK"):
                await runtime.run(config, ["echo", "test"])

    async def test_run_success(self):
        runtime = DockerRuntime()

        # Mock Docker client
        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.side_effect = [
            b'{"result": "ok"}\n',  # stdout
            b"",  # stderr
        ]
        mock_container.remove.return_value = None

        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_client.images.get.return_value = MagicMock()  # Image exists
        runtime._client = mock_client

        config = ContainerConfig(image="metaforge/test", tag="0.1.0")

        with patch("tool_registry.container_runtime.HAS_DOCKER_SDK", True):
            result = await runtime.run(config, ["python", "entrypoint.py"])

        assert result.success is True
        assert result.exit_code == 0
        assert "ok" in result.stdout
        mock_container.remove.assert_called_once_with(force=True)

    async def test_run_failure_exit_code(self):
        runtime = DockerRuntime()

        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 1}
        mock_container.logs.side_effect = [b"", b"error: segfault"]
        mock_container.remove.return_value = None

        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_client.images.get.return_value = MagicMock()
        runtime._client = mock_client

        config = ContainerConfig(image="metaforge/test")

        with patch("tool_registry.container_runtime.HAS_DOCKER_SDK", True):
            result = await runtime.run(config, ["run"])

        assert result.success is False
        assert result.exit_code == 1
        assert "segfault" in result.stderr

    async def test_run_pulls_image_if_missing(self):
        runtime = DockerRuntime()

        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.side_effect = [b"output", b""]
        mock_container.remove.return_value = None

        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_client.images.get.side_effect = Exception("not found")
        mock_client.images.pull.return_value = MagicMock()
        runtime._client = mock_client

        config = ContainerConfig(image="metaforge/cadquery", tag="0.1.0")

        with patch("tool_registry.container_runtime.HAS_DOCKER_SDK", True):
            result = await runtime.run(config, ["run"])

        assert result.success is True
        mock_client.images.pull.assert_called_once_with(
            "metaforge/cadquery", tag="0.1.0"
        )

    async def test_build_container_kwargs(self):
        runtime = DockerRuntime()
        config = ContainerConfig(
            image="metaforge/test",
            tag="1.0",
            memory_limit="1g",
            cpu_limit=2.0,
            work_dir="/data",
            env={"FOO": "bar"},
            volumes={"/host/path": "/container/path"},
        )

        kwargs = runtime._build_container_kwargs(config, ["python", "main.py"])

        assert kwargs["image"] == "metaforge/test:1.0"
        assert kwargs["command"] == ["python", "main.py"]
        assert kwargs["detach"] is True
        assert kwargs["mem_limit"] == "1g"
        assert kwargs["nano_cpus"] == 2_000_000_000
        assert kwargs["working_dir"] == "/data"
        assert kwargs["environment"]["FOO"] == "bar"
        assert kwargs["environment"]["METAFORGE_ENV"] == "docker"
        assert kwargs["network_mode"] == "none"
        assert "/host/path" in kwargs["volumes"]

    async def test_cleanup_success(self):
        runtime = DockerRuntime()
        mock_container = MagicMock()
        mock_container.remove.return_value = None

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        runtime._client = mock_client

        with patch("tool_registry.container_runtime.HAS_DOCKER_SDK", True):
            await runtime.cleanup("test-container-id")

        mock_container.remove.assert_called_once_with(force=True)

    async def test_cleanup_no_sdk(self):
        runtime = DockerRuntime()
        with patch("tool_registry.container_runtime.HAS_DOCKER_SDK", False):
            await runtime.cleanup("test-id")  # Should not raise


# --- ContainerExecutionEngine tests ---


class TestContainerExecutionEngine:
    """Tests for ContainerExecutionEngine."""

    async def test_execute_success(self):
        runtime = InMemoryRuntime()
        engine = ContainerExecutionEngine(runtime)
        config = ContainerConfig(image="test")

        result = await engine.execute("test-tool", config, ["run"])

        assert result.success is True
        assert len(runtime._run_history) == 1

    async def test_execute_unavailable_runtime(self):
        runtime = InMemoryRuntime()
        # Override is_available to return False
        runtime.is_available = AsyncMock(return_value=False)  # type: ignore[method-assign]
        engine = ContainerExecutionEngine(runtime)
        config = ContainerConfig(image="test")

        with pytest.raises(RuntimeError, match="not available"):
            await engine.execute("test-tool", config, ["run"])

    async def test_execute_timeout(self):
        async def slow_run(config, command, input_data=None):
            await asyncio.sleep(10)
            return ExecutionResult(success=True, exit_code=0)

        runtime = InMemoryRuntime()
        runtime.run = slow_run  # type: ignore[method-assign]
        engine = ContainerExecutionEngine(runtime)
        config = ContainerConfig(image="test", timeout_seconds=1)

        with pytest.raises(TimeoutError):
            await engine.execute("test-tool", config, ["run"])

    async def test_execute_with_retry_succeeds_first(self):
        runtime = InMemoryRuntime()
        engine = ContainerExecutionEngine(runtime)
        config = ContainerConfig(image="test")

        result = await engine.execute_with_retry(
            "test-tool", config, ["run"], max_retries=2
        )

        assert result.success is True
        assert len(runtime._run_history) == 1  # Only 1 attempt needed

    async def test_execute_with_retry_succeeds_on_retry(self):
        runtime = InMemoryRuntime()
        call_count = 0

        async def flaky_run(config, command, input_data=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return ExecutionResult(success=False, exit_code=1, stderr="flaky")
            return ExecutionResult(success=True, exit_code=0)

        runtime.run = flaky_run  # type: ignore[method-assign]
        engine = ContainerExecutionEngine(runtime)
        config = ContainerConfig(image="test")

        result = await engine.execute_with_retry(
            "test-tool", config, ["run"], max_retries=2
        )

        assert result.success is True
        assert call_count == 3

    async def test_execute_with_retry_exhausted(self):
        runtime = InMemoryRuntime()
        runtime.register_result(
            "failing", ExecutionResult(success=False, exit_code=1, stderr="always fails")
        )
        engine = ContainerExecutionEngine(runtime)
        config = ContainerConfig(image="failing")

        result = await engine.execute_with_retry(
            "test-tool", config, ["run"], max_retries=1
        )

        assert result.success is False
        assert len(runtime._run_history) == 2  # 1 + 1 retry
