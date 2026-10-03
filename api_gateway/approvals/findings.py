"""Turn a gate's finding text into structured findings (FORGE-507).

The workflow and the in-process executor both build findings as sentences. The
live workflow state carries them as a list; the in-process engine only leaves
them inside the approval reason. Both shapes are classified here, so a client
gets ``{category, message, items}`` either way.
"""

from __future__ import annotations

import re

from api_gateway.approvals.schemas import GateFinding

_NOT_READY = re.compile(r"NOT READY[^:]*:\s*", re.IGNORECASE)
#: A finding boundary inside the joined reason: findings are joined with "; ",
#: but a constraint finding lists violations with "; " too, so only split where
#: a new finding visibly begins.
_BOUNDARY = re.compile(r";\s+(?=phase '|\d+ constraint violation)")


def classify(text: str) -> GateFinding:
    lowered = text.lower()
    if "required deliverables" in lowered or "missing deliverable" in lowered:
        return GateFinding(kind="missing_deliverable", message=text)
    if "ungrounded" in lowered:
        return GateFinding(kind="ungrounded", message=text)
    if "constraint violation" in lowered:
        return GateFinding(kind="constraint_violation", message=text)
    if "geometry" in lowered or "interference" in lowered:
        return GateFinding(kind="geometry", message=text)
    if "analysis" in lowered:
        return GateFinding(kind="analysis", message=text)
    return GateFinding(kind="other", message=text)


def findings_from_lines(lines: list[str]) -> list[GateFinding]:
    return [classify(line) for line in lines if line.strip()]


def findings_from_reason(reason: str | None) -> list[GateFinding]:
    """Findings recoverable from an approval reason; empty for a ready gate."""
    if not reason:
        return []
    match = _NOT_READY.search(reason)
    if match is None:
        return []
    return findings_from_lines(_BOUNDARY.split(reason[match.end() :]))
