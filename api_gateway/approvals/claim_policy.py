"""A phase claim the project's approval policy already covers (FORGE-745).

In a client-mode run every phase starts with ``phase.claim``, and an
unverified MCP caller's claim is held for a person. With the project's policy
at ``auto`` a person has handed the run's gates to the reviewer, yet each phase
still waited on someone accepting a claim prompt; with nobody at the client
the run stalled (the Chair run's reworked design task sat unclaimed for 40
minutes while five claim holds expired).

A claim is narrow: it lets one session do one phase of one run, every write it
then makes is scoped to that phase, and the work it produces still goes
through the gate. So under ``auto`` the policy approves a claim on an open task
of the project's own run. No agent approves anything and no model judges it:
the approver recorded is the policy and the person who set it. Every other
write, and every other mode, is held for a person as before.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.approvals.claim_policy")

__all__ = ["POLICY_APPROVABLE_TOOLS", "claim_covered_by_policy"]

#: Tools the policy may approve. Only the claim: everything a claimed phase
#: then writes is covered by the claim's own grant, not by this list.
POLICY_APPROVABLE_TOOLS = frozenset({"phase.claim"})


async def claim_covered_by_policy(tool: str, arguments: dict[str, Any]) -> str | None:
    """The approver label when the project's policy covers this call, else ``None``.

    ``None`` with the reason logged whenever any check fails, so the call is
    held for a person exactly as it would have been.
    """
    from api_gateway.approvals.policy import effective_mode, get_policy_store
    from api_gateway.client_tasks.routes import get_client_task_store
    from api_gateway.runs import routes as run_routes
    from api_gateway.runs.schemas import project_of
    from orchestrator.design_flow.client_tasks import Intelligence

    with tracer.start_as_current_span("claim_policy.check") as span:
        span.set_attribute("tool.id", tool)

        def no(why: str) -> None:
            logger.info("claim_policy_not_covered", tool=tool, why=why)
            span.set_attribute("claim_policy.covered", False)

        if tool not in POLICY_APPROVABLE_TOOLS:
            no("not a tool the policy approves")
            return None
        task_id = str(arguments.get("task_id") or "")
        try:
            task = get_client_task_store().get(task_id)
        except Exception:  # noqa: BLE001 - an unknown task is a person's call
            no("unknown task")
            return None
        # A claimed task can be claimed again by a client that restarted or
        # lost its session handle (FORGE-746); a submitted one cannot.
        if task.status not in ("open", "claimed"):
            no(f"the task is {task.status}")
            return None
        try:
            run = run_routes.get_run_store().get(task.run_id)
        except Exception:  # noqa: BLE001
            no("unknown run")
            return None
        if run.is_terminal or not run_routes.is_design_flow_run(run):
            no("the run is not a live design flow")
            return None
        if run_routes.run_intelligence(run) is not Intelligence.CLIENT:
            no("the run does not take client tasks")
            return None
        project_id = project_of(run.request)
        if not project_id or (task.project_id and task.project_id != project_id):
            no("the task is not in the run's project")
            return None
        policy = await get_policy_store().get(str(project_id))
        mode = effective_mode(policy)
        if mode != "auto":
            no(f"policy '{mode}'")
            return None
        approver = f"policy:auto:{policy.updated_by or 'unknown'}"
        span.set_attribute("claim_policy.covered", True)
        logger.info(
            "claim_policy_covered",
            tool=tool,
            task_id=task_id,
            run_id=run.id,
            project_id=project_id,
            approver=approver,
        )
        return approver
