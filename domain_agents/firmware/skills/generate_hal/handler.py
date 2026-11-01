"""Handler for the generate_hal skill."""

from __future__ import annotations

from domain_agents.firmware.codegen import CodegenError, c_ident, generate_hal_files
from skill_registry.skill_base import SkillBase

from .schema import GenerateHalInput, GenerateHalOutput

SUPPORTED_MCU_FAMILIES = {"STM32F4", "STM32H7", "ESP32", "nRF52", "RP2040", "ATSAMD"}


class GenerateHalHandler(SkillBase[GenerateHalInput, GenerateHalOutput]):
    """Generates the board's pin definitions for the target MCU (FORGE-545).

    Pure computation over the caller's pin map: ``board_pins.h`` with the
    vendor's own pin macros per signal, and ``board_peripherals.h`` naming
    the peripheral instances in use. It used to return file paths with no
    content and an invented ``<family>_DEFAULT`` pin for every peripheral.
    The files are returned, not written; the caller stages or records them.
    """

    input_type = GenerateHalInput
    output_type = GenerateHalOutput

    async def validate_preconditions(self, input_data: GenerateHalInput) -> list[str]:
        """Check that the work_product exists in the Twin."""
        errors: list[str] = []
        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")
        return errors

    async def execute(self, input_data: GenerateHalInput) -> GenerateHalOutput:
        """Generate the pin definition headers from the pin map."""
        self.logger.info(
            "Generating HAL",
            work_product_id=input_data.work_product_id,
            mcu_family=input_data.mcu_family,
            pins=len(input_data.pin_map),
        )
        if input_data.mcu_family not in SUPPORTED_MCU_FAMILIES:
            raise ValueError(
                f"Unsupported MCU family '{input_data.mcu_family}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_MCU_FAMILIES))}"
            )
        try:
            files, mapping = generate_hal_files(
                input_data.mcu_family,
                input_data.pin_map,
                input_data.output_dir,
                input_data.source,
            )
        except CodegenError as exc:
            raise ValueError(str(exc)) from exc

        mapped = {
            c_ident(str(row["peripheral"])) for row in input_data.pin_map if row.get("peripheral")
        }
        absent = [p for p in input_data.peripherals if c_ident(p) not in mapped]
        if absent:
            raise ValueError(
                f"peripheral(s) {', '.join(absent)} have no pins in pin_map; "
                "add their pins rather than generating them unassigned"
            )

        return GenerateHalOutput(
            work_product_id=input_data.work_product_id,
            files=files,
            generated_files=[f.path for f in files],
            pin_mappings=mapping,
        )

    async def validate_output(self, output: GenerateHalOutput) -> list[str]:
        """Verify that every listed file has content."""
        if not output.files or any(not f.content for f in output.files):
            return ["No HAL file content was generated"]
        return []
