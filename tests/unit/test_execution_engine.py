"""Tests for the container-based tool execution engine (MET-25)."""

from __future__ import annotations

import asyncio

import pytest

from tool_registry.container_runtime import (
    ContainerConfig,
    ContainerExecutionEngine,
    ContainerRuntime,
    DockerRuntime,
    ExecutionResult,
    InMemoryRuntime,
)

# ---------------------------------------------------------------------------
# ContainerConfig model tests
# ---------------------------------------------------------------------------


class TestContainerConfig:
    def test_creation_with_defaults(self):
        config = ContainerConfig(image="metaforge/calculix")
        assert config.image == "metaforge/calculix"
        assert config.tag == "latest"
        assert config.memory_limit == "512m"
        assert config.cpu_limit == 1.0
        assert config.timeout_seconds == 300
        assert config.work_dir == "/workspace"
        assert config.env == {}
        assert config.volumes == {}

    def test_creation_with_custom_values(self):
        config = ContainerConfig(
            image="metaforge/freecad",
            tag="v2.1",
            memory_limit="2g",
            cpu_limit=4.0,
            timeout_seconds=600,
            work_dir="/data",
            env={"DISPLAY": ":0"},
            volumes={"/tmp/input": "/workspace/input"},
        )
        assert config.image == "metaforge/freecad"
        assert config.tag == "v2.1"
        assert config.memory_limit == "2g"
        assert config.cpu_limit == 4.0
        assert config.timeout_seconds == 600
        assert config.work_dir == "/data"
        assert config.env == {"DISPLAY": ":0"}
        assert config.volumes == {"/tmp/input": "/workspace/input"}

    def test_full_image_property(self):
        config = ContainerConfig(image="metaforge/calculix", tag="v1.0")
        assert config.full_image == "metaforge/calculix:v1.0"

    def test_full_image_default_tag(self):
        config = ContainerConfig(image="metaforge/calculix")
        assert config.full_image == "metaforge/calculix:latest"

    def test_config_validation_cpu_limit(self):
        config = ContainerConfig(image="test", cpu_limit=0.5)
        assert config.cpu_limit == 0.5

    def test_config_env_dict(self):
        config = ContainerConfig(
            image="test",
            env={"KEY1": "val1", "KEY2": "val2"},
        )
        assert len(config.env) == 2
        assert config.env["KEY1"] == "val1"

    def test_config_volumes_dict(self):
        config = ContainerConfig(
            image="test",
            volumes={"/host/a": "/container/a", "/host/b": "/container/b"},
        )
        assert len(config.volumes) == 2


# ---------------------------------------------------------------------------
# ExecutionResult model tests
# ---------------------------------------------------------------------------


class TestExecutionResult:
    def test_success_result(self):
        result = ExecutionResult(
            success=True,
            exit_code=0,
            stdout="output data",
            duration_seconds=1.5,
        )
        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "output data"
        assert result.duration_seconds == 1.5

    def test_failure_result(self):
        result = ExecutionResult(
            success=False,
            exit_code=1,
            stderr="error message",
            duration_seconds=0.3,
        )
        assert result.success is False
        assert result.exit_code == 1
        assert result.stderr == "error message"

    def test_result_with_artifacts(self):
        result = ExecutionResult(
            success=True,
            exit_code=0,
            work_products=["/output/result.vtk", "/output/stress.dat"],
        )
        assert len(result.work_products) == 2
        assert "/output/result.vtk" in result.work_products

    def test_result_defaults(self):
        result = ExecutionResult(success=True, exit_code=0)
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.duration_seconds == 0.0
        assert result.work_products == []


# ---------------------------------------------------------------------------
# InMemoryRuntime tests
# ---------------------------------------------------------------------------


class TestInMemoryRuntime:
    @pytest.fixture
    def runtime(self):
        return InMemoryRuntime()

    async def test_default_success_result(self, runtime):
        config = ContainerConfig(image="test/image")
        result = await runtime.run(config, ["echo", "hello"])
        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "mock output"
        assert result.duration_seconds == 0.1

    async def test_registered_result(self, runtime):
        custom = ExecutionResult(
            success=False,
            exit_code=42,
            stderr="custom error",
            duration_seconds=5.0,
        )
        runtime.register_result("custom/image", custom)

        config = ContainerConfig(image="custom/image")
        result = await runtime.run(config, ["run"])
        assert result.success is False
        assert result.exit_code == 42
        assert result.stderr == "custom error"

    async def test_unregistered_image_returns_default(self, runtime):
        runtime.register_result("other/image", ExecutionResult(success=False, exit_code=1))
        config = ContainerConfig(image="unregistered/image")
        result = await runtime.run(config, ["run"])
        assert result.success is True  # default

    async def test_is_available_returns_true(self, runtime):
        assert await runtime.is_available() is True

    async def test_cleanup_records_call(self, runtime):
        await runtime.cleanup("container-123")
        assert "container-123" in runtime._cleanup_calls

    async def test_run_history_tracked(self, runtime):
        config = ContainerConfig(image="test/image")
        await runtime.run(config, ["cmd1"])
        await runtime.run(config, ["cmd2"])
        assert len(runtime._run_history) == 2
        assert runtime._run_history[0][1] == ["cmd1"]
        assert runtime._run_history[1][1] == ["cmd2"]

    async def test_run_with_input_data(self, runtime):
        config = ContainerConfig(image="test/image")
        result = await runtime.run(config, ["run"], input_data={"key": "value"})
        assert result.success is True


# ---------------------------------------------------------------------------
# DockerRuntime tests
# ---------------------------------------------------------------------------


class TestDockerRuntime:
    @pytest.fixture
    def runtime(self):
        return DockerRuntime()

    async def test_is_available_returns_false(self, runtime):
        """Docker runtime should report unavailable in CI (no Docker daemon)."""
        assert await runtime.is_available() is False

    async def test_run_raises_not_implemented(self, runtime):
        config = ContainerConfig(image="test/image")
        with pytest.raises(NotImplementedError, match="docker SDK"):
            await runtime.run(config, ["echo", "hello"])

    async def test_cleanup_no_error(self, runtime):
        """Cleanup should not raise even without Docker."""
        await runtime.cleanup("nonexistent-container")


# ---------------------------------------------------------------------------
# ContainerExecutionEngine tests
# ---------------------------------------------------------------------------


class TestContainerExecutionEngine:
    @pytest.fixture
    def runtime(self):
        return InMemoryRuntime()

    @pytest.fixture
    def engine(self, runtime):
        return ContainerExecutionEngine(runtime)

    @pytest.fixture
    def config(self):
        return ContainerConfig(
            image="metaforge/calculix",
            timeout_seconds=10,
        )

    async def test_execute_success(self, engine, config):
        result = await engine.execute("calculix", config, ["run_fea"])
        assert result.success is True
        assert result.exit_code == 0

    async def test_execute_with_registered_result(self, runtime, engine):
        custom = ExecutionResult(
            success=True,
            exit_code=0,
            stdout="fea output",
            duration_seconds=12.5,
            work_products=["/output/stress.vtk"],
        )
        runtime.register_result("metaforge/calculix", custom)
        config = ContainerConfig(image="metaforge/calculix")
        result = await engine.execute("calculix", config, ["run_fea"])
        assert result.stdout == "fea output"
        assert len(result.work_products) == 1

    async def test_execute_failure_result(self, runtime, engine):
        runtime.register_result(
            "metaforge/fail",
            ExecutionResult(success=False, exit_code=1, stderr="solver crashed"),
        )
        config = ContainerConfig(image="metaforge/fail")
        result = await engine.execute("fail-tool", config, ["run"])
        assert result.success is False
        assert result.exit_code == 1

    async def test_execute_runtime_unavailable(self):
        """Engine should raise RuntimeError when runtime is unavailable."""
        runtime = DockerRuntime()
        engine = ContainerExecutionEngine(runtime)
        config = ContainerConfig(image="test/image")
        with pytest.raises(RuntimeError, match="not available"):
            await engine.execute("test-tool", config, ["run"])

    async def test_execute_timeout(self):
        """Engine should raise TimeoutError when execution exceeds timeout."""

        class SlowRuntime(ContainerRuntime):
            async def run(self, config, command, input_data=None):
                await asyncio.sleep(10)
                return ExecutionResult(success=True, exit_code=0)

            async def cleanup(self, container_id):
                pass

            async def is_available(self):
                return True

        engine = ContainerExecutionEngine(SlowRuntime())
        config = ContainerConfig(image="slow/image", timeout_seconds=1)
        with pytest.raises(TimeoutError):
            await engine.execute("slow-tool", config, ["run"])

    async def test_execute_with_retry_success_first_try(self, engine, config):
        result = await engine.execute_with_retry("calculix", config, ["run"])
        assert result.success is True

    async def test_execute_with_retry_retries_on_failure(self, runtime, engine):
        """Verify retry logic: fail twice then succeed."""
        call_count = 0

        async def run_with_retries(config, command, input_data=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return ExecutionResult(success=False, exit_code=1, stderr="transient")
            return ExecutionResult(success=True, exit_code=0, stdout="recovered")

        runtime.run = run_with_retries  # type: ignore[assignment]
        config = ContainerConfig(image="metaforge/retry")
        result = await engine.execute_with_retry("retry-tool", config, ["run"], max_retries=2)
        assert result.success is True
        assert call_count == 3

    async def test_execute_with_retry_exhausts_retries(self, runtime, engine):
        """All retries fail — should return last failure result."""
        runtime.register_result(
            "metaforge/always-fail",
            ExecutionResult(success=False, exit_code=1, stderr="permanent failure"),
        )
        config = ContainerConfig(image="metaforge/always-fail")
        result = await engine.execute_with_retry("failing-tool", config, ["run"], max_retries=2)
        assert result.success is False
        assert result.exit_code == 1

    async def test_execute_with_retry_zero_retries(self, runtime, engine):
        """max_retries=0 means only one attempt."""
        runtime.register_result(
            "metaforge/zero-retry",
            ExecutionResult(success=False, exit_code=1),
        )
        config = ContainerConfig(image="metaforge/zero-retry")
        result = await engine.execute_with_retry("tool", config, ["run"], max_retries=0)
        assert result.success is False

    async def test_execute_logging(self, engine, config):
        """Verify logging does not crash during execution."""
        # structlog emits structured events — verify no exception
        await engine.execute("logged-tool", config, ["run"])

    async def test_execute_passes_input_data(self, runtime, engine, config):
        """Verify input_data is passed through to runtime."""
        called_with_data = {}

        async def capture_run(cfg, cmd, input_data=None):
            called_with_data["input"] = input_data
            return ExecutionResult(success=True, exit_code=0)

        runtime.run = capture_run  # type: ignore[assignment]
        await engine.execute("tool", config, ["run"], input_data={"mesh": "data"})
        assert called_with_data["input"] == {"mesh": "data"}

    async def test_resource_limit_propagation(self, runtime, engine):
        """Verify resource limits from ContainerConfig reach the runtime."""
        config = ContainerConfig(
            image="metaforge/limited",
            memory_limit="1g",
            cpu_limit=2.0,
        )
        await engine.execute("limited-tool", config, ["run"])
        # Verify config was passed to runtime
        assert len(runtime._run_history) == 1
        passed_config = runtime._run_history[0][0]
        assert passed_config.memory_limit == "1g"
        assert passed_config.cpu_limit == 2.0
