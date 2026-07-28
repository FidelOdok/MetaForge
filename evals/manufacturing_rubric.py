#!/usr/bin/env python3
"""Manufacturing-readiness correctness rubric (MET-10).

The native manufacturing phase repeats the V&V false-pass pattern: a decision
claims "consolidated the BOM ... prepare for manufacturing, ensuring compliance"
while the phase summary admits "I encountered errors while attempting to export
the BOM and netlist ... preventing successful execution." It claims manufacturing
readiness while the outputs never generated, and produces no real manufacturing
artifact (Gerber / pick-and-place / fab file). Presence-completeness reads 1.0.

This rubric scores whether manufacturing readiness is *earned*:

  1. a manufacturing decision exists,
  2. the BOM is consolidated / referenced (a real BOM artifact, or in text),
  3. a concrete process is named (PCB fab / SMT / reflow / panelization / layers),
  4. a real manufacturing artifact exists (gerber / pick_and_place / fab file),
  5. an assembly / bring-up / inspection step is noted,
  6. NOT a false readiness — a readiness claim is not contradicted by text
     admitting the outputs failed to generate.

Check 4 is the honest Phase-1 boundary (no PCB layout → no Gerbers). Check 6 is
the important catch: manufacturing must not claim readiness while the fab outputs
failed to export.
"""

from __future__ import annotations

import json
import re
import urllib.request

_MFG_MARKERS = ("manufactur", "fabricat", "assembly", "production", "panel", "smt", "reflow")
_PROCESS = (
    "pcb fab",
    "fabrication",
    "smt",
    "reflow",
    "assembly",
    "panelization",
    "panelisation",
    "stencil",
    "solder paste",
    "pick and place",
    "pick-and-place",
    "wave solder",
    "layer",
    "conformal",
)
_BOM_WORDS = ("bom", "bill of materials", "procurement", "sourcing", "consolidat")
_ASSEMBLY_WORDS = (
    "assembly",
    "bring-up",
    "bringup",
    "inspection",
    "aoi",
    "rework",
    "work order",
    "test point",
    "bring up",
)
_MFG_ARTIFACTS = ("gerber", "pick_and_place", "manufacturing_file", "drill")
_READY_POS = (
    "ready",
    "readiness",
    "prepared",
    "prepare for",
    "compliance",
    "complies",
    "consolidated",
    "release to manufactur",
    "production-ready",
    "manufacturable",
)
_MFG_FAILURE = (
    "encountered error",
    "failed to",
    "could not",
    "unable to",
    "preventing successful",
    "error while",
    "were not generated",
    "was not generated",
    "not be generated",
    "export failed",
    "-32001",
    "adapter",
)
_NUM_UNIT = re.compile(r"[0-9]+(?:\.[0-9]+)?\s*(layer|-layer|mm|mil|units?|pcs|days?|%|usd|\$)")
_MFG_DECISION_NAMES = ("manufactur", "fabricat", "assembly", "production", "supply", "readiness")


def evaluate_manufacturing(
    *, decision_text: str, artifact_types: set[str], goal: str
) -> dict[str, bool]:
    """Score the manufacturing outcome from twin-derived facts (pure)."""
    tl = decision_text.lower()
    at = {a.lower() for a in artifact_types}

    mfg_decision_present = any(m in tl for m in _MFG_MARKERS)
    bom_consolidated = any(m in tl for m in _BOM_WORDS) or "bom" in at
    process_named = any(m in tl for m in _PROCESS)
    mfg_artifact_present = bool(at & set(_MFG_ARTIFACTS))
    assembly_or_bringup_noted = any(m in tl for m in _ASSEMBLY_WORDS)
    has_ready = any(m in tl for m in _READY_POS)
    admits_failure = any(m in tl for m in _MFG_FAILURE)
    not_false_ready = not (has_ready and admits_failure)

    return {
        "mfg_decision_present": mfg_decision_present,
        "bom_consolidated": bom_consolidated,
        "process_named": process_named,
        "mfg_artifact_present": mfg_artifact_present,
        "assembly_or_bringup_noted": assembly_or_bringup_noted,
        "not_false_ready": not_false_ready,
    }


def manufacturing_score(checks: dict[str, bool]) -> float:
    return round(sum(1 for v in checks.values() if v) / len(checks), 3) if checks else 0.0


def _get(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - dev gateway
        return json.loads(r.read() or "{}")


def score_manufacturing(base: str, project_id: str, goal: str) -> dict:
    """Fetch a project's twin state and apply the manufacturing rubric live."""
    try:
        proj = _get(base, f"/v1/projects/{project_id}")
    except Exception as exc:  # noqa: BLE001 - eval tooling, report not raise
        return {"error": f"fetch failed: {exc}", "score": 0.0, "checks": {}}

    wps = proj.get("work_products", [])
    artifact_types = {str(w.get("type")) for w in wps}

    decisions = [w for w in wps if w.get("type") == "design_decision"]
    mfg_text: list[str] = []
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
        if any(m in name.lower() for m in _MFG_DECISION_NAMES):
            mfg_text.append(chunk)

    text = " ".join(mfg_text or all_text)
    checks = evaluate_manufacturing(decision_text=text, artifact_types=artifact_types, goal=goal)
    return {
        "score": manufacturing_score(checks),
        "checks": checks,
        "artifact_types": sorted(artifact_types),
        "n_mfg_decisions": len(mfg_text),
    }
