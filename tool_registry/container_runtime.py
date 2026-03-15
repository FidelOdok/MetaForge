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

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.container_runtime")


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
    work_products: list[str] = Field(default_factory=list)  # paths to output files


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


# Conditional Docker SDK import
try:
    import docker  # type: ignore[import-untyped]

    HAS_DOCKER_SDK = True
except ImportError:
    docker = None  # type: ignore[assignment]
    HAS_DOCKER_SDK = False


class DockerRuntime(ContainerRuntime):
    """Docker-based container runtime using the Docker SDK.

    Manages the full container lifecycle: pull image, create container,
    run command, collect output, and remove container.

    Falls back gracefully when Docker SDK is not installed or Docker
    daemon is not running.
    """

    def __init__(self) -> None:
        self._client: Any | None = None

    def _get_client(self) -> Any:
        """Get or create the Docker client (lazy initialization)."""
        if not HAS_DOCKER_SDK:
            raise RuntimeError(
                "Docker SDK is not installed. "
                "Install with: pip install docker>=7.0"
            )
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    async def is_available(self) -> bool:
        """Check if Docker daemon is running and responsive."""
        if not HAS_DOCKER_SDK:
            return False
        try:
            client = self._get_client()
            # Run ping in a thread to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, client.ping)
            return result is True
        except Exception:
            return False

    async def run(
        self,
        config: ContainerConfig,
        command: list[str],
        input_data: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Run a command in a Docker container.

        1. Pulls the image if not locally available
        2. Creates and starts the container with resource limits
        3. Optionally sends input_data as JSON on stdin
        4. Waits for completion (with timeout)
        5. Collects stdout/stderr
        6. Removes the container

        Args:
            config: Container configuration (image, limits, volumes, etc.).
            command: Command to execute inside the container.
            input_data: Optional dict to send as JSON to container stdin.

        Returns:
            ExecutionResult with stdout, stderr, exit code, and duration.
        """
        with tracer.start_as_current_span("docker.run") as span:
            span.set_attribute("container.image", config.full_image)
            span.set_attribute("container.memory_limit", config.memory_limit)
            span.set_attribute("container.cpu_limit", config.cpu_limit)

            client = self._get_client()
            loop = asyncio.get_event_loop()
            start = time.monotonic()
            container = None

            try:
                # 1. Ensure image exists locally
                await self._ensure_image(client, config, loop)

                # 2. Build container kwargs
                container_kwargs = self._build_container_kwargs(config, command)

                # 3. Create and start container
                container = await loop.run_in_executor(
                    None,
                    lambda: client.containers.run(**container_kwargs),
                )

                # If input_data is provided, send it via stdin
                # Note: docker-py's run() with stdin_open=True requires
                # attach approach. For MCP stdio transport, we use a
                # simpler approach: pass data as environment variable
                # or mount as file. The MCP entrypoints read from stdin
                # in a loop, so we handle this at the MCP layer instead.

                # 4. Container already ran (detach=False is default)
                # docker-py's run() blocks until completion when detach=False
                # The result IS the container output (bytes)
                stdout_bytes = container if isinstance(container, bytes) else b""

                # If we got a Container object instead of bytes, logs need
                # to be fetched separately
                exit_code = 0
                stderr_str = ""

                if hasattr(container, "logs"):
                    # detach=True was used, need to wait
                    result = await loop.run_in_executor(
                        None, lambda: container.wait(timeout=config.timeout_seconds)
                    )
                    exit_code = result.get("StatusCode", -1)

                    stdout_bytes = await loop.run_in_executor(
                        None, lambda: container.logs(stdout=True, stderr=False)
                    )
                    stderr_bytes = await loop.run_in_executor(
                        None, lambda: container.logs(stdout=False, stderr=True)
                    )
                    stderr_str = stderr_bytes.decode("utf-8", errors="replace")

                stdout_str = (
                    stdout_bytes.decode("utf-8", errors="replace")
                    if isinstance(stdout_bytes, bytes)
                    else str(stdout_bytes)
                )

                elapsed = time.monotonic() - start
                success = exit_code == 0

                span.set_attribute("container.exit_code", exit_code)
                span.set_attribute("container.duration_s", round(elapsed, 3))

                logger.info(
                    "Container execution complete",
                    image=config.full_image,
                    exit_code=exit_code,
                    duration_s=round(elapsed, 3),
                )

                return ExecutionResult(
                    success=success,
                    exit_code=exit_code,
                    stdout=stdout_str,
                    stderr=stderr_str,
                    duration_seconds=round(elapsed, 3),
                )

            except Exception as exc:
                elapsed = time.monotonic() - start
                span.record_exception(exc)

                logger.error(
                    "Container execution failed",
                    image=config.full_image,
                    error=str(exc),
                    duration_s=round(elapsed, 3),
                )

                return ExecutionResult(
                    success=False,
                    exit_code=-1,
                    stderr=str(exc),
                    duration_seconds=round(elapsed, 3),
                )

            finally:
                # 5. Cleanup container
                if container is not None and hasattr(container, "remove"):
                    try:
                        await loop.run_in_executor(
                            None, lambda: container.remove(force=True)
                        )
                    except Exception:
                        pass  # Best-effort cleanup

    async def _ensure_image(
        self, client: Any, config: ContainerConfig, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Pull the Docker image if not available locally."""
        try:
            await loop.run_in_executor(
                None, lambda: client.images.get(config.full_image)
            )
            logger.debug("Image available locally", image=config.full_image)
        except Exception:
            logger.info("Pulling image", image=config.full_image)
            await loop.run_in_executor(
                None, lambda: client.images.pull(config.image, tag=config.tag)
            )
            logger.info("Image pulled", image=config.full_image)

    def _build_container_kwargs(
        self, config: ContainerConfig, command: list[str]
    ) -> dict[str, Any]:
        """Build kwargs dict for docker client.containers.run()."""
        kwargs: dict[str, Any] = {
            "image": config.full_image,
            "command": command,
            "detach": True,
            "auto_remove": False,
            "working_dir": config.work_dir,
            "environment": {
                "METAFORGE_ENV": "docker",
                **config.env,
            },
            "mem_limit": config.memory_limit,
            "nano_cpus": int(config.cpu_limit * 1e9),
            # Security: no network access by default for tool containers
            "network_mode": "none",
            # Security: read-only root filesystem
            "read_only": False,  # Tools need to write output files
            # Security: no privilege escalation
            "security_opt": ["no-new-privileges"],
        }

        # Mount volumes
        if config.volumes:
            volumes = {}
            for host_path, container_path in config.volumes.items():
                volumes[host_path] = {"bind": container_path, "mode": "rw"}
            kwargs["volumes"] = volumes

        return kwargs

    async def cleanup(self, container_id: str) -> None:
        """Remove a Docker container by ID."""
        if not HAS_DOCKER_SDK:
            return
        try:
            client = self._get_client()
            loop = asyncio.get_event_loop()
            container = await loop.run_in_executor(
                None, lambda: client.containers.get(container_id)
            )
            await loop.run_in_executor(
                None, lambda: container.remove(force=True)
            )
            logger.info("Container removed", container_id=container_id)
        except Exception as exc:
            logger.warning(
                "Container cleanup failed",
                container_id=container_id,
                error=str(exc),
            )


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
            ExecutionResult with stdout, stderr, exit code, and work_products.

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
                artifact_count=len(result.work_products),
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
