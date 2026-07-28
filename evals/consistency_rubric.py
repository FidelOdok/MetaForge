#!/usr/bin/env python3
"""Cross-phase consistency (digital-thread integrity) rubric (MET-10).

The per-phase rubrics score each phase in isolation, but MetaForge's core value is
a single source of design truth — the phases must *agree*. A design can have a
perfect electronics phase and a perfect firmware phase that nonetheless disagree
(the firmware drives a different sensor than the BOM lists). This rubric scores
the digital *thread*: whether the same device, interfaces, power, and parts flow
consistently from requirements through manufacturing.

  1. device thread — the goal's primary part traces across >= 3 phases,
  2. interface thread — a bus appears in BOTH electronics and firmware,
  3. power thread — a supply/rail voltage appears in >= 2 phases,
  4. artifact thread — the full set of expected work products all exist,
  5. requirement traced downstream — quantified requirements echo downstream,
  6. manufacturing references the BOM — and a real BOM artifact exists.

This is the first rubric that scores the whole design rather than one phase.
"""

from __future__ import annotations

import json
import re
import urllib.request

from rubric_common import goal_part_tokens

# Decision-name buckets per phase (a decision may fall in more than one).
_PHASE_NAMES = {
    "requirements": ("requirement", "spec", "prd"),
    "architecture": ("architecture", "subsystem", "system"),
    "electronics": ("schematic", "topology", "power budget", "electronic"),
    "firmware": ("firmware", "driver", "pin"),
    "vv": ("valid", "verif", "v&v", "simulation"),
    "manufacturing": ("manufactur", "fabricat", "readiness", "assembly"),
}
_BUS = ("i2c", "spi", "uart", "pwm", "can bus", "1-wire")
_VOLT = re.compile(r"[0-9]+(?:\.[0-9]+)?\s*v\b")
_NUM_UNIT = re.compile(r"[0-9]+(?:\.[0-9]+)?\s*(g|mm|w|mw|ma|v|°c|hz|mah)\b")
_CORE_ARTIFACTS = {
    "cad_model",
    "bom",
    "pinmap",
    "firmware_source",
    "test_plan",
    "manufacturing_file",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def evaluate_consistency(
    *, phase_texts: dict[str, str], artifact_types: set[str], goal: str
) -> dict[str, bool]:
    """Score digital-thread integrity from per-phase text + artifacts (pure)."""
    at = {a.lower() for a in artifact_types}
    lowered = {k: v.lower() for k, v in phase_texts.items()}

    # 1. device thread: the goal's part appears across >= 3 phases (vacuous if
    #    the goal names no specific part).
    tokens = goal_part_tokens(goal)
    if tokens:
        hits = sum(1 for txt in phase_texts.values() if any(tok in _norm(txt) for tok in tokens))
        device_thread = hits >= 3
    else:
        device_thread = True

    # 2. interface thread: a bus in BOTH electronics and firmware.
    elec = lowered.get("electronics", "")
    fw = lowered.get("firmware", "")
    interface_thread = any(b in elec for b in _BUS) and any(b in fw for b in _BUS)

    # 3. power thread: a rail voltage in >= 2 phases.
    power_thread = sum(1 for txt in lowered.values() if _VOLT.search(txt)) >= 2

    # 4. artifact thread: the full core set exists.
    artifact_thread = _CORE_ARTIFACTS <= at

    # 5. requirements quantified AND that quantification echoes downstream.
    req = lowered.get("requirements", "")
    downstream = " ".join(
        lowered.get(k, "") for k in ("architecture", "electronics", "vv", "manufacturing")
    )
    requirement_traced = bool(_NUM_UNIT.search(req)) and bool(_NUM_UNIT.search(downstream))

    # 6. manufacturing references the BOM AND a BOM artifact exists.
    mfg = lowered.get("manufacturing", "")
    mfg_references_bom = ("bom" in mfg or "bill of materials" in mfg) and "bom" in at

    return {
        "device_thread_intact": device_thread,
        "interface_thread_intact": interface_thread,
        "power_thread_intact": power_thread,
        "artifact_thread_complete": artifact_thread,
        "requirement_traced_downstream": requirement_traced,
        "manufacturing_references_bom": mfg_references_bom,
    }


def consistency_score(checks: dict[str, bool]) -> float:
    return round(sum(1 for v in checks.values() if v) / len(checks), 3) if checks else 0.0


def _get(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - dev gateway
        return json.loads(r.read() or "{}")


def score_consistency(base: str, project_id: str, goal: str) -> dict:
    """Fetch a project's twin state and apply the consistency rubric live."""
    try:
        proj = _get(base, f"/v1/projects/{project_id}")
    except Exception as exc:  # noqa: BLE001 - eval tooling, report not raise
        return {"error": f"fetch failed: {exc}", "score": 0.0, "checks": {}}

    wps = proj.get("work_products", [])
    artifact_types = {str(w.get("type")) for w in wps}

    phase_texts: dict[str, str] = {k: "" for k in _PHASE_NAMES}
    for w in (x for x in wps if x.get("type") == "design_decision"):
        try:
            node = _get(base, f"/v1/twin/nodes/{w['id']}")
        except Exception:  # noqa: BLE001
            continue
        name = str(node.get("name") or "")
        props = node.get("properties", {}) or {}
        chunk = " ".join([name, *(str(v) for v in props.values() if isinstance(v, str))])
        low = name.lower()
        for phase, markers in _PHASE_NAMES.items():
            if any(m in low for m in markers):
                phase_texts[phase] += " " + chunk

    checks = evaluate_consistency(phase_texts=phase_texts, artifact_types=artifact_types, goal=goal)
    return {
        "score": consistency_score(checks),
        "checks": checks,
        "artifact_types": sorted(artifact_types),
        "phases_seen": [k for k, v in phase_texts.items() if v.strip()],
    }
