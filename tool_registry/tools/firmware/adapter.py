"""Firmware build and emulation adapter (FORGE-674, FORGE-677).

The firmware skills write C source as text (``domain_agents/firmware``); until
this adapter nothing compiled it, so a generated HAL or RTOS configuration was
always "not attempted". Two tools close that:

``firmware.build``
    Compiles a PlatformIO project with the toolchains baked into the
    ``firmware-adapter`` image (STM32 via STM32Cube, ESP32 via ESP-IDF). The
    caller's project is copied into a run directory first, so its sources are
    only read. A project that carries a ``FreeRTOSConfig.h`` on an STM32 gets
    the FreeRTOS kernel compiled in (STM32Cube does not build it). The result
    is the compiler's own diagnostics, parsed to file:line, and flash/RAM use
    from PlatformIO's size check -- nothing is estimated.

``firmware.run_emulated``
    Runs a built ELF on an emulated board: Renode for the STM32 boards it has
    a platform description for, Espressif's QEMU fork for the ESP32. The
    caller states what must happen (UART lines, in order; lines that must not
    appear; memory words, on Renode) and gets pass/fail per assertion. A board
    neither emulator models is ``not_established``, never a pass.

Both emulators run as separate programs; this process links neither.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.firmware.config import FirmwareConfig

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.firmware.adapter")

#: Platforms whose toolchains the image carries (installed by the seed builds).
INSTALLED_PLATFORMS = ("ststm32", "espressif32")

#: PlatformIO board -> Renode platform description and the UART the firmware
#: prints on. Only boards checked against the Renode release in the image.
#: The Dockerfile loads each once at image build so Renode caches the SVD it
#: downloads; tests/unit/test_firmware_adapter.py keeps the two lists in step.
RENODE_BOARDS: dict[str, dict[str, str]] = {
    "disco_f407vg": {
        "repl": "platforms/boards/stm32f4_discovery-kit.repl",
        "uart": "sysbus.usart2",
    },
    # Nucleo-144: the ST-LINK virtual COM port is USART3 (PD8/PD9).
    "nucleo_f767zi": {
        "repl": "platforms/boards/nucleo_f767zi.repl",
        "uart": "sysbus.usart3",
    },
    "nucleo_h753zi": {
        "repl": "platforms/boards/nucleo_h753zi.repl",
        "uart": "sysbus.usart3",
    },
}

#: ESP32 chip -> (QEMU binary attribute on the config, machine, bootloader offset).
#: Only the classic ESP32 is checked end to end; the S3 and C3 need their own
#: toolchains in the image and a live check before they are listed here.
ESP_QEMU: dict[str, tuple[str, str, int]] = {
    "esp32": ("qemu_xtensa", "esp32", 0x1000),
}

#: STM32 series -> FreeRTOS GCC port directory.
_FREERTOS_PORTS: tuple[tuple[str, str], ...] = (
    ("stm32f0", "ARM_CM0"),
    ("stm32g0", "ARM_CM0"),
    ("stm32l0", "ARM_CM0"),
    ("stm32f1", "ARM_CM3"),
    ("stm32f2", "ARM_CM3"),
    ("stm32l1", "ARM_CM3"),
    ("stm32f3", "ARM_CM4F"),
    ("stm32f4", "ARM_CM4F"),
    ("stm32g4", "ARM_CM4F"),
    ("stm32l4", "ARM_CM4F"),
    ("stm32wb", "ARM_CM4F"),
    ("stm32f7", "ARM_CM7/r0p1"),
    ("stm32h7", "ARM_CM7/r0p1"),
)

#: FreeRTOS ports that need a hardware-FPU target, and the default FPU.
_FPU_FLAGS: dict[str, str] = {"ARM_CM4F": "fpv4-sp-d16", "ARM_CM7/r0p1": "fpv5-sp-d16"}


def _fpu_for(mcu: str, default: str) -> str:
    """The FPU of a part: the H7's is double precision, the rest as the port."""
    return "fpv5-d16" if mcu.lower().startswith("stm32h7") else default


_DIAG = re.compile(
    r"^(?P<file>[^\s:][^:\n]*?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<sev>fatal error|error|warning):\s*(?P<msg>.+)$",
    re.MULTILINE,
)
_LD_UNDEFINED = re.compile(
    r"^(?P<file>[^\s:][^:\n]*?):\(.*?\):\s*(?P<msg>undefined reference to .+)$", re.MULTILINE
)
_LD_OVERFLOW = re.compile(r"(?P<msg>region `[^']+' overflowed by \d+ bytes)")
_SIZE = re.compile(
    r"^(?P<kind>RAM|Flash):\s*\[[^\]]*\]\s*(?P<pct>[\d.]+)%\s*"
    r"\(used (?P<used>\d+) bytes from (?P<total>\d+) bytes\)",
    re.MULTILINE,
)
_PLATFORM_LINE = re.compile(r"^PLATFORM:\s*(?P<name>.+?)\s*\((?P<version>[^)]+)\)", re.MULTILINE)
_PACKAGE = re.compile(r"^\s*-\s*(?P<name>[\w.-]+)\s*@\s*(?P<version>[^\s(]+)", re.MULTILINE)
_SAFE_REL = re.compile(r"^[\w./ -]+$")


class FirmwareToolNotAvailableError(RuntimeError):
    """A firmware tool (PlatformIO, Renode, QEMU) is not installed where this runs."""


# ---------------------------------------------------------------------------
# Parsers (pure; tested against recorded PlatformIO / emulator output)
# ---------------------------------------------------------------------------


def parse_diagnostics(log: str, project_dir: str = "") -> list[dict[str, Any]]:
    """Compiler and linker diagnostics from a build log, deduplicated, in order."""
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []

    def add(item: dict[str, Any]) -> None:
        key = (item["file"], item["line"], item["column"], item["severity"], item["message"])
        if key not in seen:
            seen.add(key)
            out.append(item)

    prefix = project_dir.rstrip("/") + "/" if project_dir else ""
    for m in _DIAG.finditer(log):
        sev = "error" if m.group("sev") in ("error", "fatal error") else "warning"
        add(
            {
                "file": _rel(m.group("file"), prefix),
                "line": int(m.group("line")),
                "column": int(m.group("col")) if m.group("col") else None,
                "severity": sev,
                "message": m.group("msg").strip(),
            }
        )
    for m in _LD_UNDEFINED.finditer(log):
        add(
            {
                "file": _rel(m.group("file"), prefix),
                "line": None,
                "column": None,
                "severity": "error",
                "message": m.group("msg").strip(),
            }
        )
    for m in _LD_OVERFLOW.finditer(log):
        add(
            {
                "file": None,
                "line": None,
                "column": None,
                "severity": "error",
                "message": m.group("msg"),
            }
        )
    return out


def _rel(path: str, prefix: str) -> str:
    p = path.strip()
    return p[len(prefix) :] if prefix and p.startswith(prefix) else p


def parse_size(log: str) -> dict[str, dict[str, Any] | None]:
    """Flash and RAM use from PlatformIO's ``checkprogsize`` lines (last build wins)."""
    found: dict[str, dict[str, Any] | None] = {"flash": None, "ram": None}
    for m in _SIZE.finditer(log):
        used, total = int(m.group("used")), int(m.group("total"))
        found["flash" if m.group("kind") == "Flash" else "ram"] = {
            "used_bytes": used,
            "total_bytes": total,
            "used_pct": round(100.0 * used / total, 2) if total else None,
        }
    return found


def parse_toolchain(log: str) -> dict[str, Any]:
    """The platform and package versions PlatformIO printed for this build."""
    info: dict[str, Any] = {"platform": None, "platform_version": None, "packages": {}}
    m = _PLATFORM_LINE.search(log)
    if m:
        info["platform"], info["platform_version"] = m.group("name"), m.group("version")
    block = log.split("PACKAGES:", 1)
    if len(block) == 2:
        head = block[1].split("\n\n", 1)[0]
        for line in head.splitlines():
            if not line.strip().startswith("-") and line.strip():
                break
            pm = _PACKAGE.match(line)
            if pm:
                info["packages"][pm.group("name")] = pm.group("version")
    return info


_INSTALLING = re.compile(r"Manager: Installing (?P<pkg>[\w./-]+)(?: @ (?P<ver>\S+))?")
_NET_ERROR = re.compile(
    r"HTTPClientError|Could not resolve|Failed to establish|Name or service not known|"
    r"Temporary failure in name resolution|Internet connection"
)


def missing_package(log: str) -> str | None:
    """The package PlatformIO tried and failed to download, if that ended the build.

    The image carries every package a supported board needs; anything else is
    a download, and offline that fails before a line is compiled -- so the
    build did not run, which is ``not_established`` rather than a failed build.
    """
    if not _NET_ERROR.search(log):
        return None
    installs = list(_INSTALLING.finditer(log))
    if not installs:
        return None
    last = installs[-1]
    return last.group("pkg") + (f" @ {last.group('ver')}" if last.group("ver") else "")


def freertos_port(mcu: str) -> str | None:
    """The FreeRTOS GCC port for an STM32 part, or None when not mapped."""
    m = mcu.lower()
    for prefix, port in _FREERTOS_PORTS:
        if m.startswith(prefix):
            return port
    return None


def check_uart(
    uart: str, expect: list[str], forbid: list[str]
) -> tuple[list[dict[str, Any]], bool]:
    """Ordered ``expect`` regexes and absent ``forbid`` regexes against a UART log."""
    results: list[dict[str, Any]] = []
    ok = True
    pos = 0
    for pattern in expect:
        rx = re.compile(pattern, re.MULTILINE)
        m = rx.search(uart, pos)
        passed = m is not None
        if m:
            pos = m.end()
        ok &= passed
        results.append(
            {
                "kind": "uart_expect",
                "pattern": pattern,
                "status": "pass" if passed else "fail",
                "matched": m.group(0) if m else None,
            }
        )
    for pattern in forbid:
        m = re.search(pattern, uart, re.MULTILINE)
        ok &= m is None
        results.append(
            {
                "kind": "uart_forbid",
                "pattern": pattern,
                "status": "fail" if m else "pass",
                "matched": m.group(0) if m else None,
            }
        )
    return results, ok


_READ_MARK = "MFREAD"


def renode_script(
    repl: str,
    elf: str,
    uart: str,
    uart_log: str,
    sim_seconds: float,
    reads: list[int],
) -> str:
    """A Renode monitor script: load, capture the UART to a file, run, read, quit."""
    lines = [
        'mach create "metaforge"',
        f"machine LoadPlatformDescription @{repl}",
        f"sysbus LoadELF @{elf}",
        f"{uart} CreateFileBackend @{uart_log} true",
        f'emulation RunFor "{sim_seconds:g}"',
    ]
    for addr in reads:
        lines.append(f'echo "{_READ_MARK} 0x{addr:08X}"')
        lines.append(f"sysbus ReadDoubleWord 0x{addr:08X}")
    lines.append("quit")
    return "\n".join(lines) + "\n"


def parse_renode_reads(stdout: str) -> dict[int, int]:
    """Values printed after each ``MFREAD <addr>`` marker in Renode's console."""
    values: dict[int, int] = {}
    pending: int | None = None
    for raw in stdout.splitlines():
        line = raw.strip()
        mark = re.search(rf"{_READ_MARK} (0x[0-9A-Fa-f]+)", line)
        if mark:
            pending = int(mark.group(1), 16)
            continue
        if pending is not None:
            m = re.fullmatch(r"(?:\([^)]*\)\s*)?(0x[0-9A-Fa-f]+)", line)
            if m:
                values[pending] = int(m.group(1), 16)
                pending = None
    return values


def check_memory(
    expect: list[dict[str, Any]], values: dict[int, int] | None, emulator: str
) -> tuple[list[dict[str, Any]], bool, bool]:
    """``(results, all_known_pass, any_unknown)`` for memory-word assertions."""
    results: list[dict[str, Any]] = []
    ok, unknown = True, False
    for item in expect:
        addr = _int(item["address"])
        want = _int(item["value"])
        mask = _int(item.get("mask", 0xFFFFFFFF))
        if values is None or addr not in values:
            unknown = True
            results.append(
                {
                    "kind": "memory",
                    "address": f"0x{addr:08X}",
                    "status": "not_established",
                    "reason": (
                        f"{emulator} memory reads are not supported by this adapter"
                        if values is None
                        else "the emulator returned no value for this address"
                    ),
                }
            )
            continue
        got = values[addr]
        passed = (got & mask) == (want & mask)
        ok &= passed
        results.append(
            {
                "kind": "memory",
                "address": f"0x{addr:08X}",
                "expected": f"0x{want & mask:08X}",
                "actual": f"0x{got & mask:08X}",
                "mask": f"0x{mask:08X}",
                "status": "pass" if passed else "fail",
            }
        )
    return results, ok, unknown


def _int(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value), 0)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class FirmwareServer(McpToolServer):
    """Serves ``firmware.build`` and ``firmware.run_emulated``."""

    def __init__(self, config: FirmwareConfig | None = None) -> None:
        super().__init__(adapter_id="firmware", version="0.1.0")
        self.config = config or FirmwareConfig()
        self._build_lock = asyncio.Semaphore(1)
        self._boards: dict[str, dict[str, Any]] = {}
        self.register_tool(
            manifest=ToolManifest(
                tool_id="firmware.build",
                adapter_id="firmware",
                name="Build Firmware",
                description=(
                    "Compile firmware with PlatformIO in the firmware adapter (STM32 with "
                    "STM32Cube, ESP32 with ESP-IDF). Give `project_path` (a PlatformIO "
                    "project on the adapter workspace) or `files` (path and content) with "
                    "`board` and `framework`. The sources are copied, never modified. A "
                    "project with a FreeRTOSConfig.h on an STM32 gets the FreeRTOS kernel "
                    "compiled in. Returns errors and warnings at file:line, flash and RAM "
                    "used against the part's limits, the toolchain versions, and the "
                    "`elf_path` that firmware.run_emulated takes. A build that did not run "
                    "is not_established, never a pass."
                ),
                capability="firmware_build",
                input_schema=_BUILD_SCHEMA,
                output_schema={
                    "type": "object",
                    "properties": {
                        "verdict": {"type": "string", "enum": ["pass", "fail", "not_established"]},
                        "success": {"type": "boolean"},
                        "error_count": {"type": "integer"},
                        "warning_count": {"type": "integer"},
                        "diagnostics": {"type": "array"},
                        "flash": {"type": ["object", "null"]},
                        "ram": {"type": ["object", "null"]},
                        "elf_path": {"type": ["string", "null"]},
                        "toolchain": {"type": "object"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=4096, max_cpu_seconds=600, max_disk_mb=2048
                ),
            ),
            handler=self.build,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="firmware.run_emulated",
                adapter_id="firmware",
                name="Run Firmware in an Emulator",
                description=(
                    "Run a built firmware ELF (from firmware.build) on an emulated board and "
                    "check what it does. Renode runs the STM32 boards it models; Espressif "
                    "QEMU runs the ESP32. State the checks: "
                    "`uart_expect` (regexes that must appear on the UART, in order), "
                    "`uart_forbid` (must not appear), and on Renode `memory_expect` "
                    "(address, value, optional mask). Returns pass/fail per check and the "
                    "UART log. A board neither emulator models is not_established, never a "
                    "pass."
                ),
                capability="firmware_emulation",
                input_schema=_EMULATE_SCHEMA,
                output_schema={
                    "type": "object",
                    "properties": {
                        "verdict": {"type": "string", "enum": ["pass", "fail", "not_established"]},
                        "emulator": {"type": ["string", "null"]},
                        "assertions": {"type": "array"},
                        "uart_log": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=180, max_disk_mb=256
                ),
            ),
            handler=self.run_emulated,
        )

    # -- firmware.build -----------------------------------------------------

    async def build(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pio = shutil.which(self.config.pio)
        if pio is None:
            raise FirmwareToolNotAvailableError(
                f"{self.config.pio} not found; run the firmware-adapter container"
            )
        run_dir = Path(self.config.work_dir) / "firmware" / uuid.uuid4().hex[:12]
        project = run_dir / "project"
        env = self._stage_project(arguments, project)
        board = _ini_section(
            (project / "platformio.ini").read_text(encoding="utf-8", errors="replace"), env
        ).get("board")
        info = self._board_info(board) if board else None
        platform = (info or {}).get("platform")
        if info is not None and arguments.get("files"):
            _pin_platform(project / "platformio.ini", str(platform))
        if platform and platform not in INSTALLED_PLATFORMS:
            raise ValueError(
                f"board {board} is on platform {platform}; this adapter has "
                f"{', '.join(INSTALLED_PLATFORMS)} installed"
            )
        rtos_note = self._add_freertos(project, env, info)

        with tracer.start_as_current_span("firmware.build") as span:
            span.set_attribute("firmware.board", board or "")
            cmd = [pio, "run", "-d", str(project)]
            if env:
                cmd += ["-e", env]
            started = time.monotonic()
            async with self._build_lock:
                rc, log = await _run_capture(cmd, self.config.max_build_time, cwd=str(project))
            elapsed = round(time.monotonic() - started, 3)
            (run_dir / "build.log").write_text(log, encoding="utf-8")
            diagnostics = parse_diagnostics(log, str(project))
            errors = [d for d in diagnostics if d["severity"] == "error"]
            warnings = [d for d in diagnostics if d["severity"] == "warning"]
            size = parse_size(log)
            timed_out = rc == -9
            success = rc == 0
            missing = missing_package(log) if not success else None
            elf = self._find_artifact(project, env, "firmware.elf") if success else None
            if timed_out or missing:
                verdict = "not_established"
            elif success and elf is not None:
                verdict = "pass"
            else:
                verdict = "fail"
            span.set_attribute("firmware.verdict", verdict)
            logger.info(
                "firmware_build_run",
                board=board,
                returncode=rc,
                verdict=verdict,
                errors=len(errors),
                warnings=len(warnings),
                seconds=elapsed,
            )
            out: dict[str, Any] = {
                "verdict": verdict,
                "success": success,
                "board": board,
                "platform": platform,
                "mcu": (info or {}).get("mcu"),
                "env": env,
                "error_count": len(errors),
                "warning_count": len(warnings),
                "diagnostics": diagnostics[:200],
                "flash": size["flash"],
                "ram": size["ram"],
                "elf_path": self._ws_rel(elf) if elf else None,
                "project_dir": self._ws_rel(project),
                "log_path": self._ws_rel(run_dir / "build.log"),
                "toolchain": parse_toolchain(log),
                "build_time_s": elapsed,
            }
            if rtos_note:
                out["freertos"] = rtos_note
            if timed_out:
                out["reason"] = f"build exceeded {self.config.max_build_time}s and was stopped"
            elif missing:
                out["reason"] = (
                    f"the build needs {missing}, which is not in the firmware image, and it "
                    "could not be downloaded; the image carries STM32Cube for the F4, F7 and "
                    "H7 families and ESP-IDF for the ESP32"
                )
            if not success:
                out["log_tail"] = "\n".join(log.splitlines()[-60:])
            return out

    def _stage_project(self, arguments: dict[str, Any], project: Path) -> str | None:
        path, files = arguments.get("project_path"), arguments.get("files")
        if bool(path) == bool(files):
            raise ValueError("give exactly one of project_path or files")
        if path:
            src = self._ws_path(str(path))
            if not (src / "platformio.ini").is_file():
                raise ValueError(f"project_path has no platformio.ini: {path}")
            shutil.copytree(src, project, ignore=shutil.ignore_patterns(".pio", ".git"))
            env = arguments.get("env")
            return str(env) if env else None
        board, framework = arguments.get("board"), arguments.get("framework")
        if not board or not framework:
            raise ValueError("files needs board and framework (e.g. disco_f407vg, stm32cube)")
        project.mkdir(parents=True)
        for item in files or []:
            rel = str(item.get("path", "")).strip().lstrip("/")
            if not rel or ".." in Path(rel).parts or not _SAFE_REL.match(rel):
                raise ValueError(f"invalid file path: {item.get('path')!r}")
            dest = project / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(str(item.get("content", "")), encoding="utf-8")
        ini = project / "platformio.ini"
        if not ini.exists():
            flags = [str(f) for f in arguments.get("build_flags") or []]
            ini.write_text(
                "[env:app]\n"
                f"board = {board}\n"
                f"framework = {framework}\n"
                + (f"build_flags = {' '.join(flags)}\n" if flags else ""),
                encoding="utf-8",
            )
        return "app" if not arguments.get("env") else str(arguments["env"])

    def _board_info(self, board: str) -> dict[str, Any]:
        """The board's platform and MCU, from the installed platforms' manifests.

        Read from disk rather than ``pio boards``: offline, that command waits
        about a minute on the registry before answering.
        """
        if board in self._boards:
            return self._boards[board]
        core = Path(self.config.platformio_core_dir)
        for platform in INSTALLED_PLATFORMS:
            manifest = core / "platforms" / platform / "boards" / f"{board}.json"
            if manifest.is_file():
                data = json.loads(manifest.read_text(encoding="utf-8"))
                build = data.get("build", {})
                info = {
                    "id": board,
                    "name": data.get("name"),
                    "platform": platform,
                    "mcu": str(build.get("mcu", "")).upper(),
                    "frameworks": data.get("frameworks", []),
                }
                self._boards[board] = info
                return info
        raise ValueError(
            f"board {board!r} is not a board of the installed platforms "
            f"({', '.join(INSTALLED_PLATFORMS)})"
        )

    def _add_freertos(
        self, project: Path, env: str | None, info: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        configs = sorted(project.rglob("FreeRTOSConfig.h"))
        if not configs or not info or info.get("platform") != "ststm32":
            return None
        port = freertos_port(str(info.get("mcu", "")))
        kernel = Path(self.config.freertos_kernel_dir)
        if port is None:
            return {"added": False, "reason": f"no FreeRTOS port mapped for {info.get('mcu')}"}
        if not (kernel / "tasks.c").is_file():
            return {"added": False, "reason": f"FreeRTOS kernel not found at {kernel}"}
        lib = project / "lib" / "FreeRTOS-Kernel"
        lib.mkdir(parents=True, exist_ok=True)
        for name in (
            "tasks.c",
            "queue.c",
            "list.c",
            "timers.c",
            "event_groups.c",
            "stream_buffer.c",
        ):
            shutil.copy2(kernel / name, lib / name)
        shutil.copytree(kernel / "include", lib / "include", dirs_exist_ok=True)
        shutil.copytree(kernel / "portable" / "GCC" / port, lib / "port", dirs_exist_ok=True)
        shutil.copy2(kernel / "portable" / "MemMang" / "heap_4.c", lib / "heap_4.c")
        config_dir = configs[0].parent
        rel_config = os.path.relpath(config_dir, project)
        (lib / "library.json").write_text(
            json.dumps(
                {
                    "name": "FreeRTOS-Kernel",
                    "build": {
                        "flags": ["-Iinclude", "-Iport", f"-I$PROJECT_DIR/{rel_config}"],
                        "includeDir": "include",
                    },
                }
            ),
            encoding="utf-8",
        )
        flags = [
            f"-I{(lib / 'include').as_posix()}",
            f"-I{(lib / 'port').as_posix()}",
            f"-I$PROJECT_DIR/{rel_config}",
        ]
        note: dict[str, Any] = {
            "added": True,
            "port": port,
            "heap": "heap_4",
            "config": rel_config,
        }
        # The CM4F/CM7 ports save FPU context, so they refuse to compile without
        # a hardware-FPU target. softfp keeps the soft-float calling convention,
        # so the framework's libraries still link. A project that already
        # chose a float ABI keeps its own.
        ini_text = (project / "platformio.ini").read_text(encoding="utf-8")
        fpu = _FPU_FLAGS.get(port)
        if fpu and "-mfloat-abi" not in ini_text:
            fpu_flags = [f"-mfpu={_fpu_for(str(info.get('mcu', '')), fpu)}", "-mfloat-abi=softfp"]
            flags += fpu_flags
            note["fpu_flags"] = fpu_flags
        _append_build_flags(project / "platformio.ini", env, flags)
        return note

    def _find_artifact(self, project: Path, env: str | None, name: str) -> Path | None:
        build = project / ".pio" / "build"
        if env and (build / env / name).is_file():
            return build / env / name
        hits = sorted(build.glob(f"*/{name}"))
        return hits[0] if hits else None

    # -- firmware.run_emulated ----------------------------------------------

    async def run_emulated(self, arguments: dict[str, Any]) -> dict[str, Any]:
        elf = self._ws_path(str(arguments.get("elf_path") or ""))
        if not elf.is_file():
            raise ValueError(f"elf_path not found on the adapter: {arguments.get('elf_path')}")
        board = str(arguments.get("board") or "").strip()
        if not board:
            raise ValueError("board is required (the PlatformIO board the ELF was built for)")
        expect = [str(p) for p in arguments.get("uart_expect") or []]
        forbid = [str(p) for p in arguments.get("uart_forbid") or []]
        memory = list(arguments.get("memory_expect") or [])
        for pattern in expect + forbid:
            re.compile(pattern)
        if not (expect or forbid or memory):
            raise ValueError("give at least one check: uart_expect, uart_forbid or memory_expect")
        sim_seconds = float(arguments.get("sim_time_s", 2.0))
        if not 0 < sim_seconds <= 600:
            raise ValueError("sim_time_s must be in (0, 600]")

        run_dir = Path(self.config.work_dir) / "firmware" / "emulation" / uuid.uuid4().hex[:12]
        run_dir.mkdir(parents=True, exist_ok=True)
        with tracer.start_as_current_span("firmware.run_emulated") as span:
            span.set_attribute("firmware.board", board)
            chip = _esp_chip(board, elf)
            if board in RENODE_BOARDS:
                result = await self._run_renode(board, elf, run_dir, sim_seconds, memory)
            elif chip in ESP_QEMU:
                result = await self._run_esp_qemu(chip, elf, run_dir, sim_seconds)
            else:
                result = {
                    "emulator": None,
                    "uart": "",
                    "reads": None,
                    "reason": (
                        f"no emulator model for board {board}; Renode boards: "
                        f"{', '.join(sorted(RENODE_BOARDS))}; Espressif QEMU chips: "
                        f"{', '.join(sorted(ESP_QEMU))}"
                    ),
                }
            uart = result["uart"]
            assertions, uart_ok = check_uart(uart, expect, forbid)
            mem_results, mem_ok, mem_unknown = check_memory(
                memory, result.get("reads"), result.get("emulator") or "this emulator"
            )
            assertions += mem_results
            ran = result.get("emulator") is not None and not result.get("reason")
            if not ran:
                verdict = "not_established"
                for a in assertions:
                    a["status"] = "not_established"
            elif not (uart_ok and mem_ok):
                verdict = "fail"
            elif mem_unknown:
                verdict = "not_established"
            else:
                verdict = "pass"
            span.set_attribute("firmware.verdict", verdict)
            logger.info(
                "firmware_emulation_run",
                board=board,
                emulator=result.get("emulator"),
                verdict=verdict,
                checks=len(assertions),
            )
            out: dict[str, Any] = {
                "verdict": verdict,
                "emulator": result.get("emulator"),
                "emulator_version": result.get("version"),
                "board": board,
                "sim_time_s": sim_seconds,
                "wall_time_s": result.get("wall_time_s"),
                "assertions": assertions,
                "uart_log": uart[-8000:],
                "run_dir": self._ws_rel(run_dir),
            }
            if result.get("reason"):
                out["reason"] = result["reason"]
            if result.get("log_tail"):
                out["log_tail"] = result["log_tail"]
            return out

    async def _run_renode(
        self,
        board: str,
        elf: Path,
        run_dir: Path,
        sim_seconds: float,
        memory: list[dict[str, Any]],
    ) -> dict[str, Any]:
        renode = shutil.which(self.config.renode)
        if renode is None:
            return {"emulator": "renode", "uart": "", "reads": None, "reason": "renode not found"}
        spec = RENODE_BOARDS[board]
        uart_log = run_dir / "uart.txt"
        script = run_dir / "run.resc"
        reads = [_int(m["address"]) for m in memory]
        script.write_text(
            renode_script(spec["repl"], str(elf), spec["uart"], str(uart_log), sim_seconds, reads),
            encoding="utf-8",
        )
        started = time.monotonic()
        rc, out = await _run_capture(
            [renode, "--disable-gui", "--plain", "--console", str(script)],
            self.config.max_emulation_time,
            cwd=str(run_dir),
        )
        elapsed = round(time.monotonic() - started, 3)
        (run_dir / "renode.log").write_text(out, encoding="utf-8")
        uart = uart_log.read_text(errors="replace") if uart_log.exists() else ""
        result: dict[str, Any] = {
            "emulator": "renode",
            "version": await _tool_version(renode, r"Renode v?([\d.]+)"),
            "uart": uart,
            "reads": parse_renode_reads(out),
            "wall_time_s": elapsed,
        }
        if rc == -9:
            result["reason"] = f"Renode exceeded {self.config.max_emulation_time}s and was stopped"
        elif rc != 0 or "Could not" in out or "There was an error" in out:
            result["reason"] = "Renode did not run the firmware"
            result["log_tail"] = "\n".join(out.splitlines()[-40:])
        return result

    async def _run_esp_qemu(
        self, chip: str, elf: Path, run_dir: Path, sim_seconds: float
    ) -> dict[str, Any]:
        binary_attr, machine, boot_offset = ESP_QEMU[chip]
        qemu = shutil.which(getattr(self.config, binary_attr))
        if qemu is None:
            return {
                "emulator": "espressif-qemu",
                "uart": "",
                "reads": None,
                "reason": f"{getattr(self.config, binary_attr)} not found",
            }
        build = elf.parent
        parts = {
            "bootloader": build / "bootloader.bin",
            "partitions": build / "partitions.bin",
            "app": build / "firmware.bin",
        }
        missing = [str(p.name) for p in parts.values() if not p.is_file()]
        if missing:
            return {
                "emulator": "espressif-qemu",
                "uart": "",
                "reads": None,
                "reason": f"ESP32 build output is missing {', '.join(missing)} next to the ELF",
            }
        flash = run_dir / "flash.bin"
        _merge_flash(
            flash,
            [
                (boot_offset, parts["bootloader"]),
                (0x8000, parts["partitions"]),
                (0x10000, parts["app"]),
            ],
            size=4 * 1024 * 1024,
        )
        uart_log = run_dir / "uart.txt"
        cmd = [
            qemu,
            "-nographic",
            "-machine",
            machine,
            "-drive",
            f"file={flash},if=mtd,format=raw",
            "-serial",
            f"file:{uart_log}",
            "-monitor",
            "none",
            "-nic",
            "none",
        ]
        started = time.monotonic()
        wall = min(float(self.config.max_emulation_time), max(5.0, sim_seconds * 5))
        rc, out = await _run_capture(cmd, wall, cwd=str(run_dir))
        elapsed = round(time.monotonic() - started, 3)
        uart = uart_log.read_text(errors="replace") if uart_log.exists() else ""
        result: dict[str, Any] = {
            "emulator": "espressif-qemu",
            "version": await _tool_version(qemu, r"version (\S+)"),
            "uart": uart,
            "reads": None,
            "wall_time_s": elapsed,
        }
        # QEMU runs until stopped: reaching the wall limit is the normal end.
        if rc not in (0, -9):
            result["reason"] = f"QEMU exited with {rc}"
            result["log_tail"] = "\n".join(out.splitlines()[-40:])
        return result

    # -- workspace paths ----------------------------------------------------

    def _ws_path(self, path: str) -> Path:
        root = Path(self.config.work_dir).resolve()
        p = Path(path)
        p = (p if p.is_absolute() else root / p).resolve()
        if p != root and root not in p.parents:
            raise ValueError(f"path is outside the adapter workspace: {path}")
        return p

    def _ws_rel(self, path: Path) -> str:
        root = Path(self.config.work_dir).resolve()
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            return str(path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ini_section(text: str, env: str | None) -> dict[str, str]:
    """Keys of ``[env:<env>]`` (or the first env) merged over ``[env]``."""
    sections: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].rstrip()
        if not line.strip():
            continue
        m = re.match(r"^\[(.+)\]$", line.strip())
        if m:
            current = m.group(1).strip()
            sections.setdefault(current, {})
            continue
        if current and "=" in line and not line[0].isspace():
            k, v = line.split("=", 1)
            sections[current][k.strip()] = v.strip()
    envs = [s for s in sections if s.startswith("env:")]
    chosen = f"env:{env}" if env else (envs[0] if envs else None)
    merged = dict(sections.get("env", {}))
    if chosen:
        merged.update(sections.get(chosen, {}))
    return merged


def _append_build_flags(ini: Path, env: str | None, flags: list[str]) -> None:
    """Add ``flags`` to the ``build_flags`` of the env being built."""
    text = ini.read_text(encoding="utf-8")
    lines = text.splitlines()
    target = (
        f"[env:{env}]"
        if env
        else next((ln.strip() for ln in lines if ln.strip().startswith("[env:")), None)
    )
    if target is None:
        return
    out: list[str] = []
    in_target, done = False, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if in_target and not done:
                out.append(f"build_flags = {' '.join(flags)}")
                done = True
            in_target = stripped == target
        elif in_target and not done and re.match(r"^build_flags\s*=", line):
            out.append(f"{line} {' '.join(flags)}")
            done = True
            continue
        out.append(line)
    if in_target and not done:
        out.append(f"build_flags = {' '.join(flags)}")
    ini.write_text("\n".join(out) + "\n", encoding="utf-8")


def _pin_platform(ini: Path, platform: str) -> None:
    """Name the installed platform version in a generated ini, so nothing is fetched."""
    version = os.environ.get(f"{platform.upper()}_VERSION")
    spec = f"platformio/{platform}@{version}" if version else platform
    text = ini.read_text(encoding="utf-8")
    if not re.search(r"^platform\s*=", text, re.MULTILINE):
        text = text.replace("[env:", f"[env]\nplatform = {spec}\n\n[env:", 1)
        ini.write_text(text, encoding="utf-8")


def _esp_chip(board: str, elf: Path) -> str | None:
    """The ESP32 chip of a build: from its sdkconfig, else the board name."""
    # <project>/.pio/build/<env>/firmware.elf; PlatformIO writes sdkconfig.<env>.
    project = elf.parent.parent.parent.parent
    for cfg in sorted(project.glob("sdkconfig*")):
        m = re.search(r'^CONFIG_IDF_TARGET="(\w+)"', cfg.read_text(errors="replace"), re.M)
        if m:
            return m.group(1)
    b = board.lower()
    for chip in ("esp32s3", "esp32c3"):
        if chip in b.replace("-", ""):
            return chip
    return "esp32" if b.startswith("esp32") else None


def _merge_flash(out: Path, parts: list[tuple[int, Path]], size: int) -> None:
    """A full flash image: erased (0xFF) with each part written at its offset."""
    image = bytearray(b"\xff" * size)
    for offset, path in parts:
        data = path.read_bytes()
        if offset + len(data) > size:
            raise ValueError(f"{path.name} does not fit in a {size // 1024 // 1024} MB flash")
        image[offset : offset + len(data)] = data
    out.write_bytes(bytes(image))


_VERSIONS: dict[str, str | None] = {}


async def _tool_version(binary: str, pattern: str) -> str | None:
    """``<binary> --version``, parsed once per binary."""
    if binary not in _VERSIONS:
        rc, out = await _run_capture([binary, "--version"], 30, cwd=None)
        m = re.search(pattern, out)
        _VERSIONS[binary] = m.group(1) if rc == 0 and m else None
    return _VERSIONS[binary]


async def _run_capture(cmd: list[str], timeout: float, cwd: str | None) -> tuple[int, str]:
    """Run ``cmd``; return (returncode, combined output). -9 means timed out."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
        cwd=cwd,
        env={**os.environ, "PLATFORMIO_NO_ANSI": "true", "TERM": "dumb"},
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode if proc.returncode is not None else -1, stdout.decode(
            errors="replace"
        )
    except TimeoutError:
        proc.kill()
        stdout, _ = await proc.communicate()
        return -9, stdout.decode(errors="replace") if stdout else ""


_BUILD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_path": {
            "type": "string",
            "description": "A PlatformIO project folder (has platformio.ini) on the workspace",
        },
        "files": {
            "type": "array",
            "description": "Source files to build instead of a project: [{path, content}]",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
        "board": {
            "type": "string",
            "description": "PlatformIO board id with files, e.g. disco_f407vg or esp32dev",
        },
        "framework": {
            "type": "string",
            "description": "With files: stm32cube (STM32) or espidf (ESP32)",
        },
        "env": {"type": "string", "description": "PlatformIO env to build; default the first"},
        "build_flags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Extra compiler flags with files, e.g. -DHSE_VALUE=8000000",
        },
    },
}

_EMULATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "elf_path": {
            "type": "string",
            "description": "The elf_path firmware.build returned (adapter workspace)",
        },
        "board": {"type": "string", "description": "The PlatformIO board it was built for"},
        "sim_time_s": {
            "type": "number",
            "default": 2.0,
            "description": "Emulated seconds (Renode); ESP32 QEMU gets ~5x this in wall time",
        },
        "uart_expect": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Regexes that must appear on the UART, in this order",
        },
        "uart_forbid": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Regexes that must not appear, e.g. HardFault, assert",
        },
        "memory_expect": {
            "type": "array",
            "description": "Renode only: 32-bit words to check after the run",
            "items": {
                "type": "object",
                "properties": {
                    "address": {"type": ["string", "integer"]},
                    "value": {"type": ["string", "integer"]},
                    "mask": {"type": ["string", "integer"]},
                },
                "required": ["address", "value"],
            },
        },
    },
    "required": ["elf_path", "board"],
}
