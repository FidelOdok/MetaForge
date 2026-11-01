"""Handler for the scaffold_driver skill."""

from __future__ import annotations

from domain_agents.firmware.codegen import CodegenError, c_ident, generate_driver_files
from skill_registry.skill_base import SkillBase

from .schema import ScaffoldDriverInput, ScaffoldDriverOutput

SUPPORTED_INTERFACES = {"spi", "i2c", "uart", "parallel"}


class ScaffoldDriverHandler(SkillBase[ScaffoldDriverInput, ScaffoldDriverOutput]):
    """Generates a register-level driver from the part's datasheet registers (FORGE-545).

    ``<name>_regs.h`` (addresses, reset and expected values), ``<name>.h``
    and ``<name>.c``: register read/write over bus callbacks the board
    supplies, and an ``init`` that checks the identity register when one is
    given. Registers are taken as 8 bits wide. The files are returned, not
    written; the caller stages or records them.
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
        """Generate the driver files from the register list."""
        self.logger.info(
            "Scaffolding driver",
            work_product_id=input_data.work_product_id,
            peripheral_type=input_data.peripheral_type,
            interface=input_data.interface,
            driver_name=input_data.driver_name,
            registers=len(input_data.registers),
        )
        if input_data.interface not in SUPPORTED_INTERFACES:
            raise ValueError(
                f"Unsupported interface '{input_data.interface}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_INTERFACES))}"
            )
        try:
            files, register_map = generate_driver_files(
                input_data.driver_name,
                input_data.interface,
                input_data.registers,
                input_data.address_bits,
                f"firmware/drivers/{c_ident(input_data.driver_name).lower()}",
                input_data.source,
            )
        except CodegenError as exc:
            raise ValueError(str(exc)) from exc

        return ScaffoldDriverOutput(
            work_product_id=input_data.work_product_id,
            files=files,
            driver_files=[f.path for f in files],
            interface_type=input_data.interface,
            register_map=register_map,
        )

    async def validate_output(self, output: ScaffoldDriverOutput) -> list[str]:
        """Verify that every listed file has content."""
        if not output.files or any(not f.content for f in output.files):
            return ["No driver file content was generated"]
        return []
