"""Apply-on-approve executor for design-change proposals (MET-548, Phase 3b).

When a human approves a ``twin.propose_change`` proposal, this runs its
structured ``diff`` against the twin via the existing recorders — closing the
gated loop: prompt → propose → approve → **apply** → twin updated
(``CHANGE_APPLIED``). Vertical slice: ``record_decision`` is fully wired;
``regenerate_geometry`` / ``update_properties`` return an explicit
"unsupported" so approval never silently no-ops (they're the next slice).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from api_gateway.assistant.schemas import DesignChangeProposal

logger = structlog.get_logger(__name__)

ApplyExecutor = Callable[[DesignChangeProposal], Awaitable[dict[str, Any]]]


def make_apply_executor(decision_recorder: Any) -> ApplyExecutor:
    """Return an ``apply(proposal)`` that executes the proposal's diff action."""

    async def apply(proposal: DesignChangeProposal) -> dict[str, Any]:
        diff = proposal.diff or {}
        action = str(diff.get("action") or "").strip()

        if action == "record_decision" and decision_recorder is not None:
            result = await decision_recorder(
                title=str(diff.get("title") or proposal.description),
                rationale=str(diff.get("rationale") or proposal.description),
                alternatives=diff.get("alternatives"),
                project_id=proposal.project_id,
                session_id=str(proposal.session_id) if proposal.session_id else None,
            )
            return {"applied": True, "action": action, **(result or {})}

        logger.info(
            "proposal_apply_unsupported_action",
            action=action or "(none)",
            change_id=str(proposal.change_id),
        )
        return {
            "applied": False,
            "action": action,
            "reason": f"apply not yet supported for action '{action or '(none)'}'",
        }

    return apply
