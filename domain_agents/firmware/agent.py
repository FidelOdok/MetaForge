"""Firmware engineering domain agent.

Orchestrates skill execution for firmware development:
HAL generation, driver scaffolding, and RTOS configuration.

Supports two modes:
- **LLM mode**: PydanticAI Agent() with LLM-driven tool selection
- **Hardcoded mode**: Deterministic dispatch by task_type (fallback)
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID, uuid4

try:
    from pydantic_ai import RunContext
except ImportError:
    RunContext = None  # type: ignore[assignment,misc]

import structlog
from pydantic import BaseModel, Field

from domain_agents.base_agent import (
    AgentDependencies,
    AgentResult,
    get_llm_model,
    is_llm_available,
)
from domain_agents.firmware.skills.configure_rtos.handler import ConfigureRtosHandler
from domain_agents.firmware.skills.configure_rtos.schema import ConfigureRtosInput
from domain_agents.firmware.skills.generate_hal.handler import GenerateHalHandler
from domain_agents.firmware.skills.generate_hal.schema import GenerateHalInput
from domain_agents.firmware.skills.scaffold_driver.handler import ScaffoldDriverHandler
from domain_agents.firmware.skills.scaffold_driver.schema import ScaffoldDriverInput
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.firmware")


# ---------------------------------------------------------------------------
# Domain-specific result model for PydanticAI structured output
# ---------------------------------------------------------------------------


class FirmwareResult(AgentResult):
    """Structured output from the firmware agent's PydanticAI run."""

    overall_passed: bool = Field(
        default=True,
        description="Whether all firmware tasks completed successfully",
    )
    generated_files: list[str] = Field(
        default_factory=list,
        description="List of generated firmware files",
    )


# ---------------------------------------------------------------------------
# Backward-compatible request/result models
# ---------------------------------------------------------------------------


class TaskRequest(BaseModel):
    """A request for the firmware agent to perform a task."""

    task_type: str  # "generate_hal", "scaffold_driver", "configure_rtos", "full_build"
    artifact_id: UUID
    parameters: dict[str, Any] = {}
    branch: str = "main"


class TaskResult(BaseModel):
    """Result of a firmware agent task."""

    task_type: str
    artifact_id: UUID
    success: bool
    skill_results: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# PydanticAI agent factory (lazy, created once per process)
# ---------------------------------------------------------------------------

_pydantic_agent: Any | None = None

FIRMWARE_SYSTEM_PROMPT = """\
You are an expert firmware engineer working within the MetaForge design \
validation platform. You have deep knowledge of embedded systems, \
microcontroller architectures (STM32, ESP32, NRF52), real-time operating \
systems (FreeRTOS, Zephyr), peripheral drivers, and hardware abstraction layers.

You have access to the following tools:

- **generate_hal**: Generate a Hardware Abstraction Layer for a target MCU. \
Provide mcu_family and list of peripherals (GPIO, SPI, I2C, UART, etc.).
- **scaffold_driver**: Scaffold a peripheral driver with register map and \
interface code. Provide peripheral_type, interface (spi/i2c/uart), and driver_name.
- **configure_rtos**: Configure an RTOS for the target firmware. Provide \
rtos_name, task_definitions (name, priority, stack_size), heap_size_kb, \
and tick_rate_hz.

Given a user request, determine which tools to call and in what order. \
For a full firmware build, generate HAL first, then scaffold drivers, \
then configure RTOS. Provide a clear assessment of generated artifacts.
"""


def _get_or_create_pydantic_agent() -> Any:
    """Lazily create the PydanticAI Agent for firmware engineering."""
    global _pydantic_agent
    if _pydantic_agent is not None:
        return _pydantic_agent

    try:
        from pydantic_ai import Agent
    except ImportError:
        logger.warning("pydantic_ai_not_installed")
        return None

    model = get_llm_model()
    if model is None:
        return None

    agent = Agent(
        model,
        system_prompt=FIRMWARE_SYSTEM_PROMPT,
        output_type=FirmwareResult,
        deps_type=AgentDependencies,
    )

    # -- Tool: generate_hal ---------------------------------------------------

    @agent.tool
    async def generate_hal(
        ctx: RunContext[AgentDependencies],
        mcu_family: str,
        peripherals: list[str],
        output_dir: str = "firmware/hal",
    ) -> dict[str, Any]:
        """Generate a Hardware Abstraction Layer for a target MCU.

        Args:
            mcu_family: MCU family identifier (e.g. 'STM32F4', 'ESP32').
            peripherals: List of peripherals to generate HAL for.
            output_dir: Output directory for generated files.
        """
        skill_ctx = SkillContext(
            twin=ctx.deps.twin,
            mcp=ctx.deps.mcp_bridge,
            logger=logger,
            session_id=UUID(ctx.deps.session_id),
            branch=ctx.deps.branch,
        )

        skill_input = GenerateHalInput(
            artifact_id=str(UUID("00000000-0000-0000-0000-000000000000")),
            mcu_family=mcu_family,
            peripherals=peripherals,
            output_dir=output_dir,
        )

        handler = GenerateHalHandler(skill_ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return {"skill": "generate_hal", "success": False, "errors": result.errors}

        output = result.data
        return {
            "skill": "generate_hal",
            "success": True,
            "generated_files": output.generated_files,
            "pin_mappings": output.pin_mappings,
            "hal_version": output.hal_version,
        }

    # -- Tool: scaffold_driver ------------------------------------------------

    @agent.tool
    async def scaffold_driver(
        ctx: RunContext[AgentDependencies],
        peripheral_type: str,
        driver_name: str,
        interface: str = "spi",
    ) -> dict[str, Any]:
        """Scaffold a peripheral driver with register map and interface code.

        Args:
            peripheral_type: Type of peripheral (e.g. 'accelerometer', 'gyroscope').
            driver_name: Name for the driver (e.g. 'bmi088').
            interface: Communication interface ('spi', 'i2c', 'uart').
        """
        skill_ctx = SkillContext(
            twin=ctx.deps.twin,
            mcp=ctx.deps.mcp_bridge,
            logger=logger,
            session_id=UUID(ctx.deps.session_id),
            branch=ctx.deps.branch,
        )

        skill_input = ScaffoldDriverInput(
            artifact_id=str(UUID("00000000-0000-0000-0000-000000000000")),
            peripheral_type=peripheral_type,
            interface=interface,
            driver_name=driver_name,
        )

        handler = ScaffoldDriverHandler(skill_ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return {"skill": "scaffold_driver", "success": False, "errors": result.errors}

        output = result.data
        return {
            "skill": "scaffold_driver",
            "success": True,
            "driver_files": output.driver_files,
            "interface_type": output.interface_type,
            "register_map": output.register_map,
        }

    # -- Tool: configure_rtos -------------------------------------------------

    @agent.tool
    async def configure_rtos(
        ctx: RunContext[AgentDependencies],
        rtos_name: str,
        task_definitions: list[dict[str, Any]],
        heap_size_kb: int = 64,
        tick_rate_hz: int = 1000,
    ) -> dict[str, Any]:
        """Configure an RTOS for the target firmware.

        Args:
            rtos_name: RTOS name (e.g. 'FreeRTOS', 'Zephyr').
            task_definitions: List of task definitions with name, priority, stack_size.
            heap_size_kb: Heap size in KB.
            tick_rate_hz: RTOS tick rate in Hz.
        """
        skill_ctx = SkillContext(
            twin=ctx.deps.twin,
            mcp=ctx.deps.mcp_bridge,
            logger=logger,
            session_id=UUID(ctx.deps.session_id),
            branch=ctx.deps.branch,
        )

        skill_input = ConfigureRtosInput(
            artifact_id=str(UUID("00000000-0000-0000-0000-000000000000")),
            rtos_name=rtos_name,
            task_definitions=task_definitions,
            heap_size_kb=heap_size_kb,
            tick_rate_hz=tick_rate_hz,
        )

        handler = ConfigureRtosHandler(skill_ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return {"skill": "configure_rtos", "success": False, "errors": result.errors}

        output = result.data
        return {
            "skill": "configure_rtos",
            "success": True,
            "config_file": output.config_file,
            "tasks_configured": output.tasks_configured,
            "memory_estimate_kb": output.memory_estimate_kb,
        }

    _pydantic_agent = agent
    return agent


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------


class FirmwareAgent:
    """Firmware engineering domain agent.

    Orchestrates skill execution for firmware development:
    HAL generation, peripheral driver scaffolding, and RTOS configuration.

    Supports two execution modes:
    - PydanticAI mode: LLM-driven tool selection (when METAFORGE_LLM_PROVIDER is set)
    - Hardcoded mode: Deterministic dispatch by task_type (fallback)

    The agent is stateless -- all state lives in the Digital Twin.

    Usage:
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        agent = FirmwareAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(TaskRequest(
            task_type="generate_hal",
            artifact_id=artifact.id,
            parameters={"mcu_family": "STM32F4", "peripherals": ["GPIO", "SPI"]},
        ))
    """

    SUPPORTED_TASKS = {"generate_hal", "scaffold_driver", "configure_rtos", "full_build"}

    def __init__(
        self,
        twin: Any,  # TwinAPI -- avoid circular import at module level
        mcp: McpBridge,
        session_id: UUID | None = None,
    ) -> None:
        self.twin = twin
        self.mcp = mcp
        self.session_id = session_id or uuid4()
        self.logger = logger.bind(agent="firmware", session_id=str(self.session_id))

    async def run_task(self, request: TaskRequest) -> TaskResult:
        """Execute a firmware engineering task.

        If an LLM is configured, attempts PydanticAI-driven execution.
        Falls back to hardcoded dispatch on LLM unavailability or error.
        """
        with tracer.start_as_current_span("agent.execute") as span:
            span.set_attribute("agent.code", "firmware")
            span.set_attribute("session.id", str(self.session_id))
            span.set_attribute("task.type", request.task_type)

            self.logger.info(
                "Running task",
                task_type=request.task_type,
                artifact_id=str(request.artifact_id),
            )

            # Try PydanticAI path if LLM is available
            if is_llm_available() and request.task_type in self.SUPPORTED_TASKS:
                try:
                    result = await self._run_with_llm(request)
                    span.set_attribute("agent.mode", "llm")
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    self.logger.warning(
                        "LLM execution failed, falling back to hardcoded dispatch",
                        error=str(exc),
                    )

            # Hardcoded dispatch (fallback)
            span.set_attribute("agent.mode", "hardcoded")
            return await self._run_hardcoded(request)

    async def _run_with_llm(self, request: TaskRequest) -> TaskResult:
        """Execute a task using PydanticAI agent with LLM reasoning."""
        agent = _get_or_create_pydantic_agent()
        if agent is None:
            raise RuntimeError("PydanticAI agent could not be created")

        # Verify artifact exists first
        artifact = await self.twin.get_artifact(request.artifact_id, branch=request.branch)
        if artifact is None:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=[f"Artifact {request.artifact_id} not found on branch '{request.branch}'"],
            )

        deps = AgentDependencies(
            twin=self.twin,
            mcp_bridge=self.mcp,
            session_id=str(self.session_id),
            branch=request.branch,
        )

        prompt = self._build_prompt(request)

        t0 = time.monotonic()
        result = await agent.run(prompt, deps=deps)
        elapsed = time.monotonic() - t0

        self.logger.info(
            "LLM execution completed",
            task_type=request.task_type,
            elapsed_s=round(elapsed, 3),
        )

        firmware_result: FirmwareResult = result.output

        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=firmware_result.overall_passed,
            skill_results=firmware_result.tool_calls
            if firmware_result.tool_calls
            else [firmware_result.analysis],
            warnings=(
                firmware_result.recommendations if not firmware_result.overall_passed else []
            ),
        )

    def _build_prompt(self, request: TaskRequest) -> str:
        """Build a natural language prompt from a structured TaskRequest."""
        parts = [f"Perform a '{request.task_type}' task on artifact {request.artifact_id}."]
        if request.parameters:
            parts.append(f"Parameters: {request.parameters}")
        return " ".join(parts)

    # --- Hardcoded dispatch (original implementation) ---

    async def _run_hardcoded(self, request: TaskRequest) -> TaskResult:
        """Original hardcoded dispatch path."""
        if request.task_type not in self.SUPPORTED_TASKS:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=[
                    f"Unsupported task type: {request.task_type}. "
                    f"Supported: {', '.join(sorted(self.SUPPORTED_TASKS))}"
                ],
            )

        # Verify artifact exists
        artifact = await self.twin.get_artifact(request.artifact_id, branch=request.branch)
        if artifact is None:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=[f"Artifact {request.artifact_id} not found on branch '{request.branch}'"],
            )

        # Route to handler
        handler = self._get_handler(request.task_type)
        return await handler(request)

    def _get_handler(
        self, task_type: str
    ) -> Callable[[TaskRequest], Coroutine[Any, Any, TaskResult]]:
        """Return the handler coroutine function for the given task type."""
        handlers: dict[str, Callable[[TaskRequest], Coroutine[Any, Any, TaskResult]]] = {
            "generate_hal": self._run_generate_hal,
            "scaffold_driver": self._run_scaffold_driver,
            "configure_rtos": self._run_configure_rtos,
            "full_build": self._run_full_build,
        }
        return handlers[task_type]

    async def _run_generate_hal(self, request: TaskRequest) -> TaskResult:
        """Generate a Hardware Abstraction Layer for the target MCU."""
        mcu_family: str = request.parameters.get("mcu_family", "")
        if not mcu_family:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: mcu_family"],
            )

        peripherals: list[str] = request.parameters.get("peripherals", [])
        if not peripherals:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: peripherals"],
            )

        self.logger.info(
            "HAL generation requested",
            mcu_family=mcu_family,
            peripherals=peripherals,
        )

        ctx = self._create_skill_context(request.branch)
        skill_input = GenerateHalInput(
            artifact_id=str(request.artifact_id),
            mcu_family=mcu_family,
            peripherals=peripherals,
            output_dir=request.parameters.get("output_dir", "firmware/hal"),
        )

        handler = GenerateHalHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=True,
            skill_results=[
                {
                    "skill": "generate_hal",
                    "generated_files": output.generated_files,
                    "pin_mappings": output.pin_mappings,
                    "hal_version": output.hal_version,
                }
            ],
        )

    async def _run_scaffold_driver(self, request: TaskRequest) -> TaskResult:
        """Scaffold a peripheral driver."""
        peripheral_type: str = request.parameters.get("peripheral_type", "")
        if not peripheral_type:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: peripheral_type"],
            )

        driver_name: str = request.parameters.get("driver_name", "")
        if not driver_name:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: driver_name"],
            )

        self.logger.info(
            "Driver scaffolding requested",
            peripheral_type=peripheral_type,
            driver_name=driver_name,
        )

        ctx = self._create_skill_context(request.branch)
        skill_input = ScaffoldDriverInput(
            artifact_id=str(request.artifact_id),
            peripheral_type=peripheral_type,
            interface=request.parameters.get("interface", "spi"),
            driver_name=driver_name,
        )

        handler = ScaffoldDriverHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=True,
            skill_results=[
                {
                    "skill": "scaffold_driver",
                    "driver_files": output.driver_files,
                    "interface_type": output.interface_type,
                    "register_map": output.register_map,
                }
            ],
        )

    async def _run_configure_rtos(self, request: TaskRequest) -> TaskResult:
        """Configure an RTOS for the target firmware."""
        rtos_name: str = request.parameters.get("rtos_name", "")
        if not rtos_name:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: rtos_name"],
            )

        task_definitions: list[dict[str, Any]] = request.parameters.get("task_definitions", [])
        if not task_definitions:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: task_definitions"],
            )

        self.logger.info(
            "RTOS configuration requested",
            rtos_name=rtos_name,
            num_tasks=len(task_definitions),
        )

        ctx = self._create_skill_context(request.branch)
        skill_input = ConfigureRtosInput(
            artifact_id=str(request.artifact_id),
            rtos_name=rtos_name,
            task_definitions=task_definitions,
            heap_size_kb=request.parameters.get("heap_size_kb", 64),
            tick_rate_hz=request.parameters.get("tick_rate_hz", 1000),
        )

        handler = ConfigureRtosHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=True,
            skill_results=[
                {
                    "skill": "configure_rtos",
                    "config_file": output.config_file,
                    "tasks_configured": output.tasks_configured,
                    "memory_estimate_kb": output.memory_estimate_kb,
                }
            ],
        )

    async def _run_full_build(self, request: TaskRequest) -> TaskResult:
        """Run a full firmware build pipeline (HAL + driver + RTOS)."""
        all_results: list[dict[str, Any]] = []
        all_errors: list[str] = []
        all_warnings: list[str] = []
        overall_success = True
        steps_run = 0

        # Step 1: Generate HAL if mcu_family and peripherals are provided
        if request.parameters.get("mcu_family") and request.parameters.get("peripherals"):
            hal_result = await self._run_generate_hal(request)
            all_results.extend(hal_result.skill_results)
            all_errors.extend(hal_result.errors)
            all_warnings.extend(hal_result.warnings)
            if not hal_result.success:
                overall_success = False
            steps_run += 1

        # Step 2: Scaffold driver if peripheral_type and driver_name are provided
        if request.parameters.get("peripheral_type") and request.parameters.get("driver_name"):
            driver_result = await self._run_scaffold_driver(request)
            all_results.extend(driver_result.skill_results)
            all_errors.extend(driver_result.errors)
            all_warnings.extend(driver_result.warnings)
            if not driver_result.success:
                overall_success = False
            steps_run += 1

        # Step 3: Configure RTOS if rtos_name and task_definitions are provided
        if request.parameters.get("rtos_name") and request.parameters.get("task_definitions"):
            rtos_result = await self._run_configure_rtos(request)
            all_results.extend(rtos_result.skill_results)
            all_errors.extend(rtos_result.errors)
            all_warnings.extend(rtos_result.warnings)
            if not rtos_result.success:
                overall_success = False
            steps_run += 1

        if steps_run == 0:
            return TaskResult(
                task_type="full_build",
                artifact_id=request.artifact_id,
                success=False,
                errors=[
                    "No build steps could be run. "
                    "Provide parameters for at least one of: "
                    "generate_hal (mcu_family + peripherals), "
                    "scaffold_driver (peripheral_type + driver_name), "
                    "configure_rtos (rtos_name + task_definitions)"
                ],
            )

        return TaskResult(
            task_type="full_build",
            artifact_id=request.artifact_id,
            success=overall_success,
            skill_results=all_results,
            errors=all_errors,
            warnings=all_warnings,
        )

    def _create_skill_context(self, branch: str = "main") -> SkillContext:
        """Create a SkillContext for skill execution."""
        return SkillContext(
            twin=self.twin,
            mcp=self.mcp,
            logger=self.logger,
            session_id=self.session_id,
            branch=branch,
        )
