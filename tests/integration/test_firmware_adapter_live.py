"""Firmware adapter, live: real PlatformIO, Renode and Espressif QEMU (FORGE-674/677).

Runs where the tools are -- the firmware-adapter image -- and skips anywhere
else. From a checkout::

    docker run --rm -v "$PWD:/src" -w /src -e PYTHONPATH=/src \\
        --entrypoint bash ghcr.io/fidelodok/metaforge-firmware-adapter:latest \\
        -c "pip install -q pytest pytest-asyncio && \\
            python -m pytest -q -p no:cacheprovider tests/integration/test_firmware_adapter_live.py"

The STM32 firmware is the generator's own output: ``generate_hal_files`` and
``generate_rtos_files`` (the generate_hal and configure_rtos skills) write the
pin header, ``FreeRTOSConfig.h`` and the task table; only ``main.c`` and the
two task bodies are written here. ``firmware.build`` compiles them with the
real FreeRTOS kernel, and ``firmware.run_emulated`` boots the ELF on Renode's
STM32F4-Discovery, reads what each task prints on USART2, and reads back the
GPIO register the generated pin macros drove.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from domain_agents.firmware.codegen import RtosTask, generate_hal_files, generate_rtos_files
from tool_registry.tools.firmware.adapter import FirmwareServer
from tool_registry.tools.firmware.config import FirmwareConfig

pytestmark = pytest.mark.skipif(
    shutil.which("pio") is None, reason="needs PlatformIO (the firmware-adapter image)"
)

STM32_MAIN = r"""
#include "stm32f4xx_hal.h"
#include "FreeRTOS.h"
#include "task.h"
#include "board_pins.h"

int app_tasks_create(void);

static void uart_init(void)
{
    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;
    USART2->BRR = 0x16D; /* 115200 baud at 42 MHz APB1 */
    USART2->CR1 = USART_CR1_UE | USART_CR1_TE;
}

void uart_puts(const char *s)
{
    while (*s) {
        while (!(USART2->SR & USART_SR_TXE)) {
        }
        USART2->DR = (uint8_t)*s++;
    }
}

void sensor_task(void *arg)
{
    (void)arg;
    uart_puts("task sensor started\r\n");
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

void telemetry_task(void *arg)
{
    (void)arg;
    uart_puts("task telemetry started\r\n");
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(250));
        uart_puts("telemetry tick\r\n");
    }
}

int main(void)
{
    SystemCoreClockUpdate();
    uart_init();
    /* The generated pin macros drive the status LED (PD12). */
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIODEN;
    BOARD_STATUS_LED_PORT->MODER |= 1u << (12 * 2);
    BOARD_STATUS_LED_PORT->BSRR = BOARD_STATUS_LED_PIN;
    uart_puts("MetaForge boot\r\n");
    if (app_tasks_create() != 0) {
        uart_puts("task create failed\r\n");
    }
    vTaskStartScheduler();
    uart_puts("scheduler returned\r\n");
    for (;;) {
    }
}
"""


def _stm32_project(root: Path) -> Path:
    project = root / "stm32_freertos"
    hal, _ = generate_hal_files(
        "STM32F4",
        [
            {"signal": "STATUS_LED", "pin": "PD12", "direction": "output"},
            {"signal": "DEBUG_TX", "pin": "PA2", "peripheral": "USART2", "function": "TX"},
        ],
        "src/board",
        "live test pin map",
    )
    rtos, _ = generate_rtos_files(
        "FreeRTOS",
        [
            RtosTask(name="sensor", priority=3, stack_size=1024),
            RtosTask(name="telemetry", priority=2, stack_size=1024),
        ],
        heap_size_kb=16,
        tick_rate_hz=1000,
        output_dir="src/rtos",
        stack_word_bytes=4,
    )
    for f in [*hal, *rtos]:
        dest = project / f.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f.content)
    (project / "src" / "main.c").write_text(STM32_MAIN)
    (project / "platformio.ini").write_text(
        "[env:disco]\n"
        "platform = platformio/ststm32@20.0.0\n"
        "board = disco_f407vg\n"
        "framework = stm32cube\n"
        "build_flags = -Isrc/board -Isrc/rtos\n"
    )
    return project


@pytest.fixture
def server(tmp_path: Path) -> FirmwareServer:
    return FirmwareServer(FirmwareConfig.from_env().model_copy(update={"work_dir": str(tmp_path)}))


async def test_generated_freertos_app_builds_and_boots_on_renode(
    server: FirmwareServer, tmp_path: Path
) -> None:
    _stm32_project(tmp_path)
    built = await server.build({"project_path": "stm32_freertos"})
    assert built["verdict"] == "pass", built.get("log_tail") or built
    assert built["freertos"]["added"] and built["freertos"]["port"] == "ARM_CM4F"
    assert built["flash"]["used_bytes"] > 0 and built["ram"]["total_bytes"] == 131072
    assert built["toolchain"]["platform_version"] == "20.0.0"

    ran = await server.run_emulated(
        {
            "elf_path": built["elf_path"],
            "board": "disco_f407vg",
            "sim_time_s": 1.0,
            "uart_expect": [
                r"MetaForge boot",
                r"task sensor started",
                r"task telemetry started",
                r"telemetry tick",
            ],
            "uart_forbid": [r"task create failed", r"scheduler returned"],
            # GPIOD ODR: the generated BOARD_STATUS_LED_PIN (PD12) was driven high.
            "memory_expect": [{"address": "0x40020C14", "value": "0x1000", "mask": "0x1000"}],
        }
    )
    assert ran["emulator"] == "renode"
    assert ran["verdict"] == "pass", ran


NUCLEO_HELLO = r"""
#include "%(header)s"

int main(void)
{
    %(clock)s
    USART3->BRR = 0x1D4C;
    USART3->CR1 = USART_CR1_UE | USART_CR1_TE;
    for (const char *s = "MetaForge hello from %(board)s\r\n"; *s; s++) {
        while (!(USART3->ISR & USART_ISR_TXE_TXFNF)) {
        }
        USART3->TDR = (uint8_t)*s;
    }
    for (;;) {
    }
}
"""


@pytest.mark.parametrize(
    ("board", "header", "clock"),
    [
        (
            "nucleo_f767zi",
            "stm32f7xx.h",
            "RCC->APB1ENR |= RCC_APB1ENR_USART3EN;",
        ),
        (
            "nucleo_h753zi",
            "stm32h7xx.h",
            "RCC->APB1LENR |= RCC_APB1LENR_USART3EN;",
        ),
    ],
)
async def test_nucleo_boards_boot_on_renode(
    server: FirmwareServer, board: str, header: str, clock: str
) -> None:
    source = NUCLEO_HELLO % {"header": header, "clock": clock, "board": board}
    if board == "nucleo_f767zi":
        source = source.replace("USART_ISR_TXE_TXFNF", "USART_ISR_TXE")
    built = await server.build(
        {
            "files": [{"path": "src/main.c", "content": source}],
            "board": board,
            "framework": "stm32cube",
        }
    )
    assert built["verdict"] == "pass", built.get("log_tail") or built
    ran = await server.run_emulated(
        {
            "elf_path": built["elf_path"],
            "board": board,
            "sim_time_s": 0.2,
            "uart_expect": [f"MetaForge hello from {board}"],
        }
    )
    assert ran["emulator"] == "renode"
    assert ran["verdict"] == "pass", ran


DEFECTIVE = r"""
#include <string.h>

int main(void)
{
    char buf[4];
    int unused;
    strcpy(buf, "too long string");
    int *p = 0;
    return *p + buf[5];
}
"""


async def test_static_check_finds_real_defects(server: FirmwareServer) -> None:
    out = await server.static_check(
        {
            "files": [{"path": "src/main.c", "content": DEFECTIVE}],
            "board": "disco_f407vg",
            "framework": "stm32cube",
        }
    )
    assert out["verdict"] == "fail", out
    ids = {(f["id"], f["severity"]) for f in out["findings"]}
    assert ("nullPointer", "high") in ids
    assert ("arrayIndexOutOfBounds", "high") in ids
    assert ("unusedVariable", "low") in ids
    assert all(f["file"] == "src/main.c" for f in out["findings"])


async def test_static_check_suppression_and_clean_code(server: FirmwareServer) -> None:
    clean = "int main(void)\n{\n    int unused;\n    for (;;) {\n    }\n}\n"
    out = await server.static_check(
        {
            "files": [{"path": "src/main.c", "content": clean}],
            "board": "disco_f407vg",
            "framework": "stm32cube",
        }
    )
    assert out["verdict"] == "pass", out
    assert out["counts"]["low"] >= 1  # the unused variable, which does not fail it
    suppressed = await server.static_check(
        {
            "files": [{"path": "src/main.c", "content": clean}],
            "board": "disco_f407vg",
            "framework": "stm32cube",
            "fail_on": "low",
            "suppressions": [{"id": "unusedVariable", "file": "src/main.c", "reason": "live test"}],
        }
    )
    assert "unusedVariable" not in {f["id"] for f in suppressed["findings"]}, suppressed


async def test_clang_tidy_runs_offline(server: FirmwareServer) -> None:
    out = await server.static_check(
        {
            "files": [{"path": "src/main.c", "content": DEFECTIVE}],
            "board": "disco_f407vg",
            "framework": "stm32cube",
            "tools": ["cppcheck", "clangtidy"],
            "fail_on": "low",
        }
    )
    assert {r["tool"] for r in out["runs"]} == {"cppcheck", "clangtidy"}, out
    assert all(r["succeeded"] for r in out["runs"]), out
    assert out["verdict"] == "fail"


async def test_a_broken_source_fails_with_the_compiler_error(server: FirmwareServer) -> None:
    out = await server.build(
        {
            "files": [{"path": "src/main.c", "content": "int main(void) { return undeclared; }\n"}],
            "board": "disco_f407vg",
            "framework": "stm32cube",
        }
    )
    assert out["verdict"] == "fail"
    err = next(d for d in out["diagnostics"] if d["severity"] == "error")
    assert err["file"] == "src/main.c" and err["line"] == 1
    assert "undeclared" in err["message"]


@pytest.mark.skipif(shutil.which("qemu-system-xtensa") is None, reason="needs Espressif QEMU")
async def test_esp_idf_app_builds_and_boots_on_espressif_qemu(server: FirmwareServer) -> None:
    built = await server.build(
        {
            "files": [
                {
                    "path": "CMakeLists.txt",
                    "content": "cmake_minimum_required(VERSION 3.16.0)\n"
                    "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\nproject(hello)\n",
                },
                {
                    "path": "src/CMakeLists.txt",
                    "content": 'idf_component_register(SRCS "main.c")\n',
                },
                {
                    "path": "src/main.c",
                    "content": "#include <stdio.h>\nvoid app_main(void)\n{\n"
                    '    printf("MetaForge hello from ESP32\\n");\n}\n',
                },
            ],
            "board": "esp32dev",
            "framework": "espidf",
        }
    )
    assert built["verdict"] == "pass", built.get("log_tail") or built
    ran = await server.run_emulated(
        {
            "elf_path": built["elf_path"],
            "board": "esp32dev",
            "sim_time_s": 4,
            "uart_expect": [r"MetaForge hello from ESP32"],
            "uart_forbid": [r"Guru Meditation"],
        }
    )
    assert ran["emulator"] == "espressif-qemu"
    assert ran["verdict"] == "pass", ran
