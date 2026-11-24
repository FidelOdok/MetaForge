"""With approvals off, nothing waits for a person (METAFORGE_APPROVALS=off).

The switch already lets an ordinary tool call run at once (``mcp_core.guardrails``).
Everything else that reaches ``/v1/approvals`` used to keep waiting: steps that record
a person's decision, release steps, design-flow gates and flow proposals, design-change
proposals, sketch and drawing approvals, design-loop winners, re-checks and uploads.

Rather than teach each of those subsystems the switch, the gateway sweeps the approvals
list while the switch is off and approves every pending item through that item's own
decide route (:func:`api_gateway.approvals.service.decide`), so each kind's real effect
happens: the gate continues, the proposal is applied, the upload is imported, the held
call runs. The record names the switch as the approver, never a person.

Two things still wait, by design:

* an item that does not offer ``approve``. A gate offers it only when its own checks
  pass, so a failing gate (a violated constraint, a missing deliverable) stays a
  failure for someone to fix, not something the switch overrides;
* an order (:data:`mcp_core.guardrails.SPENDING_TOOLS`): it spends money outside
  MetaForge and cannot be taken back.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

from mcp_core.guardrails import SPENDING_TOOLS, Approver, approvals_off
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.approvals.approvals_off")

#: Who an automatic decision is recorded as: the owner's switch, not a person.
APPROVER = Approver(
    actor_id="system:approvals-off",
    verified=False,
    display_name="Approvals off (METAFORGE_APPROVALS=off)",
)
REASON = "Approved automatically: approvals are off on this MetaForge (METAFORGE_APPROVALS=off)."

#: Seconds between sweeps. A held call waits at most about this long.
SWEEP_ENV = "METAFORGE_APPROVALS_OFF_SWEEP_S"
_DEFAULT_SWEEP_S = 2.0

_collector: Any = None


def _count(outcome: str, kind: str) -> None:
    global _collector  # noqa: PLW0603
    try:
        if _collector is None:
            from observability.metrics import collector_for

            _collector = collector_for("metaforge-gateway")
        _collector.record_approval_decision(kind, "approve", "approvals_off", outcome)
    except Exception:  # noqa: BLE001 - a metric never fails a sweep
        pass


def why_it_waits(item: Any) -> str | None:
    """Why ``item`` still waits with approvals off, or ``None`` when it is approved."""
    if item.status != "pending":
        return "not pending"
    if "approve" not in (item.allowed_decisions or []) or not item.decidable:
        return (
            "it cannot be approved as it stands (a gate whose checks fail, or a workflow "
            "that no longer exists)"
        )
    tool = str((item.detail or {}).get("tool") or "")
    if tool in SPENDING_TOOLS:
        return f"{tool} spends money outside MetaForge and cannot be taken back"
    return None


async def sweep_once(app: Any) -> list[str]:
    """Approve every pending item the switch covers; returns the ids approved."""
    if not approvals_off():
        return []
    from fastapi import HTTPException

    from api_gateway.approvals import service

    approved: list[str] = []
    with tracer.start_as_current_span("approvals_off.sweep") as span:
        for item in await service.list_items(app):
            if why_it_waits(item) is not None:
                continue
            try:
                await service.decide(
                    app,
                    item.id,
                    decision="approve",
                    reason=REASON,
                    to_phase="",
                    approver=APPROVER,
                    surface="unknown",
                    on_behalf_of=None,
                )
            except HTTPException as exc:
                # Decided meanwhile, moved on, or refused by its own rules: next sweep.
                logger.info(
                    "approvals_off_not_approved",
                    approval_id=item.id,
                    status=exc.status_code,
                    detail=str(exc.detail)[:300],
                )
                _count("refused", item.kind)
                continue
            except Exception as exc:  # noqa: BLE001 - one item must not stop the sweep
                span.record_exception(exc)
                logger.warning("approvals_off_failed", approval_id=item.id, error=str(exc))
                _count("failed", item.kind)
                continue
            approved.append(item.id)
            _count("approved", item.kind)
            logger.info("approvals_off_approved", approval_id=item.id, kind=item.kind)
        span.set_attribute("approvals_off.approved", len(approved))
    return approved


def _interval() -> float:
    try:
        return max(0.5, float(os.environ.get(SWEEP_ENV, _DEFAULT_SWEEP_S)))
    except ValueError:
        return _DEFAULT_SWEEP_S


async def run_sweep_loop(app: Any) -> None:
    """Sweep for the gateway's life while the switch is off; never raises."""
    while True:
        try:
            await sweep_once(app)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the loop outlives any one failure
            logger.warning("approvals_off_sweep_failed", error=str(exc))
        await asyncio.sleep(_interval())
