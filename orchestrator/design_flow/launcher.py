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

from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from observability.tracing import get_tracer
from orchestrator.design_flow.frozen import FrozenFlow
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
    "WorkflowHandleLike",
    "connect_temporal",
    "workflow_id_for",
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


@dataclass
class DesignFlowLauncher:
    """Starts runs and relays human decisions into them."""

    client: ClientLike
    task_queue: str = TASK_QUEUE

    async def start(
        self,
        *,
        run_id: str,
        goal: str,
        flow: FrozenFlow,
        project_id: str | None = None,
        session_id: str | None = None,
        gate_timeout_seconds: float | None = None,
    ) -> str:
        """Start a run. Returns the workflow id."""
        flow.verify()
        payload = DesignFlowInput(
            run_id=run_id,
            goal=goal,
            flow=flow,
            project_id=project_id,
            session_id=session_id,
        )
        if gate_timeout_seconds is not None:
            payload.gate_timeout_seconds = gate_timeout_seconds
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
        self, run_id: str, *, approved: bool, decided_by: str, comment: str = ""
    ) -> None:
        """Relay a human's gate decision into the waiting run.

        ``decided_by`` is the approver from the approval record (FORGE-393),
        not a name supplied by whatever is calling this.
        """
        handle = self.client.get_workflow_handle(workflow_id_for(run_id))
        await handle.signal(
            "submit_gate_decision",
            GateAnswer(approved=approved, decided_by=decided_by, comment=comment),
        )
        logger.info(
            "design_flow_gate_answered",
            run_id=run_id,
            approved=approved,
            decided_by=decided_by,
        )

    async def request_change(
        self, run_id: str, *, flow: FrozenFlow, requested_by: str, rationale: str = ""
    ) -> None:
        """Queue a flow change; the run applies it at its next gate boundary."""
        flow.verify()
        handle = self.client.get_workflow_handle(workflow_id_for(run_id))
        await handle.signal(
            "request_change",
            ChangeRequest(flow=flow, requested_by=requested_by, rationale=rationale),
        )
        logger.info("design_flow_change_requested", run_id=run_id, requested_by=requested_by)

    async def state(self, run_id: str) -> dict[str, Any]:
        """Current state, read from the workflow rather than a cache of it."""
        handle = self.client.get_workflow_handle(workflow_id_for(run_id))
        result: dict[str, Any] = await handle.query("state")
        return result

    async def events(self, run_id: str) -> list[dict[str, Any]]:
        handle = self.client.get_workflow_handle(workflow_id_for(run_id))
        result: list[dict[str, Any]] = await handle.query("events")
        return result
