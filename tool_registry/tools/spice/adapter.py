"""SPICE circuit simulation adapter, ngspice in batch mode (FORGE-542).

``run_spice`` named ``spice.run_simulation`` while nothing registered it:
this package's files were empty, so a client could export a netlist and go
no further. This adapter runs a real ngspice over the caller's netlist and
returns the solved vectors.

The deck is the caller's circuit plus one analysis card built from
``analysis_type`` and ``params`` (or the netlist's own card, when it carries
exactly one of the requested kind and no params are given). ngspice writes
an ASCII rawfile, which is parsed here; nothing is estimated.
"""

from __future__ import annotations

import asyncio
import cmath
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.spice.config import SpiceConfig

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.spice.adapter")

ANALYSES = ("op", "dc", "ac", "transient")
_CARD = re.compile(r"^\s*\.(op|dc|ac|tran)\b", re.IGNORECASE | re.MULTILINE)
_CONTROL = re.compile(r"^\s*\.(control|endc)\b", re.IGNORECASE | re.MULTILINE)
_FATAL = re.compile(
    r"(error|singular matrix|timestep too small|no convergence|aborted|"
    r"unknown subckt|could not find|fatal)",
    re.IGNORECASE,
)


class SpiceNotAvailableError(RuntimeError):
    """ngspice is not installed where this adapter runs."""


class SpiceServer(McpToolServer):
    """Serves ``spice.run_simulation``."""

    def __init__(self, config: SpiceConfig | None = None) -> None:
        super().__init__(adapter_id="spice", version="0.1.0")
        self.config = config or SpiceConfig()
        self.register_tool(
            manifest=ToolManifest(
                tool_id="spice.run_simulation",
                adapter_id="spice",
                name="Run SPICE Simulation",
                description=(
                    "Simulate a circuit with ngspice. Give the circuit as `netlist` "
                    "(text) or `netlist_path` (a file on the adapter workspace, e.g. "
                    "from kicad.export_netlist with output_format spice), and "
                    "`analysis_type` op, dc, ac or transient with its `params` "
                    "(dc: source, start, stop, step; ac: variation, points, fstart, "
                    "fstop; transient: step, stop, optional start and max_step). "
                    "Returns each vector's final, min and max (AC: magnitude in dB "
                    "and phase), a decimated waveform, and whether it converged; a "
                    "failed run returns convergence false with the ngspice log."
                ),
                capability="circuit_simulation",
                input_schema=_INPUT_SCHEMA,
                output_schema={
                    "type": "object",
                    "properties": {
                        "convergence": {"type": "boolean"},
                        "analysis": {"type": "string"},
                        "scale": {"type": ["string", "null"]},
                        "results": {"type": "object"},
                        "waveform_data": {"type": "object"},
                        "waveforms": {"type": "array", "items": {"type": "string"}},
                        "sim_time_s": {"type": "number"},
                        "log": {"type": "string"},
                    },
                },
                phase=1,
                resource_limits=ResourceLimits(
                    max_memory_mb=1024, max_cpu_seconds=180, max_disk_mb=256
                ),
            ),
            handler=self.run_simulation,
        )

    async def run_simulation(self, arguments: dict[str, Any]) -> dict[str, Any]:
        analysis = str(arguments.get("analysis_type", "")).lower()
        if analysis not in ANALYSES:
            raise ValueError(f"analysis_type must be one of {', '.join(ANALYSES)}")
        netlist = self._netlist_text(arguments)
        params = dict(arguments.get("params") or {})
        deck = build_deck(netlist, analysis, params)
        probes = [str(p) for p in arguments.get("probes") or []]
        max_points = int(arguments.get("max_points", 200))
        if max_points < 2:
            raise ValueError("max_points must be at least 2")

        binary = shutil.which(self.config.ngspice)
        if binary is None:
            raise SpiceNotAvailableError(
                f"{self.config.ngspice} not found; run the spice-adapter container"
            )

        with tracer.start_as_current_span("spice.run_simulation") as span:
            span.set_attribute("spice.analysis", analysis)
            run_dir = Path(self.config.work_dir) / "spice" / uuid.uuid4().hex[:12]
            run_dir.mkdir(parents=True, exist_ok=True)
            deck_path, raw_path, log_path = (
                run_dir / "deck.cir",
                run_dir / "out.raw",
                run_dir / "ngspice.log",
            )
            deck_path.write_text(deck, encoding="utf-8")
            started = time.monotonic()
            rc = await _run(
                [binary, "-b", "-r", str(raw_path), "-o", str(log_path), str(deck_path)],
                self.config.max_operation_time,
            )
            elapsed = round(time.monotonic() - started, 3)
            log = log_path.read_text(errors="replace") if log_path.exists() else ""
            raw = raw_path.read_text(errors="replace") if raw_path.exists() else ""
            plots = parse_ascii_raw(raw) if raw else []
            fatal = [ln.strip() for ln in log.splitlines() if _FATAL.search(ln)]
            converged = rc == 0 and bool(plots) and not fatal
            span.set_attribute("spice.converged", converged)
            logger.info(
                "spice_simulation_run",
                analysis=analysis,
                returncode=rc,
                converged=converged,
                plots=len(plots),
                seconds=elapsed,
            )
            out: dict[str, Any] = {
                "convergence": converged,
                "analysis": analysis,
                "deck_path": str(deck_path),
                "waveforms": [str(raw_path)] if raw_path.exists() else [],
                "sim_time_s": elapsed,
                "results": {},
                "waveform_data": {},
                "scale": None,
            }
            if not converged:
                out["log"] = "\n".join((fatal or log.splitlines())[-40:])
                return out
            plot = plots[-1]
            out["plot"] = plot["plotname"]
            summary, waveform, scale = summarise(plot, probes, max_points)
            out["results"], out["waveform_data"], out["scale"] = summary, waveform, scale
            return out

    def _netlist_text(self, arguments: dict[str, Any]) -> str:
        text = arguments.get("netlist")
        path = arguments.get("netlist_path")
        if bool(text) == bool(path):
            raise ValueError("give exactly one of netlist (text) or netlist_path")
        if text:
            return str(text)
        p = Path(str(path))
        if not p.is_absolute():
            p = Path(self.config.work_dir) / p
        if not p.is_file():
            raise ValueError(f"netlist_path not found on the adapter: {path}")
        return p.read_text(encoding="utf-8", errors="replace")


def build_deck(netlist: str, analysis: str, params: dict[str, Any]) -> str:
    """The ngspice deck: a title, the circuit, one analysis card, ``.end``."""
    if _CONTROL.search(netlist):
        raise ValueError("netlist has a .control block; remove it, the adapter runs the analysis")
    body = re.sub(r"^\s*\.end\s*$", "", netlist, flags=re.IGNORECASE | re.MULTILINE).rstrip()
    cards = [m.group(1).lower() for m in _CARD.finditer(body)]
    want = "tran" if analysis == "transient" else analysis
    if params:
        if cards:
            raise ValueError(
                f"netlist already has analysis card(s) ({', '.join('.' + c for c in cards)}); "
                "pass params or keep the card, not both"
            )
        card = analysis_card(analysis, params)
    elif analysis == "op" and not cards:
        card = ".op"
    elif cards == [want]:
        card = ""  # the netlist's own card
    else:
        raise ValueError(
            f"no params for {analysis}, and the netlist does not hold exactly one .{want} card"
        )
    lines = ["* MetaForge spice.run_simulation deck", body]
    if card:
        lines.append(card)
    lines.append(".end")
    return "\n".join(lines) + "\n"


def analysis_card(analysis: str, params: dict[str, Any]) -> str:
    """One analysis card from typed params; every required value must be given."""

    def need(*keys: str) -> list[str]:
        missing = [k for k in keys if params.get(k) in (None, "")]
        if missing:
            raise ValueError(f"{analysis} needs params: {', '.join(missing)}")
        return [_spice_value(params[k], k) for k in keys]

    if analysis == "op":
        return ".op"
    if analysis == "dc":
        source, start, stop, step = need("source", "start", "stop", "step")
        return f".dc {source} {start} {stop} {step}"
    if analysis == "ac":
        variation = str(params.get("variation", "dec")).lower()
        if variation not in {"dec", "oct", "lin"}:
            raise ValueError("ac variation must be dec, oct or lin")
        points, fstart, fstop = need("points", "fstart", "fstop")
        return f".ac {variation} {points} {fstart} {fstop}"
    step, stop = need("step", "stop")
    card = f".tran {step} {stop}"
    if params.get("start") not in (None, "") or params.get("max_step") not in (None, ""):
        card += f" {_spice_value(params.get('start', 0), 'start')}"
        if params.get("max_step") not in (None, ""):
            card += f" {_spice_value(params['max_step'], 'max_step')}"
    return card


_VALUE = re.compile(r"^[A-Za-z0-9_.+\-]+$")


def _spice_value(value: Any, key: str) -> str:
    text = str(value).strip()
    if not _VALUE.match(text):
        raise ValueError(f"param {key}={value!r} is not a plain SPICE value or name")
    return text


def parse_ascii_raw(text: str) -> list[dict[str, Any]]:
    """Every plot in an ngspice ASCII rawfile.

    Each plot: ``plotname``, ``flags`` (``real``/``complex``), ``variables``
    (``[(name, type)]``, the scale first) and ``values`` (one list per
    variable, floats or complex).
    """
    plots: list[dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].startswith("Title:"):
            i += 1
            continue
        header: dict[str, str] = {}
        variables: list[tuple[str, str]] = []
        i += 1
        while i < len(lines) and not lines[i].startswith("Values:"):
            line = lines[i]
            if line.startswith("Variables:"):
                count = int(header.get("No. Variables", "0"))
                for j in range(count):
                    parts = lines[i + 1 + j].split()
                    variables.append((parts[1], parts[2] if len(parts) > 2 else ""))
                i += count
            elif ":" in line:
                key, _, value = line.partition(":")
                header[key.strip()] = value.strip()
            i += 1
        i += 1  # past "Values:"
        complex_ = "complex" in header.get("Flags", "")
        n_points = int(header.get("No. Points", "0"))
        values: list[list[Any]] = [[] for _ in variables]
        tokens: list[str] = []
        while i < len(lines) and not lines[i].startswith("Title:"):
            tokens.extend(lines[i].split())
            i += 1
        k = 0
        for _ in range(n_points):
            k += 1  # point index
            for v in range(len(variables)):
                raw = tokens[k] if k < len(tokens) else "0"
                k += 1
                if complex_:
                    re_s, _, im_s = raw.partition(",")
                    values[v].append(complex(float(re_s), float(im_s or 0)))
                else:
                    values[v].append(float(raw))
        plots.append(
            {
                "plotname": header.get("Plotname", ""),
                "flags": "complex" if complex_ else "real",
                "variables": variables,
                "values": values,
            }
        )
    return plots


def summarise(
    plot: dict[str, Any], probes: list[str], max_points: int
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Per-vector summary and a decimated waveform for the requested probes."""
    names = [name for name, _ in plot["variables"]]
    lookup = {n.lower(): idx for idx, n in enumerate(names)}
    is_op = len(plot["values"][0]) == 1 if plot["values"] else True
    scale_idx = None if is_op else 0
    if probes:
        missing = [p for p in probes if p.lower() not in lookup]
        if missing:
            raise ValueError(f"probe(s) not in the results: {missing}; available: {names}")
        chosen = [lookup[p.lower()] for p in probes]
    else:
        chosen = [i for i in range(len(names)) if i != scale_idx][:40]

    results: dict[str, Any] = {}
    waveform: dict[str, Any] = {}
    n = len(plot["values"][0]) if plot["values"] else 0
    stride = max(1, math.ceil(n / max_points))
    idx = list(range(0, n, stride))
    if n and idx[-1] != n - 1:
        idx.append(n - 1)
    if scale_idx is not None:
        waveform[names[0]] = [_real(plot["values"][0][j]) for j in idx]
    for c in chosen:
        series = plot["values"][c]
        name = names[c]
        unit = plot["variables"][c][1]
        if plot["flags"] == "complex":
            mags = [20 * math.log10(abs(z)) if abs(z) > 0 else float("-inf") for z in series]
            phases = [math.degrees(cmath.phase(z)) for z in series]
            results[name] = {
                "unit": unit,
                "mag_db_final": _num(mags[-1]),
                "mag_db_min": _num(min(mags)),
                "mag_db_max": _num(max(mags)),
                "phase_deg_final": _num(phases[-1]),
            }
            waveform[name] = {
                "mag_db": [_num(mags[j]) for j in idx],
                "phase_deg": [_num(phases[j]) for j in idx],
            }
        else:
            vals = [_real(v) for v in series]
            results[name] = {
                "unit": unit,
                "final": _num(vals[-1]),
                "min": _num(min(vals)),
                "max": _num(max(vals)),
            }
            if scale_idx is not None:
                waveform[name] = [_num(vals[j]) for j in idx]
    return results, waveform, names[0] if scale_idx is not None else None


def _real(v: Any) -> float:
    return float(v.real) if isinstance(v, complex) else float(v)


def _num(x: float) -> float | None:
    """JSON-safe: ``-inf`` (a zero magnitude) becomes None."""
    return None if math.isinf(x) or math.isnan(x) else round(x, 9)


async def _run(cmd: list[str], timeout: float) -> int:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "SPICE_ASCIIRAWFILE": "1"},
        cwd=tempfile.gettempdir(),
    )
    try:
        return await asyncio.wait_for(proc.wait(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -9


_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "netlist": {"type": "string", "description": "The circuit as SPICE text"},
        "netlist_path": {
            "type": "string",
            "description": "A netlist file on the adapter workspace (relative to it, or absolute)",
        },
        "analysis_type": {"type": "string", "enum": list(ANALYSES)},
        "params": {
            "type": "object",
            "description": (
                "dc: source, start, stop, step. ac: variation (dec/oct/lin), points, "
                "fstart, fstop. transient: step, stop, start, max_step. op: none. "
                "Omit to use the netlist's own single analysis card"
            ),
        },
        "probes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Vectors to return, e.g. v(out), i(v1); default all (up to 40)",
        },
        "max_points": {"type": "integer", "minimum": 2, "default": 200},
    },
    "required": ["analysis_type"],
}
