"""Handler for the scaffold_driver skill."""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import ScaffoldDriverInput, ScaffoldDriverOutput

SUPPORTED_INTERFACES = {"spi", "i2c", "uart", "parallel"}


class ScaffoldDriverHandler(SkillBase[ScaffoldDriverInput, ScaffoldDriverOutput]):
    """Scaffolds a peripheral driver with header, source, and register map.

    This skill is pure computation -- it generates driver boilerplate
    without invoking external MCP tools.
    """

    input_type = ScaffoldDriverInput
    output_type = ScaffoldDriverOutput

    async def validate_preconditions(self, input_data: ScaffoldDriverInput) -> list[str]:
        """Check that the work_product exists in the Twin."""
        errors: list[str] = []
        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")
        return errors

    async def execute(self, input_data: ScaffoldDriverInput) -> ScaffoldDriverOutput:
        """Generate driver scaffold files."""
        self.logger.info(
            "Scaffolding driver",
            work_product_id=input_data.work_product_id,
            peripheral_type=input_data.peripheral_type,
            interface=input_data.interface,
            driver_name=input_data.driver_name,
        )

        if input_data.interface not in SUPPORTED_INTERFACES:
            raise ValueError(
                f"Unsupported interface '{input_data.interface}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_INTERFACES))}"
            )

        base_dir = f"firmware/drivers/{input_data.driver_name}"
        driver_files = [
            f"{base_dir}/{input_data.driver_name}.h",
            f"{base_dir}/{input_data.driver_name}.c",
            f"{base_dir}/{input_data.driver_name}_regs.h",
        ]

        # Generate a basic register map template
        register_map = {
            "WHO_AM_I": {"address": "0x00", "access": "read-only"},
            "CTRL_REG1": {"address": "0x20", "access": "read-write"},
            "STATUS_REG": {"address": "0x27", "access": "read-only"},
            "DATA_OUT": {"address": "0x28", "access": "read-only"},
        }

        return ScaffoldDriverOutput(
            work_product_id=input_data.work_product_id,
            driver_files=driver_files,
            interface_type=input_data.interface,
            register_map=register_map,
        )

    async def validate_output(self, output: ScaffoldDriverOutput) -> list[str]:
        """Verify that driver files were generated."""
        errors: list[str] = []
        if not output.driver_files:
            errors.append("No driver files were generated")
        return errors
