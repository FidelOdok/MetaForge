"""Running a phase on the connected client instead of a model (FORGE-581).

In client mode a phase is not an agent loop on the server. It is posted as a
task with the brief a phase brain would have been given, and the phase
"runs" until a client submits it. Both engines use this:

* the Temporal worker, through :class:`HttpTaskChannel` (the store is the
  gateway's, and the worker is another process);
* the in-process engine, through :class:`ClientPhaseBrain` over
  :class:`LocalTaskChannel`.

Waiting is polling with a short interval. The activity heartbeats around it
(``DesignFlowActivities.run_phase``), so a dead worker is still told apart
from a client that is taking its time, and the activity's own timeout is the
limit on how long a client may take.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
import structlog
from temporalio.exceptions import ApplicationError

from observability.tracing import get_tracer
from orchestrator.design_flow.client_tasks import (
    ClientTaskStore,
    PhaseTask,
    TaskStatus,
    task_id_for,
)
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.grounding import phase_status
from orchestrator.design_flow.spec import Phase
from orchestrator.design_flow.temporal_flow import PhaseRequest, PhaseResult

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.runs.client_phase")

__all__ = [
    "CLIENT_POLL_ENV",
    "ClientPhaseBrain",
    "ClientTaskCancelledError",
    "HttpTaskChannel",
    "LocalTaskChannel",
    "TaskChannel",
    "brief_for",
    "run_client_phase",
]

#: Seconds between checks for a submission. Small: the client is waiting on
#: nothing but the gate that follows.
CLIENT_POLL_ENV = "METAFORGE_CLIENT_TASK_POLL_SECONDS"
_DEFAULT_POLL = 5.0

#: Said in every brief, so the client knows what a submission means.
SUBMIT_GUIDANCE = (
    "Do this phase's work with MetaForge tools, recording each required deliverable "
    "in the twin under this project (pass the slot's item_key when you record one). "
    "Then call phase.submit with a short summary of what you did and the ids of what "
    "you recorded. The gate that follows checks the twin, not the summary: a "
    "deliverable you describe but did not record fails the gate."
)


class ClientTaskCancelledError(RuntimeError):
    """The task was withdrawn while the phase waited on it."""


class TaskChannel(Protocol):
    async def open(self, task: PhaseTask) -> PhaseTask: ...

    async def get(self, task_id: str) -> PhaseTask: ...

    async def cancel(self, task_id: str, reason: str) -> None: ...


class LocalTaskChannel:
    """The store in this process (the gateway, or tests)."""

    def __init__(self, store: ClientTaskStore | Callable[[], ClientTaskStore]) -> None:
        self._store = store

    def _resolve(self) -> ClientTaskStore:
        return self._store() if callable(self._store) else self._store

    async def open(self, task: PhaseTask) -> PhaseTask:
        return self._resolve().open_task(task)

    async def get(self, task_id: str) -> PhaseTask:
        return self._resolve().get(task_id)

    async def cancel(self, task_id: str, reason: str) -> None:
        self._resolve().cancel(task_id, reason)


class HttpTaskChannel:
    """The gateway's store over ``/v1/client-tasks`` (the Temporal worker)."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._base = base_url.rstrip("/") + "/v1/client-tasks"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client
        self._timeout = timeout

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = self._base + path
        if self._client is not None:
            return await self._client.request(
                method, url, headers=self._headers, timeout=self._timeout, **kwargs
            )
        async with httpx.AsyncClient() as client:
            return await client.request(
                method, url, headers=self._headers, timeout=self._timeout, **kwargs
            )

    async def open(self, task: PhaseTask) -> PhaseTask:
        resp = await self._request(
            "POST",
            "",
            json={
                "runId": task.run_id,
                "phaseId": task.phase_id,
                "attempt": task.attempt,
                "projectId": task.project_id,
                "brief": task.brief,
            },
        )
        if resp.status_code in (404, 409, 422):
            # The run is not a client-mode run the gateway knows: no amount of
            # retrying changes that.
            raise ApplicationError(
                f"the gateway refused the client task: {resp.text}",
                type="ClientTaskRefused",
                non_retryable=True,
            )
        resp.raise_for_status()
        return PhaseTask(**resp.json())

    async def get(self, task_id: str) -> PhaseTask:
        resp = await self._request("GET", f"/{task_id}")
        resp.raise_for_status()
        return PhaseTask(**resp.json())

    async def cancel(self, task_id: str, reason: str) -> None:
        resp = await self._request("POST", f"/{task_id}/cancel", json={"reason": reason})
        resp.raise_for_status()


def _poll_seconds() -> float:
    raw = os.environ.get(CLIENT_POLL_ENV, "").strip()
    try:
        return max(float(raw), 0.05) if raw else _DEFAULT_POLL
    except ValueError:
        return _DEFAULT_POLL


def _slot_rows(phase: Any) -> list[dict[str, str]]:
    rows = []
    for slot in getattr(phase, "slots", None) or ():
        get = slot.get if isinstance(slot, dict) else lambda k, s=slot: getattr(s, k, "")
        rows.append(
            {
                "item_type": str(get("item_type") or ""),
                "name": str(get("name") or ""),
                "item_key": str(get("item_key") or ""),
            }
        )
    return rows


def brief_for(
    *,
    goal: str,
    phase: Any,
    flow_context: str = "",
    retry_feedback: str = "",
    prior: list[str] | None = None,
    attempt: int = 1,
    project_id: str | None = None,
) -> dict[str, Any]:
    """What the client is told: what a phase brain would have been told."""
    return {
        "goal": goal,
        "project_id": project_id,
        "title": getattr(phase, "title", ""),
        "objective": getattr(phase, "objective", ""),
        "expected_artifacts": list(getattr(phase, "expected_artifacts", ()) or ()),
        "required_deliverables": list(getattr(phase, "required_deliverables", ()) or ()),
        "enforce_deliverables": bool(getattr(phase, "enforce_deliverables", True)),
        "disciplines": list(getattr(phase, "disciplines", ()) or ()),
        "slots": _slot_rows(phase),
        "gate": getattr(getattr(phase, "gate", None), "name", None),
        "flow_context": flow_context,
        "retry_feedback": retry_feedback,
        "prior_phases": list(prior or []),
        "attempt": attempt,
        "how_to_submit": SUBMIT_GUIDANCE,
    }


async def run_client_phase(
    channel: TaskChannel,
    task: PhaseTask,
    *,
    poll_seconds: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[str, list[str]]:
    """Post ``task`` and wait for the client's submission: ``(summary, artifacts)``.

    A gateway that cannot be reached is waited out, not a failure: the
    activity's heartbeat and timeout bound the wait. A task withdrawn by
    somebody else raises :class:`ClientTaskCancelledError`. If this wait is
    cancelled (the worker is stopping), the task is withdrawn on the way out
    so no client starts work nobody will read; a retried activity reopens it.
    """
    interval = _poll_seconds() if poll_seconds is None else poll_seconds
    with tracer.start_as_current_span("client_phase.wait") as span:
        span.set_attribute("task.id", task.id)
        opened = await channel.open(task)
        logger.info("client_phase_posted", task_id=task.id, run_id=task.run_id, phase=task.phase_id)
        current = opened
        try:
            while True:
                if current.status == TaskStatus.SUBMITTED.value:
                    span.set_attribute("task.submitted_by", current.submitted_by or "")
                    logger.info(
                        "client_phase_submitted",
                        task_id=task.id,
                        submitted_by=current.submitted_by,
                    )
                    return current.summary, list(current.artifacts)
                if current.status == TaskStatus.CANCELLED.value:
                    raise ClientTaskCancelledError(
                        f"client task '{task.id}' was cancelled"
                        + (f": {current.cancel_reason}" if current.cancel_reason else "")
                    )
                await sleep(interval)
                try:
                    current = await channel.get(task.id)
                except (httpx.HTTPError, OSError) as exc:
                    logger.warning("client_phase_poll_failed", task_id=task.id, error=str(exc))
        except asyncio.CancelledError:
            try:
                await channel.cancel(task.id, "the phase stopped waiting (worker stopping)")
            except Exception as exc:  # noqa: BLE001 - best effort on the way out
                logger.warning("client_phase_cancel_failed", task_id=task.id, error=str(exc))
            raise


def task_from_request(request: PhaseRequest) -> PhaseTask:
    return PhaseTask(
        id=task_id_for(request.run_id, request.phase.id, request.attempt),
        run_id=request.run_id,
        phase_id=request.phase.id,
        attempt=request.attempt,
        project_id=request.project_id,
        brief=brief_for(
            goal=request.goal,
            phase=request.phase,
            flow_context=request.flow_context,
            retry_feedback=request.retry_feedback,
            prior=request.prior,
            attempt=request.attempt,
            project_id=request.project_id,
        ),
    )


async def run_client_phase_request(channel: TaskChannel, request: PhaseRequest) -> PhaseResult:
    """The worker's phase runner in client mode."""
    try:
        summary, artifacts = await run_client_phase(channel, task_from_request(request))
    except ClientTaskCancelledError as exc:
        raise ApplicationError(str(exc), type="ClientTaskCancelled", non_retryable=True) from exc
    return PhaseResult(
        summary=summary, artifacts=artifacts, status=phase_status(summary, "completed")
    )


class ClientPhaseBrain:
    """A phase brain that hands every phase to the client (the in-process engine)."""

    def __init__(self, run_id: str, channel: TaskChannel) -> None:
        self._run_id = run_id
        self._channel = channel

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        task = PhaseTask(
            id=task_id_for(self._run_id, phase.id, context.attempt),
            run_id=self._run_id,
            phase_id=phase.id,
            attempt=context.attempt,
            project_id=context.project_id,
            brief=brief_for(
                goal=goal,
                phase=phase,
                flow_context=context.flow_context,
                retry_feedback=context.retry_feedback,
                prior=[outcome.summary for _, outcome in context.completed],
                attempt=context.attempt,
                project_id=context.project_id,
            ),
        )
        summary, artifacts = await run_client_phase(self._channel, task)
        return PhaseOutcome(summary=summary, artifacts=artifacts, status="completed")
