#!/usr/bin/env python3
"""Requirements correctness rubric (MET-10).

Requirements is the most foundational phase — everything downstream traces to
it — so a weak requirements set silently caps the whole design. Live inspection
found the substantive requirements *decision* is generic prose ("the following
requirements ensure functionality, operational efficiency, and compliance ...
they address power, size, mounting, and environmental considerations") with no
numbers; the quantified constraints live only in the auto-generated phase
summary, and nothing states how each requirement will be *verified*.

This rubric scores whether a requirements set is engineering-grade:

  1. a requirements decision exists,
  2. functional requirements are stated (what the product does),
  3. constraints are quantified (mass / size / power with units),
  4. interfaces / IO are specified,
  5. the operating environment is stated (temperature / supply voltage),
  6. verification / acceptance criteria are given (ISO-15288 verifiability —
     how each requirement will be checked).

Check 6 is the usual gap: constraints get quantified but never tied to an
acceptance criterion or verification method, so they aren't testable.
"""

from __future__ import annotations

import json
import re
import urllib.request

_REQ_MARKERS = ("requirement", "shall", "must ", "constraint", "specification", "spec ")
_FUNCTIONAL = (
    "expose",
    "provide",
    "regulat",
    "support",
    "mount",
    "measure",
    "sense",
    "store",
    "convert",
    "log",
    "drive",
    "read",
)
_INTERFACE = ("i2c", "spi", "uart", "usb", "header", "gpio", "connector", "0.1-inch", "pin ")
_ENV = (
    "temperature",
    "operating range",
    "-40",
    "85",
    "°c",
    "ambient",
    "humidity",
    "supply voltage",
    "input voltage",
    "5 v",
    "3.3 v",
    "operating environment",
)
_VERIFY = (
    "verif",
    "acceptance",
    "measured",
    "criteria",
    "tolerance",
    "pass/fail",
    "validated",
    "inspection",
    "test plan",
    "test that",
)
_NUM_UNIT = re.compile(
    r"[0-9]+(?:\.[0-9]+)?\s*(g|grams?|mm|cm|w|mw|ma|a|v|mv|°c|hz|khz|mhz|%|mil)\b"
)
_REQ_DECISION_NAMES = ("requirement", "spec", "constraint", "prd")


def evaluate_requirements(
    *, decision_text: str, artifact_types: set[str], goal: str
) -> dict[str, bool]:
    """Score the requirements outcome from twin-derived facts (pure)."""
    tl = decision_text.lower()

    requirements_decision_present = any(m in tl for m in _REQ_MARKERS)
    functional_reqs_stated = any(m in tl for m in _FUNCTIONAL)
    quantified_constraints = bool(_NUM_UNIT.search(tl))
    interfaces_specified = any(m in tl for m in _INTERFACE)
    operating_environment = any(m in tl for m in _ENV)
    verification_criteria = any(m in tl for m in _VERIFY)

    return {
        "requirements_decision_present": requirements_decision_present,
        "functional_reqs_stated": functional_reqs_stated,
        "quantified_constraints": quantified_constraints,
        "interfaces_specified": interfaces_specified,
        "operating_environment": operating_environment,
        "verification_criteria": verification_criteria,
    }


def requirements_score(checks: dict[str, bool]) -> float:
    return round(sum(1 for v in checks.values() if v) / len(checks), 3) if checks else 0.0


def _get(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - dev gateway
        return json.loads(r.read() or "{}")


def score_requirements(base: str, project_id: str, goal: str) -> dict:
    """Fetch a project's twin state and apply the requirements rubric live."""
    try:
        proj = _get(base, f"/v1/projects/{project_id}")
    except Exception as exc:  # noqa: BLE001 - eval tooling, report not raise
        return {"error": f"fetch failed: {exc}", "score": 0.0, "checks": {}}

    wps = proj.get("work_products", [])
    artifact_types = {str(w.get("type")) for w in wps}

    decisions = [w for w in wps if w.get("type") == "design_decision"]
    req_text: list[str] = []
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
        if any(m in name.lower() for m in _REQ_DECISION_NAMES):
            req_text.append(chunk)

    text = " ".join(req_text or all_text)
    checks = evaluate_requirements(decision_text=text, artifact_types=artifact_types, goal=goal)
    return {
        "score": requirements_score(checks),
        "checks": checks,
        "artifact_types": sorted(artifact_types),
        "n_req_decisions": len(req_text),
    }
