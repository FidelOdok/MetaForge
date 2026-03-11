"""Container-based tool execution engine with Docker and in-memory runtimes.

Provides abstraction for running tool adapters in Docker containers with
resource limits, timeout enforcement, and automatic cleanup. The InMemoryRuntime
allows testing without Docker.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any

import structlog
from pydantic import BaseModel, Field


class ContainerConfig(BaseModel):
    """Configuration for a containerized tool execution."""

    image: str
    tag: str = "latest"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    timeout_seconds: int = 300
    work_dir: str = "/workspace"
    env: dict[str, str] = Field(default_factory=dict)
    volumes: dict[str, str] = Field(default_factory=dict)  # host_path -> container_path

    @property
    def full_image(self) -> str:
        """Return image:tag string."""
        return f"{self.image}:{self.tag}"


class ExecutionResult(BaseModel):
    """Result of a container-based tool execution."""

    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    artifacts: list[str] = Field(default_factory=list)  # paths to output files


class ContainerRuntime(ABC):
    """Abstract container runtime interface.

    Implementations handle the actual container lifecycle: pulling images,
    running containers, extracting results, and cleanup.
    """

    @abstractmethod
    async def run(
        self,
        config: ContainerConfig,
        command: list[str],
        input_data: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Run a command in a container with the given configuration."""
        ...

    @abstractmethod
    async def cleanup(self, container_id: str) -> None:
        """Clean up a container by ID (remove container and temporary volumes)."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the runtime is available (e.g., Docker daemon running)."""
        ...


class DockerRuntime(ContainerRuntime):
    """Docker-based container runtime (real implementation).

    Delegates to the Docker SDK or CLI. Raises NotImplementedError until
    docker[asyncio] is installed.
    """

    async def run(
        self,
        config: ContainerConfig,
        command: list[str],
        input_data: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Run a command in a Docker container.

        Raises NotImplementedError until docker SDK is available.
        """
        raise NotImplementedError("Docker runtime requires docker SDK — install docker[asyncio]")

    async def cleanup(self, container_id: str) -> None:
        """Clean up a Docker container."""
        pass

    async def is_available(self) -> bool:
        """Check if Docker daemon is running."""
        return False


class InMemoryRuntime(ContainerRuntime):
    """In-memory runtime for testing without Docker.

    Allows pre-registering results for specific images so tests can
    control execution outcomes.
    """

    def __init__(self) -> None:
        self._results: dict[str, ExecutionResult] = {}
        self._cleanup_calls: list[str] = []
        self._run_history: list[tuple[ContainerConfig, list[str]]] = []

    def register_result(self, image: str, result: ExecutionResult) -> None:
        """Pre-register a result for a given image (for test fixtures)."""
        self._results[image] = result

    async def run(
        self,
        config: ContainerConfig,
        command: list[str],
        input_data: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Return pre-registered result or a default success result."""
        self._run_history.append((config, command))
        if config.image in self._results:
            return self._results[config.image]
        return ExecutionResult(
            success=True,
            exit_code=0,
            stdout="mock output",
            duration_seconds=0.1,
        )

    async def cleanup(self, container_id: str) -> None:
        """Track cleanup calls for test verification."""
        self._cleanup_calls.append(container_id)

    async def is_available(self) -> bool:
        """In-memory runtime is always available."""
        return True


class ContainerExecutionEngine:
    """Orchestrates containerized tool execution with resource limits and cleanup.

    Wraps a ContainerRuntime with logging, timeout enforcement, and retry logic.
    """

    def __init__(self, runtime: ContainerRuntime) -> None:
        self.runtime = runtime
        self.logger = structlog.get_logger()

    async def execute(
        self,
        tool_name: str,
        config: ContainerConfig,
        command: list[str],
        input_data: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute a tool in a container with timeout and resource enforcement.

        Args:
            tool_name: Human-readable name of the tool being executed.
            config: Container configuration (image, limits, etc.).
            command: Command to run inside the container.
            input_data: Optional data to pass into the container.

        Returns:
            ExecutionResult with stdout, stderr, exit code, and artifacts.

        Raises:
            TimeoutError: If execution exceeds config.timeout_seconds.
            RuntimeError: If the runtime is not available.
        """
        if not await self.runtime.is_available():
            raise RuntimeError(f"Container runtime is not available for tool '{tool_name}'")

        self.logger.info(
            "Container execution started",
            tool_name=tool_name,
            image=config.full_image,
            timeout=config.timeout_seconds,
            memory_limit=config.memory_limit,
            cpu_limit=config.cpu_limit,
        )

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self.runtime.run(config, command, input_data),
                timeout=float(config.timeout_seconds),
            )
        except TimeoutError:
            elapsed = time.monotonic() - start
            self.logger.error(
                "Container execution timed out",
                tool_name=tool_name,
                image=config.full_image,
                timeout=config.timeout_seconds,
                elapsed_seconds=round(elapsed, 2),
            )
            raise

        elapsed = time.monotonic() - start

        if result.success:
            self.logger.info(
                "Container execution succeeded",
                tool_name=tool_name,
                image=config.full_image,
                exit_code=result.exit_code,
                duration_seconds=round(elapsed, 3),
                artifact_count=len(result.artifacts),
            )
        else:
            self.logger.warning(
                "Container execution failed",
                tool_name=tool_name,
                image=config.full_image,
                exit_code=result.exit_code,
                duration_seconds=round(elapsed, 3),
                stderr=result.stderr[:500] if result.stderr else "",
            )

        return result

    async def execute_with_retry(
        self,
        tool_name: str,
        config: ContainerConfig,
        command: list[str],
        max_retries: int = 2,
        input_data: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute with automatic retry on transient failures.

        Only retries when the execution result indicates failure (success=False).
        Timeout errors and runtime unavailability are NOT retried.

        Args:
            tool_name: Human-readable name of the tool being executed.
            config: Container configuration (image, limits, etc.).
            command: Command to run inside the container.
            max_retries: Maximum number of retry attempts (default 2).
            input_data: Optional data to pass into the container.

        Returns:
            ExecutionResult from the first successful execution or the final attempt.
        """
        last_result: ExecutionResult | None = None
        total_attempts = 1 + max_retries

        for attempt in range(total_attempts):
            result = await self.execute(tool_name, config, command, input_data)

            if result.success:
                if attempt > 0:
                    self.logger.info(
                        "Container execution succeeded after retry",
                        tool_name=tool_name,
                        attempt=attempt + 1,
                    )
                return result

            last_result = result

            if attempt < total_attempts - 1:
                self.logger.warning(
                    "Container execution failed, retrying",
                    tool_name=tool_name,
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    exit_code=result.exit_code,
                )
            else:
                self.logger.error(
                    "Container execution failed after all retries",
                    tool_name=tool_name,
                    attempts=total_attempts,
                    exit_code=result.exit_code,
                )

        assert last_result is not None  # noqa: S101
        return last_result
