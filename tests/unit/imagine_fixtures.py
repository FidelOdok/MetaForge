"""A formgraph product config with an inline context, for the Imagine tests (FORGE-821).

Built from the plugin's ``outdoor-air-sensor`` example, with one real-shaped
part per function, the board and the battery, so a studio can be handed over.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

_EXAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "imagine" / "outdoor-air-sensor.json"


def _part(pid: str, role: str, name: str, env: list[float], bus: str) -> dict[str, Any]:
    return {
        "id": pid,
        "role": role,
        "name": name,
        "mpn": f"{pid.upper()}-01",
        "manufacturer": "Example Parts",
        "datasheet": f"https://example.com/{pid}.pdf",
        "retrieved": "2026-11-06",
        "mass": 3.0,
        "interfaces": [{"id": "io", "bus": bus, "dir": "bi"}],
        "geometry": {
            "envelope": env,
            "source": "datasheet mechanical drawing",
            "body": [{"shape": "box", "at": [0, 0, env[2] / 2], "size": env}],
        },
    }


def base_config() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    return data


def full_config() -> dict[str, Any]:
    """The example plus an inline context: parts, options and confirmed scenarios."""
    cfg = copy.deepcopy(base_config())
    parts = [
        _part("usbc-a", "fn:e_usbc", "USB-C receptacle", [9.0, 7.5, 3.2], "usb"),
        _part("th-a", "fn:e_th", "Temperature and humidity sensor", [3.0, 3.0, 1.0], "i2c"),
        _part("th-b", "fn:e_th", "Temperature sensor, budget", [2.5, 2.5, 0.9], "i2c"),
        _part("lte-a", "fn:e_lte", "LTE-M module", [16.0, 18.0, 2.4], "uart"),
        _part("btn-a", "fn:e_btn", "Tactile switch", [6.0, 6.0, 4.3], "gpio"),
        _part("led-a", "fn:e_led", "RGB LED", [2.0, 2.0, 0.6], "gpio"),
    ]
    cfg["context"] = {
        "components": {"schemaVersion": 1, "parts": parts},
        "options": {
            "schemaVersion": 1,
            "decisions": [
                {
                    "id": "th",
                    "kind": "part",
                    "label": "Temperature sensor",
                    "candidates": ["th-a", "th-b"],
                    "pick": "th-a",
                }
            ],
        },
    }
    cfg["scenarios"] = [dict(s, by="user") for s in cfg.get("scenarios") or []]
    return cfg


def mesh_glb() -> dict[str, Any]:
    """Metadata of an illustrative concept mesh, as concept_mesh records it."""
    return {"authority": "illustrative", "generated_by": "twin.generate_concept_mesh"}
