#!/usr/bin/env python3
"""Mechanical hardware-design correctness rubric (MET-10).

Presence-based completeness lies: a phase can record a `cad_model` + a
`design_decision` and score 1.0 while the geometry is a hardcoded part that
ignores the goal and the "V&V" is a hand-calc, not real FEA. This rubric scores
the *engineering outcome* of a mechanical run against what a real mechanical
design must produce:

  1. a cad_model exists,
  2. it is actually loadable/viewable (content_hash or minio_object_key),
  3. the geometry traces to the goal (not a hardcoded part for a different goal),
  4. a real FEA ran (CalculiX / finite-element) — NOT a hand-calc,
  5. a numeric safety factor is recorded,
  6. a pass/fail verdict is recorded against the requirement.

The pure ``evaluate_mechanical`` is unit-testable with synthetic data;
``score_mechanical`` fetches a project's twin state and applies it.
"""

from __future__ import annotations

import json
import re
import urllib.request

# Names that indicate the deterministic quadruped-demo handler produced the part
# regardless of the goal (the "hollow success" signal).
_HARDCODED_MARKERS = ("hipbracket", "hip bracket", "hip-mount", "hip mount")
_QUADRUPED_GOAL = ("quadruped", "hip", "leg", "hip-mount")
_FEA_MARKERS = ("calculix", "finite element", "finite-element", " fea", "von mises", "von-mises")
_HANDCALC_MARKERS = (
    "hand-calc",
    "hand calc",
    "closed-form",
    "closed form",
    "first-order",
    "not fea",
)


def evaluate_mechanical(
    *, cad_names: list[str], cad_loadable: bool, decision_text: str, goal: str
) -> dict[str, bool]:
    """Score the mechanical outcome from twin-derived facts (pure)."""
    tl = decision_text.lower()
    goal_l = goal.lower()

    has_cad = bool(cad_names)
    # goal-traceable: a hardcoded quadruped part for a non-quadruped goal fails.
    hardcoded = any(any(m in n.lower() for m in _HARDCODED_MARKERS) for n in cad_names)
    goal_is_quadruped = any(m in goal_l for m in _QUADRUPED_GOAL)
    goal_traceable = has_cad and not (hardcoded and not goal_is_quadruped)

    fea_real = any(m in tl for m in _FEA_MARKERS) and not any(m in tl for m in _HANDCALC_MARKERS)
    has_sf = bool(re.search(r"safety[ -]?factor[^0-9]{0,24}([0-9]+(?:\.[0-9]+)?)", tl))
    has_verdict = ("pass" in tl or "fail" in tl) and "safety" in tl

    return {
        "cad_model_present": has_cad,
        "cad_model_loadable": bool(cad_loadable),
        "goal_traceable_geometry": goal_traceable,
        "real_fea_run": fea_real,
        "safety_factor_recorded": has_sf,
        "verdict_vs_requirement": has_verdict,
    }


def mechanical_score(checks: dict[str, bool]) -> float:
    return round(sum(1 for v in checks.values() if v) / len(checks), 3) if checks else 0.0


def _get(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - dev gateway
        return json.loads(r.read() or "{}")


def score_mechanical(base: str, project_id: str, goal: str) -> dict:
    """Fetch a project's twin state and apply the mechanical rubric live."""
    try:
        proj = _get(base, f"/v1/projects/{project_id}")
    except Exception as exc:  # noqa: BLE001 - eval tooling, report not raise
        return {"error": f"fetch failed: {exc}", "score": 0.0, "checks": {}}

    wps = proj.get("work_products", [])
    cad = [w for w in wps if w.get("type") == "cad_model"]
    decisions = [w for w in wps if w.get("type") == "design_decision"]

    cad_names: list[str] = []
    cad_loadable = False
    for w in cad:
        try:
            node = _get(base, f"/v1/twin/nodes/{w['id']}")
        except Exception:  # noqa: BLE001
            node = {}
        props = node.get("properties", {}) or {}
        cad_names.append(str(node.get("name") or w.get("name") or ""))
        if props.get("minio_object_key") or props.get("content_hash"):
            cad_loadable = True

    text_parts: list[str] = []
    for w in decisions:
        try:
            node = _get(base, f"/v1/twin/nodes/{w['id']}")
        except Exception:  # noqa: BLE001
            continue
        props = node.get("properties", {}) or {}
        text_parts.append(str(node.get("name") or ""))
        text_parts.extend(str(v) for v in props.values() if isinstance(v, str))

    checks = evaluate_mechanical(
        cad_names=cad_names,
        cad_loadable=cad_loadable,
        decision_text=" ".join(text_parts),
        goal=goal,
    )
    return {
        "score": mechanical_score(checks),
        "checks": checks,
        "cad_names": cad_names,
        "n_cad": len(cad),
        "n_decisions": len(decisions),
    }
