"""End-to-end tests for the firmware engineering vertical.

Exercises the full stack: FirmwareAgent → Skills (pure computation) → Digital Twin.
Firmware skills are computation-only (no MCP tool calls), so InMemoryMcpBridge
suffices with no pre-registered tool responses.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from domain_agents.firmware.agent import FirmwareAgent, TaskRequest
from skill_registry.mcp_bridge import InMemoryMcpBridge
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


def _make_firmware_artifact() -> WorkProduct:
    """Create a realistic firmware project work_product."""
    return WorkProduct(
        name="drone-fc-firmware",
        type=WorkProductType.FIRMWARE_SOURCE,
        domain="firmware",
        file_path="firmware/src/main.c",
        content_hash="sha256:fw112233",
        format="c",
        created_by="human",
        metadata={
            "mcu": "STM32F405",
            "rtos": "FreeRTOS",
            "clock_mhz": 168,
            "flash_kb": 1024,
            "ram_kb": 192,
        },
    )


# FORGE-545: the skills generate from real inputs and refuse without them.
FC_PIN_MAP = [
    {"signal": "IMU_CS", "pin": "PA4", "peripheral": "SPI1", "direction": "out"},
    {"signal": "IMU_SCK", "pin": "PA5", "peripheral": "SPI1"},
    {"signal": "BARO_SCL", "pin": "PB6", "peripheral": "I2C1"},
    {"signal": "BARO_SDA", "pin": "PB7", "peripheral": "I2C1"},
]
BMI088_ACC_REGS = [
    {"name": "ACC_CHIP_ID", "address": "0x00", "access": "r", "expected": "0x1E"},
    {"name": "ACC_CONF", "address": "0x40", "access": "rw", "reset": "0xA8"},
    {"name": "ACC_SOFTRESET", "address": "0x7E", "access": "w"},
]
BMP280_REGS = [{"name": "ID", "address": "0xD0", "access": "r", "expected": "0x58"}]


# ---------------------------------------------------------------------------
# Test class: HAL generation through FirmwareAgent
# ---------------------------------------------------------------------------


class TestGenerateHalE2E:
    """E2E tests for HAL generation pipeline."""

    @pytest.fixture
    async def stack(self):
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_firmware_artifact())
        agent = FirmwareAgent(twin=twin, mcp=mcp)
        return {"twin": twin, "agent": agent, "work_product": work_product}

    async def test_generate_hal_stm32f4(self, stack):
        """HAL generation for STM32F4 with GPIO and SPI."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_hal",
                work_product_id=s["work_product"].id,
                parameters={
                    "mcu_family": "STM32F4",
                    "pin_map": FC_PIN_MAP,
                    "peripherals": ["SPI1", "I2C1"],
                    "output_dir": "firmware/hal",
                },
            )
        )

        assert result.success is True
        assert result.task_type == "generate_hal"
        assert len(result.skill_results) == 1

        hal_result = result.skill_results[0]
        assert hal_result["skill"] == "generate_hal"
        assert hal_result["generated_files"] == [
            "firmware/hal/board_pins.h",
            "firmware/hal/board_peripherals.h",
        ]
        assert hal_result["pin_mappings"]["BARO_SDA"] == "PB7"
        peripherals_h = hal_result["files"][1]["content"]
        assert "#define BOARD_USES_SPI1 1" in peripherals_h
        assert "#define BOARD_USES_I2C1 1" in peripherals_h

    async def test_generate_hal_esp32(self, stack):
        """HAL generation works for ESP32 family."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_hal",
                work_product_id=s["work_product"].id,
                parameters={
                    "mcu_family": "ESP32",
                    "pin_map": [{"signal": "GPS_RX", "pin": "GPIO16", "peripheral": "UART2"}],
                },
            )
        )

        assert result.success is True
        hal_result = result.skill_results[0]
        assert "#define BOARD_GPS_RX_PIN GPIO_NUM_16" in hal_result["files"][0]["content"]

    async def test_generate_hal_unsupported_mcu(self, stack):
        """HAL generation fails for unsupported MCU family."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_hal",
                work_product_id=s["work_product"].id,
                parameters={
                    "mcu_family": "PIC32MX",
                    "pin_map": [{"signal": "LED", "pin": "RB0"}],
                },
            )
        )

        assert result.success is False

    async def test_generate_hal_missing_mcu_family(self, stack):
        """Missing mcu_family parameter returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_hal",
                work_product_id=s["work_product"].id,
                parameters={"pin_map": FC_PIN_MAP},
            )
        )

        assert result.success is False
        assert any("mcu_family" in e for e in result.errors)

    async def test_generate_hal_missing_pin_map(self, stack):
        """Missing pin map returns an error; pins are never invented."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="generate_hal",
                work_product_id=s["work_product"].id,
                parameters={"mcu_family": "STM32F4"},
            )
        )

        assert result.success is False
        assert any("pin_map" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test class: Driver scaffolding through FirmwareAgent
# ---------------------------------------------------------------------------


class TestScaffoldDriverE2E:
    """E2E tests for peripheral driver scaffolding pipeline."""

    @pytest.fixture
    async def stack(self):
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_firmware_artifact())
        agent = FirmwareAgent(twin=twin, mcp=mcp)
        return {"twin": twin, "agent": agent, "work_product": work_product}

    async def test_scaffold_spi_driver(self, stack):
        """Scaffold an SPI accelerometer driver."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="scaffold_driver",
                work_product_id=s["work_product"].id,
                parameters={
                    "peripheral_type": "accelerometer",
                    "interface": "spi",
                    "driver_name": "bmi088",
                    "registers": BMI088_ACC_REGS,
                    "registers_source": "BMI088 datasheet rev 1.9, table 6",
                },
            )
        )

        assert result.success is True
        assert result.task_type == "scaffold_driver"
        driver_result = result.skill_results[0]
        assert driver_result["skill"] == "scaffold_driver"
        assert driver_result["interface_type"] == "spi"
        assert len(driver_result["driver_files"]) == 3
        assert "BMI088 datasheet rev 1.9" in driver_result["files"][0]["content"]

    async def test_scaffold_i2c_driver(self, stack):
        """Scaffold an I2C temperature sensor driver."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="scaffold_driver",
                work_product_id=s["work_product"].id,
                parameters={
                    "peripheral_type": "temperature_sensor",
                    "interface": "i2c",
                    "driver_name": "bmp280",
                    "registers": BMP280_REGS,
                },
            )
        )

        assert result.success is True
        assert result.skill_results[0]["interface_type"] == "i2c"

    async def test_scaffold_missing_peripheral_type(self, stack):
        """Missing peripheral_type returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="scaffold_driver",
                work_product_id=s["work_product"].id,
                parameters={"driver_name": "bmi088"},
            )
        )

        assert result.success is False
        assert any("peripheral_type" in e for e in result.errors)

    async def test_scaffold_missing_driver_name(self, stack):
        """Missing driver_name returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="scaffold_driver",
                work_product_id=s["work_product"].id,
                parameters={"peripheral_type": "accelerometer"},
            )
        )

        assert result.success is False
        assert any("driver_name" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test class: RTOS configuration through FirmwareAgent
# ---------------------------------------------------------------------------


class TestConfigureRtosE2E:
    """E2E tests for RTOS configuration pipeline."""

    @pytest.fixture
    async def stack(self):
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_firmware_artifact())
        agent = FirmwareAgent(twin=twin, mcp=mcp)
        return {"twin": twin, "agent": agent, "work_product": work_product}

    async def test_configure_freertos(self, stack):
        """Configure FreeRTOS with multiple tasks."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="configure_rtos",
                work_product_id=s["work_product"].id,
                parameters={
                    "rtos_name": "FreeRTOS",
                    "task_definitions": [
                        {"name": "imu_read", "priority": 5, "stack_size": 512},
                        {"name": "motor_ctrl", "priority": 6, "stack_size": 1024},
                        {"name": "telemetry", "priority": 3, "stack_size": 256},
                    ],
                    "heap_size_kb": 128,
                    "tick_rate_hz": 1000,
                },
            )
        )

        assert result.success is True
        assert result.task_type == "configure_rtos"
        rtos_result = result.skill_results[0]
        assert rtos_result["skill"] == "configure_rtos"
        assert rtos_result["tasks_configured"] == 3
        assert rtos_result["memory_estimate_kb"] > 0

    async def test_configure_rtos_missing_name(self, stack):
        """Missing rtos_name returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="configure_rtos",
                work_product_id=s["work_product"].id,
                parameters={
                    "task_definitions": [{"name": "main", "priority": 1, "stack_size": 256}],
                },
            )
        )

        assert result.success is False
        assert any("rtos_name" in e for e in result.errors)

    async def test_configure_rtos_missing_tasks(self, stack):
        """Missing task_definitions returns error."""
        s = stack
        result = await s["agent"].run_task(
            TaskRequest(
                task_type="configure_rtos",
                work_product_id=s["work_product"].id,
                parameters={"rtos_name": "FreeRTOS"},
            )
        )

        assert result.success is False
        assert any("task_definitions" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test class: Full build pipeline
# ---------------------------------------------------------------------------


class TestFullBuildE2E:
    """E2E tests for the full firmware build pipeline."""

    async def test_full_build_all_steps(self):
        """Full build runs HAL + driver + RTOS and aggregates results."""
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_firmware_artifact())
        agent = FirmwareAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="full_build",
                work_product_id=work_product.id,
                parameters={
                    "mcu_family": "STM32F4",
                    "pin_map": FC_PIN_MAP,
                    "peripheral_type": "accelerometer",
                    "interface": "spi",
                    "driver_name": "bmi088",
                    "registers": BMI088_ACC_REGS,
                    "rtos_name": "FreeRTOS",
                    "task_definitions": [
                        {"name": "imu_read", "priority": 5, "stack_size": 512},
                    ],
                },
            )
        )

        assert result.success is True
        assert result.task_type == "full_build"
        assert len(result.skill_results) == 3

        skills_run = {r["skill"] for r in result.skill_results}
        assert skills_run == {"generate_hal", "scaffold_driver", "configure_rtos"}

    async def test_full_build_hal_only(self):
        """Full build runs only HAL when other params are missing."""
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_firmware_artifact())
        agent = FirmwareAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="full_build",
                work_product_id=work_product.id,
                parameters={"mcu_family": "STM32F4", "pin_map": FC_PIN_MAP},
            )
        )

        assert result.success is True
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["skill"] == "generate_hal"

    async def test_full_build_no_params_fails(self):
        """Full build fails when no step parameters are provided."""
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_firmware_artifact())
        agent = FirmwareAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="full_build",
                work_product_id=work_product.id,
                parameters={},
            )
        )

        assert result.success is False
        assert any("No build steps" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Test class: Common agent behaviours
# ---------------------------------------------------------------------------


class TestFirmwareAgentCommonE2E:
    """Common agent behaviour tests."""

    async def test_artifact_not_found(self):
        """Agent returns error when work_product doesn't exist."""
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        agent = FirmwareAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="generate_hal",
                work_product_id=uuid4(),
                parameters={"mcu_family": "STM32F4", "pin_map": FC_PIN_MAP},
            )
        )

        assert result.success is False
        assert any("not found" in e for e in result.errors)

    async def test_unsupported_task_type(self):
        """Agent rejects unknown task types."""
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_firmware_artifact())
        agent = FirmwareAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="flash_firmware",
                work_product_id=work_product.id,
            )
        )

        assert result.success is False
        assert any("Unsupported" in e for e in result.errors)

    async def test_twin_update_after_hal_generation(self):
        """Verify Twin work_product can be updated after HAL generation."""
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        work_product = await twin.create_work_product(_make_firmware_artifact())
        agent = FirmwareAgent(twin=twin, mcp=mcp)

        result = await agent.run_task(
            TaskRequest(
                task_type="generate_hal",
                work_product_id=work_product.id,
                parameters={"mcu_family": "STM32F4", "pin_map": FC_PIN_MAP},
            )
        )
        assert result.success is True

        updated = await twin.update_work_product(
            work_product.id,
            {
                "metadata": {
                    **work_product.metadata,
                    "hal_generated": True,
                    "hal_files": result.skill_results[0]["generated_files"],
                },
            },
        )
        assert updated.metadata["hal_generated"] is True
        assert len(updated.metadata["hal_files"]) == 2  # board_pins.h, board_peripherals.h
