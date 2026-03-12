"""Standalone PydanticAI agent definition for firmware engineering.

Provides a self-contained Agent() instance with tool definitions that
delegate to existing skill handlers. This module can be used independently
of the FirmwareAgent class in agent.py, or imported by it for the
LLM-driven execution path.

Usage::

    from domain_agents.firmware.pydantic_ai_agent import (
        create_firmware_agent,
        FirmwareAgentDeps,
        run_agent,
    )

    deps = FirmwareAgentDeps(twin=twin, mcp_bridge=mcp, session_id="s1")
    result = await run_agent("Generate HAL for STM32F4", deps)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

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
tracer = get_tracer("domain_agents.firmware.pydantic_ai")

# ---------------------------------------------------------------------------
# Dependencies dataclass
# ---------------------------------------------------------------------------


@dataclass
class FirmwareAgentDeps:
    """Dependencies injected into PydanticAI RunContext for the firmware agent."""

    twin: Any  # TwinAPI -- avoid circular import
    mcp_bridge: McpBridge
    session_id: str = ""
    branch: str = "main"


# ---------------------------------------------------------------------------
# Structured result model
# ---------------------------------------------------------------------------


class FirmwareAgentResult(BaseModel):
    """Structured output from the firmware PydanticAI agent."""

    overall_passed: bool = Field(
        default=True,
        description="Whether all firmware tasks completed successfully",
    )
    generated_files: list[str] = Field(
        default_factory=list,
        description="List of generated firmware files",
    )
    artifacts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Artifacts produced or modified",
    )
    analysis: dict[str, Any] = Field(
        default_factory=dict,
        description="Analysis report",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Engineering recommendations",
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Record of tool calls made during execution",
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
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


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def create_firmware_agent(
    model: str | Any = "test",
) -> Agent[FirmwareAgentDeps, FirmwareAgentResult]:
    """Create a PydanticAI Agent for firmware engineering.

    Args:
        model: PydanticAI model string (e.g. 'openai:gpt-4o') or model
            instance. Defaults to 'test' for deterministic testing.

    Returns:
        Configured Agent instance with firmware engineering tools.
    """
    agent: Agent[FirmwareAgentDeps, FirmwareAgentResult] = Agent(
        model,
        system_prompt=SYSTEM_PROMPT,
        result_type=FirmwareAgentResult,
        deps_type=FirmwareAgentDeps,
    )

    # -- Tool: generate_hal ---------------------------------------------------

    @agent.tool
    async def generate_hal(
        ctx: RunContext[FirmwareAgentDeps],
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
        with tracer.start_as_current_span("tool.generate_hal") as span:
            span.set_attribute("mcu_family", mcu_family)
            logger.info("Generating HAL", mcu_family=mcu_family)

            skill_ctx = SkillContext(
                twin=ctx.deps.twin,
                mcp=ctx.deps.mcp_bridge,
                logger=logger,
                session_id=UUID(ctx.deps.session_id) if ctx.deps.session_id else UUID(int=0),
                branch=ctx.deps.branch,
            )

            skill_input = GenerateHalInput(
                artifact_id=str(UUID(int=0)),
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
        ctx: RunContext[FirmwareAgentDeps],
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
        with tracer.start_as_current_span("tool.scaffold_driver") as span:
            span.set_attribute("peripheral_type", peripheral_type)
            logger.info("Scaffolding driver", peripheral_type=peripheral_type)

            skill_ctx = SkillContext(
                twin=ctx.deps.twin,
                mcp=ctx.deps.mcp_bridge,
                logger=logger,
                session_id=UUID(ctx.deps.session_id) if ctx.deps.session_id else UUID(int=0),
                branch=ctx.deps.branch,
            )

            skill_input = ScaffoldDriverInput(
                artifact_id=str(UUID(int=0)),
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
        ctx: RunContext[FirmwareAgentDeps],
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
        with tracer.start_as_current_span("tool.configure_rtos") as span:
            span.set_attribute("rtos_name", rtos_name)
            logger.info("Configuring RTOS", rtos_name=rtos_name)

            skill_ctx = SkillContext(
                twin=ctx.deps.twin,
                mcp=ctx.deps.mcp_bridge,
                logger=logger,
                session_id=UUID(ctx.deps.session_id) if ctx.deps.session_id else UUID(int=0),
                branch=ctx.deps.branch,
            )

            skill_input = ConfigureRtosInput(
                artifact_id=str(UUID(int=0)),
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

    logger.debug("firmware_pydantic_ai_agent_created")
    return agent


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------


async def run_agent(
    prompt: str,
    deps: FirmwareAgentDeps,
    *,
    model: str | Any = "test",
) -> dict[str, Any]:
    """Run the firmware PydanticAI agent with a natural-language prompt.

    Args:
        prompt: Natural-language description of the task.
        deps: Agent dependencies (twin, mcp_bridge, etc.).
        model: PydanticAI model string or instance.

    Returns:
        Dictionary with agent results including analysis, recommendations,
        and tool call records.
    """
    with tracer.start_as_current_span("firmware.run_agent") as span:
        span.set_attribute("prompt_length", len(prompt))
        logger.info("Running firmware agent", prompt_preview=prompt[:100])

        agent = create_firmware_agent(model=model)
        result = await agent.run(prompt, deps=deps)
        data: FirmwareAgentResult = result.data

        logger.info(
            "Firmware agent completed",
            overall_passed=data.overall_passed,
            num_generated_files=len(data.generated_files),
        )

        return {
            "overall_passed": data.overall_passed,
            "generated_files": data.generated_files,
            "artifacts": data.artifacts,
            "analysis": data.analysis,
            "recommendations": data.recommendations,
            "tool_calls": data.tool_calls,
        }
