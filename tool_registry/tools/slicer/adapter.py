"""PrusaSlicer adapter: ``slicer.*``, G-code and print estimates.

Two tools:

- ``slicer.slice`` slices a workspace model (STL, 3MF, OBJ or STEP) with
  PrusaSlicer's command line and writes the G-code to the workspace. Print
  time, filament length, volume and weight, layer count and height are read
  back from the comments PrusaSlicer writes into the G-code: they are the
  slicer's own figures, nothing is estimated here.
- ``slicer.list_profiles`` lists the built-in printer and material presets.

The built-in printers are generic Marlin machines named by bed size. Their
estimates are sound, but their start G-code is generic: to print on a real
machine, export its config from PrusaSlicer (File > Export > Export Config)
and pass it as ``config_path``.

No configuration may run a program here. A post-processing script in a
user's config or 3MF project is refused, an override config that blanks
``post_process`` is always loaded last, and PrusaSlicer runs with no stdin,
so its own "Continue (Y/N)?" prompt for such a script reads no.

Registers in-process so tools/list is stable; a call needs the
slicer-adapter container (``METAFORGE_ADAPTER_SLICER_URL``), since the
gateway and sidecar images have no PrusaSlicer and raise
SlicerNotAvailableError in-process.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.slicer.config import SlicerConfig

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.slicer.adapter")

MODEL_SUFFIXES = (".stl", ".3mf", ".obj", ".step", ".stp")
MAX_COPIES = 100

_START_GCODE = (
    "G90\nM83\nM140 S[first_layer_bed_temperature]\nM104 S[first_layer_temperature]\n"
    "G28\nM190 S[first_layer_bed_temperature]\nM109 S[first_layer_temperature]\n"
    "G1 Z5 F3000\nG92 E0"
)
_END_GCODE = "M104 S0\nM140 S0\nM107\nG91\nG1 Z10 F600\nG90\nM84"

# Generic Marlin printers by bed size (width, depth, height in mm).
PRINTERS: dict[str, dict[str, Any]] = {
    "generic_220": {"size_mm": (220, 220, 250), "class": "Ender-3 sized bed"},
    "generic_250": {"size_mm": (250, 210, 220), "class": "Prusa MK4 sized bed"},
    "generic_256": {"size_mm": (256, 256, 256), "class": "Bambu Lab P1/X1 sized bed"},
    "generic_300": {"size_mm": (300, 300, 400), "class": "large format"},
}

# Temperatures in C, density in g/cm3, volumetric speed cap in mm3/s.
MATERIALS: dict[str, dict[str, Any]] = {
    "PLA": {"nozzle_c": 210, "bed_c": 60, "density": 1.24, "max_mm3_s": 15, "fan": (100, 100)},
    "PETG": {"nozzle_c": 240, "bed_c": 80, "density": 1.27, "max_mm3_s": 8, "fan": (30, 50)},
    "ABS": {"nozzle_c": 250, "bed_c": 100, "density": 1.04, "max_mm3_s": 11, "fan": (15, 30)},
    "ASA": {"nozzle_c": 260, "bed_c": 100, "density": 1.07, "max_mm3_s": 11, "fan": (15, 30)},
    "TPU": {"nozzle_c": 230, "bed_c": 50, "density": 1.21, "max_mm3_s": 3, "fan": (50, 100)},
}

DEFAULT_PRINTER = "generic_220"
DEFAULT_MATERIAL = "PLA"

# Applied only when no config_path is given; a user's config keeps its own.
_DEFAULTS: dict[str, str] = {
    "nozzle_diameter": "0.4",
    "filament_diameter": "1.75",
    "gcode_flavor": "marlin2",
    "use_relative_e_distances": "1",
    # Relative E needs the extruder position reset at every layer.
    "layer_gcode": "G92 E0",
    "layer_height": "0.2",
    "first_layer_height": "0.2",
    "fill_density": "20%",
    "perimeters": "2",
    "support_material": "0",
    "brim_width": "0",
    "start_gcode": _START_GCODE,
    "end_gcode": _END_GCODE,
}

# Loaded last on every run: no script may run, and the G-code is text.
_SAFETY: dict[str, str] = {"post_process": "", "binary_gcode": "0"}

# Object labels make PrusaSlicer list the objects it actually printed in
# ``; objects_info``. It silently leaves out copies that do not fit the bed,
# so without this a run asked for four parts could estimate one. "octoprint"
# adds comments only; a config that asks for "firmware" (M486) keeps it.
_LABEL_FIRMWARE = re.compile(r"^[ \t]*gcode_label_objects[ \t]*=[ \t]*firmware", re.MULTILINE)

# Horizontal space only: an empty value must not reach the next line.
_POST_PROCESS = re.compile(r"^[ \t;]*post_process[ \t]*=[ \t]*\S", re.MULTILINE)
_PROGRESS = re.compile(r"^\d+ => ")
_LOG_PREFIX = re.compile(r"^\[[^\]]*\] \[[^\]]*\] \[\w+\]\s*")
_DURATION = re.compile(r"(\d+)\s*([dhms])")
_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}

# G-code comment keys read back, renamed to carry their unit.
_FOOTER = {
    "filament used [mm]": "filament_mm",
    "filament used [cm3]": "filament_cm3",
    "total filament used [g]": "filament_g",
    "total filament cost": "filament_cost",
}
_SETTINGS = (
    "layer_height",
    "first_layer_height",
    "fill_density",
    "perimeters",
    "support_material",
    "brim_width",
    "nozzle_diameter",
    "filament_type",
    "gcode_flavor",
    "bed_shape",
    "max_print_height",
)


class SlicerNotAvailableError(RuntimeError):
    """PrusaSlicer is not installed where this adapter runs."""


class SlicerServer(McpToolServer):
    """Serves ``slicer.slice`` and ``slicer.list_profiles``."""

    def __init__(self, config: SlicerConfig | None = None) -> None:
        super().__init__(adapter_id="slicer", version="0.1.0")
        self.config = config or SlicerConfig()
        self._slots = asyncio.Semaphore(self.config.max_concurrent)
        self.register_tool(
            manifest=ToolManifest(
                tool_id="slicer.slice",
                adapter_id="slicer",
                name="Slice for 3D Printing",
                description=(
                    "Slice a model on the adapter workspace (STL, 3MF, OBJ or STEP, "
                    "in millimetres) with PrusaSlicer and write FDM G-code to the "
                    "workspace. Returns the slicer's own print time, filament length "
                    "(mm), volume (cm3) and weight (g), layer count and print height, "
                    "and the settings used. Pick a built-in 'printer' and 'material' "
                    "(see slicer.list_profiles), or pass 'config_path', a config "
                    "exported from PrusaSlicer for the real printer; the built-in "
                    "printers write generic Marlin start G-code. 'layer_height', "
                    "'infill_percent', 'perimeters', 'supports', 'brim_mm', "
                    "'nozzle_mm' and 'copies' override either. Configs with a "
                    "post-processing script are refused. Free and local."
                ),
                capability="slicing",
                input_schema=_SLICE_SCHEMA,
                output_schema={
                    "type": "object",
                    "properties": {
                        "gcode_path": {"type": "string"},
                        "print_time_s": {"type": "integer"},
                        "print_time": {"type": "string"},
                        "filament_mm": {"type": "number"},
                        "filament_cm3": {"type": "number"},
                        "filament_g": {"type": "number"},
                        "layers": {"type": "integer"},
                        "height_mm": {"type": "number"},
                        "settings": {"type": "object"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(
                    max_memory_mb=2048, max_cpu_seconds=600, max_disk_mb=1024
                ),
            ),
            handler=self.slice,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="slicer.list_profiles",
                adapter_id="slicer",
                name="Slicer Profiles",
                description=(
                    "The built-in printer presets (bed size, generic Marlin) and "
                    "material presets (temperatures, density, flow limit) that "
                    "slicer.slice accepts, and the defaults it uses. Read-only."
                ),
                capability="slicing",
                input_schema={"type": "object", "properties": {}},
                output_schema={
                    "type": "object",
                    "properties": {
                        "printers": {"type": "array"},
                        "materials": {"type": "array"},
                        "defaults": {"type": "object"},
                    },
                },
                phase=2,
                resource_limits=ResourceLimits(max_memory_mb=64, max_cpu_seconds=5, max_disk_mb=1),
            ),
            handler=self.list_profiles,
        )

    async def list_profiles(self, arguments: dict[str, Any]) -> dict[str, Any]:
        printers = [
            {
                "printer": name,
                "bed_mm": list(spec["size_mm"][:2]),
                "max_height_mm": spec["size_mm"][2],
                "class": spec["class"],
                "gcode_flavor": "marlin2",
            }
            for name, spec in PRINTERS.items()
        ]
        materials = [
            {
                "material": name,
                "nozzle_c": spec["nozzle_c"],
                "bed_c": spec["bed_c"],
                "density_g_cm3": spec["density"],
                "max_volumetric_speed_mm3_s": spec["max_mm3_s"],
            }
            for name, spec in MATERIALS.items()
        ]
        return {
            "printers": printers,
            "materials": materials,
            "defaults": {
                "printer": DEFAULT_PRINTER,
                "material": DEFAULT_MATERIAL,
                "layer_height": 0.2,
                "infill_percent": 20,
                "perimeters": 2,
                "supports": False,
                "nozzle_mm": 0.4,
                "filament_diameter_mm": 1.75,
            },
        }

    async def slice(self, arguments: dict[str, Any]) -> dict[str, Any]:
        model = self._workspace_file(arguments.get("file_path"), "file_path", MODEL_SUFFIXES)
        if model.suffix.lower() == ".3mf" and _3mf_has_post_process(model):
            raise ValueError(
                "the 3MF project carries a post-processing script; remove it, "
                "the slicer adapter runs no scripts"
            )
        user_config: Path | None = None
        label = "octoprint"
        if arguments.get("config_path") is not None:
            user_config = self._workspace_file(arguments["config_path"], "config_path", (".ini",))
            text = user_config.read_text(encoding="utf-8", errors="replace")
            if _POST_PROCESS.search(text):
                raise ValueError(
                    "config_path sets post_process; remove it, the slicer adapter runs no scripts"
                )
            if _LABEL_FIRMWARE.search(text):
                label = "firmware"
        printer = _choice(arguments, "printer", PRINTERS)
        material = _choice(arguments, "material", MATERIALS)
        if user_config is None:
            printer = printer or DEFAULT_PRINTER
            material = material or DEFAULT_MATERIAL
        copies = _int(arguments, "copies", 1, MAX_COPIES) or 1
        values = build_config(printer, material, overrides(arguments), defaults=user_config is None)
        values["gcode_label_objects"] = label

        binary = shutil.which(self.config.prusaslicer)
        if binary is None:
            raise SlicerNotAvailableError(
                f"{self.config.prusaslicer} not found; run the slicer-adapter container"
            )

        root = Path(self.config.work_dir).resolve()
        run_dir = root / "output" / "slicer" / uuid.uuid4().hex[:12]
        run_dir.mkdir(parents=True, exist_ok=True)
        config_file = run_dir / "config.ini"
        config_file.write_text(render_ini(values), encoding="utf-8")
        gcode = run_dir / f"{model.stem}.gcode"

        with tracer.start_as_current_span("slicer.slice") as span:
            span.set_attribute("slicer.model_suffix", model.suffix.lower())
            span.set_attribute("slicer.copies", copies)
            started = time.monotonic()
            with tempfile.TemporaryDirectory() as datadir:
                cmd = [binary, "--datadir", datadir]
                if user_config is not None:
                    cmd += ["--load", str(user_config)]
                cmd += ["--load", str(config_file), "--export-gcode"]
                if copies > 1:
                    cmd += ["--duplicate", str(copies)]
                cmd += ["--output", str(gcode), str(model)]
                async with self._slots:
                    rc, output = await _run(cmd, self.config.max_operation_time)
            elapsed = round(time.monotonic() - started, 3)
            messages = slicer_messages(output)
            if rc != 0 or not gcode.is_file():
                if rc == -9:
                    reason = (
                        f"slicing took longer than {self.config.max_operation_time} s "
                        "and was stopped"
                    )
                else:
                    reason = "; ".join(messages[-5:]) or f"PrusaSlicer exited with {rc}"
                exc = ValueError(f"slicer.slice failed: {reason}")
                span.record_exception(exc)
                logger.warning("slicer_slice_failed", returncode=rc, seconds=elapsed)
                raise exc
            summary = read_gcode_summary(gcode)
            if "print_time_s" not in summary:
                raise ValueError("slicer.slice failed: PrusaSlicer wrote no print estimate")
            printed = summary.get("objects_printed")
            if printed is not None and printed < copies:
                raise ValueError(
                    f"slicer.slice failed: only {printed} of {copies} copies fit on the "
                    "bed, and PrusaSlicer leaves out what does not fit; ask for fewer "
                    "copies or a larger printer"
                )
            span.set_attribute("slicer.print_time_s", summary["print_time_s"])
            logger.info(
                "slicer_sliced",
                print_time_s=summary["print_time_s"],
                layers=summary.get("layers"),
                seconds=elapsed,
            )

        result: dict[str, Any] = {
            "gcode_path": gcode.relative_to(root).as_posix(),
            "config_used": config_file.relative_to(root).as_posix(),
            "model": model.relative_to(root).as_posix(),
            "printer": printer or "from config_path",
            "material": material or "from config_path",
            "copies": copies,
            **summary,
            "slice_time_s": elapsed,
        }
        if "filament_cost_per_kg" not in arguments:
            result.pop("filament_cost", None)
        if messages:
            result["messages"] = messages[-20:]
        if user_config is None:
            result["note"] = (
                "Built-in printers write generic Marlin start G-code. To print on a "
                "real machine, slice with its exported PrusaSlicer config as config_path."
            )
        return result

    def _workspace_file(self, raw: Any, key: str, suffixes: tuple[str, ...]) -> Path:
        """A file under the workspace; anything that escapes it is refused."""
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{key!r} is required and must be a non-empty string")
        root = Path(self.config.work_dir).resolve()
        path = Path(raw.strip())
        resolved = (path if path.is_absolute() else root / path).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"{key} {raw!r} is outside the adapter workspace")
        if resolved.suffix.lower() not in suffixes:
            raise ValueError(f"{key} {raw!r} must be one of {', '.join(suffixes)}")
        if not resolved.is_file():
            raise ValueError(f"no file at {key} {raw!r} on the adapter workspace")
        size = resolved.stat().st_size
        if size == 0:
            raise ValueError(f"{key} {raw!r} is empty")
        if size > self.config.max_input_mb * 1024 * 1024:
            raise ValueError(
                f"{key} {raw!r} is {size // (1024 * 1024)} MB; "
                f"the limit is {self.config.max_input_mb} MB"
            )
        return resolved


def overrides(arguments: dict[str, Any]) -> dict[str, str]:
    """PrusaSlicer keys for the per-call settings the caller gave."""
    out: dict[str, str] = {}
    layer = _number(arguments, "layer_height", 0.05, 1.0)
    if layer is not None:
        out["layer_height"] = _fmt(layer)
    nozzle = _number(arguments, "nozzle_mm", 0.1, 1.2)
    if nozzle is not None:
        out["nozzle_diameter"] = _fmt(nozzle)
    infill = _number(arguments, "infill_percent", 0, 100)
    if infill is not None:
        out["fill_density"] = f"{_fmt(infill)}%"
    perimeters = _int(arguments, "perimeters", 1, 20)
    if perimeters is not None:
        out["perimeters"] = str(perimeters)
    supports = arguments.get("supports")
    if supports is not None:
        if not isinstance(supports, bool):
            raise ValueError("'supports' must be true or false")
        out["support_material"] = "1" if supports else "0"
        out["support_material_auto"] = "1"
    brim = _number(arguments, "brim_mm", 0, 50)
    if brim is not None:
        out["brim_width"] = _fmt(brim)
    cost = _number(arguments, "filament_cost_per_kg", 0, 100_000)
    if cost is not None:
        out["filament_cost"] = _fmt(cost)
    return out


def build_config(
    printer: str | None,
    material: str | None,
    settings: dict[str, str],
    *,
    defaults: bool,
) -> dict[str, str]:
    """The config loaded last: defaults, presets, the caller's settings, safety."""
    values: dict[str, str] = dict(_DEFAULTS) if defaults else {}
    if printer is not None:
        width, depth, height = PRINTERS[printer]["size_mm"]
        values["bed_shape"] = f"0x0,{width}x0,{width}x{depth},0x{depth}"
        values["max_print_height"] = str(height)
    if material is not None:
        spec = MATERIALS[material]
        values.update(
            {
                "filament_type": material,
                "filament_density": _fmt(spec["density"]),
                "filament_max_volumetric_speed": str(spec["max_mm3_s"]),
                "temperature": str(spec["nozzle_c"]),
                "first_layer_temperature": str(spec["nozzle_c"] + 5),
                "bed_temperature": str(spec["bed_c"]),
                "first_layer_bed_temperature": str(spec["bed_c"]),
                "min_fan_speed": str(spec["fan"][0]),
                "max_fan_speed": str(spec["fan"][1]),
            }
        )
    values.update(settings)
    values.update(_SAFETY)
    return values


def render_ini(values: dict[str, str]) -> str:
    """PrusaSlicer's ini form: one ``key = value`` per line, newlines escaped."""
    return "".join(f"{key} = {_escape(value)}\n" for key, value in values.items())


def _escape(value: str) -> str:
    return value.replace("\n", "\\n")


def parse_duration(text: str) -> int | None:
    """Seconds in PrusaSlicer's "1d 2h 3m 4s" form."""
    parts = _DURATION.findall(text)
    if not parts:
        return None
    return sum(int(n) * _SECONDS[unit] for n, unit in parts)


def read_gcode_summary(path: Path) -> dict[str, Any]:
    """The estimates and settings PrusaSlicer wrote as G-code comments."""
    summary: dict[str, Any] = {}
    settings: dict[str, str] = {}
    layers = 0
    height: float | None = None
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith(";"):
                continue
            if line.startswith(";LAYER_CHANGE"):
                layers += 1
                continue
            if line.startswith(";Z:"):
                height = _float(line[3:]) or height
                continue
            if line.startswith("; objects_info = "):
                try:
                    objects = json.loads(line[len("; objects_info = ") :])["objects"]
                    summary["objects_printed"] = len(objects)
                except (ValueError, KeyError, TypeError):
                    pass
                continue
            if line.startswith("; generated by "):
                summary["slicer"] = line[len("; generated by ") :].split(" on ")[0].strip()
                continue
            key, sep, value = line[1:].partition(" = ")
            if not sep:
                continue
            key, value = key.strip(), value.strip()
            if key in _FOOTER:
                total = sum(_float(v) or 0.0 for v in value.split(","))
                summary[_FOOTER[key]] = round(total, 3)
            elif key == "estimated printing time (normal mode)":
                summary["print_time"] = value
                seconds = parse_duration(value)
                if seconds is not None:
                    summary["print_time_s"] = seconds
            elif key == "estimated first layer printing time (normal mode)":
                seconds = parse_duration(value)
                if seconds is not None:
                    summary["first_layer_time_s"] = seconds
            elif key in _SETTINGS:
                settings[key] = value
    summary["layers"] = layers
    if height is not None:
        summary["height_mm"] = height
    summary["settings"] = settings
    return summary


def slicer_messages(output: str) -> list[str]:
    """PrusaSlicer's output minus its progress lines, log prefixes dropped."""
    messages: list[str] = []
    for line in output.splitlines():
        line = _LOG_PREFIX.sub("", line.strip())
        if not line or _PROGRESS.match(line) or line.startswith("Slicing result exported"):
            continue
        messages.append(line[:300])
    return messages


def _3mf_has_post_process(path: Path) -> bool:
    """Whether a 3MF carries a PrusaSlicer config that runs a script."""
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.lower().endswith(".config"):
                    text = archive.read(name).decode("utf-8", errors="replace")
                    if _POST_PROCESS.search(text):
                        return True
    except (zipfile.BadZipFile, OSError):
        return False  # PrusaSlicer reports an unreadable 3MF itself
    return False


def _choice(arguments: dict[str, Any], key: str, options: dict[str, Any]) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        for option in options:
            if option.lower() == value.strip().lower():
                return option
    raise ValueError(f"{key!r} must be one of {', '.join(options)}")


def _number(arguments: dict[str, Any], key: str, low: float, high: float) -> float | None:
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key!r} must be a number")
    if not low <= value <= high:
        raise ValueError(f"{key!r} must be between {_fmt(low)} and {_fmt(high)}")
    return float(value)


def _int(arguments: dict[str, Any], key: str, low: int, high: int) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key!r} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"{key!r} must be between {low} and {high}")
    return value


def _float(text: str) -> float | None:
    try:
        return float(text.strip())
    except ValueError:
        return None


def _fmt(value: float) -> str:
    return f"{value:g}"


async def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    """Run PrusaSlicer with no stdin; its stdout and stderr, combined."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=tempfile.gettempdir(),
        env={**os.environ, "HOME": tempfile.gettempdir()},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -9, ""
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


_SLICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "Workspace-relative model: .stl, .3mf, .obj, .step or .stp, in mm",
        },
        "printer": {"type": "string", "enum": list(PRINTERS)},
        "material": {"type": "string", "enum": list(MATERIALS)},
        "config_path": {
            "type": "string",
            "description": (
                "Workspace-relative .ini exported from PrusaSlicer for the real "
                "printer, filament and print settings"
            ),
        },
        "layer_height": {"type": "number", "minimum": 0.05, "maximum": 1.0},
        "nozzle_mm": {"type": "number", "minimum": 0.1, "maximum": 1.2},
        "infill_percent": {"type": "number", "minimum": 0, "maximum": 100},
        "perimeters": {"type": "integer", "minimum": 1, "maximum": 20},
        "supports": {"type": "boolean"},
        "brim_mm": {"type": "number", "minimum": 0, "maximum": 50},
        "copies": {"type": "integer", "minimum": 1, "maximum": MAX_COPIES, "default": 1},
        "filament_cost_per_kg": {
            "type": "number",
            "minimum": 0,
            "description": "Adds filament_cost to the result, in the same currency",
        },
    },
    "required": ["file_path"],
}
