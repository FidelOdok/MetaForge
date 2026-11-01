"""Starting and steering a design-flow run on Temporal (FORGE-401).

The one rule this module exists to enforce: **if Temporal is not there, say
so and start nothing.**

Falling back to the in-process executor would be the tempting thing to do and
the wrong one. The fallback works — that is the problem. Runs keep starting,
the dashboard keeps filling, and nobody discovers the engine is not durable
until a gateway restart eats a run somebody spent a day on. This codebase has
produced that shape repeatedly: a graceful fallback with no alarm becomes the
permanent state. So a launcher with no Temporal raises, logs at error, and
creates no run record — a run that does not exist is easier to explain than a
run that quietly is not what it claims to be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from observability.tracing import get_tracer
from orchestrator.design_flow.frozen import FrozenFlow
from orchestrator.design_flow.retry import max_phase_retries as max_phase_retries_default
from orchestrator.design_flow.rework import max_rework_cycles as max_rework_cycles_default
from orchestrator.design_flow.temporal_flow import (
    TASK_QUEUE,
    ChangeRequest,
    DesignFlowInput,
    GateAnswer,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.design_flow.launcher")

__all__ = [
    "DesignFlowLauncher",
    "TemporalUnavailableError",
    "WorkflowNotFoundError",
    "WorkflowHandleLike",
    "connect_temporal",
    "workflow_id_for",
    "DesignFlowWorkerUnavailableError",
]


class TemporalUnavailableError(RuntimeError):
    """The workflow engine could not be reached.

    Deliberately not caught anywhere that would let a run start anyway.
    """

    def __init__(self, target: str, detail: str) -> None:
        self.target = target
        super().__init__(
            f"Temporal at '{target}' is unreachable: {detail}. Design-flow runs need a "
            "durable engine (ADR-001, FORGE-401) and no run was created. Start Temporal "
            "(`docker compose up temporal`, or `temporal server start-dev` locally) and "
            "try again. There is no in-process fallback on purpose: a run that is not "
            "durable must not look like one that is."
        )


class WorkflowNotFoundError(RuntimeError):
    """The engine answers, but the run's workflow no longer exists (FORGE-516).

    Distinct from :class:`TemporalUnavailableError`: nothing is down, the
    workflow was purged, reset or never survived a Temporal wipe. The run
    cannot continue, so a decision that would resume it can never be delivered.
    """

    def __init__(self, run_id: str, detail: str = "") -> None:
        self.run_id = run_id
        super().__init__(
            f"the workflow for run '{run_id}' no longer exists" + (f" ({detail})" if detail else "")
        )


def _is_not_found(exc: BaseException) -> bool:
    """True for a Temporal RPC ``NOT_FOUND``; temporalio stays an optional import."""
    try:
        from temporalio.service import RPCError, RPCStatusCode
    except ImportError:  # pragma: no cover - dependency is declared
        return False
    return isinstance(exc, RPCError) and exc.status == RPCStatusCode.NOT_FOUND


def workflow_id_for(run_id: str) -> str:
    """One workflow per run, named from the run so it can be found again."""
    return f"design-flow-{run_id}"


class WorkflowHandleLike(Protocol):
    async def signal(self, name: Any, arg: Any = None) -> None: ...
    async def query(self, name: Any, arg: Any = None) -> Any: ...


class ClientLike(Protocol):
    async def start_workflow(self, *args: Any, **kwargs: Any) -> Any: ...
    def get_workflow_handle(self, workflow_id: str) -> Any: ...


async def connect_temporal(target: str, namespace: str = "default") -> ClientLike:
    """Connect, or raise :class:`TemporalUnavailableError`.

    Import is local so that ``temporalio`` stays an optional dependency for
    anyone running the gateway without design flows.
    """
    try:
        from temporalio.client import Client
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise TemporalUnavailableError(target, f"temporalio is not installed ({exc})") from exc
    try:
        return await Client.connect(target, namespace=namespace)
    except Exception as exc:
        logger.error("temporal_connect_failed", target=target, error=str(exc))
        raise TemporalUnavailableError(target, str(exc)) from exc


class DesignFlowWorkerUnavailableError(RuntimeError):
    """Temporal is up but nothing polls the design-flow queue (FORGE-475).

    Distinct from :class:`TemporalUnavailableError`: the engine answers, so a
    started run would be accepted and then sit queued with no one to run it.
    """

    def __init__(self, task_queue: str, reason: str) -> None:
        self.task_queue = task_queue
        self.reason = reason
        super().__init__(
            f"No design-flow worker is available: {reason}. A run started now would "
            f"sit queued on '{task_queue}' and never advance, so no run was created. "
            "Start the worker (`docker compose up design-flow-worker`) and try again."
        )


@dataclass
class DesignFlowLauncher:
    """Starts runs and relays human decisions into them."""

    client: ClientLike
    task_queue: str = TASK_QUEUE
    _presence: Any = field(default=None, init=False, repr=False)

    async def worker_presence(self) -> tuple[bool, str, list[str]]:
        """``(present, reason, identities)``, cached for a few seconds."""
        from orchestrator.design_flow.worker_presence import WorkerPresence, describe_pollers

        if self._presence is None:
            self._presence = WorkerPresence(
                probe=lambda: describe_pollers(self.client, self.task_queue),
                task_queue=self.task_queue,
            )
        result: tuple[bool, str, list[str]] = await self._presence.check()
        return result

    async def require_worker(self) -> None:
        """Raise :class:`DesignFlowWorkerUnavailableError` unless a worker polls."""
        present, reason, _ = await self.worker_presence()
        if not present:
            raise DesignFlowWorkerUnavailableError(self.task_queue, reason)

    async def start(
        self,
        *,
        run_id: str,
        goal: str,
        flow: FrozenFlow,
        project_id: str | None = None,
        session_id: str | None = None,
        gate_timeout_seconds: float | None = None,
        max_phase_retries: int | None = None,
        max_rework_cycles: int | None = None,
        intelligence: str = "server",
    ) -> str:
        """Start a run. Returns the workflow id."""
        flow.verify()
        payload = DesignFlowInput(
            run_id=run_id,
            goal=goal,
            flow=flow,
            project_id=project_id,
            session_id=session_id,
            intelligence=intelligence,
        )
        if gate_timeout_seconds is not None:
            payload.gate_timeout_seconds = gate_timeout_seconds
        payload.max_phase_retries = (
            max_phase_retries if max_phase_retries is not None else max_phase_retries_default()
        )
        payload.max_rework_cycles = (
            max_rework_cycles if max_rework_cycles is not None else max_rework_cycles_default()
        )
        workflow_id = workflow_id_for(run_id)
        with tracer.start_as_current_span("design_flow.start") as span:
            span.set_attribute("run.id", run_id)
            span.set_attribute("flow.template_id", flow.template_id)
            span.set_attribute("flow.content_hash", flow.content_hash)
            try:
                await self.client.start_workflow(
                    "DesignFlow",
                    payload,
                    id=workflow_id,
                    task_queue=self.task_queue,
                )
            except Exception as exc:
                span.record_exception(exc)
                logger.error("design_flow_start_failed", run_id=run_id, error=str(exc))
                raise
        logger.info(
            "design_flow_started",
            run_id=run_id,
            workflow_id=workflow_id,
            flow=flow.template_id,
            version=flow.version,
            content_hash=flow.content_hash[:12],
        )
        return workflow_id

    async def answer_gate(
        self,
        run_id: str,
        *,
        approved: bool,
        decided_by: str,
        comment: str = "",
        retry: bool = False,
        rework_to: str = "",
    ) -> None:
        """Relay a human's gate decision into the waiting run.

        ``decided_by`` is the approver from the approval record (FORGE-393),
        not a name supplied by whatever is calling this.
        """
        handle = self.client.get_workflow_handle(workflow_id_for(run_id))
        try:
            await handle.signal(
                "submit_gate_decision",
                GateAnswer(
                    approved=approved,
                    decided_by=decided_by,
                    comment=comment,
                    retry=retry,
                    rework_to=rework_to,
                ),
            )
        except Exception as exc:
            if _is_not_found(exc):
                raise WorkflowNotFoundError(run_id, str(exc)) from exc
            raise
        logger.info(
            "design_flow_gate_answered",
            run_id=run_id,
            approved=approved,
            retry=retry,
            rework_to=rework_to,
            decided_by=decided_by,
        )

    async def request_change(
        self,
        run_id: str,
        *,
        flow: FrozenFlow,
        requested_by: str,
        rationale: str = "",
        rerun: list[str] | None = None,
    ) -> None:
        """Queue a flow change; the run applies it at its next gate boundary.

        ``rerun`` (FORGE-539) names the phases an approved patch re-runs; the
        rest keep their results. Omitted, every completed phase is kept.
        """
        flow.verify()
        handle = self.client.get_workflow_handle(workflow_id_for(run_id))
        await handle.signal(
            "request_change",
            ChangeRequest(
                flow=flow,
                requested_by=requested_by,
                rationale=rationale,
                rerun=list(rerun or []),
            ),
        )
        logger.info(
            "design_flow_change_requested",
            run_id=run_id,
            requested_by=requested_by,
            rerun=list(rerun or []),
        )

    async def state(self, run_id: str) -> dict[str, Any]:
        """Current state, read from the workflow rather than a cache of it."""
        handle = self.client.get_workflow_handle(workflow_id_for(run_id))
        try:
            result: dict[str, Any] = await handle.query("state")
        except Exception as exc:
            if _is_not_found(exc):
                raise WorkflowNotFoundError(run_id, str(exc)) from exc
            raise
        return result

    async def events(self, run_id: str) -> list[dict[str, Any]]:
        handle = self.client.get_workflow_handle(workflow_id_for(run_id))
        result: list[dict[str, Any]] = await handle.query("events")
        return result
