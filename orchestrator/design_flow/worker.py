"""Building a worker that can actually run the design-flow workflow (FORGE-401).

Temporal runs workflow code in a sandbox that reimports the workflow module
and proxies everything it touches, so that a stray global or a
non-deterministic import is caught rather than silently corrupting a replay.

``orchestrator.design_flow.temporal_flow`` cannot survive that unaided:
importing it runs ``orchestrator/design_flow/__init__.py``, which imports the
executor, which imports ``twin_core`` and ``structlog``. Proxying those fails
outright ("Using subclasses of proxied objects is unsupported").

The remedy is passthrough — telling the sandbox to use the already-imported
modules rather than reimporting them. That is safe here because the workflow
*itself* calls none of them; they arrive only as a side effect of the package
import. Passthrough does not weaken the determinism checks on workflow code.

This is a function rather than a note in a docstring because the failure it
prevents does not look like a configuration mistake: the worker refuses to
start with ``Failed validating workflow DesignFlow``, which reads like a bug
in the workflow. Anyone wiring a worker by hand will hit it.
"""

from __future__ import annotations

from typing import Any

import structlog
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from orchestrator.design_flow.temporal_activities import DesignFlowActivities
from orchestrator.design_flow.temporal_flow import TASK_QUEUE, DesignFlowWorkflow

logger = structlog.get_logger(__name__)

__all__ = ["PASSTHROUGH_MODULES", "build_design_flow_worker", "design_flow_runner"]

#: Modules the sandbox reuses instead of reimporting.
#:
#: These are pulled in by the workflow module's *package*, not by the workflow.
#: Keep the list short: each entry is determinism checking given up, and the
#: reason it is safe is that workflow code never calls into them.
PASSTHROUGH_MODULES: tuple[str, ...] = (
    "orchestrator",
    "twin_core",
    "observability",
    "structlog",
    "pydantic",
)


def design_flow_runner() -> SandboxedWorkflowRunner:
    """The sandbox runner the design-flow workflow needs."""
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules(*PASSTHROUGH_MODULES)
    )


def build_design_flow_worker(
    client: Any,
    activities: DesignFlowActivities,
    *,
    task_queue: str = TASK_QUEUE,
    **kwargs: Any,
) -> Worker:
    """A worker wired to run design flows.

    Use this rather than constructing :class:`Worker` directly, so the
    sandbox configuration above cannot be forgotten in one place and present
    in another.
    """
    logger.info(
        "design_flow_worker_built",
        task_queue=task_queue,
        passthrough=len(PASSTHROUGH_MODULES),
    )
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[DesignFlowWorkflow],
        activities=activities.all(),
        workflow_runner=design_flow_runner(),
        **kwargs,
    )
