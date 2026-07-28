#!/usr/bin/env python3
"""Firmware correctness rubric (MET-10).

Firmware is the last native phase the flywheel had not scored. The decision
backstop guarantees the phase *records a decision*, so presence-completeness
reads 1.0 — but the recorded firmware "design" is typically generic architecture
prose ("RTOS configuration, control loop design, pin mappings") with no concrete
bus driver, no register-level detail, and — crucially — no real artifacts: no
``pinmap`` and no ``firmware_source`` scaffold, both of which are first-class
work-product types. This rubric scores the *engineering outcome* of a firmware
run:

  1. a firmware decision exists,
  2. a concrete interface driver is defined (I2C/SPI/… init + read/write),
  3. register / sampling detail (device address, config registers, sample rate),
  4. a ``pinmap`` artifact exists (not just "pin mappings" in prose),
  5. a ``firmware_source`` scaffold artifact exists,
  6. a bring-up / self-test step is noted.

Checks 4 and 5 require a real work product — the firmware analog of
"real FEA, not a hand-calc" and "real BOM, not a prose BOM plan". To attribute
the score to the *firmware* phase (and not borrow I2C/register detail from the
electronics decision), ``score_firmware`` reads only firmware-relevant decisions.
"""

from __future__ import annotations

import json
import re
import urllib.request

_FW_MARKERS = ("firmware", "control loop", "rtos", "driver", "bare-metal", "bare metal", "hal ")
_BUS_MARKERS = ("i2c", "spi", "uart", "1-wire", "smbus")
_DRIVER_ACTIONS = ("init", "read", "write", "begin", "transaction", "poll", "sample", "configure")
_BRINGUP_MARKERS = (
    "bring-up",
    "bringup",
    "bring up",
    "self-test",
    "selftest",
    "smoke test",
    "verify",
    "verification",
    "who_am_i",
    "whoami",
)
# Decision names that belong to the firmware phase.
_FW_DECISION_NAMES = ("firmware", "control", "pin", "driver", "rtos", "bring")


def evaluate_firmware(
    *, decision_text: str, artifact_types: set[str], goal: str
) -> dict[str, bool]:
    """Score the firmware outcome from twin-derived facts (pure)."""
    tl = decision_text.lower()
    at = {a.lower() for a in artifact_types}

    firmware_decision_present = any(m in tl for m in _FW_MARKERS)
    interface_driver_defined = any(b in tl for b in _BUS_MARKERS) and any(
        a in tl for a in _DRIVER_ACTIONS
    )
    # concrete register / sampling detail: a hex register/address, a named config
    # register, or a numeric sample rate.
    register_or_sampling_detail = bool(
        re.search(r"0x[0-9a-f]{2}", tl)
        or re.search(r"\b(register|config|who_am_i|odr|full[- ]?scale)\b", tl)
        or re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(hz|khz|sps)\b", tl)
    )
    pinmap_present = "pinmap" in at
    firmware_source_present = "firmware_source" in at
    bringup_or_test_noted = any(m in tl for m in _BRINGUP_MARKERS)

    return {
        "firmware_decision_present": firmware_decision_present,
        "interface_driver_defined": interface_driver_defined,
        "register_or_sampling_detail": register_or_sampling_detail,
        "pinmap_present": pinmap_present,
        "firmware_source_present": firmware_source_present,
        "bringup_or_test_noted": bringup_or_test_noted,
    }


def firmware_score(checks: dict[str, bool]) -> float:
    return round(sum(1 for v in checks.values() if v) / len(checks), 3) if checks else 0.0


def _get(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - dev gateway
        return json.loads(r.read() or "{}")


def score_firmware(base: str, project_id: str, goal: str) -> dict:
    """Fetch a project's twin state and apply the firmware rubric live."""
    try:
        proj = _get(base, f"/v1/projects/{project_id}")
    except Exception as exc:  # noqa: BLE001 - eval tooling, report not raise
        return {"error": f"fetch failed: {exc}", "score": 0.0, "checks": {}}

    wps = proj.get("work_products", [])
    artifact_types = {str(w.get("type")) for w in wps}

    decisions = [w for w in wps if w.get("type") == "design_decision"]
    # Attribute the score to the firmware phase: prefer firmware-named decisions,
    # fall back to all if none match (so a differently-worded run still scores).
    fw_text: list[str] = []
    all_text: list[str] = []
    for w in decisions:
        try:
            node = _get(base, f"/v1/twin/nodes/{w['id']}")
        except Exception:  # noqa: BLE001
            continue
        name = str(node.get("name") or "")
        props = node.get("properties", {}) or {}
        chunk = " ".join([name, *(str(v) for v in props.values() if isinstance(v, str))])
        all_text.append(chunk)
        if any(m in name.lower() for m in _FW_DECISION_NAMES):
            fw_text.append(chunk)

    text = " ".join(fw_text or all_text)
    checks = evaluate_firmware(
        decision_text=text,
        artifact_types=artifact_types,
        goal=goal,
    )
    return {
        "score": firmware_score(checks),
        "checks": checks,
        "artifact_types": sorted(artifact_types),
        "n_firmware_decisions": len(fw_text),
    }
