"""Gazebo Sim result parsing -- reads an optional stats JSON file.

Gazebo Sim has no single standard result-file format analogous to
CalculiX's ``.frd`` (deep telemetry lives in a binary state log that needs
the ``gz`` tools to replay). For this first slice, results are read from a
minimal, MetaForge-defined stats JSON file that a world's plugin
configuration is expected to write:

    {
      "sim_time_s": 1.0,
      "real_time_s": 1.02,
      "iterations": 1000,
      "model_poses": {"<model_name>": [x, y, z, roll, pitch, yaw]}
    }

Only ``sim_time_s``, ``real_time_s``, and ``iterations`` are required;
``model_poses`` is optional. Extracting contact forces or full trajectories
is deferred -- see MET-635.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StatsParseError(Exception):
    """Raised when a Gazebo stats file cannot be parsed."""


_REQUIRED_KEYS = ("sim_time_s", "real_time_s", "iterations")


def parse_stats_file(stats_path: str) -> dict[str, Any]:
    """Parse a Gazebo stats JSON file into a structured dict.

    Args:
        stats_path: Path to the stats JSON file.

    Returns:
        Dict with keys: sim_time_s, real_time_s, iterations, model_poses.

    Raises:
        FileNotFoundError: If the stats file does not exist.
        StatsParseError: If the file is not valid JSON or is missing required keys.
    """
    path = Path(stats_path)
    if not path.exists():
        raise FileNotFoundError(f"Stats file not found: {stats_path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StatsParseError(f"Invalid JSON in stats file: {exc}") from exc

    if not isinstance(data, dict):
        raise StatsParseError("Stats file must contain a JSON object")

    missing = [key for key in _REQUIRED_KEYS if key not in data]
    if missing:
        raise StatsParseError(f"Stats file missing required keys: {missing}")

    return {
        "sim_time_s": float(data["sim_time_s"]),
        "real_time_s": float(data["real_time_s"]),
        "iterations": int(data["iterations"]),
        "model_poses": data.get("model_poses", {}),
    }


def extract_results(stats_path: str) -> dict[str, Any]:
    """Parse an existing Gazebo stats file, mirroring calculix.extract_results."""
    return parse_stats_file(stats_path)
