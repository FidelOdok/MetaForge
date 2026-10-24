"""Firmware adapter: PlatformIO build and Renode / Espressif QEMU emulation (FORGE-674/677).

The parsers are checked against output captured from the real tools in the
firmware-adapter image (``tests/unit/fixtures/firmware/``); the handlers are
checked with the binaries absent and with fake binaries standing in for them,
so this suite needs no toolchain. The live checks are in
``tests/integration/test_firmware_adapter_live.py`` and run in the container.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from tool_registry.tools.firmware.adapter import (
    RENODE_BOARDS,
    FirmwareServer,
    FirmwareToolNotAvailableError,
    _append_build_flags,
    _esp_chip,
    _ini_section,
    _merge_flash,
    check_memory,
    check_uart,
    freertos_port,
    missing_package,
    parse_diagnostics,
    parse_renode_reads,
    parse_size,
    parse_toolchain,
    renode_script,
)
from tool_registry.tools.firmware.config import FirmwareConfig

FIXTURES = Path(__file__).parent / "fixtures" / "firmware"


def _core_dir(tmp_path: Path) -> Path:
    """A PlatformIO core dir with the two board manifests the tests use."""
    core = tmp_path / "pio-core"
    for platform, board, mcu in (
        ("ststm32", "disco_f407vg", "stm32f407vgt6"),
        ("espressif32", "esp32dev", "esp32"),
    ):
        boards = core / "platforms" / platform / "boards"
        boards.mkdir(parents=True, exist_ok=True)
        (boards / f"{board}.json").write_text(
            json.dumps({"name": board, "build": {"mcu": mcu}, "frameworks": []})
        )
    return core


def _server(tmp_path: Path, **kw: str) -> FirmwareServer:
    kw.setdefault("platformio_core_dir", str(_core_dir(tmp_path)))
    return FirmwareServer(FirmwareConfig(work_dir=str(tmp_path), **kw))


# -- parsers -----------------------------------------------------------------


class TestParseDiagnostics:
    def test_gcc_errors_and_warnings_relative_to_the_project(self) -> None:
        log = (
            "/ws/p/src/main.c:12:5: error: 'foo' undeclared (first use in this function)\n"
            "/ws/p/src/main.c:20:9: warning: unused variable 'x' [-Wunused-variable]\n"
            "/ws/p/include/board.h:3:10: fatal error: missing.h: No such file or directory\n"
        )
        diags = parse_diagnostics(log, "/ws/p")
        assert [(d["file"], d["line"], d["column"], d["severity"]) for d in diags] == [
            ("src/main.c", 12, 5, "error"),
            ("src/main.c", 20, 9, "warning"),
            ("include/board.h", 3, 10, "error"),
        ]
        assert diags[0]["message"].startswith("'foo' undeclared")

    def test_linker_undefined_reference_and_overflow_are_errors(self) -> None:
        log = (
            ".pio/build/app/src/main.o: in function `main':\n"
            "src/main.c:(.text.main+0x8): undefined reference to `app_tasks_create'\n"
            "/opt/x/ld: firmware.elf section `.bss' will not fit in region `RAM'\n"
            "/opt/x/ld: region `RAM' overflowed by 4096 bytes\n"
            "collect2: error: ld returned 1 exit status\n"
        )
        diags = parse_diagnostics(log)
        msgs = [d["message"] for d in diags]
        assert "undefined reference to `app_tasks_create'" in msgs
        assert "region `RAM' overflowed by 4096 bytes" in msgs
        assert all(d["severity"] == "error" for d in diags)

    def test_duplicates_are_reported_once(self) -> None:
        line = "src/a.c:1:1: warning: x\n"
        assert len(parse_diagnostics(line * 3)) == 1

    def test_clean_log_has_none(self) -> None:
        assert parse_diagnostics("Compiling .pio/build/app/src/main.o\n[SUCCESS]\n") == []


class TestMissingPackage:
    def test_offline_download_is_named(self) -> None:
        # Recorded offline in the image: a Nucleo-H753ZI build when only the F4
        # STM32Cube package was baked in.
        log = (
            "Processing app (board: nucleo_h753zi; framework: stm32cube; "
            "platform: platformio/ststm32@20.0.0)\n"
            "Tool Manager: Installing platformio/framework-stm32cubeh7 @ ~1.13.0\n"
            "HTTPClientError: \n"
        )
        assert missing_package(log) == "platformio/framework-stm32cubeh7 @ ~1.13.0"

    def test_a_compile_error_is_not_a_missing_package(self) -> None:
        assert missing_package("src/main.c:1:1: error: x\n") is None


class TestParseSize:
    def test_flash_and_ram(self) -> None:
        log = (
            "RAM:   [          ]   0.0% (used 44 bytes from 131072 bytes)\n"
            "Flash: [          ]   0.1% (used 604 bytes from 1048576 bytes)\n"
        )
        size = parse_size(log)
        assert size["ram"] == {"used_bytes": 44, "total_bytes": 131072, "used_pct": 0.03}
        assert size["flash"] == {"used_bytes": 604, "total_bytes": 1048576, "used_pct": 0.06}

    def test_absent_when_the_build_did_not_link(self) -> None:
        assert parse_size("error: x\n") == {"flash": None, "ram": None}


class TestParseToolchain:
    def test_platform_and_packages(self) -> None:
        log = (
            "PLATFORM: ST STM32 (20.0.0) > ST STM32F4DISCOVERY\n"
            "HARDWARE: STM32F407VGT6 168MHz, 128KB RAM, 1MB Flash\n"
            "PACKAGES: \n"
            " - framework-stm32cubef4 @ 1.28.2 \n"
            " - toolchain-gccarmnoneeabi @ 1.120301.0 (12.3.1) \n"
            "LDF: Library Dependency Finder\n"
        )
        info = parse_toolchain(log)
        assert info["platform"] == "ST STM32"
        assert info["platform_version"] == "20.0.0"
        assert info["packages"] == {
            "framework-stm32cubef4": "1.28.2",
            "toolchain-gccarmnoneeabi": "1.120301.0",
        }


class TestRecordedOutput:
    """Output captured in the firmware-adapter image (PlatformIO 6.2.0, ststm32
    20.0.0, Renode 1.17.0): the generated FreeRTOS app of the live test, a
    deliberately broken main.c, and Renode's console for the app's run."""

    def test_successful_stm32_freertos_build(self) -> None:
        log = (FIXTURES / "stm32_build_ok.log").read_text()
        size = parse_size(log)
        assert size["flash"] is not None and size["flash"]["used_bytes"] > 0
        assert size["ram"] is not None and size["ram"]["total_bytes"] == 131072
        tc = parse_toolchain(log)
        assert tc["platform"] == "ST STM32" and tc["platform_version"] == "20.0.0"
        assert "toolchain-gccarmnoneeabi" in tc["packages"]
        assert [d for d in parse_diagnostics(log) if d["severity"] == "error"] == []

    def test_failed_stm32_build(self) -> None:
        log = (FIXTURES / "stm32_build_error.log").read_text()
        diags = parse_diagnostics(log)
        errors = [d for d in diags if d["severity"] == "error"]
        warnings = [d for d in diags if d["severity"] == "warning"]
        assert [(e["file"], e["line"]) for e in errors] == [("src/main.c", 5)]
        assert "undeclared" in errors[0]["message"]
        assert [(w["file"], w["line"]) for w in warnings] == [("src/main.c", 4)]
        assert parse_size(log) == {"flash": None, "ram": None}

    def test_renode_console_read(self) -> None:
        console = (FIXTURES / "renode_console.log").read_text()
        assert parse_renode_reads(console) == {0x40020C14: 0x1000}

    def test_renode_script_matches_the_recorded_one(self) -> None:
        recorded = (FIXTURES / "renode_run.resc").read_text().splitlines()
        assert recorded[0] == 'mach create "metaforge"'
        assert recorded[-1] == "quit"
        assert any(line.startswith('echo "MFREAD 0x40020C14"') for line in recorded)


# -- checks ------------------------------------------------------------------


class TestCheckUart:
    def test_expectations_must_appear_in_order(self) -> None:
        uart = "boot\ntask sensor started\ntask telemetry started\n"
        results, ok = check_uart(uart, ["sensor started", "telemetry started"], [])
        assert ok and [r["status"] for r in results] == ["pass", "pass"]
        results, ok = check_uart(uart, ["telemetry started", "sensor started"], [])
        assert not ok and [r["status"] for r in results] == ["pass", "fail"]

    def test_forbidden_line_fails(self) -> None:
        results, ok = check_uart("HardFault at 0x08000123\n", [], ["HardFault"])
        assert not ok
        assert results[0] == {
            "kind": "uart_forbid",
            "pattern": "HardFault",
            "status": "fail",
            "matched": "HardFault",
        }


class TestCheckMemory:
    def test_masked_comparison(self) -> None:
        results, ok, unknown = check_memory(
            [{"address": "0x20000000", "value": "0x12", "mask": "0xFF"}],
            {0x20000000: 0xABCD0012},
            "renode",
        )
        assert ok and not unknown and results[0]["status"] == "pass"

    def test_mismatch_fails(self) -> None:
        _, ok, _ = check_memory([{"address": 0x10, "value": 1}], {0x10: 2}, "renode")
        assert not ok

    def test_no_reads_is_not_established(self) -> None:
        results, ok, unknown = check_memory([{"address": 0x10, "value": 1}], None, "qemu")
        assert ok and unknown
        assert results[0]["status"] == "not_established"


class TestRenodeScript:
    def test_loads_captures_runs_reads_and_quits(self) -> None:
        script = renode_script(
            "platforms/boards/stm32f4_discovery-kit.repl",
            "/ws/fw.elf",
            "sysbus.usart2",
            "/ws/uart.txt",
            2.0,
            [0x20000000],
        )
        lines = script.splitlines()
        assert lines[0] == 'mach create "metaforge"'
        assert "sysbus LoadELF @/ws/fw.elf" in lines
        assert "sysbus.usart2 CreateFileBackend @/ws/uart.txt true" in lines
        assert 'emulation RunFor "2"' in lines
        assert "sysbus ReadDoubleWord 0x20000000" in lines
        assert lines[-1] == "quit"

    def test_read_values_follow_their_markers(self) -> None:
        out = "(monitor) MFREAD 0x20000000\n0x00000012\nMFREAD 0x20000004\n0xDEADBEEF\n"
        assert parse_renode_reads(out) == {0x20000000: 0x12, 0x20000004: 0xDEADBEEF}


# -- helpers -----------------------------------------------------------------


class TestHelpers:
    def test_freertos_port_by_series(self) -> None:
        assert freertos_port("STM32F407VGT6") == "ARM_CM4F"
        assert freertos_port("stm32f103c8t6") == "ARM_CM3"
        assert freertos_port("STM32H743ZIT6") == "ARM_CM7/r0p1"
        assert freertos_port("ESP32") is None

    def test_ini_section_merges_env_defaults(self) -> None:
        ini = (
            "[env]\nplatform = ststm32\n\n"
            "[env:a]\nboard = disco_f407vg ; comment\n[env:b]\nboard = x\n"
        )
        assert _ini_section(ini, None) == {"platform": "ststm32", "board": "disco_f407vg"}
        assert _ini_section(ini, "b")["board"] == "x"

    def test_append_build_flags_extends_or_adds(self, tmp_path: Path) -> None:
        ini = tmp_path / "platformio.ini"
        ini.write_text("[env:a]\nboard = b\nbuild_flags = -DX\n[env:c]\nboard = d\n")
        _append_build_flags(ini, "a", ["-Iinc"])
        _append_build_flags(ini, "c", ["-Iinc"])
        text = ini.read_text()
        assert "build_flags = -DX -Iinc" in text
        assert text.rstrip().endswith("build_flags = -Iinc")

    def test_merge_flash_places_parts_on_erased_flash(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a.bin", tmp_path / "b.bin"
        a.write_bytes(b"\x01\x02")
        b.write_bytes(b"\x03")
        out = tmp_path / "flash.bin"
        _merge_flash(out, [(0x10, a), (0x20, b)], size=0x40)
        data = out.read_bytes()
        assert len(data) == 0x40
        assert data[0x10:0x12] == b"\x01\x02" and data[0x20] == 3 and data[0] == 0xFF
        with pytest.raises(ValueError, match="does not fit"):
            _merge_flash(out, [(0x3F, a)], size=0x40)

    def test_esp_chip_from_sdkconfig_then_board(self, tmp_path: Path) -> None:
        elf = tmp_path / ".pio" / "build" / "app" / "firmware.elf"
        elf.parent.mkdir(parents=True)
        elf.write_bytes(b"")
        assert _esp_chip("esp32dev", elf) == "esp32"
        (tmp_path / "sdkconfig.app").write_text('CONFIG_IDF_TARGET="esp32s3"\n')
        assert _esp_chip("esp32dev", elf) == "esp32s3"
        assert _esp_chip("disco_f407vg", tmp_path / "x.elf") is None


# -- handlers ----------------------------------------------------------------


class TestBuildHandler:
    async def test_without_platformio_it_says_so(self, tmp_path: Path) -> None:
        server = _server(tmp_path, pio="definitely-not-pio")
        with pytest.raises(FirmwareToolNotAvailableError, match="firmware-adapter container"):
            await server.build({"project_path": "p"})

    async def test_needs_exactly_one_source(self, tmp_path: Path, fake_pio: str) -> None:
        server = _server(tmp_path, pio=fake_pio)
        with pytest.raises(ValueError, match="exactly one"):
            await server.build({})

    async def test_path_outside_the_workspace_is_refused(
        self, tmp_path: Path, fake_pio: str
    ) -> None:
        server = _server(tmp_path, pio=fake_pio)
        with pytest.raises(ValueError, match="outside the adapter workspace"):
            await server.build({"project_path": "../../etc"})

    async def test_files_with_unsafe_paths_are_refused(self, tmp_path: Path, fake_pio: str) -> None:
        server = _server(tmp_path, pio=fake_pio)
        with pytest.raises(ValueError, match="invalid file path"):
            await server.build(
                {
                    "files": [{"path": "../evil.c", "content": ""}],
                    "board": "disco_f407vg",
                    "framework": "stm32cube",
                }
            )

    async def test_successful_build_reports_size_and_elf(
        self, tmp_path: Path, fake_pio: str
    ) -> None:
        server = _server(tmp_path, pio=fake_pio)
        out = await server.build(
            {
                "files": [{"path": "src/main.c", "content": "int main(void){for(;;);}"}],
                "board": "disco_f407vg",
                "framework": "stm32cube",
            }
        )
        assert out["verdict"] == "pass" and out["success"]
        assert out["error_count"] == 0
        assert out["flash"]["used_bytes"] == 604
        assert out["elf_path"].endswith(".pio/build/app/firmware.elf")
        assert out["platform"] == "ststm32"
        ini = (tmp_path / out["project_dir"] / "platformio.ini").read_text()
        assert "platform = " in ini and "board = disco_f407vg" in ini

    async def test_caller_project_is_copied_not_modified(
        self, tmp_path: Path, fake_pio: str
    ) -> None:
        src = tmp_path / "proj"
        (src / "src").mkdir(parents=True)
        (src / "platformio.ini").write_text("[env:a]\nplatform = ststm32\nboard = disco_f407vg\n")
        (src / "src" / "main.c").write_text("int main(void){return 0;}")
        before = sorted(p.relative_to(src).as_posix() for p in src.rglob("*"))
        server = _server(tmp_path, pio=fake_pio)
        out = await server.build({"project_path": "proj"})
        assert out["verdict"] == "pass"
        assert sorted(p.relative_to(src).as_posix() for p in src.rglob("*")) == before

    async def test_failed_build_reports_errors_and_log(self, tmp_path: Path, fake_pio: str) -> None:
        server = _server(tmp_path, pio=fake_pio)
        out = await server.build(
            {
                "files": [{"path": "src/main.c", "content": "FAIL"}],
                "board": "disco_f407vg",
                "framework": "stm32cube",
            }
        )
        assert out["verdict"] == "fail" and not out["success"]
        assert out["error_count"] == 1
        assert out["diagnostics"][0]["file"] == "src/main.c"
        assert out["elf_path"] is None
        assert "log_tail" in out

    async def test_board_platform_comes_from_the_installed_manifests(
        self, tmp_path: Path, fake_pio: str
    ) -> None:
        server = _server(tmp_path, pio=fake_pio)
        assert server._board_info("disco_f407vg") == {
            "id": "disco_f407vg",
            "name": "disco_f407vg",
            "platform": "ststm32",
            "mcu": "STM32F407VGT6",
            "frameworks": [],
        }

    async def test_unknown_board_is_refused(self, tmp_path: Path, fake_pio: str) -> None:
        server = _server(tmp_path, pio=fake_pio)
        with pytest.raises(ValueError, match="not a board of the installed platforms"):
            await server.build(
                {"files": [{"path": "src/m.c", "content": ""}], "board": "uno", "framework": "x"}
            )

    async def test_freertos_kernel_is_added_for_an_stm32_project_with_a_config(
        self, tmp_path: Path, fake_pio: str
    ) -> None:
        kernel = tmp_path / "kernel"
        (kernel / "include").mkdir(parents=True)
        (kernel / "portable" / "GCC" / "ARM_CM4F").mkdir(parents=True)
        (kernel / "portable" / "MemMang").mkdir(parents=True)
        for name in (
            "tasks.c",
            "queue.c",
            "list.c",
            "timers.c",
            "event_groups.c",
            "stream_buffer.c",
        ):
            (kernel / name).write_text("")
        (kernel / "include" / "FreeRTOS.h").write_text("")
        (kernel / "portable" / "GCC" / "ARM_CM4F" / "port.c").write_text("")
        (kernel / "portable" / "MemMang" / "heap_4.c").write_text("")
        server = _server(tmp_path, pio=fake_pio, freertos_kernel_dir=str(kernel))
        out = await server.build(
            {
                "files": [
                    {"path": "src/main.c", "content": ""},
                    {"path": "src/rtos/FreeRTOSConfig.h", "content": ""},
                ],
                "board": "disco_f407vg",
                "framework": "stm32cube",
            }
        )
        assert out["freertos"] == {
            "added": True,
            "port": "ARM_CM4F",
            "heap": "heap_4",
            "config": "src/rtos",
            # The CM4F port refuses to compile without a hardware-FPU target.
            "fpu_flags": ["-mfpu=fpv4-sp-d16", "-mfloat-abi=softfp"],
        }
        ini = (tmp_path / out["project_dir"] / "platformio.ini").read_text()
        assert "-mfloat-abi=softfp" in ini
        lib = tmp_path / out["project_dir"] / "lib" / "FreeRTOS-Kernel"
        assert (lib / "port" / "port.c").is_file() and (lib / "heap_4.c").is_file()
        assert json.loads((lib / "library.json").read_text())["name"] == "FreeRTOS-Kernel"


class TestRunEmulatedHandler:
    async def test_unmodelled_board_is_not_established(self, tmp_path: Path) -> None:
        (tmp_path / "fw.elf").write_bytes(b"\x7fELF")
        out = await _server(tmp_path).run_emulated(
            {"elf_path": "fw.elf", "board": "nucleo_l031k6", "uart_expect": ["hello"]}
        )
        assert out["verdict"] == "not_established"
        assert out["emulator"] is None
        assert "no emulator model" in out["reason"]
        assert out["assertions"][0]["status"] == "not_established"

    async def test_needs_a_check(self, tmp_path: Path) -> None:
        (tmp_path / "fw.elf").write_bytes(b"\x7fELF")
        with pytest.raises(ValueError, match="at least one check"):
            await _server(tmp_path).run_emulated({"elf_path": "fw.elf", "board": "x"})

    async def test_missing_elf_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="elf_path not found"):
            await _server(tmp_path).run_emulated(
                {"elf_path": "nope.elf", "board": "x", "uart_expect": ["a"]}
            )

    async def test_renode_absent_is_not_established(self, tmp_path: Path) -> None:
        (tmp_path / "fw.elf").write_bytes(b"\x7fELF")
        out = await _server(tmp_path, renode="no-renode-here").run_emulated(
            {"elf_path": "fw.elf", "board": "disco_f407vg", "uart_expect": ["hi"]}
        )
        assert out["verdict"] == "not_established"
        assert out["reason"] == "renode not found"

    async def test_renode_run_passes_and_fails_on_the_uart(
        self, tmp_path: Path, fake_renode: str
    ) -> None:
        (tmp_path / "fw.elf").write_bytes(b"\x7fELF")
        server = _server(tmp_path, renode=fake_renode)
        ok = await server.run_emulated(
            {
                "elf_path": "fw.elf",
                "board": "disco_f407vg",
                "uart_expect": ["MetaForge boot", "task \\w+ started"],
                "uart_forbid": ["HardFault"],
                "memory_expect": [{"address": "0x20000000", "value": "0x12"}],
            }
        )
        assert ok["verdict"] == "pass", ok
        assert ok["emulator"] == "renode"
        bad = await server.run_emulated(
            {"elf_path": "fw.elf", "board": "disco_f407vg", "uart_expect": ["never printed"]}
        )
        assert bad["verdict"] == "fail"

    async def test_esp32_without_bins_is_not_established(
        self, tmp_path: Path, fake_qemu: str
    ) -> None:
        elf = tmp_path / "p" / ".pio" / "build" / "app" / "firmware.elf"
        elf.parent.mkdir(parents=True)
        elf.write_bytes(b"\x7fELF")
        out = await _server(tmp_path, qemu_xtensa=fake_qemu).run_emulated(
            {"elf_path": "p/.pio/build/app/firmware.elf", "board": "esp32dev", "uart_expect": ["x"]}
        )
        assert out["verdict"] == "not_established"
        assert "missing" in out["reason"]

    async def test_esp32_run_merges_flash_and_reads_the_uart(
        self, tmp_path: Path, fake_qemu: str
    ) -> None:
        build = tmp_path / "p" / ".pio" / "build" / "app"
        build.mkdir(parents=True)
        (build / "firmware.elf").write_bytes(b"\x7fELF")
        for name in ("bootloader.bin", "partitions.bin", "firmware.bin"):
            (build / name).write_bytes(b"\x00" * 16)
        out = await _server(tmp_path, qemu_xtensa=fake_qemu).run_emulated(
            {
                "elf_path": "p/.pio/build/app/firmware.elf",
                "board": "esp32dev",
                "sim_time_s": 1,
                "uart_expect": ["app_main"],
                "memory_expect": [{"address": 0x3FFB0000, "value": 1}],
            }
        )
        assert out["emulator"] == "espressif-qemu"
        # The UART check passes; QEMU memory reads are not supported, so the
        # run as a whole cannot be called a pass.
        assert out["assertions"][0]["status"] == "pass"
        assert out["assertions"][1]["status"] == "not_established"
        assert out["verdict"] == "not_established"
        assert (tmp_path / out["run_dir"] / "flash.bin").stat().st_size == 4 * 1024 * 1024


def test_renode_boards_name_a_repl_and_a_uart() -> None:
    for spec in RENODE_BOARDS.values():
        assert spec["repl"].endswith(".repl") and spec["uart"].startswith("sysbus.")


def test_every_renode_board_is_warmed_in_the_image() -> None:
    """Renode downloads an SVD per board on first load; the image must have it cached.

    Otherwise a run on a board the image never loaded reaches for the network,
    which the adapter promises it does not need.
    """
    dockerfile = (
        Path(__file__).resolve().parents[2] / "tool_registry" / "tools" / "firmware" / "Dockerfile"
    ).read_text()
    for board, spec in RENODE_BOARDS.items():
        assert f"LoadPlatformDescription @{spec['repl']}" in dockerfile, board


# -- fake binaries -------------------------------------------------------------


def _script(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def fake_pio(tmp_path: Path) -> str:
    """`pio run -d <dir>` with recorded-shape output."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    return _script(
        bindir / "pio",
        r"""
import json, sys, pathlib
args = sys.argv[1:]
d = pathlib.Path(args[args.index("-d") + 1])
main = (d / "src" / "main.c")
print("PLATFORM: ST STM32 (20.0.0) > ST STM32F4DISCOVERY")
print("PACKAGES: ")
print(" - toolchain-gccarmnoneeabi @ 1.120301.0 (12.3.1) ")
print("")
if main.exists() and main.read_text() == "FAIL":
    print(f"{d}/src/main.c:1:1: error: unknown type name 'FAIL'")
    print("*** [.pio/build/app/src/main.o] Error 1")
    sys.exit(1)
out = d / ".pio" / "build" / "app"
out.mkdir(parents=True, exist_ok=True)
(out / "firmware.elf").write_bytes(b"\x7fELF")
print("RAM:   [          ]   0.0% (used 44 bytes from 131072 bytes)")
print("Flash: [          ]   0.1% (used 604 bytes from 1048576 bytes)")
""",
    )


@pytest.fixture
def fake_renode(tmp_path: Path) -> str:
    """Reads the .resc, writes the UART file it names, answers the reads."""
    bindir = tmp_path / "bin-renode"
    bindir.mkdir()
    return _script(
        bindir / "renode",
        r"""
import re, sys, pathlib
script = pathlib.Path(sys.argv[-1]).read_text()
uart = re.search(r"CreateFileBackend @(\S+) true", script).group(1)
pathlib.Path(uart).write_text("MetaForge boot\ntask sensor started\n")
print("Renode v1.17.0")
for m in re.finditer(r'echo "MFREAD (0x[0-9A-F]+)"', script):
    print("MFREAD " + m.group(1))
    print("0x00000012")
""",
    )


@pytest.fixture
def fake_qemu(tmp_path: Path) -> str:
    """Writes to the -serial file:<path> and reports a version."""
    bindir = tmp_path / "bin-qemu"
    bindir.mkdir()
    return _script(
        bindir / "qemu-system-xtensa",
        r"""
import sys, pathlib
args = sys.argv[1:]
if "--version" in args:
    print("QEMU emulator version 9.2.2 (esp_develop_9.2.2_20260417)")
    sys.exit(0)
serial = args[args.index("-serial") + 1].split("file:", 1)[1]
pathlib.Path(serial).write_text("I (31) boot: ESP-IDF\nI (300) main_task: Calling app_main()\n")
""",
    )
