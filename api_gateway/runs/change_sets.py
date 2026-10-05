"""A design-flow run's change set at its gates (FORGE-525).

Every definition a run writes is a draft in the run's change set
(``twin_core.items.change_sets``). This module is where the gate decision
reaches it, for both engines, because both answer gates through
``routes.decide_run_gate``:

* **approve**: :func:`commit_for_approval` moves the heads before the run
  record moves. A conflict (spec section 41) raises
  ``ChangeSetConflictError``, the route answers 409 with its rebase message,
  and the run stays parked at its gate. The message is kept, so a retry of
  that gate hands it to the phase (:func:`retry_note`).
* **reject, retry, rework**: :func:`close_for_decision` closes the drafts
  (``rejected`` / ``abandoned``) with the reviewer's reason. HEAD never moves.
* **the run ends any other way** (:func:`on_run_transition`, from the run
  store's observer): completed commits what is left (phases with no human
  gate after the last one), failed or canceled abandons it, and a rejection
  nobody answered (a gate timeout) rejects it.

Best-effort where a human is not waiting on it: the terminal hooks log and
count, they never break a transition.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

from observability.tracing import get_tracer
from orchestrator.harness.runs import ApprovalDecision, Run, RunStatus

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.runs.change_sets")

#: Runs whose closing is being done by ``decide_run_gate`` itself, so the
#: transition observer does not close them a second time with a vaguer reason.
_deciding: set[str] = set()
#: The last refused approval per run: the rebase note a retry passes on.
_last_conflict: dict[str, str] = {}
#: Background terminal-hook tasks, kept referenced so they are not GC'd.
_tasks: set[asyncio.Task[None]] = set()


@contextmanager
def phase_scope(run_id: str, phase_id: str, project_id: str | None) -> Iterator[object]:
    """The in-process executor's phase scope: the MCP call context of a phase.

    The Temporal worker sets the same fields (``flow_worker._phase_scope``);
    this is the in-process engine's copy, layered over whatever context the
    gateway already has so project and actor are kept.
    """
    from mcp_core.context import current_context, with_context

    base = current_context()
    update: dict[str, Any] = {"run_id": run_id, "phase": phase_id}
    if base.project_id is None and project_id:
        try:
            update["project_id"] = uuid.UUID(str(project_id))
        except ValueError:
            pass
    with with_context(base.model_copy(update=update)) as ctx:
        yield ctx


def _twin() -> Any:
    from api_gateway.twin.routes import get_twin

    return get_twin()


def _enabled(twin: Any) -> bool:
    from twin_core.items import supports_items

    return supports_items(twin)


def _project(run: Run) -> str | None:
    """The run's project as a UUID string, or ``None`` (then the run id alone scopes)."""
    pid = run.request.get("project_id")
    if not pid:
        return None
    try:
        return str(uuid.UUID(str(pid)))
    except ValueError:
        return None


async def commit_for_approval(
    run: Run, *, gate: str | None, decided_by: str, reason: str
) -> dict[str, Any] | None:
    """Commit the run's drafts for an approval. Raises ``ChangeSetConflictError``.

    Returns a summary (``items``: the refs that became current) or ``None``
    when there is no twin to commit into.
    """
    from twin_core.items import ChangeSetConflictError, commit_change_set

    twin = _twin()
    if not _enabled(twin):
        return None
    note = (reason or "").strip() or (run.approval_reason or "").strip()
    change_reason = f"Approved at gate '{gate or 'gate'}' by {decided_by or 'a reviewer'}" + (
        f": {note}" if note else ""
    )
    try:
        result = await commit_change_set(
            twin,
            run.id,
            project_id=_project(run),
            gate=gate,
            decided_by=decided_by,
            reason=change_reason,
        )
    except ChangeSetConflictError as exc:
        _last_conflict[run.id] = str(exc)
        raise
    _last_conflict.pop(run.id, None)
    return {"items": result.refs, "outcome": result.outcome}


def retry_note(run_id: str, reason: str) -> str:
    """The reviewer's retry reason, plus the refused approval's rebase message if any."""
    conflict = _last_conflict.pop(run_id, None)
    if not conflict:
        return reason
    return (f"{reason.strip()} | " if reason.strip() else "") + conflict


def begin_decision(run_id: str) -> None:
    _deciding.add(run_id)


def end_decision(run_id: str) -> None:
    _deciding.discard(run_id)


async def close_for_decision(run: Run, decision: ApprovalDecision, reason: str) -> None:
    """Close the run's drafts after a reject, retry or rework. Never raises."""
    status = "rejected" if decision is ApprovalDecision.REJECT else "abandoned"
    why = {
        ApprovalDecision.REJECT: "gate rejected",
        ApprovalDecision.RETRY: "phase retried",
        ApprovalDecision.REWORK: "run sent back for rework",
    }.get(decision, decision.value)
    await _close(run, status, f"{why}: {reason}" if reason.strip() else why)


async def _close(run: Run, status: str, reason: str) -> None:
    from twin_core.items import close_change_set

    twin = _twin()
    if not _enabled(twin):
        return
    try:
        await close_change_set(twin, run.id, status=status, reason=reason, project_id=_project(run))
    except Exception as exc:  # noqa: BLE001 - closing drafts must not break a decision
        logger.error("run_change_set_close_failed", run_id=run.id, status=status, error=str(exc))
    if status == "rejected" or run.is_terminal:
        _last_conflict.pop(run.id, None)


async def _settle_terminal(run: Run) -> None:
    """What a run's change set becomes when the run ends outside a gate decision."""
    from twin_core.items import ChangeSetCommitError, ChangeSetConflictError, commit_change_set

    with tracer.start_as_current_span("run.change_set.settle") as span:
        span.set_attribute("run.id", run.id)
        span.set_attribute("run.status", run.status.value)
        if run.status is RunStatus.COMPLETED:
            twin = _twin()
            if not _enabled(twin):
                return
            try:
                await commit_change_set(
                    twin,
                    run.id,
                    project_id=_project(run),
                    gate="run completed",
                    decided_by="",
                    reason="Committed when the run completed: phases after its last human gate",
                )
            except ChangeSetConflictError as exc:
                # Nobody is left to retry: close the drafts, they stay in history.
                await _close(run, "abandoned", str(exc))
            except ChangeSetCommitError as exc:
                logger.error("run_change_set_settle_failed", run_id=run.id, error=str(exc))
            return
        if run.status is RunStatus.REJECTED:
            await _close(run, "rejected", run.error or "run rejected")
        else:
            await _close(run, "abandoned", f"run {run.status.value}: {run.error or ''}".strip())


def on_run_transition(run: Run) -> None:
    """Run-store observer: settle the change set of a design-flow run that just ended."""
    if not run.is_terminal or run.id in _deciding:
        return
    req = run.request
    if not (req.get("flow") or req.get("flow_version_id") or req.get("kind") == "design_flow"):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("run_change_set_settle_skipped", run_id=run.id, reason="no event loop")
        return
    task = loop.create_task(_settle_terminal(run))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def reset() -> None:
    """Test hook."""
    _deciding.clear()
    _last_conflict.clear()
