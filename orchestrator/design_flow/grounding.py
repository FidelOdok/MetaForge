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

#: FORGE-520: the turn made successful tool calls (FreeCAD session work, say),
#: but none of them wrote to the twin, and the reply says something was saved.
#: Only the chat harness adds this one; it does not change a phase status.
NO_TWIN_COMMIT_BANNER = (
    "⚠ No twin commit happened this turn: no twin write tool (such as "
    "twin.commit_geometry) succeeded, so anything described below as saved, "
    "committed or stored in the twin was NOT persisted. Treat it as unverified."
)

#: FORGE-520: prefix of the banner naming node ids the reply quotes that no
#: tool returned this turn. The ids follow it, then ``UNVERIFIED_NODE_ID_NOTE``.
UNVERIFIED_NODE_ID_BANNER = "⚠ Unverified node id(s): "
UNVERIFIED_NODE_ID_NOTE = (
    ". No tool call this turn returned them as twin node ids. FreeCAD session "
    "object ids such as part_N or assembly_N are not twin node ids."
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
