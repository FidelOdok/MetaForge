#!/usr/bin/env python3
"""Project-brief adherence rubric for the chat context evals (MET-570).

A project-scoped thread gets a synthetic project brief prepended to its
history every turn (``_project_brief`` in ``api_gateway/chat/routes.py``),
including an instruction to pass ``project_id=`` to the twin write tools.
This rubric scores whether the agent actually absorbed it: does it know which
project it is in, do its twin writes carry the right ``project_id`` (and never
a foreign one), and did the asked-for work products actually land in the
project (fetched live by ``score_chat_brief``, like the runs-suite rubrics do).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from chat_rubric_common import score, tool_steps

# Twin write tools the brief instructs the agent to scope (mcp_<server>_<tool>).
TWIN_WRITE_TOOLS = ("mcp_twin_record_decision", "mcp_twin_commit_geometry")

# Structural check ids this rubric can contribute (wiring-test contract).
CHECKS = (
    "project_name_known",
    "decision_scoped_to_project",
    "no_foreign_project_id",
    "work_product_landed",
)


def evaluate_chat_brief(
    turns: list[dict[str, Any]], project_id: str, project_name: str
) -> dict[str, bool]:
    """Pure evaluator over the runner's turn records."""
    replies = " ".join(str(t.get("reply") or "") for t in turns)
    writes = [s for t in turns for s in tool_steps(t) if s.get("tool") in TWIN_WRITE_TOOLS]
    scoped = [
        s for s in writes if str((s.get("arguments") or {}).get("project_id")) == str(project_id)
    ]
    foreign = [
        s
        for s in writes
        if (s.get("arguments") or {}).get("project_id") not in (None, "", project_id)
    ]
    return {
        "project_name_known": bool(project_name) and project_name.casefold() in replies.casefold(),
        "decision_scoped_to_project": bool(scoped),
        "no_foreign_project_id": not foreign,
    }


def _work_products_present(base: str, project_id: str) -> bool:
    url = base.rstrip("/") + f"/v1/projects/{project_id}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - dev gateway
            proj = json.loads(r.read() or "{}")
    except (urllib.error.URLError, json.JSONDecodeError):
        return False
    return bool(proj.get("work_products"))


def score_chat_brief(
    base: str, project_id: str, project_name: str, turns: list[dict[str, Any]]
) -> dict[str, Any]:
    checks = evaluate_chat_brief(turns, project_id, project_name)
    checks["work_product_landed"] = _work_products_present(base, project_id)
    return {"score": score(checks), "checks": checks, "project_id": project_id}
