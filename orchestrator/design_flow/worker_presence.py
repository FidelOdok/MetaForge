"""Is anything polling the design-flow task queue? (FORGE-475)

A run started while no worker polls ``metaforge-design-flows`` is accepted by
Temporal and then sits queued forever, and every state query against it times
out. From the outside that reads as "slow", not "nobody is there", so the
gateway asks before it accepts a run.

Presence means *some* worker is polling. ``orchestrator.worker_healthcheck``
answers a different question (is *this* container polling), which is why it
is not reused as is: it matches on the local hostname. The describe call and
the queue name are shared.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from orchestrator.design_flow.temporal_flow import TASK_QUEUE

logger = structlog.get_logger(__name__)

__all__ = [
    "DEFAULT_CACHE_SECONDS",
    "WorkerPresence",
    "WorkerPresenceProbe",
    "describe_pollers",
]

#: Long enough that a burst of run starts costs one describe call, short
#: enough that a worker coming up is noticed within a retry or two.
DEFAULT_CACHE_SECONDS = 10.0

#: Returns the identities of the workers currently polling the queue.
WorkerPresenceProbe = Callable[[], Awaitable[list[str]]]


async def describe_pollers(client: Any, task_queue: str = TASK_QUEUE) -> list[str]:
    """Identities polling ``task_queue``'s workflow tasks, via DescribeTaskQueue."""
    from temporalio.api.enums.v1 import TaskQueueType
    from temporalio.api.taskqueue.v1 import TaskQueue
    from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

    response = await client.workflow_service.describe_task_queue(
        DescribeTaskQueueRequest(
            namespace=getattr(client, "namespace", "default"),
            task_queue=TaskQueue(name=task_queue),
            task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
        )
    )
    return [poller.identity for poller in response.pollers]


@dataclass
class WorkerPresence:
    """Cached answer to "is a design-flow worker polling?"."""

    probe: WorkerPresenceProbe
    task_queue: str = TASK_QUEUE
    cache_seconds: float = DEFAULT_CACHE_SECONDS
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _at: float | None = field(default=None, init=False, repr=False)
    _value: tuple[bool, str, list[str]] = field(default=(False, "", []), init=False, repr=False)

    async def check(self) -> tuple[bool, str, list[str]]:
        """``(present, reason, identities)``. Never raises.

        A probe that fails is reported as absent with the error as the
        reason: "could not tell" must not be read as "someone is there".
        """
        now = self._clock()
        if self._at is not None and now - self._at < self.cache_seconds:
            return self._value
        try:
            identities = await self.probe()
        except Exception as exc:  # noqa: BLE001 - a presence check reports, it does not raise
            logger.warning("design_flow_worker_probe_failed", error=str(exc))
            value: tuple[bool, str, list[str]] = (
                False,
                f"could not determine whether a worker polls {self.task_queue!r}: {exc}",
                [],
            )
        else:
            if identities:
                value = (
                    True,
                    f"{len(identities)} worker(s) polling {self.task_queue!r}",
                    identities,
                )
            else:
                value = (False, f"no worker is polling {self.task_queue!r}", [])
        self._at, self._value = now, value
        return value

    def invalidate(self) -> None:
        self._at = None
