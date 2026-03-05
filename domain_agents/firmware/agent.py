"""Firmware engineering domain agent.

Orchestrates skill execution for firmware development:
HAL generation, driver scaffolding, and RTOS configuration.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel

from domain_agents.firmware.skills.configure_rtos.handler import ConfigureRtosHandler
from domain_agents.firmware.skills.configure_rtos.schema import ConfigureRtosInput
from domain_agents.firmware.skills.generate_hal.handler import GenerateHalHandler
from domain_agents.firmware.skills.generate_hal.schema import GenerateHalInput
from domain_agents.firmware.skills.scaffold_driver.handler import ScaffoldDriverHandler
from domain_agents.firmware.skills.scaffold_driver.schema import ScaffoldDriverInput
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger()


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


class FirmwareAgent:
    """Firmware engineering domain agent.

    Orchestrates skill execution for firmware development:
    HAL generation, peripheral driver scaffolding, and RTOS configuration.

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

        Routes to the appropriate handler based on task_type.
        """
        self.logger.info(
            "Running task",
            task_type=request.task_type,
            artifact_id=str(request.artifact_id),
        )

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
                errors=[
                    f"Artifact {request.artifact_id} not found on branch '{request.branch}'"
                ],
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
        """Generate a Hardware Abstraction Layer for the target MCU.

        Requires 'mcu_family' and 'peripherals' in request.parameters.
        """
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
        """Scaffold a peripheral driver.

        Requires 'peripheral_type' and 'driver_name' in request.parameters.
        """
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
        """Configure an RTOS for the target firmware.

        Requires 'rtos_name' and 'task_definitions' in request.parameters.
        """
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
        """Run a full firmware build pipeline (HAL + driver + RTOS).

        Runs all applicable steps sequentially and aggregates results.
        Skips individual steps if their required parameters are not provided.
        """
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
