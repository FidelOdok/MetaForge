"""Tests for the firmware engineering domain agent."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from domain_agents.firmware.agent import (
    FirmwareAgent,
    FirmwareResult,
    TaskRequest,
    TaskResult,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge


@pytest.fixture
def mock_twin() -> AsyncMock:
    twin = AsyncMock()
    # Default: work_product exists
    twin.get_work_product.return_value = MagicMock(
        id=uuid4(), name="drone-fc-firmware", domain="firmware"
    )
    return twin


@pytest.fixture
def mcp_bridge() -> InMemoryMcpBridge:
    return InMemoryMcpBridge()


@pytest.fixture
def agent(mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge) -> FirmwareAgent:
    return FirmwareAgent(twin=mock_twin, mcp=mcp_bridge)


# --- FirmwareAgent construction and metadata ---


class TestFirmwareAgent:
    """Basic agent construction and properties."""

    async def test_agent_creation(self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge):
        agent = FirmwareAgent(twin=mock_twin, mcp=mcp_bridge)
        assert agent.twin is mock_twin
        assert agent.mcp is mcp_bridge
        assert agent.session_id is not None

    async def test_agent_creation_with_session_id(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        sid = uuid4()
        agent = FirmwareAgent(twin=mock_twin, mcp=mcp_bridge, session_id=sid)
        assert agent.session_id == sid

    async def test_supported_tasks(self):
        assert FirmwareAgent.SUPPORTED_TASKS == {
            "generate_hal",
            "scaffold_driver",
            "configure_rtos",
            "full_build",
        }

    async def test_unsupported_task_type_fails(self, agent: FirmwareAgent):
        request = TaskRequest(
            task_type="do_magic",
            work_product_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("Unsupported task type" in e for e in result.errors)
        assert "do_magic" in result.errors[0]

    async def test_missing_artifact(self, agent: FirmwareAgent, mock_twin: AsyncMock):
        """Missing work_product should produce an error."""
        mock_twin.get_work_product.return_value = None
        request = TaskRequest(
            task_type="generate_hal",
            work_product_id=uuid4(),
            parameters={"mcu_family": "STM32F4", "peripherals": ["GPIO"]},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("not found" in e for e in result.errors)


# --- HAL generation ---


PIN_MAP = [
    {"signal": "IMU_CS", "pin": "PA4", "peripheral": "SPI1"},
    {"signal": "IMU_SCK", "pin": "PA5", "peripheral": "SPI1"},
    {"signal": "BARO_SDA", "pin": "PB7", "peripheral": "I2C1"},
]
BMI088_REGS = [
    {"name": "ACC_CHIP_ID", "address": "0x00", "access": "r", "expected": "0x1E"},
    {"name": "ACC_PWR_CTRL", "address": "0x7D", "access": "rw", "reset": "0x00"},
]


class TestGenerateHal:
    """Tests for the generate_hal task type."""

    async def test_hal_generation_succeeds(self, agent: FirmwareAgent):
        """HAL generation with valid MCU and peripherals should succeed."""
        work_product_id = uuid4()
        request = TaskRequest(
            task_type="generate_hal",
            work_product_id=work_product_id,
            parameters={"mcu_family": "STM32F4", "pin_map": PIN_MAP},
        )
        result = await agent.run_task(request)

        assert result.success is True
        assert result.task_type == "generate_hal"
        assert result.work_product_id == work_product_id
        assert len(result.skill_results) == 1
        out = result.skill_results[0]
        assert out["skill"] == "generate_hal"
        # FORGE-545: real content from the pin map, not paths with nothing behind them
        assert out["generated_files"] == [
            "firmware/hal/board_pins.h",
            "firmware/hal/board_peripherals.h",
        ]
        pins_h = out["files"][0]["content"]
        assert "#define BOARD_IMU_CS_PORT GPIOA" in pins_h
        assert "#define BOARD_IMU_CS_PIN GPIO_PIN_4" in pins_h

    async def test_hal_missing_mcu_family(self, agent: FirmwareAgent):
        """HAL should fail when mcu_family is missing."""
        request = TaskRequest(
            task_type="generate_hal",
            work_product_id=uuid4(),
            parameters={"peripherals": ["GPIO"]},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("mcu_family" in e for e in result.errors)

    async def test_hal_missing_pin_map(self, agent: FirmwareAgent):
        """HAL should fail when the pin map is missing; pins are never invented."""
        request = TaskRequest(
            task_type="generate_hal",
            work_product_id=uuid4(),
            parameters={"mcu_family": "STM32F4"},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("pin_map" in e for e in result.errors)

    async def test_hal_pin_mappings(self, agent: FirmwareAgent):
        """pin_mappings are the board's own signal to pin assignments."""
        request = TaskRequest(
            task_type="generate_hal",
            work_product_id=uuid4(),
            parameters={
                "mcu_family": "ESP32",
                "pin_map": [
                    {"signal": "GPS_TX", "pin": "GPIO17", "peripheral": "UART2"},
                    {"signal": "LED", "pin": "IO2"},
                ],
            },
        )
        result = await agent.run_task(request)

        assert result.success is True
        assert result.skill_results[0]["pin_mappings"] == {"GPS_TX": "GPIO17", "LED": "IO2"}
        assert "GPIO_NUM_17" in result.skill_results[0]["files"][0]["content"]


# --- Driver scaffolding ---


class TestScaffoldDriver:
    """Tests for the scaffold_driver task type."""

    async def test_driver_scaffold_succeeds(self, agent: FirmwareAgent):
        """Driver scaffolding with valid parameters should succeed."""
        work_product_id = uuid4()
        request = TaskRequest(
            task_type="scaffold_driver",
            work_product_id=work_product_id,
            parameters={
                "peripheral_type": "accelerometer",
                "interface": "spi",
                "driver_name": "bmi088",
                "registers": BMI088_REGS,
            },
        )
        result = await agent.run_task(request)

        assert result.success is True
        assert result.task_type == "scaffold_driver"
        assert len(result.skill_results) == 1
        out = result.skill_results[0]
        assert out["skill"] == "scaffold_driver"
        assert len(out["driver_files"]) == 3
        assert out["interface_type"] == "spi"
        # FORGE-545: the part's own registers, not one hard-coded WHO_AM_I map
        assert set(out["register_map"]) == {"ACC_CHIP_ID", "ACC_PWR_CTRL"}
        assert "#define BMI088_REG_ACC_CHIP_ID 0x00u" in out["files"][0]["content"]

    async def test_driver_missing_registers(self, agent: FirmwareAgent):
        request = TaskRequest(
            task_type="scaffold_driver",
            work_product_id=uuid4(),
            parameters={"peripheral_type": "accelerometer", "driver_name": "bmi088"},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("registers" in e for e in result.errors)

    async def test_driver_missing_peripheral_type(self, agent: FirmwareAgent):
        """Driver should fail when peripheral_type is missing."""
        request = TaskRequest(
            task_type="scaffold_driver",
            work_product_id=uuid4(),
            parameters={"driver_name": "bmi088"},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("peripheral_type" in e for e in result.errors)

    async def test_driver_missing_driver_name(self, agent: FirmwareAgent):
        """Driver should fail when driver_name is missing."""
        request = TaskRequest(
            task_type="scaffold_driver",
            work_product_id=uuid4(),
            parameters={"peripheral_type": "accelerometer"},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("driver_name" in e for e in result.errors)


# --- RTOS configuration ---


class TestConfigureRtos:
    """Tests for the configure_rtos task type."""

    async def test_rtos_config_succeeds(self, agent: FirmwareAgent):
        """RTOS configuration with valid parameters should succeed."""
        work_product_id = uuid4()
        request = TaskRequest(
            task_type="configure_rtos",
            work_product_id=work_product_id,
            parameters={
                "rtos_name": "FreeRTOS",
                "task_definitions": [
                    {"name": "sensor_task", "priority": 3, "stack_size": 4096},
                    {"name": "comms_task", "priority": 2, "stack_size": 8192},
                ],
                "heap_size_kb": 128,
                "tick_rate_hz": 1000,
            },
        )
        result = await agent.run_task(request)

        assert result.success is True
        assert result.task_type == "configure_rtos"
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["skill"] == "configure_rtos"
        assert result.skill_results[0]["tasks_configured"] == 2
        assert result.skill_results[0]["config_file"] == "firmware/rtos/FreeRTOSConfig.h"
        assert result.skill_results[0]["memory_estimate_kb"] > 0

    async def test_rtos_missing_rtos_name(self, agent: FirmwareAgent):
        """RTOS should fail when rtos_name is missing."""
        request = TaskRequest(
            task_type="configure_rtos",
            work_product_id=uuid4(),
            parameters={
                "task_definitions": [{"name": "task1", "priority": 1}],
            },
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("rtos_name" in e for e in result.errors)

    async def test_rtos_missing_task_definitions(self, agent: FirmwareAgent):
        """RTOS should fail when task_definitions is missing."""
        request = TaskRequest(
            task_type="configure_rtos",
            work_product_id=uuid4(),
            parameters={"rtos_name": "FreeRTOS"},
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("task_definitions" in e for e in result.errors)


# --- Full build ---


class TestFullBuild:
    """Tests for the full_build task type."""

    async def test_full_build_runs_all_steps(self, agent: FirmwareAgent):
        """Full build should run HAL + driver + RTOS and aggregate results."""
        work_product_id = uuid4()
        request = TaskRequest(
            task_type="full_build",
            work_product_id=work_product_id,
            parameters={
                "mcu_family": "STM32F4",
                "pin_map": PIN_MAP,
                "peripheral_type": "accelerometer",
                "driver_name": "bmi088",
                "registers": BMI088_REGS,
                "rtos_name": "FreeRTOS",
                "task_definitions": [
                    {"name": "main_task", "priority": 1, "stack_size": 4096},
                ],
            },
        )
        result = await agent.run_task(request)

        assert result.task_type == "full_build"
        assert result.work_product_id == work_product_id
        assert result.success is True
        assert len(result.skill_results) == 3
        skills_run = {r["skill"] for r in result.skill_results}
        assert skills_run == {"generate_hal", "scaffold_driver", "configure_rtos"}

    async def test_full_build_partial_parameters(self, agent: FirmwareAgent):
        """Full build should only run steps for which parameters are provided."""
        request = TaskRequest(
            task_type="full_build",
            work_product_id=uuid4(),
            parameters={"mcu_family": "STM32F4", "pin_map": PIN_MAP},
        )
        result = await agent.run_task(request)

        assert result.task_type == "full_build"
        assert result.success is True
        assert len(result.skill_results) == 1
        assert result.skill_results[0]["skill"] == "generate_hal"

    async def test_full_build_no_parameters(self, agent: FirmwareAgent):
        """Full build with no parameters should error."""
        request = TaskRequest(
            task_type="full_build",
            work_product_id=uuid4(),
        )
        result = await agent.run_task(request)

        assert result.success is False
        assert any("No build steps could be run" in e for e in result.errors)


# --- TaskRequest model ---


class TestTaskRequest:
    """Tests for the TaskRequest Pydantic model."""

    def test_task_request_defaults(self):
        work_product_id = uuid4()
        req = TaskRequest(task_type="generate_hal", work_product_id=work_product_id)
        assert req.branch == "main"
        assert req.parameters == {}

    def test_task_request_with_parameters(self):
        work_product_id = uuid4()
        params = {"mcu_family": "STM32F4", "peripherals": ["GPIO"]}
        req = TaskRequest(
            task_type="generate_hal",
            work_product_id=work_product_id,
            parameters=params,
            branch="feature-1",
        )
        assert req.branch == "feature-1"
        assert req.parameters == params
        assert req.work_product_id == work_product_id


# --- TaskResult model ---


class TestTaskResult:
    """Tests for the TaskResult Pydantic model."""

    def test_task_result_defaults(self):
        work_product_id = uuid4()
        res = TaskResult(
            task_type="generate_hal",
            work_product_id=work_product_id,
            success=True,
        )
        assert res.skill_results == []
        assert res.errors == []
        assert res.warnings == []

    def test_task_result_with_data(self):
        work_product_id = uuid4()
        res = TaskResult(
            task_type="generate_hal",
            work_product_id=work_product_id,
            success=False,
            errors=["Something failed"],
            warnings=["Unsupported peripheral"],
            skill_results=[{"skill": "generate_hal", "data": {}}],
        )
        assert not res.success
        assert len(res.errors) == 1
        assert len(res.warnings) == 1
        assert len(res.skill_results) == 1


# --- PydanticAI integration ---


class TestFirmwareResult:
    """Tests for the FirmwareResult structured output model."""

    def test_firmware_result_defaults(self):
        result = FirmwareResult()
        assert result.overall_passed is True
        assert result.generated_files == []
        assert result.work_products == []
        assert result.analysis == {}

    def test_firmware_result_with_data(self):
        result = FirmwareResult(
            overall_passed=True,
            generated_files=["hal_gpio.h", "hal_gpio.c"],
            work_products=[{"type": "hal", "mcu": "STM32F4"}],
            tool_calls=[{"tool": "generate_hal", "result": "success"}],
        )
        assert result.overall_passed is True
        assert len(result.generated_files) == 2
        assert len(result.tool_calls) == 1


class TestFirmwareHardcodedFallback:
    """Tests verifying hardcoded dispatch when LLM is unavailable."""

    async def test_fallback_when_no_llm_configured(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        """Agent should use hardcoded dispatch when METAFORGE_LLM_PROVIDER is not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            agent = FirmwareAgent(twin=mock_twin, mcp=mcp_bridge)

            request = TaskRequest(
                task_type="generate_hal",
                work_product_id=uuid4(),
                parameters={"mcu_family": "STM32F4", "pin_map": PIN_MAP},
            )
            result = await agent.run_task(request)

            assert result.success is True
            assert result.task_type == "generate_hal"

    async def test_unsupported_task_in_hardcoded_mode(
        self, mock_twin: AsyncMock, mcp_bridge: InMemoryMcpBridge
    ):
        """Unsupported tasks should fail gracefully in hardcoded mode."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("METAFORGE_LLM_PROVIDER", None)
            agent = FirmwareAgent(twin=mock_twin, mcp=mcp_bridge)

            request = TaskRequest(
                task_type="unsupported_task",
                work_product_id=uuid4(),
            )
            result = await agent.run_task(request)

            assert result.success is False
            assert any("Unsupported task type" in e for e in result.errors)
