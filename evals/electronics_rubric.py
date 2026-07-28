#!/usr/bin/env python3
"""Electronics hardware-design correctness rubric (MET-10).

The native-phase decision backstop (PR #488) guarantees the electronics phase
*records a decision*, so presence-based completeness now reads 1.0. But a
recorded decision is prose — it does not prove a real electronics design. A
phase can write "I chose an LDO and decoupling caps" and score 1.0 while never
selecting concrete parts, closing a power budget, running ERC, or producing a
BOM/netlist. This rubric scores the *engineering outcome* of an electronics run:

  1. schematic topology defined (IMU/load + power + connector chain),
  2. concrete components selected (>=2 distinct classes, not hand-wavy),
  3. power budget closed (numeric current/power AND rail voltages),
  4. ERC or netlist actually produced — an ARTIFACT, not prose (the electronics
     analog of "real FEA, not a hand-calc"; KiCad Phase-1 export is read-only
     but real: ERC / netlist / Gerber),
  5. a BOM deliverable exists (an artifact, not a prose "BOM plan"),
  6. interface + pinout defined (I2C/SPI/… with address/pins/header).

Checks 4 and 5 deliberately require a real work product, not prose, because a
brand-new board with no schematic file can *claim* ERC/BOM in text while never
running them — that is the hollow-success failure mode this rubric exists to
expose. The pure ``evaluate_electronics`` is unit-testable with synthetic data;
``score_electronics`` fetches a project's twin state and applies it.
"""

from __future__ import annotations

import json
import re
import urllib.request

# Prose evidence markers.
_SCHEMATIC_MARKERS = ("schematic", "topology", "net ", "connectivity", "rail")
_TOPOLOGY_CONTEXT = ("decoupling", "header", "rail", "regulator", "ldo", "pull-up", "pullup")
_INTERFACE_MARKERS = ("i2c", "spi", "uart", "usb", "gpio", "1-wire", "can bus")
_PINOUT_MARKERS = (
    "address 0x",
    "0x68",
    "0x69",
    "sda",
    "scl",
    "pinout",
    "pin map",
    "header",
    "pin ",
)

# Distinct component classes — a real BOM needs several of these.
_COMPONENT_CLASSES: tuple[tuple[str, ...], ...] = (
    ("ldo", "regulator", "buck", "ldo regulator"),
    ("decoupling", "bypass cap", "0.1uf", "0.1 uf", "100nf", "capacitor"),
    ("pull-up", "pullup", "pull up", "resistor"),
    ("header", "connector", "0.1-inch", "0.1 inch", "2.54mm", "pin header"),
    ("imu", "mpu-6050", "mpu6050", "accelerometer", "gyroscope", "sensor ic"),
    ("test point", "led", "ferrite", "tvs", "esd"),
)

# Artifact types that count as real electronics validation / deliverables.
_ERC_ARTIFACTS = ("netlist", "erc_report", "erc", "gerber", "drc_report")
_BOM_ARTIFACTS = ("bom", "bill_of_materials")


def evaluate_electronics(
    *, decision_text: str, artifact_types: set[str], goal: str
) -> dict[str, bool]:
    """Score the electronics outcome from twin-derived facts (pure)."""
    tl = decision_text.lower()
    at = {a.lower() for a in artifact_types}

    schematic_defined = any(m in tl for m in _SCHEMATIC_MARKERS) and any(
        m in tl for m in _TOPOLOGY_CONTEXT
    )

    classes_hit = sum(1 for cls in _COMPONENT_CLASSES if any(tok in tl for tok in cls))
    components_selected = classes_hit >= 2

    has_rail_v = bool(re.search(r"([0-9]+(?:\.[0-9]+)?)\s*v\b", tl))
    _power_re = r"([0-9]+(?:\.[0-9]+)?)\s*(m?a|m?w|milliamp|milliwatt|watt)\b"
    has_power_num = bool(re.search(_power_re, tl))
    power_budget_closed = has_rail_v and has_power_num

    # Artifact-based (honest): a real ERC/netlist/Gerber or BOM work product.
    erc_or_netlist_run = any(a in at for a in _ERC_ARTIFACTS)
    bom_present = any(a in at for a in _BOM_ARTIFACTS)

    interface_pinout = any(m in tl for m in _INTERFACE_MARKERS) and any(
        m in tl for m in _PINOUT_MARKERS
    )

    return {
        "schematic_topology_defined": schematic_defined,
        "components_selected": components_selected,
        "power_budget_closed": power_budget_closed,
        "erc_or_netlist_run": erc_or_netlist_run,
        "bom_present": bom_present,
        "interface_pinout_defined": interface_pinout,
    }


def electronics_score(checks: dict[str, bool]) -> float:
    return round(sum(1 for v in checks.values() if v) / len(checks), 3) if checks else 0.0


def _get(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - dev gateway
        return json.loads(r.read() or "{}")


def score_electronics(base: str, project_id: str, goal: str) -> dict:
    """Fetch a project's twin state and apply the electronics rubric live."""
    try:
        proj = _get(base, f"/v1/projects/{project_id}")
    except Exception as exc:  # noqa: BLE001 - eval tooling, report not raise
        return {"error": f"fetch failed: {exc}", "score": 0.0, "checks": {}}

    wps = proj.get("work_products", [])
    # Every work-product type that isn't the prose/geometry carriers is a
    # candidate electronics artifact (netlist, bom, erc_report, gerber, …).
    artifact_types = {
        str(w.get("type")) for w in wps if w.get("type") not in ("design_decision", "cad_model")
    }

    decisions = [w for w in wps if w.get("type") == "design_decision"]
    text_parts: list[str] = []
    for w in decisions:
        try:
            node = _get(base, f"/v1/twin/nodes/{w['id']}")
        except Exception:  # noqa: BLE001
            continue
        props = node.get("properties", {}) or {}
        text_parts.append(str(node.get("name") or ""))
        text_parts.extend(str(v) for v in props.values() if isinstance(v, str))

    checks = evaluate_electronics(
        decision_text=" ".join(text_parts),
        artifact_types=artifact_types,
        goal=goal,
    )
    return {
        "score": electronics_score(checks),
        "checks": checks,
        "artifact_types": sorted(artifact_types),
        "n_decisions": len(decisions),
    }
