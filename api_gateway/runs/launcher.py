"""Run launcher for chat-triggered design flows (MET-587).

Builds the injected callables behind the ``runs`` MCP adapter's
``run.start_design_flow`` / ``run.get_status`` tools — the seam that lets the
chat agent start a gated lifecycle instead of requiring a human to fire
``POST /v1/runs`` separately. Drives the SAME in-process path as the route
(run store + ``_launch_flow``), no HTTP self-call.

HITL note: launching is deliberately NOT pre-gated. The flow itself pauses at
every phase boundary for human sign-off — the gates ARE the approval
mechanism; this tool only queues work a human must repeatedly approve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class RunLauncher:
    """Injected into the ``runs`` MCP adapter (keeps tool_registry free of
    api_gateway/orchestrator imports)."""

    async def start(
        self,
        *,
        goal: str,
        flow: str,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        from api_gateway.runs import routes
        from orchestrator.design_flow.spec import FLOWS

        if flow not in FLOWS:
            raise ValueError(f"unknown flow '{flow}'; known flows: {sorted(FLOWS)}")
        request: dict[str, Any] = {"goal": goal, "flow": flow}
        if project_id:
            request["project_id"] = project_id
        if session_id:
            request["session_id"] = session_id
        run = routes._store.create(request)
        routes._launch_flow(run.id)
        definition = FLOWS[flow]
        logger.info(
            "design_flow_launched_from_tool",
            run_id=run.id,
            flow=flow,
            project_id=project_id,
        )
        return {
            "run_id": run.id,
            "flow": flow,
            "phases": [p.id for p in definition.phases],
            "status": "running",
            "note": (
                "The run pauses at each phase gate for human approval "
                "(POST /v1/runs/{id}/approval or `forge runs approve`)."
            ),
        }

    async def status(self, *, run_id: str) -> dict[str, Any]:
        from api_gateway.runs import routes

        run = routes._store.get(run_id)
        out: dict[str, Any] = {
            "run_id": run.id,
            "status": run.status.value,
            "flow": run.request.get("flow"),
            "goal": run.request.get("goal"),
        }
        if run.approval_reason:
            out["awaiting_approval_reason"] = run.approval_reason
        if run.error:
            out["error"] = run.error
        if run.result:
            out["result"] = run.result
        return out


def make_run_launcher() -> RunLauncher:
    """Factory (mirrors the other recorder factories for wiring symmetry)."""
    return RunLauncher()
