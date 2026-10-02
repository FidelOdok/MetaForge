"""Shared definition of an ungrounded phase reply (FORGE-484).

The chat harness prepends ``UNGROUNDED_BANNER`` to a reply that claims completed
design actions although the turn made no successful tool call. A design-flow
phase reuses that harness, so its summary carries the banner too. Both flow
engines read it from here, so "ungrounded" means the same thing on each.
"""

from __future__ import annotations

UNGROUNDED_BANNER = (
    "⚠ No tool calls were made this turn \u2014 the actions described below "
    "were NOT actually performed; treat this reply as unverified."
)

#: Phase status for a reply that claimed work it did not do. Never "completed".
UNGROUNDED_STATUS = "ungrounded"


def is_ungrounded(summary: str | None) -> bool:
    return bool(summary) and UNGROUNDED_BANNER in (summary or "")[: len(UNGROUNDED_BANNER) + 8]


def phase_status(summary: str | None, status: str) -> str:
    """``status``, downgraded when the summary is flagged ungrounded."""
    if status == "completed" and is_ungrounded(summary):
        return UNGROUNDED_STATUS
    return status
