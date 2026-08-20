"""Apply-on-approve executor for design-change proposals (MET-548, Phase 3b).

When a human approves a ``twin.propose_change`` proposal, this runs its
structured ``diff`` against the twin via the existing recorders — closing the
gated loop: prompt → propose → approve → **apply** → twin updated
(``CHANGE_APPLIED``). ``record_decision`` and ``regenerate_geometry`` (MET-630)
are fully wired; any other action returns an explicit "unsupported" so
approval never silently no-ops.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from api_gateway.assistant.schemas import DesignChangeProposal

logger = structlog.get_logger(__name__)

ApplyExecutor = Callable[[DesignChangeProposal], Awaitable[dict[str, Any]]]


def make_apply_executor(
    decision_recorder: Any,
    mcp_bridge: Any = None,
    geometry_recorder: Any = None,
) -> ApplyExecutor:
    """Return an ``apply(proposal)`` that executes the proposal's diff action.

    Args:
        mcp_bridge: Optional ``McpBridge`` (MET-630) — required alongside
            ``geometry_recorder`` to apply ``regenerate_geometry`` actions.
    """

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

        if (
            action == "regenerate_geometry"
            and mcp_bridge is not None
            and geometry_recorder is not None
        ):
            return await _apply_regenerate_geometry(proposal, diff, mcp_bridge, geometry_recorder)

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


async def _apply_regenerate_geometry(
    proposal: DesignChangeProposal,
    diff: dict[str, Any],
    mcp_bridge: Any,
    geometry_recorder: Any,
) -> dict[str, Any]:
    from api_gateway.twin.regenerate_geometry import (
        RegenerateGeometryError,
        perform_regenerate_geometry,
    )

    script_source = diff.get("script_source")
    if not script_source or not isinstance(script_source, str):
        return {
            "applied": False,
            "action": "regenerate_geometry",
            "reason": "diff.script_source is required to regenerate geometry",
        }
    name = str(diff.get("name") or proposal.description)
    parameters = diff.get("parameters") if isinstance(diff.get("parameters"), dict) else None

    try:
        result = await perform_regenerate_geometry(
            bridge=mcp_bridge,
            recorder=geometry_recorder,
            script_source=script_source,
            name=name,
            project_id=proposal.project_id,
            domain=str(diff.get("domain") or "mechanical"),
            parameters=parameters,
        )
    except RegenerateGeometryError as exc:
        logger.warning(
            "proposal_apply_regenerate_failed",
            change_id=str(proposal.change_id),
            error=str(exc),
        )
        return {"applied": False, "action": "regenerate_geometry", "reason": str(exc)}

    return {"applied": True, "action": "regenerate_geometry", **(result or {})}
