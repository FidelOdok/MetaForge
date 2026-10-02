"""Temporal activity for human-in-the-loop approval gates.

Blocks until a Temporal signal delivers the approval decision via the
activity heartbeat mechanism. In production the workflow sends a signal.

Without a Temporal runtime there is nothing to wait on, so the activity
fails closed (FORGE-469): it raises :class:`ApprovalRuntimeUnavailableError`
and never approves. It used to auto-approve here, which meant a missing
``temporalio`` install silently waved every human gate through. Tests that
need an approved gate inject an explicit test double instead.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from observability.metrics import MetricsCollector, collector_for
from observability.tracing import get_tracer
from orchestrator.activities.base_activity import ApprovalRequest, ApprovalResult

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.activities.approval")

try:
    from temporalio import activity

    HAS_TEMPORAL = True
except ImportError:
    HAS_TEMPORAL = False

_metrics: MetricsCollector | None = None


def _get_metrics() -> MetricsCollector:
    global _metrics
    if _metrics is None:
        _metrics = collector_for("metaforge-orchestrator")
    return _metrics


def set_approval_activity_metrics(metrics: MetricsCollector | None) -> None:
    """Inject a collector (tests), or ``None`` to resolve it lazily again."""
    global _metrics
    _metrics = metrics


class ApprovalRuntimeUnavailableError(RuntimeError):
    """An approval gate was reached with no Temporal runtime to wait on.

    Raised instead of approving: a gate that cannot reach a human must
    stop the run, not pass it.
    """


def _activity_defn(func: Any) -> Any:
    """Apply @activity.defn when the Temporal SDK is available."""
    if HAS_TEMPORAL:
        return activity.defn(func)
    return func


@_activity_defn
async def wait_for_approval(request: ApprovalRequest) -> ApprovalResult:
    """Block until a human approves or rejects the gate.

    In a real Temporal deployment the workflow calls this activity with a
    long start-to-close timeout. The activity heartbeats periodically and
    waits for cancellation (which the workflow triggers after receiving
    the approval signal). The workflow then passes the approval result
    directly.

    Without a Temporal runtime the activity fails closed: it raises
    :class:`ApprovalRuntimeUnavailableError` and never approves (FORGE-469).
    """
    with tracer.start_as_current_span("activity.wait_for_approval") as span:
        span.set_attribute("approval.id", request.approval_id)
        span.set_attribute("approval.run_id", request.run_id)
        span.set_attribute("approval.step_id", request.step_id)
        span.set_attribute("approval.required_role", request.required_role)

        logger.info(
            "approval_activity_waiting",
            approval_id=request.approval_id,
            run_id=request.run_id,
            step_id=request.step_id,
            description=request.description,
        )

        if HAS_TEMPORAL:
            # In Temporal: heartbeat while waiting for cancellation.
            # The parent workflow will cancel this activity once it
            # receives the approval signal, then return the result.
            try:
                while True:
                    activity.heartbeat(f"waiting:{request.approval_id}")
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                logger.info(
                    "approval_activity_cancelled",
                    approval_id=request.approval_id,
                )
                # Return a default result; the workflow overrides this
                # with the actual signal payload.
                return ApprovalResult(
                    approved=False,
                    approver_id="",
                    comment="Activity cancelled by workflow signal",
                    timestamp=datetime.now(UTC).isoformat(),
                )

        # No Temporal runtime: nothing can deliver a human decision, so
        # refuse rather than approve (FORGE-469).
        span.set_attribute("approval.outcome", "no_runtime")
        logger.error(
            "approval_activity_no_runtime",
            approval_id=request.approval_id,
            run_id=request.run_id,
            step_id=request.step_id,
            required_role=request.required_role,
            reason="temporalio not importable",
        )
        _get_metrics().record_approval_gate_no_runtime(request.required_role)
        exc = ApprovalRuntimeUnavailableError(
            f"Approval gate {request.approval_id!r} (step {request.step_id!r}) "
            "cannot be decided: no Temporal runtime is available "
            "(temporalio is not importable). Refusing to approve."
        )
        span.record_exception(exc)
        raise exc
