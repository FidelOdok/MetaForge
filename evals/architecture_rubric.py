#!/usr/bin/env python3
"""Architecture correctness rubric (MET-10).

Architecture turns requirements into a subsystem decomposition with budgets the
downstream phases must live within. Live inspection found it decomposes well and
names components, but claims "each subsystem has allocated budgets for mass and
cost" with no actual numbers — a budget allocation you can't hold anyone to is
not a real allocation.

This rubric scores whether an architecture is engineering-grade:

  1. an architecture decision exists,
  2. the product is decomposed into subsystems (>= 2 named),
  3. subsystems are mapped to concrete components / technology choices,
  4. budgets are allocated AND quantified (mass / power / cost with units),
  5. it traces back to the requirements / constraints,
  6. inter-subsystem interfaces are defined (bus / power distribution).

Check 4 is the usual gap: "budgets allocated" is asserted but never given a
per-subsystem number, so nothing downstream can be held to it.
"""

from __future__ import annotations

import json
import re
import urllib.request

_ARCH_MARKERS = (
    "architecture",
    "subsystem",
    "decompos",
    "system-level",
    "allocation",
    "topology",
    "block diagram",
)
_SUBSYSTEMS = (
    "sensing",
    "sense",
    "compute",
    "processing",
    "power",
    "structure",
    "storage",
    "actuation",
    "communication",
    "comms",
    "thermal",
    "timekeeping",
)
_COMPONENTS = (
    "mpu",
    "bme",
    "ldo",
    "regulator",
    "microcontroller",
    "mcu",
    "sensor",
    " ic",
    "module",
    "connector",
    "capacitor",
    "microsd",
    "rtc",
)
_PART_RE = re.compile(r"\b[A-Za-z]{2,}-?\d{2,}[A-Za-z0-9]*\b")
_BUDGET_WORDS = ("budget", "allocat")
_BUDGET_UNIT = re.compile(r"[0-9]+(?:\.[0-9]+)?\s*(g|grams?|mg|kg|w|mw|mah|mwh|\$|usd|%|cents?)\b")
_REQ_TRACE = ("requirement", "constraint", "compliance", "complies", "spec", "target")
_IFACE = (
    "i2c",
    "spi",
    "uart",
    "bus",
    "rail",
    "power distribution",
    "3.3 v",
    "5 v",
    "regulation",
    "interface between",
    "connects",
)
_ARCH_DECISION_NAMES = ("architecture", "subsystem", "system")


def evaluate_architecture(
    *, decision_text: str, artifact_types: set[str], goal: str
) -> dict[str, bool]:
    """Score the architecture outcome from twin-derived facts (pure)."""
    tl = decision_text.lower()

    architecture_decision_present = any(m in tl for m in _ARCH_MARKERS)
    subsystems_decomposed = sum(1 for s in set(_SUBSYSTEMS) if s in tl) >= 2
    components_mapped = any(c in tl for c in _COMPONENTS) or bool(_PART_RE.search(tl))
    budget_allocated_quantified = any(w in tl for w in _BUDGET_WORDS) and bool(
        _BUDGET_UNIT.search(tl)
    )
    traces_to_requirements = any(w in tl for w in _REQ_TRACE)
    interfaces_defined = any(m in tl for m in _IFACE)

    return {
        "architecture_decision_present": architecture_decision_present,
        "subsystems_decomposed": subsystems_decomposed,
        "components_mapped": components_mapped,
        "budget_allocated_quantified": budget_allocated_quantified,
        "traces_to_requirements": traces_to_requirements,
        "interfaces_defined": interfaces_defined,
    }


def architecture_score(checks: dict[str, bool]) -> float:
    return round(sum(1 for v in checks.values() if v) / len(checks), 3) if checks else 0.0


def _get(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - dev gateway
        return json.loads(r.read() or "{}")


def score_architecture(base: str, project_id: str, goal: str) -> dict:
    """Fetch a project's twin state and apply the architecture rubric live."""
    try:
        proj = _get(base, f"/v1/projects/{project_id}")
    except Exception as exc:  # noqa: BLE001 - eval tooling, report not raise
        return {"error": f"fetch failed: {exc}", "score": 0.0, "checks": {}}

    wps = proj.get("work_products", [])
    artifact_types = {str(w.get("type")) for w in wps}

    decisions = [w for w in wps if w.get("type") == "design_decision"]
    arch_text: list[str] = []
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
        if any(m in name.lower() for m in _ARCH_DECISION_NAMES):
            arch_text.append(chunk)

    text = " ".join(arch_text or all_text)
    checks = evaluate_architecture(decision_text=text, artifact_types=artifact_types, goal=goal)
    return {
        "score": architecture_score(checks),
        "checks": checks,
        "artifact_types": sorted(artifact_types),
        "n_arch_decisions": len(arch_text),
    }
