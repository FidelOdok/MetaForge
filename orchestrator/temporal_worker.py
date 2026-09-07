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
from digital_twin.memory.consolidation.workflow import (  # noqa: E402
    ConsolidationWorkflow,
    run_consolidation_pass_activity,
)
from orchestrator.activities.approval_activity import wait_for_approval  # noqa: E402
from orchestrator.activities.electronics_activity import run_electronics_agent  # noqa: E402
from orchestrator.activities.firmware_activity import run_firmware_agent  # noqa: E402
from orchestrator.activities.mechanical_activity import run_mechanical_agent  # noqa: E402
from orchestrator.activities.simulation_activity import run_simulation_agent  # noqa: E402
from orchestrator.workflows.hardware_design_workflow import HardwareDesignWorkflow  # noqa: E402

# Workflows
from orchestrator.workflows.single_agent_workflow import SingleAgentWorkflow  # noqa: E402

# All registered activities
ALL_ACTIVITIES = [
    run_mechanical_agent,
    run_electronics_agent,
    run_firmware_agent,
    run_simulation_agent,
    wait_for_approval,
    # MET-567 follow-up: the consolidation pass had a Temporal workflow since
    # MET-454 and was never registered here, so even a running worker could
    # not have picked it up. Registering it is what lets a worker own the
    # cadence instead of the gateway's in-process asyncio loop.
    run_consolidation_pass_activity,
]

# All registered workflows
ALL_WORKFLOWS = [
    SingleAgentWorkflow,
    HardwareDesignWorkflow,
    ConsolidationWorkflow,
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


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
#
# `create_worker` / `run_worker` have existed since MET-186 (2026-03-08) with
# **no caller outside tests** and no client connect anywhere in the tree, so
# the Temporal server ran with zero workflow executions for six months while
# `TEMPORAL_HOST` was passed to the gateway and read by nothing. This is the
# missing last mile: something that resolves the host, connects, and runs.


DEFAULT_TEMPORAL_HOST = "localhost:7233"
DEFAULT_NAMESPACE = "default"


def temporal_host() -> str:
    """Server address; ``TEMPORAL_HOST`` (as docker-compose already sets)."""
    import os

    return os.environ.get("TEMPORAL_HOST", "").strip() or DEFAULT_TEMPORAL_HOST


def temporal_namespace() -> str:
    import os

    return os.environ.get("TEMPORAL_NAMESPACE", "").strip() or DEFAULT_NAMESPACE


async def connect_client(
    host: str | None = None,
    namespace: str | None = None,
    *,
    attempts: int = 10,
    sleep: Any = None,
) -> Any:
    """Connect a Temporal client, retrying while the server comes up.

    Same lesson as MET-710: a worker container and its server start together,
    so a first-attempt failure is usually a race, not an absence. Retrying
    turns that into a slow start instead of a dead worker.
    """
    if not HAS_TEMPORAL:
        raise ImportError(
            "temporalio is required to run the worker. Install with: "
            "pip install 'metaforge[temporal]'"
        )
    from temporalio.client import Client

    target = host or temporal_host()
    ns = namespace or temporal_namespace()
    delay = sleep if sleep is not None else asyncio.sleep
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            client = await Client.connect(target, namespace=ns)
            logger.info("temporal_client_connected", host=target, namespace=ns)
            return client
        except Exception as exc:  # noqa: BLE001 — retried, re-raised below
            last = exc
            if attempt == attempts - 1:
                break
            wait = min(2.0 * (attempt + 1), 10.0)
            logger.warning(
                "temporal_client_retrying",
                host=target,
                attempt=attempt + 1,
                of=attempts,
                retry_in_s=wait,
                error=str(exc),
            )
            await delay(wait)
    assert last is not None
    logger.error("temporal_client_connect_failed", host=target, error=str(last))
    raise last


async def main(task_queue: str = DEFAULT_TASK_QUEUE) -> None:
    """Connect and run the worker until SIGINT/SIGTERM."""
    from observability.logging import configure_logging

    try:
        configure_logging()
    except Exception:  # noqa: BLE001 — logging config must not block the worker
        pass
    client = await connect_client()
    await run_worker(client, task_queue)


if __name__ == "__main__":  # pragma: no cover — process entrypoint
    import os

    asyncio.run(main(os.environ.get("TEMPORAL_TASK_QUEUE") or DEFAULT_TASK_QUEUE))
