#!/usr/bin/env python3
"""V&V / simulation correctness rubric (MET-10).

The V&V phase is the one that is supposed to *catch* problems, so a hollow
success here is the most dangerous kind. Live inspection found exactly that: a
native V&V decision claiming "the simulations demonstrated compliance with ...
stress limits, thermal performance, and electrical circuit integrity" while the
phase summary admits "the attempt to run the mechanical simulations (FEA and
thermal) and electrical validations (ERC and DRC) encountered failures." The
verdict asserts PASS while the checks never actually ran — a false pass.

This rubric scores whether a V&V verdict is *earned*:

  1. a verdict is recorded (pass / fail / compliance),
  2. a concrete check type is named (ERC / DRC / FEA / thermal / timing / …),
  3. it traces to a stated requirement / constraint / limit,
  4. a real check artifact exists (simulation_result / erc_report / test_result),
  5. a quantified result backs the verdict (a value with units),
  6. NOT a false pass — a positive verdict is not contradicted by text admitting
     the checks failed to run.

Check 6 is the important one: it catches a V&V phase that claims compliance
while the underlying analyses never executed. Check 4 (a real artifact) is the
honest Phase-1 boundary — with no authored schematic/mesh, ERC/FEA cannot run,
so a native V&V should score low until those checks are wired.
"""

from __future__ import annotations

import json
import re
import urllib.request

_VERDICT_POS = (
    "pass",
    "compliance",
    "complies",
    "comply",
    "meets",
    "satisfied",
    "satisfies",
    "within limits",
    "within spec",
    "verified",
    "no violations",
    "no errors",
)
_VERDICT_NEG = ("fail", "does not meet", "violation", "out of spec", "exceeds")
_CHECK_TYPES = (
    "erc",
    "drc",
    "fea",
    "finite element",
    "thermal",
    "timing",
    "power analysis",
    "signal integrity",
    "tolerance",
    "stress",
    "simulation",
)
_FAILURE_TO_RUN = (
    "encountered failure",
    "failed to run",
    "could not run",
    "unable to run",
    "attempt to run",
    "were not run",
    "was not run",
    "not be run",
    "simulation failed",
    "simulations failed",
    "-32001",
    "adapter",
    "error running",
)
_REQ_WORDS = ("requirement", "constraint", "limit", "spec", "target", "budget", "threshold")
# a number with an engineering unit (the evidence of a computed/checked value)
_NUM_UNIT = re.compile(
    r"[0-9]+(?:\.[0-9]+)?\s*(mm|cm|w|mw|kw|ma|a|v|mv|°c|hz|khz|mhz|%|n|mpa|kpa|g|kg|mm2|sf)\b"
)
_VV_DECISION_NAMES = (
    "valid",
    "verif",
    "v&v",
    "simulation",
    "sign-off",
    "signoff",
    "check",
    "review",
)


def evaluate_vv(*, decision_text: str, artifact_types: set[str], goal: str) -> dict[str, bool]:
    """Score the V&V outcome from twin-derived facts (pure)."""
    tl = decision_text.lower()
    at = {a.lower() for a in artifact_types}

    has_pos = any(m in tl for m in _VERDICT_POS)
    has_neg = any(m in tl for m in _VERDICT_NEG)
    verdict_recorded = has_pos or has_neg
    check_type_named = any(m in tl for m in _CHECK_TYPES)
    has_num = bool(_NUM_UNIT.search(tl))
    traces_to_requirement = any(w in tl for w in _REQ_WORDS) and has_num
    real_check_artifact = bool(
        at & {"simulation_result", "erc_report", "drc_report", "test_result", "test_plan"}
    )
    quantified_result = has_num
    admits_failure_to_run = any(m in tl for m in _FAILURE_TO_RUN)
    not_false_pass = not (has_pos and admits_failure_to_run)

    return {
        "verdict_recorded": verdict_recorded,
        "check_type_named": check_type_named,
        "traces_to_requirement": traces_to_requirement,
        "real_check_artifact": real_check_artifact,
        "quantified_result": quantified_result,
        "not_false_pass": not_false_pass,
    }


def vv_score(checks: dict[str, bool]) -> float:
    return round(sum(1 for v in checks.values() if v) / len(checks), 3) if checks else 0.0


def _get(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - dev gateway
        return json.loads(r.read() or "{}")


def score_vv(base: str, project_id: str, goal: str) -> dict:
    """Fetch a project's twin state and apply the V&V rubric live."""
    try:
        proj = _get(base, f"/v1/projects/{project_id}")
    except Exception as exc:  # noqa: BLE001 - eval tooling, report not raise
        return {"error": f"fetch failed: {exc}", "score": 0.0, "checks": {}}

    wps = proj.get("work_products", [])
    artifact_types = {str(w.get("type")) for w in wps}

    decisions = [w for w in wps if w.get("type") == "design_decision"]
    vv_text: list[str] = []
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
        if any(m in name.lower() for m in _VV_DECISION_NAMES):
            vv_text.append(chunk)

    text = " ".join(vv_text or all_text)
    checks = evaluate_vv(decision_text=text, artifact_types=artifact_types, goal=goal)
    return {
        "score": vv_score(checks),
        "checks": checks,
        "artifact_types": sorted(artifact_types),
        "n_vv_decisions": len(vv_text),
    }
