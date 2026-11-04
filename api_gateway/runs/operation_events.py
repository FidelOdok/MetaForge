"""Runs as operations on the project stream (FORGE-785).

A run is one operation, ``run:<run_id>``; a design flow's phases are its
stages. Only real transitions are reported, as the run store and the run's
tool events make them:

* the run store creating a run: ``accepted`` then ``queued``;
* ``queued -> running``: ``started``; ``awaiting_approval -> running``:
  ``resumed``;
* ``awaiting_approval``: ``review_required`` with the approval to act on;
* the first tool call of a phase: ``milestone`` with that phase as the
  stage, now running;
* the end: ``completed``, ``failed`` or ``cancelled``;
* a run refused before it started (and deleted): ``failed``.

The message on each event is written here, never copied from a prompt, a
tool's arguments or a worker's free text. A run with no project yet has
nowhere to report; its events begin when it has one.
"""

from __future__ import annotations

from typing import Any

import structlog

from api_gateway.operations import publisher as operations
from mcp_core.operation_events import domain_for_text
from orchestrator.harness.runs import Run, RunStatus

logger = structlog.get_logger(__name__)

__all__ = ["on_run_discarded", "on_run_tool_event", "on_run_transition", "reset"]

#: The last status reported per run, so a notify without a status change
#: (``annotate``) is not reported twice.
_last_status: dict[str, RunStatus] = {}
#: Phases already reported as started, per run.
_stages: dict[str, set[str]] = {}

#: status -> (type, state, severity, message) for the states with one meaning.
_TERMINAL: dict[RunStatus, tuple[str, str, str, str]] = {
    RunStatus.COMPLETED: ("completed", "completed", "info", "The run finished"),
    RunStatus.FAILED: ("failed", "failed", "error", "The run failed"),
    RunStatus.REJECTED: ("cancelled", "cancelled", "info", "The run was rejected at review"),
    RunStatus.CANCELED: ("cancelled", "cancelled", "info", "The run was cancelled"),
    RunStatus.TIMED_OUT: (
        "failed",
        "failed",
        "error",
        "Nobody answered the review before its deadline",
    ),
}


def reset() -> None:
    """Forget what was reported (tests, and the run store being replaced)."""
    _last_status.clear()
    _stages.clear()


def _operation_id(run_id: str) -> str:
    return f"run:{run_id}"


def _is_design_flow(run: Run) -> bool:
    request = run.request
    return (
        bool(request.get("flow"))
        or bool(request.get("flow_version_id"))
        or request.get("kind") == "design_flow"
    )


def _definition(run: Run) -> Any | None:
    if not _is_design_flow(run):
        return None
    try:
        from api_gateway.runs.routes import _run_definition

        return _run_definition(run)
    except Exception:  # noqa: BLE001 - a title is never worth failing a transition
        return None


def _title_and_domain(run: Run) -> tuple[str, str]:
    if not _is_design_flow(run):
        return "Run", "general"
    definition = _definition(run)
    flow_id = str(run.request.get("flow") or run.request.get("flow_template_id") or "")
    title = str(getattr(definition, "name", "") or "Design flow")
    return title, domain_for_text(flow_id or getattr(definition, "id", ""))


def _common(run: Run) -> dict[str, Any]:
    title, domain = _title_and_domain(run)
    common: dict[str, Any] = {
        "run_id": run.id,
        "kind": "run",
        "title": title[:200],
        "domain": domain,
    }
    retry_of = run.request.get("retry_of")
    if retry_of:
        # FORGE-788: a run started by retrying a failed one names it.
        common["retry_of"] = _operation_id(str(retry_of))[:200]
    return common


def _cancel_action(run: Run) -> list[dict[str, str]]:
    # FORGE-788: a design flow is cancellable too (its workflow is stopped).
    if run.is_terminal:
        return []
    return [{"kind": "cancel", "id": _operation_id(run.id), "label": "Cancel"}]


def _retry_action(run: Run) -> list[dict[str, str]]:
    """``retry`` only where re-running is safe: a failed design flow (FORGE-788)."""
    try:
        from api_gateway.operations.scheduling import run_retryable

        if run_retryable(run):
            return [{"kind": "retry", "id": _operation_id(run.id), "label": "Retry"}]
    except Exception:  # noqa: BLE001 - an action is never worth failing a transition
        pass
    return []


def _approval_action(run: Run) -> list[dict[str, str]]:
    approval_id = f"gate:{run.id}" if _is_design_flow(run) else f"run:{run.id}"
    return [{"kind": "approval", "id": approval_id, "label": "Review and decide"}]


def _known(project_id: str, run_id: str) -> bool:
    try:
        store = operations.get_operation_publisher().store
        return store.get(project_id, _operation_id(run_id)) is not None
    except Exception:  # noqa: BLE001
        return False


def on_run_transition(run: Run) -> None:
    """Report a run store notification as operation events. Never raises."""
    try:
        _report(run)
    except Exception as exc:  # noqa: BLE001 - a status line never breaks a transition
        logger.warning("run_operation_event_failed", run_id=run.id, error=str(exc))


def _report(run: Run) -> None:
    project_id = run.request.get("project_id")
    if not project_id:
        return
    project_id = str(project_id)
    if _last_status.get(run.id) is run.status:
        return
    first = run.id not in _last_status
    _last_status[run.id] = run.status
    op = _operation_id(run.id)
    common = _common(run)
    if first and not _known(project_id, run.id):
        operations.publish(
            project_id,
            op,
            "accepted",
            "queued",
            message="Request accepted",
            action_refs=_cancel_action(run),
            **common,
        )
    status = run.status
    if status is RunStatus.QUEUED:
        operations.publish(
            project_id,
            op,
            "queued",
            "queued",
            message="Waiting to start",
            action_refs=_cancel_action(run),
            **common,
        )
    elif status is RunStatus.RUNNING:
        resumed = len(run.history) >= 2 and run.history[-2] is RunStatus.AWAITING_APPROVAL
        operations.publish(
            project_id,
            op,
            "resumed" if resumed else "started",
            "running",
            message="Continuing after the decision" if resumed else "Working",
            action_refs=_cancel_action(run),
            **common,
        )
    elif status is RunStatus.AWAITING_APPROVAL:
        operations.publish(
            project_id,
            op,
            "review_required",
            "waiting_for_user",
            message="Waiting for your decision before the run continues",
            severity="warning",
            requires_action=True,
            action_refs=_approval_action(run),
            **common,
        )
    elif status in _TERMINAL:
        event_type, state, severity, message = _TERMINAL[status]
        operations.publish(
            project_id,
            op,
            event_type,
            state,
            message=message,
            severity=severity,
            action_refs=_retry_action(run),
            **common,
        )
    if run.is_terminal:
        _stages.pop(run.id, None)


def on_run_discarded(run: Run, reason: str = "") -> None:
    """A run that was created and then refused before it started. Never raises."""
    try:
        project_id = run.request.get("project_id")
        if not project_id or run.id not in _last_status:
            return
        _last_status.pop(run.id, None)
        _stages.pop(run.id, None)
        operations.publish(
            str(project_id),
            _operation_id(run.id),
            "failed",
            "failed",
            message="The run could not be started",
            severity="error",
            **_common(run),
        )
        logger.info("run_operation_discarded", run_id=run.id, reason=reason[:200])
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_operation_event_failed", run_id=run.id, error=str(exc))


def on_run_tool_event(run: Run, *, phase: str, tool: str) -> None:
    """Report a phase's first tool call as that stage starting. Never raises."""
    try:
        project_id = run.request.get("project_id")
        if not project_id or not phase or run.is_terminal:
            return
        seen = _stages.setdefault(run.id, set())
        if phase in seen:
            return
        seen.add(phase)
        project_id = str(project_id)
        op = _operation_id(run.id)
        try:
            snapshot = operations.get_operation_publisher().store.get(project_id, op)
        except Exception:  # noqa: BLE001
            snapshot = None
        if snapshot is not None and any(s.stage_id == phase for s in snapshot.stages):
            return  # reported before this process restarted
        stage_title, stage_domain = _stage(run, phase, tool)
        common = _common(run)
        common["domain"] = stage_domain
        operations.publish(
            project_id,
            op,
            "milestone",
            "running",
            stage_id=phase[:200],
            stage_title=stage_title[:200],
            message=f"Working on {stage_title}"[:500],
            action_refs=_cancel_action(run),
            **common,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_operation_event_failed", run_id=run.id, error=str(exc))


def _stage(run: Run, phase_id: str, tool: str) -> tuple[str, str]:
    """The phase's title and domain: from its definition, else its id and the tool."""
    from mcp_core.operation_events import domain_for_tool

    definition = _definition(run)
    for phase in getattr(definition, "phases", ()) or ():
        if getattr(phase, "id", None) == phase_id:
            disciplines = tuple(getattr(phase, "disciplines", ()) or ())
            domain = domain_for_text(disciplines[0]) if disciplines else "general"
            if domain == "general":
                domain = domain_for_text(phase_id)
            if domain == "general":
                domain = domain_for_tool(tool)
            return str(getattr(phase, "title", "") or phase_id), domain
    domain = domain_for_text(phase_id)
    return phase_id, domain if domain != "general" else domain_for_tool(tool)
