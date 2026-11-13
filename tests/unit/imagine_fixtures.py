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
    data["form"] = authored_lamp_form()
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


def authored_lamp_form() -> dict[str, Any]:
    """A desk lamp written by hand as an authored form (spec 23.6, IMG-FORM-001).

    Base, arm, shade, light panel, touch button and USB-C port. Units are millimetres
    and Y is up. The shade's top is at base_h + arm_len + head_h.
    """

    def part(
        pid: str, name: str, kind: str, uses: list[str], code: str, **extra: Any
    ) -> dict[str, Any]:
        return {"id": pid, "name": name, "kind": kind, "uses": uses, "code": code, **extra}

    return {
        "template": "authored",
        "params": {
            "base_r": 55,
            "base_h": 12,
            "arm_len": 260,
            "arm_lean": 40,
            "head_r": 50,
            "head_h": 70,
        },
        "ranges": {"arm_len": [120, 480], "arm_lean": [-80, 80], "head_r": [30, 80]},
        "authored": {
            "parts": [
                part(
                    "base",
                    "Weighted base",
                    "generated",
                    ["base_r", "base_h"],
                    "return [g.cylinder(p.base_r, p.base_h, 48).at(0, p.base_h / 2, 0)];",
                    body=True,
                ),
                part(
                    "arm",
                    "Arm",
                    "generated",
                    ["base_h", "arm_len", "arm_lean"],
                    "const top = p.base_h + p.arm_len;"
                    "return [g.tube([[0, p.base_h, 0],"
                    " [p.arm_lean * 0.2, p.base_h + p.arm_len * 0.5, 0],"
                    " [p.arm_lean, top, 0]], 4, 16)];",
                ),
                part(
                    "shade",
                    "Shade",
                    "generated",
                    ["base_h", "arm_len", "arm_lean", "head_r", "head_h"],
                    "return [g.frustum(p.head_r, 12, p.head_h, 48)"
                    ".at(p.arm_lean, p.base_h + p.arm_len + p.head_h / 2, 0)];",
                ),
                part(
                    "panel",
                    "Light panel",
                    "primitive",
                    ["base_h", "arm_len", "arm_lean", "head_r"],
                    "return [g.cylinder(p.head_r * 0.85, 3, 48)"
                    ".at(p.arm_lean, p.base_h + p.arm_len + 1.5, 0)];",
                ),
                part(
                    "button",
                    "Touch button",
                    "component",
                    ["base_r", "base_h"],
                    "return [g.cylinder(7, 4, 24).at(p.base_r * 0.55, p.base_h + 2, 0)];",
                ),
                part(
                    "usbc",
                    "USB-C receptacle",
                    "component",
                    ["base_r", "base_h"],
                    "return [g.box(9, 3.2, 7.5).at(0, p.base_h / 2, -(p.base_r - 3))];",
                ),
            ]
        },
    }
