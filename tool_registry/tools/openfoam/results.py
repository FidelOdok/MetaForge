"""Read an OpenFOAM 11 run back: logs, boundary file, post-processing (FORGE-557).

The formats here were read from a real run, not remembered. Every function takes
text or a path and returns plain data, so the parsers are tested against
captured output without OpenFOAM installed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# --- checkMesh ----------------------------------------------------------------

_CELLS = re.compile(r"^\s*cells:\s+(\d+)", re.MULTILINE)
_NON_ORTHO = re.compile(r"Mesh non-orthogonality Max:\s*([0-9.eE+-]+)")
_SKEW = re.compile(r"Max skewness\s*=\s*([0-9.eE+-]+)")
_FAILED = re.compile(r"Failed\s+(\d+)\s+mesh checks")


def parse_check_mesh(text: str) -> dict[str, Any]:
    """Cell count and the quality numbers checkMesh prints."""
    cells = _CELLS.search(text)
    ortho = _NON_ORTHO.search(text)
    skew = _SKEW.search(text)
    failed = _FAILED.search(text)
    return {
        "cells": int(cells.group(1)) if cells else None,
        "max_non_orthogonality_deg": float(ortho.group(1)) if ortho else None,
        "max_skewness": float(skew.group(1)) if skew else None,
        "mesh_ok": "Mesh OK" in text and failed is None,
        "failed_checks": int(failed.group(1)) if failed else 0,
    }


# --- fatal errors -------------------------------------------------------------

_FATAL_BLOCK = re.compile(r"FOAM FATAL (?:IO )?ERROR:?[ \t]*\n(.*?)(?:\n\s*\n|\Z)", re.DOTALL)


def fatal_message(text: str) -> str | None:
    """The reason OpenFOAM gave for stopping, from its ``FOAM FATAL ERROR`` block.

    The block is a header line, the message, a blank line, then ``From function``
    and file details; only the message is wanted, on one line.
    """
    match = _FATAL_BLOCK.search(text)
    if not match:
        return None
    return " ".join(match.group(1).split()) or None


# --- polyMesh/boundary --------------------------------------------------------

_PATCH = re.compile(r"^\s{4}(\w+)\s*\n\s{4}\{(.*?)\n\s{4}\}", re.MULTILINE | re.DOTALL)
_NFACES = re.compile(r"nFaces\s+(\d+);")


def parse_boundary(text: str) -> dict[str, int]:
    """Face count per patch."""
    out: dict[str, int] = {}
    for match in _PATCH.finditer(text):
        faces = _NFACES.search(match.group(2))
        if faces:
            out[match.group(1)] = int(faces.group(1))
    return out


# --- foamRun log --------------------------------------------------------------

_TIME = re.compile(r"^Time = (\S+?)s?\s*$", re.MULTILINE)
_RESIDUAL = re.compile(r"Solving for (\w+), Initial residual = ([0-9.eE+-]+)")
_CONVERGED = re.compile(r"SIMPLE solution converged in (\d+) iterations")


def parse_solver_log(text: str) -> dict[str, Any]:
    """Whether the residual controls were met, how many iterations ran, and the
    initial residual of each field on the last iteration."""
    converged = _CONVERGED.search(text)
    times = _TIME.findall(text)
    blocks = _TIME.split(text)
    residuals: dict[str, float] = {}
    if len(blocks) >= 3:
        for name, value in _RESIDUAL.findall(blocks[-1]):
            residuals[name] = float(value)
    if converged:
        iterations = int(converged.group(1))
    else:
        iterations = int(float(times[-1])) if times else 0
    return {"converged": converged is not None, "iterations": iterations, "residuals": residuals}


# --- postProcessing -----------------------------------------------------------


def _numbers(token_text: str) -> list[float]:
    return [float(t) for t in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", token_text)]


def read_series(path: Path) -> list[list[float]]:
    """Rows of a post-processing ``.dat`` file: ``[time, value...]``. Vectors
    written as ``(x y z)`` are flattened into the row."""
    rows: list[list[float]] = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        rows.append(_numbers(line.replace("(", " ").replace(")", " ")))
    return rows


def header_value(path: Path, label: str) -> float | None:
    """A number from a ``# Label : value`` header line, e.g. ``Area``."""
    for line in path.read_text().splitlines():
        if not line.startswith("#"):
            break
        key, _, rest = line[1:].partition(":")
        if key.strip() == label:
            nums = _numbers(rest)
            return nums[0] if nums else None
    return None


def find_dat(case_dir: Path, function_prefix: str, filename: str) -> Path | None:
    """``postProcessing/<function>*/<latest time>/<filename>``.

    The function directory name has its spaces stripped and its arguments in
    it, so it is matched by prefix, and the newest time directory wins.
    """
    root = case_dir / "postProcessing"
    if not root.is_dir():
        return None
    for fdir in sorted(root.iterdir()):
        if not fdir.is_dir() or not fdir.name.startswith(function_prefix):
            continue
        times = [t for t in fdir.iterdir() if t.is_dir() and (t / filename).is_file()]
        if not times:
            continue
        times.sort(key=lambda t: _time_key(t.name))
        return times[-1] / filename
    return None


def _time_key(name: str) -> float:
    try:
        return float(name)
    except ValueError:
        return -1.0


def drift(series: list[float]) -> float | None:
    """Relative spread of a series over its last tenth (at least 10 points):
    ``(max - min) / |last|``. Small means the value has stopped moving."""
    if len(series) < 2:
        return None
    tail = series[-max(10, len(series) // 10) :]
    last = tail[-1]
    if last == 0:
        return None
    return (max(tail) - min(tail)) / abs(last)


def read_y_plus(path: Path) -> dict[str, float] | None:
    """Min, max and mean y+ across the wall patches at the last written time.

    ``yPlus.dat`` has one row per patch per time: ``time patch min max average``.
    """
    rows: list[list[str]] = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if parts and not line.lstrip().startswith("#") and len(parts) >= 5:
            rows.append(parts)
    if not rows:
        return None
    last_time = rows[-1][0]
    latest = [r for r in rows if r[0] == last_time]
    return {
        "min": min(float(r[2]) for r in latest),
        "max": max(float(r[3]) for r in latest),
        "average": sum(float(r[4]) for r in latest) / len(latest),
    }
