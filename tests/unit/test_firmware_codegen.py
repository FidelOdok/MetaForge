"""Firmware source generation from real inputs (FORGE-545)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from domain_agents.firmware.codegen import (
    CodegenError,
    RtosTask,
    generate_driver_files,
    generate_hal_files,
    generate_rtos_files,
    pin_macros,
)


class TestPinMacros:
    @pytest.mark.parametrize(
        "family, pin, expected",
        [
            ("STM32F4", "PA4", [("PORT", "GPIOA"), ("PIN", "GPIO_PIN_4")]),
            ("STM32H7", "pk15", [("PORT", "GPIOK"), ("PIN", "GPIO_PIN_15")]),
            ("ESP32", "GPIO17", [("PIN", "GPIO_NUM_17")]),
            ("ESP32", "IO2", [("PIN", "GPIO_NUM_2")]),
            ("RP2040", "GP25", [("PIN", "25")]),
            ("nRF52", "P0.13", [("PIN", "NRF_GPIO_PIN_MAP(0, 13)")]),
            ("ATSAMD", "PA05", [("PIN", "PIN_PA05")]),
        ],
    )
    def test_vendor_macros(self, family: str, pin: str, expected: list) -> None:
        assert pin_macros(family, pin) == expected

    @pytest.mark.parametrize(
        "family, pin",
        [("STM32F4", "PA16"), ("ESP32", "PA4"), ("RP2040", "GP30"), ("nRF52", "P2.1")],
    )
    def test_invalid_pins_are_refused(self, family: str, pin: str) -> None:
        with pytest.raises(CodegenError, match="not a valid"):
            pin_macros(family, pin)


class TestHal:
    def test_pin_conflict_is_refused(self) -> None:
        with pytest.raises(CodegenError, match="assigned to both"):
            generate_hal_files(
                "STM32F4",
                [{"signal": "A", "pin": "PA4"}, {"signal": "B", "pin": "pa4"}],
                "hal",
                "",
            )

    def test_source_is_cited_in_the_header(self) -> None:
        files, _ = generate_hal_files(
            "STM32F4", [{"signal": "LED", "pin": "PC13"}], "hal", "fc.kicad_sch rev B"
        )
        assert "Source: fc.kicad_sch rev B" in files[0].content
        assert len(files) == 1  # no peripheral column, no peripherals header


REGS = [
    {"name": "CHIP_ID", "address": "0x00", "access": "r", "expected": "0x1E"},
    {"name": "PWR_CTRL", "address": 0x7D, "access": "rw", "reset": 0},
    {"name": "SOFTRESET", "address": "0x7E", "access": "w", "description": "write 0xB6"},
]


class TestDriver:
    def test_register_header_and_identity_check(self) -> None:
        files, reg_map = generate_driver_files("BMI088 acc", "spi", REGS, 8, "drv", "DS rev 1.9")
        regs_h, head_h, src_c = (f.content for f in files)
        assert "#define BMI088_ACC_REG_CHIP_ID 0x00u" in regs_h
        assert "#define BMI088_ACC_CHIP_ID_EXPECTED 0x1Eu" in regs_h
        assert "write 0xB6" in regs_h
        assert "BMI088_ACC_CHIP_ID_EXPECTED ? 0 : -2" in src_c
        assert reg_map["PWR_CTRL"] == {"address": "0x7D", "access": "read-write", "reset": "0x00"}
        assert "int bmi088_acc_init(const bmi088_acc_t *dev);" in head_h

    @pytest.mark.parametrize(
        "regs, match",
        [
            ([{"name": "A", "address": "0x1FF", "access": "r"}], "does not fit"),
            ([{"name": "A", "address": "0", "access": "x"}], "access"),
            (
                [
                    {"name": "A", "address": 1, "access": "r"},
                    {"name": "B", "address": 1, "access": "r"},
                ],
                "share address",
            ),
            ([{"name": "A", "address": 1, "access": "w", "expected": 1}], "write-only"),
            ([{"name": "A", "address": "zz", "access": "r"}], "not an integer"),
        ],
    )
    def test_bad_register_lists_are_refused(self, regs: list, match: str) -> None:
        with pytest.raises(CodegenError, match=match):
            generate_driver_files("x", "i2c", regs, 8, "drv", "")

    @pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
    def test_generated_driver_compiles(self, tmp_path: Path) -> None:
        files, _ = generate_driver_files("bmi088", "spi", REGS, 8, "drv", "")
        for f in files:
            (tmp_path / Path(f.path).name).write_text(f.content)
        proc = subprocess.run(
            ["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-c", "bmi088.c", "-o", "x.o"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr


TASKS = [
    RtosTask(name="sensor task", priority=3, stack_size=4096),
    RtosTask(name="comms", priority=1, stack_size=2048, entry="comms_main"),
]


class TestRtos:
    def test_freertos_config_and_table(self) -> None:
        files, ram = generate_rtos_files("FreeRTOS", TASKS, 64, 1000, "rtos", 4)
        cfg, table = files[0].content, files[1].content
        assert "#define configMAX_PRIORITIES 4" in cfg
        assert "#define configTOTAL_HEAP_SIZE ((size_t)65536)" in cfg
        assert "#define configMINIMAL_STACK_SIZE ((uint16_t)512)" in cfg
        assert '{ sensor_task_task, "sensor task", 1024u, 3u },' in table
        assert "void comms_main(void *arg);" in table
        assert ram == 65536 + 4096 + 2048

    def test_zephyr_inverts_priority(self) -> None:
        files, _ = generate_rtos_files("Zephyr", TASKS, 16, 100, "rtos", 4)
        conf, threads = files[0].content, files[1].content
        assert "CONFIG_SYS_CLOCK_TICKS_PER_SEC=100" in conf
        # priority 3 is the most urgent input, so Zephyr priority 0
        assert (
            "K_THREAD_DEFINE(sensor_task_tid, 4096, sensor_task_task, NULL, NULL, NULL, 0, 0, 0);"
            in threads
        )
        assert ", comms_main, NULL, NULL, NULL, 2, 0, 0);" in threads

    def test_unsupported_rtos_and_bad_stack_are_refused(self) -> None:
        with pytest.raises(CodegenError, match="no generator"):
            generate_rtos_files("ThreadX", TASKS, 64, 1000, "rtos", 4)
        with pytest.raises(CodegenError, match="multiple"):
            generate_rtos_files(
                "FreeRTOS", [RtosTask(name="a", priority=1, stack_size=1001)], 8, 1000, "r", 4
            )

    def test_missing_stack_is_a_validation_error(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RtosTask.model_validate({"name": "a", "priority": 1})
