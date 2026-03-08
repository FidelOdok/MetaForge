"""Temporal worker setup for MetaForge orchestrator.

Registers all activities and workflows with a Temporal worker and provides
a factory function for creating workers bound to a task queue.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.temporal_worker")

# Activities
from orchestrator.activities.mechanical_activity import run_mechanical_agent
from orchestrator.activities.electronics_activity import run_electronics_agent
from orchestrator.activities.firmware_activity import run_firmware_agent
from orchestrator.activities.simulation_activity import run_simulation_agent
from orchestrator.activities.approval_activity import wait_for_approval

# Workflows
from orchestrator.workflows.single_agent_workflow import SingleAgentWorkflow
from orchestrator.workflows.hardware_design_workflow import HardwareDesignWorkflow

# All registered activities
ALL_ACTIVITIES = [
    run_mechanical_agent,
    run_electronics_agent,
    run_firmware_agent,
    run_simulation_agent,
    wait_for_approval,
]

# All registered workflows
ALL_WORKFLOWS = [
    SingleAgentWorkflow,
    HardwareDesignWorkflow,
]

DEFAULT_TASK_QUEUE = "metaforge-agent-tasks"

try:
    from temporalio.worker import Worker

    HAS_TEMPORAL = True
except ImportError:
    HAS_TEMPORAL = False


def create_worker(client: Any, task_queue: str = DEFAULT_TASK_QUEUE) -> Any:
    """Create a Temporal Worker registered with all MetaForge activities and workflows.

    Args:
        client: A connected ``temporalio.client.Client`` instance.
        task_queue: The Temporal task queue name. Defaults to ``metaforge-agent-tasks``.

    Returns:
        A ``temporalio.worker.Worker`` ready to be started via ``await worker.run()``.

    Raises:
        ImportError: If the ``temporalio`` package is not installed.
    """
    if not HAS_TEMPORAL:
        raise ImportError(
            "temporalio is required for worker creation. "
            "Install with: pip install 'metaforge[temporal]'"
        )

    logger.info(
        "temporal_worker_created",
        task_queue=task_queue,
        activity_count=len(ALL_ACTIVITIES),
        workflow_count=len(ALL_WORKFLOWS),
    )

    return Worker(
        client,
        task_queue=task_queue,
        workflows=ALL_WORKFLOWS,
        activities=ALL_ACTIVITIES,
    )


async def run_worker(client: Any, task_queue: str = DEFAULT_TASK_QUEUE) -> None:
    """Create and run a Temporal worker with graceful shutdown.

    Registers SIGINT and SIGTERM handlers for clean shutdown.

    Args:
        client: A connected ``temporalio.client.Client`` instance.
        task_queue: The Temporal task queue name.
    """
    worker = create_worker(client, task_queue)

    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("temporal_worker_shutdown_signal_received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows does not support add_signal_handler
            pass

    logger.info(
        "temporal_worker_starting",
        task_queue=task_queue,
    )

    async with worker:
        await shutdown_event.wait()

    logger.info("temporal_worker_stopped", task_queue=task_queue)
