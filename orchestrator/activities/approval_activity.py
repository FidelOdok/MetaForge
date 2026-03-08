"""Temporal activity for human-in-the-loop approval gates.

Blocks until a Temporal signal delivers the approval decision via the
activity heartbeat mechanism. In production the workflow sends a signal;
in tests the activity can be resolved directly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from observability.tracing import get_tracer
from orchestrator.activities.base_activity import ApprovalRequest, ApprovalResult

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.activities.approval")

try:
    from temporalio import activity

    HAS_TEMPORAL = True
except ImportError:
    HAS_TEMPORAL = False


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

    When running outside Temporal (e.g. unit tests), the activity returns
    immediately with an auto-approved result.
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
        else:
            # Outside Temporal: auto-approve for testing
            logger.info(
                "approval_activity_auto_approved",
                approval_id=request.approval_id,
                reason="no_temporal_runtime",
            )
            return ApprovalResult(
                approved=True,
                approver_id="auto",
                comment="Auto-approved (no Temporal runtime)",
                timestamp=datetime.now(UTC).isoformat(),
            )
