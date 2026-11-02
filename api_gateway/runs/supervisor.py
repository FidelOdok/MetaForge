"""The run supervisor: drive a design run to a verified result (FORGE-734..739).

Like a ``/loop`` over a run. On every tick it reads the supervised run and acts:

* **Running**: nothing; it watches.
* **At a gate**: the gate is reviewed and, where the project's approval policy
  allows, decided by the reviewer (FORGE-726). The supervisor never decides a
  gate itself. When a person has to, it says so and waits. When the gate keeps
  failing in a way the agent cannot fix (FORGE-736), it stops and reports it
  rather than spending retries on it.
* **Failed**: it diagnoses the failure (FORGE-736). A design failure restarts
  the run as a new, linked run whose brief says what the last attempt lacked;
  a tooling failure restarts after a pause; a platform failure (a check or
  capability that cannot be satisfied) or a decision that needs a person stops
  it, with the reason (FORGE-737).
* **Completed**: it reads the completion verdict. ``COMPLETED_VERIFIED`` ends
  the supervision; anything else restarts with the unmet requirements in the
  brief (FORGE-738).
* **Rejected or cancelled by a person**: it stops.

Budgets bound it (FORGE-735): actions taken, restarts, model calls across the
chain of runs, and wall time. The record lives on the run
(``request["supervision"]``), so a gateway restart resumes it, and a restart
hands it to the new run with the chain of runs it came from.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.runs.supervisor")

__all__ = [
    "DEFAULT_BUDGETS",
    "SUPERVISION_KEY",
    "diagnose",
    "run_supervisor_loop",
    "start_supervision",
    "stop_supervision",
    "supervise_tick",
    "supervision_of",
]

SUPERVISION_KEY = "supervision"
SUPERVISOR_INTERVAL_SECONDS = 30.0
#: A tooling failure is retried after this pause rather than at once.
TOOLING_PAUSE_SECONDS = 120.0
_MAX_LOG = 100

DEFAULT_BUDGETS: dict[str, float] = {
    "max_actions": 20,
    "max_restarts": 3,
    "max_model_calls": 600,
    "max_hours": 12,
}

#: Keys of a run's request that a restart carries over; everything else is
#: per-run state (decisions, reviews, supervision, engine bookkeeping).
_CARRIED = (
    "kind",
    "flow",
    "flow_template_id",
    "flow_version_id",
    "goal",
    "project_id",
    "intelligence",
    "flow_context",
    "flow_facts",
    "caller",
)

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _store() -> Any:
    from api_gateway.runs import routes as run_routes

    return run_routes.get_run_store()


def supervision_of(run: Any) -> dict[str, Any] | None:
    sup = run.request.get(SUPERVISION_KEY)
    return sup if isinstance(sup, dict) else None


def _save(run_id: str, sup: dict[str, Any]) -> None:
    sup["updated_at"] = _now()
    sup["log"] = sup.get("log", [])[-_MAX_LOG:]
    _store().annotate(run_id, SUPERVISION_KEY, sup)


def _log(sup: dict[str, Any], run_id: str, action: str, detail: str) -> None:
    sup.setdefault("log", []).append(
        {"at": _now(), "run_id": run_id, "action": action, "detail": detail[:1000]}
    )
    logger.info("run_supervisor_action", run_id=run_id, action=action, detail=detail[:300])


def start_supervision(
    run_id: str, *, budgets: dict[str, float] | None = None, started_by: str = ""
) -> dict[str, Any]:
    """Begin supervising a run (or update the budgets of one already supervised)."""
    run = _store().get(run_id)
    sup = supervision_of(run) or {
        "root_run": run_id,
        "chain": [run_id],
        "counters": {"actions": 0, "restarts": 0},
        "log": [],
        "started_at": _now(),
        "started_epoch": time.time(),
        "started_by": started_by,
    }
    # ``is not None``, not truthiness: a budget of 0 (no restarts) is a real choice.
    chosen = {k: v for k, v in (budgets or {}).items() if v is not None}
    sup["budgets"] = {**DEFAULT_BUDGETS, **chosen}
    sup["active"] = True
    sup["status"] = "watching"
    sup["reason"] = ""
    _log(sup, run_id, "start", f"supervising with budgets {sup['budgets']}")
    _save(run_id, sup)
    _collector().record_supervisor_action("start", "ok")
    return sup


def stop_supervision(run_id: str, reason: str, *, status: str = "stopped") -> dict[str, Any] | None:
    run = _store().get(run_id)
    sup = supervision_of(run)
    if sup is None:
        return None
    sup["active"] = False
    sup["status"] = status
    sup["reason"] = reason
    _log(sup, run_id, status, reason)
    _save(run_id, sup)
    _collector().record_supervisor_action(status, _reason_label(reason))
    return sup


def _reason_label(reason: str) -> str:
    low = reason.lower()
    for word in ("platform", "budget", "person", "verified", "tooling", "design"):
        if word in low:
            return word
    return "other"


# ── diagnosis (FORGE-736) ───────────────────────────────────────────────────


def diagnose(
    *,
    error: str = "",
    findings: list[str] | None = None,
    finding_history: list[list[str]] | None = None,
    review: dict[str, Any] | None = None,
    gate_ready: bool = True,
) -> dict[str, str]:
    """``{"kind": design|tooling|platform|person, "why": ...}`` for a stop or a failing gate."""
    if error:
        from orchestrator.design_flow.failures import classify_failure

        verdict = classify_failure(error)
        cls = str(getattr(verdict, "failure_class", "") or getattr(verdict, "cls", ""))
        cls = cls.split(".")[-1].lower()
        if "authorization" in cls:
            return {"kind": "person", "why": "the run was refused for lack of authorisation"}
        if any(c in cls for c in ("transient", "tool", "environment")):
            return {"kind": "tooling", "why": f"{cls} failure: {error[:200]}"}
        if "capability" in cls:
            return {
                "kind": "platform",
                "why": f"a capability the run needs is missing: {error[:200]}",
            }
        return {"kind": "design", "why": f"{cls or 'design'} failure: {error[:200]}"}
    if not gate_ready and review and review.get("recommendation") == "approve":
        return {
            "kind": "platform",
            "why": "the reviewer judges the work sound but the gate's checks keep failing: "
            "a check may be wrong",
        }
    history = [sorted(h) for h in (finding_history or []) if h]
    if len(history) >= 2 and history[-1] == history[-2] and findings:
        return {
            "kind": "platform",
            "why": "the same findings came back after a retry meant to clear them: "
            + "; ".join(findings[:3]),
        }
    return {"kind": "design", "why": "; ".join((findings or [])[:3]) or "the gate is not ready"}


# ── the tick (FORGE-735, 737, 738) ──────────────────────────────────────────


def _over_budget(sup: dict[str, Any]) -> str | None:
    from orchestrator.harness.providers.usage import run_usage

    budgets, counters = sup.get("budgets") or DEFAULT_BUDGETS, sup.get("counters") or {}
    if counters.get("actions", 0) >= budgets.get("max_actions", 20):
        return "action budget used"
    hours = (time.time() - float(sup.get("started_epoch") or time.time())) / 3600
    if hours >= budgets.get("max_hours", 12):
        return f"wall-time budget used ({hours:.1f} h)"
    calls = 0
    for rid in sup.get("chain") or []:
        try:
            calls += int((run_usage(rid) or {}).get("calls") or 0)
        except Exception:  # noqa: BLE001 - no usage store: calls are not counted
            break
    if calls >= budgets.get("max_model_calls", 600):
        return f"model-call budget used ({calls} calls)"
    return None


def _amended_request(run: Any, lesson: str, attempt: int) -> dict[str, Any]:
    request = {k: run.request[k] for k in _CARRIED if k in run.request}
    note = (
        f"Supervisor, attempt {attempt}: the previous run ({run.id}) did not reach a "
        f"verified result. {lesson.strip()} Do not repeat what it lacked."
    )
    context = str(request.get("flow_context") or "").strip()
    request["flow_context"] = f"{context}\n\n{note}".strip() if context else note
    request["supervised_from"] = run.id
    return request


async def _restart(run: Any, sup: dict[str, Any], lesson: str, kind: str) -> str | None:
    from api_gateway.runs.routes import create_run
    from api_gateway.runs.schemas import CreateRunRequest

    budgets, counters = sup["budgets"], sup["counters"]
    if counters.get("restarts", 0) >= budgets.get("max_restarts", 3):
        stop_supervision(run.id, f"restart budget used after a {kind} failure: {lesson[:200]}")
        return None
    attempt = counters.get("restarts", 0) + 2
    new = await create_run(
        CreateRunRequest(request=_amended_request(run, lesson, attempt), start=True)
    )
    new_id = new.id
    counters["restarts"] = counters.get("restarts", 0) + 1
    counters["actions"] = counters.get("actions", 0) + 1
    handed = {**sup, "chain": [*sup.get("chain", []), new_id], "counters": counters}
    _log(handed, new_id, "restart", f"{kind}: restarted from {run.id}. {lesson[:400]}")
    handed.update(active=True, status="watching", reason="")
    _save(new_id, handed)
    sup.update(active=False, status="handed_over", reason=f"restarted as {new_id}", next_run=new_id)
    _log(sup, run.id, "handed_over", f"continued in {new_id}")
    _save(run.id, sup)
    _collector().record_supervisor_action("restart", kind)
    return new_id


async def supervise_tick(run_id: str) -> dict[str, Any] | None:
    """One supervisor pass over one run. Returns the supervision record after it."""
    from api_gateway.approvals import service
    from orchestrator.harness.runs import RunStatus

    run = _store().get(run_id)
    sup = supervision_of(run)
    if not sup or not sup.get("active"):
        return sup
    with tracer.start_as_current_span("run_supervisor.tick") as span:
        span.set_attribute("run.id", run_id)
        span.set_attribute("run.status", run.status.value)
        over = _over_budget(sup)
        if over:
            return stop_supervision(run_id, over)
        status = run.status
        if status in (RunStatus.QUEUED, RunStatus.RUNNING):
            if sup.get("status") != "watching":
                sup["status"] = "watching"
                _save(run_id, sup)
            return sup
        if status is RunStatus.AWAITING_APPROVAL:
            return await _at_gate(run, sup, service)
        if status is RunStatus.FAILED:
            if sup.get("pause_until", 0) > time.time():
                return sup
            found = diagnose(error=run.error or "")
            if found["kind"] in ("platform", "person"):
                return stop_supervision(
                    run_id, f"{found['kind']} issue, needs a person: {found['why']}"
                )
            if found["kind"] == "tooling" and not sup.get("paused_for"):
                sup.update(pause_until=time.time() + TOOLING_PAUSE_SECONDS, paused_for=run_id)
                _log(
                    sup,
                    run_id,
                    "pause",
                    f"tooling failure; restarting after a pause. {found['why']}",
                )
                _save(run_id, sup)
                return sup
            await _restart(run, sup, _lesson_from(run, found["why"]), found["kind"])
            return supervision_of(_store().get(run_id))
        if status is RunStatus.COMPLETED:
            return await _at_completion(run, sup)
        # Rejected, cancelled or timed out: a person (or the clock) ended it.
        return stop_supervision(run_id, f"the run ended as {status.value}; a person decided it")


def _lesson_from(run: Any, why: str) -> str:
    reviews = run.request.get("gate_reviews") or []
    last = reviews[-1] if reviews else {}
    parts = [f"It stopped because: {why}."]
    if last.get("reasons"):
        parts.append(f"The reviewer said: {str(last['reasons'])[:600]}")
    return " ".join(parts)


async def _at_gate(run: Any, sup: dict[str, Any], service: Any) -> dict[str, Any]:
    try:
        item = await service.get_item(None, f"gate:{run.id}")
    except Exception as exc:  # noqa: BLE001 - try again next tick
        logger.info("run_supervisor_gate_unreadable", run_id=run.id, error=str(exc))
        return sup
    phase = str(item.detail.get("phase") or "")
    attempt = item.detail.get("attempt")
    key = f"{phase}:{attempt}"
    lines = [f.message for f in item.findings]
    history = sup.setdefault("finding_history", {}).setdefault(phase, [])
    if key != sup.get("gate_key"):
        history.append(lines)
        sup["finding_history"][phase] = history[-5:]
    review = item.review.model_dump() if item.review is not None else None
    ready = "approve" in item.allowed_decisions
    found = diagnose(findings=lines, finding_history=history, review=review, gate_ready=ready)
    if found["kind"] == "platform":
        return stop_supervision(run.id, f"platform issue at the {phase} gate: {found['why']}")
    if key != sup.get("gate_key"):
        sup["gate_key"] = key
        sup["status"] = "waiting_for_person"
        detail = f"gate at {phase} (attempt {attempt}) is open"
        if review:
            detail += f"; reviewer recommends {review.get('recommendation')}"
            if review.get("applied") == "held":
                detail += f" ({review.get('applied_note')})"
        detail += ". The approval policy decides it, or a person does."
        _log(sup, run.id, "gate", detail)
        _save(run.id, sup)
    return sup


async def _at_completion(run: Any, sup: dict[str, Any]) -> dict[str, Any] | None:
    from api_gateway.runs.routes import get_run_lifecycle

    try:
        view = await get_run_lifecycle(run.id)
        completion = (view.lifecycle or {}).get("completion") or {}
    except Exception as exc:  # noqa: BLE001 - no verdict: a person checks
        return stop_supervision(
            run.id, f"the run completed but its verdict could not be read: {exc}"
        )
    verdict = str(completion.get("classification") or "")
    if verdict == "COMPLETED_VERIFIED":
        return stop_supervision(run.id, "the design is verified", status="verified")
    unmet = completion.get("unmet_requirements") or []
    gaps = json.dumps(unmet[:8], default=str)[:1200]
    lesson = (
        f"It completed as {verdict or 'unverified'}, not COMPLETED_VERIFIED. "
        f"Unmet requirements: {gaps}."
    )
    await _restart(run, sup, lesson, "verification")
    return supervision_of(_store().get(run.id))


async def run_supervisor_loop(interval: float = SUPERVISOR_INTERVAL_SECONDS) -> None:
    """Tick every supervised run, indefinitely (started in the gateway's lifespan)."""
    while True:
        try:
            for run in list(_store().list()):
                sup = supervision_of(run)
                if sup and sup.get("active"):
                    try:
                        await supervise_tick(run.id)
                    except Exception as exc:  # noqa: BLE001 - one run must not stop the rest
                        logger.warning("run_supervisor_tick_failed", run_id=run.id, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - the loop must outlive one bad pass
            logger.warning("run_supervisor_pass_failed", error=str(exc))
        await asyncio.sleep(interval)
