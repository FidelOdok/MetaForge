"""Handler for the configure_rtos skill."""

from __future__ import annotations

from typing import Any

from skill_registry.skill_base import SkillBase

from .schema import ConfigureRtosInput, ConfigureRtosOutput

SUPPORTED_RTOS = {"FreeRTOS", "Zephyr", "ChibiOS", "ThreadX", "RTEMS"}

# Default stack size per task (KB) if not specified
DEFAULT_STACK_SIZE_KB = 4


class ConfigureRtosHandler(SkillBase[ConfigureRtosInput, ConfigureRtosOutput]):
    """Generates RTOS configuration based on task definitions.

    This skill is pure computation -- it produces configuration files
    without invoking external MCP tools.
    """

    input_type = ConfigureRtosInput
    output_type = ConfigureRtosOutput

    async def validate_preconditions(self, input_data: ConfigureRtosInput) -> list[str]:
        """Check that the artifact exists in the Twin."""
        errors: list[str] = []
        artifact = await self.context.twin.get_artifact(
            input_data.artifact_id, branch=self.context.branch
        )
        if artifact is None:
            errors.append(f"Artifact {input_data.artifact_id} not found in Twin")
        return errors

    async def execute(self, input_data: ConfigureRtosInput) -> ConfigureRtosOutput:
        """Generate RTOS configuration from task definitions."""
        self.logger.info(
            "Configuring RTOS",
            artifact_id=input_data.artifact_id,
            rtos_name=input_data.rtos_name,
            num_tasks=len(input_data.task_definitions),
            heap_size_kb=input_data.heap_size_kb,
        )

        if input_data.rtos_name not in SUPPORTED_RTOS:
            raise ValueError(
                f"Unsupported RTOS '{input_data.rtos_name}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_RTOS))}"
            )

        # Compute memory estimate: heap + sum of stack sizes
        total_stack_kb = self._compute_total_stack(input_data.task_definitions)
        memory_estimate_kb = input_data.heap_size_kb + total_stack_kb

        # Generate config file path based on RTOS
        config_file = self._config_file_path(input_data.rtos_name)

        return ConfigureRtosOutput(
            artifact_id=input_data.artifact_id,
            config_file=config_file,
            tasks_configured=len(input_data.task_definitions),
            memory_estimate_kb=memory_estimate_kb,
        )

    async def validate_output(self, output: ConfigureRtosOutput) -> list[str]:
        """Verify that at least one task was configured."""
        errors: list[str] = []
        if output.tasks_configured <= 0:
            errors.append("No tasks were configured")
        return errors

    @staticmethod
    def _compute_total_stack(task_definitions: list[dict[str, Any]]) -> int:
        """Sum the stack sizes from all task definitions."""
        total = 0
        for task_def in task_definitions:
            stack_bytes = task_def.get("stack_size", DEFAULT_STACK_SIZE_KB * 1024)
            total += stack_bytes // 1024 if stack_bytes >= 1024 else DEFAULT_STACK_SIZE_KB
        return total

    @staticmethod
    def _config_file_path(rtos_name: str) -> str:
        """Return the conventional configuration file path for the RTOS."""
        paths = {
            "FreeRTOS": "firmware/rtos/FreeRTOSConfig.h",
            "Zephyr": "firmware/rtos/prj.conf",
            "ChibiOS": "firmware/rtos/chconf.h",
            "ThreadX": "firmware/rtos/tx_user.h",
            "RTEMS": "firmware/rtos/rtems_config.h",
        }
        return paths.get(rtos_name, f"firmware/rtos/{rtos_name.lower()}_config.h")
