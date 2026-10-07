"""Handler for the configure_rtos skill."""

from __future__ import annotations

from domain_agents.firmware.codegen import GENERATED_RTOS, CodegenError, generate_rtos_files
from skill_registry.skill_base import SkillBase

from .schema import ConfigureRtosInput, ConfigureRtosOutput


class ConfigureRtosHandler(SkillBase[ConfigureRtosInput, ConfigureRtosOutput]):
    """Generates the RTOS kernel configuration and task table (FORGE-545).

    FreeRTOS: ``FreeRTOSConfig.h`` and ``app_tasks.c`` (an ``xTaskCreate``
    table, depths in stack words). Zephyr: ``prj.conf`` and
    ``app_threads.c`` (``K_THREAD_DEFINE`` per task, priority inverted to
    Zephyr's lower-is-higher order). Other RTOSes are refused: the handler
    used to return a conventional path for any of five RTOSes and generate
    nothing. The files are returned, not written.
    """

    input_type = ConfigureRtosInput
    output_type = ConfigureRtosOutput

    async def validate_preconditions(self, input_data: ConfigureRtosInput) -> list[str]:
        """Check that the work_product exists in the Twin."""
        errors: list[str] = []
        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")
        return errors

    async def execute(self, input_data: ConfigureRtosInput) -> ConfigureRtosOutput:
        """Generate the RTOS configuration from the task definitions."""
        self.logger.info(
            "Configuring RTOS",
            work_product_id=input_data.work_product_id,
            rtos_name=input_data.rtos_name,
            num_tasks=len(input_data.task_definitions),
            heap_size_kb=input_data.heap_size_kb,
        )
        if input_data.rtos_name not in GENERATED_RTOS:
            raise ValueError(
                f"Unsupported RTOS '{input_data.rtos_name}'. Supported: {', '.join(GENERATED_RTOS)}"
            )
        try:
            files, ram_bytes = generate_rtos_files(
                input_data.rtos_name,
                input_data.task_definitions,
                input_data.heap_size_kb,
                input_data.tick_rate_hz,
                input_data.output_dir,
                input_data.stack_word_bytes,
            )
        except CodegenError as exc:
            raise ValueError(str(exc)) from exc

        return ConfigureRtosOutput(
            work_product_id=input_data.work_product_id,
            files=files,
            config_file=files[0].path,
            tasks_configured=len(input_data.task_definitions),
            memory_estimate_kb=-(-ram_bytes // 1024),
        )

    async def validate_output(self, output: ConfigureRtosOutput) -> list[str]:
        """Verify that at least one task was configured and files have content."""
        errors: list[str] = []
        if output.tasks_configured <= 0:
            errors.append("No tasks were configured")
        if not output.files or any(not f.content for f in output.files):
            errors.append("No RTOS configuration content was generated")
        return errors
