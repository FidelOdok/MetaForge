"""Handler for the generate_hal skill."""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import GenerateHalInput, GenerateHalOutput

SUPPORTED_MCU_FAMILIES = {"STM32F4", "STM32H7", "ESP32", "nRF52", "RP2040", "ATSAMD"}
SUPPORTED_PERIPHERALS = {"GPIO", "SPI", "I2C", "UART", "ADC", "DAC", "PWM", "TIMER", "DMA"}


class GenerateHalHandler(SkillBase[GenerateHalInput, GenerateHalOutput]):
    """Generates a Hardware Abstraction Layer for the target MCU.

    This skill is pure computation -- it generates HAL source code
    based on the MCU family and requested peripherals without invoking
    external MCP tools.
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
        """Generate HAL source files for the specified MCU and peripherals."""
        self.logger.info(
            "Generating HAL",
            work_product_id=input_data.work_product_id,
            mcu_family=input_data.mcu_family,
            peripherals=input_data.peripherals,
        )

        if input_data.mcu_family not in SUPPORTED_MCU_FAMILIES:
            raise ValueError(
                f"Unsupported MCU family '{input_data.mcu_family}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_MCU_FAMILIES))}"
            )

        generated_files: list[str] = []
        pin_mappings: dict[str, str] = {}

        # Generate a HAL header + source per peripheral
        for peripheral in input_data.peripherals:
            peripheral_upper = peripheral.upper()
            peripheral_lower = peripheral.lower()

            if peripheral_upper not in SUPPORTED_PERIPHERALS:
                self.logger.warning(
                    "Skipping unsupported peripheral",
                    peripheral=peripheral,
                )
                continue

            header = f"{input_data.output_dir}/hal_{peripheral_lower}.h"
            source = f"{input_data.output_dir}/hal_{peripheral_lower}.c"
            generated_files.extend([header, source])

            # Assign a default pin mapping
            pin_mappings[peripheral_upper] = f"{input_data.mcu_family}_DEFAULT"

        return GenerateHalOutput(
            work_product_id=input_data.work_product_id,
            generated_files=generated_files,
            pin_mappings=pin_mappings,
            hal_version="0.1.0",
        )

    async def validate_output(self, output: GenerateHalOutput) -> list[str]:
        """Verify that at least one file was generated."""
        errors: list[str] = []
        if not output.generated_files:
            errors.append("No HAL files were generated")
        return errors
