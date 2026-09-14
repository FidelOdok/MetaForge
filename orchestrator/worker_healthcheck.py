"""Liveness probe for the Temporal worker container (MET-726).

The worker reuses the gateway image and so inherited its ``HEALTHCHECK``, which
curls ``localhost:8000/health``. The worker serves no HTTP, so it reported
``unhealthy`` from the moment it was added while running perfectly — and a
container that is always unhealthy teaches everyone to ignore the one signal
that would show a genuinely dead one.

What actually matters is whether this worker is **polling its task queue**.
``DescribeTaskQueue`` answers exactly that, and each poller carries an identity
of ``pid@hostname`` — so the probe can insist on *this* container rather than
accepting any worker anywhere. That distinction is the whole point: a dead
worker sharing a queue with a healthy peer must not report healthy.

Run as ``python -m orchestrator.worker_healthcheck``; exits 0 when this
container is polling, 1 otherwise. Every failure path prints a one-line reason,
because a health probe whose output is a traceback is how the inherited one
managed to look like noise for so long.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 5.0


def expected_identity_suffix() -> str:
    """The ``@hostname`` a poller from this container will report.

    Temporal builds a worker identity as ``pid@hostname``. The pid is not
    knowable from outside the process, so match on the host half; inside a
    container that is the container id, which no other worker shares.
    """
    return f"@{socket.gethostname()}"


async def _poller_identities(task_queue: str, host: str, namespace: str) -> list[str]:
    from temporalio.api.enums.v1 import TaskQueueType
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
    from temporalio.client import Client

    client = await Client.connect(host, namespace=namespace)
    response = await client.workflow_service.describe_task_queue(
        DescribeTaskQueueRequest(
            namespace=namespace,
            task_queue=TaskQueue(name=task_queue),
            task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
        )
    )
    return [poller.identity for poller in response.pollers]


def is_polling(identities: list[str], suffix: str | None = None) -> bool:
    """True when one of ``identities`` belongs to this container."""
    want = suffix if suffix is not None else expected_identity_suffix()
    return any(identity.endswith(want) for identity in identities)


async def check(
    task_queue: str | None = None,
    host: str | None = None,
    namespace: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Return ``(healthy, reason)``. Never raises."""
    from orchestrator.temporal_worker import (
        DEFAULT_TASK_QUEUE,
        temporal_host,
        temporal_namespace,
    )

    queue = task_queue or os.environ.get("TEMPORAL_TASK_QUEUE", "").strip() or DEFAULT_TASK_QUEUE
    resolved_host = host or temporal_host()
    resolved_ns = namespace or temporal_namespace()

    try:
        identities: list[Any] = await asyncio.wait_for(
            _poller_identities(queue, resolved_host, resolved_ns), timeout=timeout
        )
    except TimeoutError:
        return False, f"describe_task_queue timed out after {timeout}s ({resolved_host})"
    except Exception as exc:  # noqa: BLE001 — a probe reports, it does not raise
        return False, f"{type(exc).__name__}: {exc}"

    if not identities:
        return False, f"no workers polling {queue!r}"
    if not is_polling(identities):
        return False, (
            f"{len(identities)} worker(s) polling {queue!r}, none from this "
            f"container ({expected_identity_suffix()}): {identities}"
        )
    return True, f"polling {queue!r} as {identities}"


def main() -> int:
    healthy, reason = asyncio.run(check())
    print(("healthy: " if healthy else "unhealthy: ") + reason)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
