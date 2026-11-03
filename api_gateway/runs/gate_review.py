"""The gate reviewer: a second opinion on every design-flow gate (FORGE-726..729).

Runs right after a gate opens, when the project's approval policy asks for it
(``api_gateway.approvals.policy``). Three parts:

* **The review brief** (FORGE-727): what the phase was asked to do, what it
  recorded (the twin's nodes for the project, with their content), what the
  gate's checks found, and the run's earlier decisions with their reasons.
  Built over the gateway's REST API, so the Temporal worker and the in-process
  engine build the same brief.
* **The reviewer** (FORGE-729): a review turn of the harness with its own model
  (``reviewer_model``), the ``gate-review`` skill as its rubric and the
  read-only ``review`` tool set (FORGE-728). It has no write tools, so it
  cannot fix what it is judging, and it answers with a structured review.
* **Posting** the review to ``POST /v1/runs/{id}/gate-review``, where the
  gateway stores it on the gate and applies the policy: ``advise`` only shows
  it; ``auto_clean`` and ``auto`` may decide as ``reviewer:server:<model>``.

Every step is best-effort. A reviewer that fails posts a review with
``recommendation: none`` and the error, and the gate simply waits for a
person, as it would have without a reviewer.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import structlog

from api_gateway.runs.gate_announce import gateway_url
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.runs.gate_review")

__all__ = [
    "build_review_brief",
    "parse_review",
    "request_open_gate_review",
    "request_reviews_for_project",
    "review_gate",
    "review_open_gate",
    "review_skill",
]

_TIMEOUT_SECONDS = 20.0
#: Tool steps the reviewer may take: enough to read the thread, not to wander.
REVIEW_MAX_STEPS = 12
_MAX_NODES = 30
_MAX_PROPERTY_CHARS = 700
_SKILL = (
    Path(__file__).resolve().parents[2] / "mcp_core" / "plugin_skills" / "gate-review" / "SKILL.md"
)


def _headers() -> dict[str, str]:
    key = (os.environ.get("METAFORGE_GATEWAY_API_KEY") or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


class _Gateway:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def request(self, method: str, path: str, *, json_body: Any = None) -> Any:
        url = f"{gateway_url()}{path}"
        if self._client is not None:
            resp = await self._client.request(
                method, url, json=json_body, headers=_headers(), timeout=_TIMEOUT_SECONDS
            )
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method, url, json=json_body, headers=_headers(), timeout=_TIMEOUT_SECONDS
                )
        resp.raise_for_status()
        return resp.json() if resp.content else None


def review_skill() -> str:
    """The ``gate-review`` skill's body: the rubric both reviewers use."""
    try:
        text = _SKILL.read_text(encoding="utf-8")
    except OSError:
        return ""
    return text.split("---", 2)[2].strip() if text.startswith("---") else text


def _compact(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= _MAX_PROPERTY_CHARS else text[:_MAX_PROPERTY_CHARS] + " …"


async def build_review_brief(payload: dict[str, Any], gateway: _Gateway) -> dict[str, Any]:
    """The reviewer's context for one gate (FORGE-727)."""
    run_id = str(payload.get("run_id") or "")
    project_id = payload.get("project_id")
    phase = payload.get("phase") or {}
    brief: dict[str, Any] = {
        "run_id": run_id,
        "project_id": project_id,
        "goal": payload.get("goal") or "",
        "phase": {
            "id": phase.get("id"),
            "title": phase.get("title"),
            "objective": phase.get("objective"),
            "required_deliverables": phase.get("required_deliverables") or [],
            "disciplines": phase.get("disciplines") or [],
        },
        "gate": payload.get("gate") or "",
        "attempt": payload.get("attempt"),
        "checks": {
            "ready": bool(payload.get("ready")),
            "findings": list(payload.get("findings") or []),
            "reason": payload.get("reason") or "",
        },
    }
    try:
        run = await gateway.request("GET", f"/v1/runs/{run_id}")
        request = (run or {}).get("request") or {}
        brief["earlier_decisions"] = [
            {k: d.get(k) for k in ("decision", "reason", "approver", "surface", "decided_at")}
            for d in (request.get("decisions") or [])[-10:]
        ]
        brief["flow"] = request.get("flow_template_id") or request.get("flow")
    except Exception as exc:  # noqa: BLE001 - the brief is still useful without the run
        brief["earlier_decisions"] = []
        logger.info("gate_review_brief_run_unavailable", run_id=run_id, error=str(exc))
    if project_id:
        try:
            listing = await gateway.request(
                "GET", f"/v1/twin/nodes?project_id={project_id}&limit=500"
            )
            nodes = listing.get("nodes", listing) if isinstance(listing, dict) else listing
            wanted = set(brief["phase"]["required_deliverables"]) | {
                "constraint_set",
                "design_decision",
                "cad_model",
                "load_case",
                "simulation_result",
            }
            picked = []
            for node in nodes or []:
                props = node.get("properties") or {}
                wp_type = props.get("wp_type") or node.get("type")
                if wp_type not in wanted:
                    continue
                picked.append(
                    {
                        "id": node.get("id"),
                        "type": wp_type,
                        "name": node.get("name"),
                        "created_by": props.get("created_by") or props.get("authored_by"),
                        "material": props.get("material"),
                        # FORGE-759: a sketch or render shows intent, never evidence.
                        "authority": (
                            "illustrative"
                            if wp_type in ("design_sketch", "render", "image")
                            or props.get("authority") == "illustrative"
                            else props.get("authority") or "record"
                        ),
                        "summary": _compact(
                            props.get("rationale")
                            or props.get("content")
                            or props.get("description")
                            or {k: v for k, v in props.items() if k not in ("minio_object_key",)}
                        ),
                    }
                )
            brief["work_products"] = picked[-_MAX_NODES:]
        except Exception as exc:  # noqa: BLE001
            brief["work_products"] = []
            logger.info("gate_review_brief_nodes_unavailable", run_id=run_id, error=str(exc))
    return brief


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_review(text: str) -> dict[str, Any]:
    """The reviewer's JSON answer (the last JSON object in its reply)."""
    candidates = _JSON_BLOCK.findall(text or "")
    if not candidates:
        start, end = (text or "").rfind("{"), (text or "").rfind("}")
        if start != -1 and end > start:
            candidates = [text[start : end + 1]]
    for raw in reversed(candidates):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and "recommendation" in data:
            rec = str(data.get("recommendation") or "").strip().lower()
            if rec not in ("approve", "retry", "rework", "reject"):
                rec = "none"
            try:
                confidence = min(max(float(data.get("confidence") or 0.0), 0.0), 1.0)
            except (TypeError, ValueError):
                confidence = 0.0
            return {
                "recommendation": rec,
                "to_phase": str(data.get("to_phase") or ""),
                "reasons": str(data.get("reasons") or "")[:4000],
                "concerns": [str(c)[:500] for c in (data.get("concerns") or [])][:20],
                "confidence": confidence,
            }
    raise ValueError("the reviewer's reply carried no review JSON")


def _review_prompt(brief: dict[str, Any]) -> str:
    return (
        "You are MetaForge's independent gate reviewer. You did not do this work and you "
        "cannot change it: read, judge, and recommend. Follow this procedure:\n\n"
        f"{review_skill()}\n\n"
        "The review brief for this gate:\n"
        f"```json\n{json.dumps(brief, indent=1, default=str)[:40000]}\n```\n\n"
        "Use the read-only tools if you need more, then end with the JSON object."
    )


def _split_model(ref: str) -> tuple[str | None, str | None]:
    ref = (ref or "").strip()
    if not ref:
        return None, None
    provider, sep, model = ref.partition(":")
    return (provider, model) if sep and model else (None, ref)


async def server_review(brief: dict[str, Any], *, model_ref: str) -> dict[str, Any]:
    """One review turn of the harness, read-only, with the reviewer's own model."""
    from api_gateway.chat.harness_backend import run_chat_turn
    from api_gateway.chat.routes import get_mcp_bridge, get_metrics
    from mcp_core.profiles import REVIEW_TOOLS
    from orchestrator.harness.providers.usage import usage_scope

    provider, model = _split_model(model_ref)
    bridge = get_mcp_bridge()
    phase_id = (brief.get("phase") or {}).get("id") or "gate"
    with usage_scope(run_id=brief.get("run_id"), phase=phase_id, role="gate_check"):
        reply = await run_chat_turn(
            _review_prompt(brief),
            mcp_bridge=bridge,
            metrics=get_metrics(),
            session_id=f"review:{brief.get('run_id')}:{phase_id}",
            max_steps=REVIEW_MAX_STEPS,
            provider=provider,
            model=model,
            tool_allowlist=REVIEW_TOOLS,
            approval_mode="forward",
        )
    return parse_review(reply)


def client_review_wait_seconds() -> float:
    """How long to wait for a client reviewer, ``METAFORGE_CLIENT_REVIEW_WAIT_SECONDS``."""
    try:
        return max(float(os.environ.get("METAFORGE_CLIENT_REVIEW_WAIT_SECONDS", "") or 600), 0.0)
    except ValueError:
        return 600.0


_CLIENT_POLL_SECONDS = 10.0


async def _post_none(
    gateway: _Gateway, payload: dict[str, Any], source: str, model: str, why: str
) -> None:
    phase = payload.get("phase") or {}
    try:
        await gateway.request(
            "POST",
            f"/v1/runs/{payload.get('run_id')}/gate-review",
            json_body={
                "phase": str(phase.get("id") or ""),
                "attempt": payload.get("attempt"),
                "gate": str(payload.get("gate") or ""),
                "source": source,
                "model": model or "unknown",
                "recommendation": "none",
                "error": why,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("gate_review_post_failed", run_id=payload.get("run_id"), error=str(exc))


async def _client_review(
    payload: dict[str, Any], gateway: _Gateway, model_ref: str, *, sleep: Any = None
) -> dict[str, Any] | None:
    """Open a review task and wait for a client to submit it. None when nobody did."""
    import asyncio
    import time

    pause = sleep or asyncio.sleep
    phase = payload.get("phase") or {}
    brief = await build_review_brief(payload, gateway)
    task = await gateway.request(
        "POST",
        "/v1/review-tasks",
        json_body={
            "run_id": payload.get("run_id"),
            "project_id": payload.get("project_id"),
            "phase": str(phase.get("id") or ""),
            "attempt": payload.get("attempt"),
            "gate": str(payload.get("gate") or ""),
            "brief": brief,
            "requested_model": model_ref,
        },
    )
    task_id = task["id"]
    logger.info(
        "gate_review_task_waiting", task_id=task_id, wait_seconds=client_review_wait_seconds()
    )
    deadline = time.monotonic() + client_review_wait_seconds()
    while True:
        current = await gateway.request("GET", f"/v1/review-tasks/{task_id}")
        status = (current or {}).get("status")
        if status == "submitted":
            return {"status": "reviewed", "source": "client", "task_id": task_id}
        if status == "cancelled" or time.monotonic() >= deadline:
            break
        await pause(_CLIENT_POLL_SECONDS)
    await gateway.request(
        "POST",
        f"/v1/review-tasks/{task_id}/cancel",
        json_body={"reason": "no client reviewer in time"},
    )
    logger.info("gate_review_task_unclaimed", task_id=task_id)
    return None


async def review_gate(
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    reviewer: Any = None,
    sleep: Any = None,
) -> dict[str, Any]:
    """Review one open gate if the project's policy asks for it, and post the review."""
    gateway = _Gateway(client)
    run_id = str(payload.get("run_id") or "")
    project_id = payload.get("project_id")
    phase = payload.get("phase") or {}
    with tracer.start_as_current_span("gate_review.review") as span:
        span.set_attribute("run.id", run_id)
        span.set_attribute("phase.id", str(phase.get("id") or ""))
        if not project_id:
            return {"status": "skipped", "why": "no project"}
        try:
            view = await gateway.request("GET", f"/v1/projects/{project_id}/approval-policy")
        except Exception as exc:  # noqa: BLE001 - no policy, no review
            logger.info("gate_review_policy_unavailable", run_id=run_id, error=str(exc))
            return {"status": "skipped", "why": "policy unavailable"}
        mode = view.get("effective_mode", "off")
        policy = view.get("policy") or {}
        span.set_attribute("policy.mode", mode)
        if mode == "off":
            return {"status": "skipped", "why": "policy off"}
        source = str(policy.get("reviewer") or "server")
        model_ref = str(policy.get("reviewer_model") or "")
        if source in ("client", "client_then_server"):
            # FORGE-730: a connected client's reviewer subagent does the review.
            try:
                outcome = await _client_review(payload, gateway, model_ref, sleep=sleep)
            except Exception as exc:  # noqa: BLE001 - fall back as the policy says
                logger.warning("gate_review_task_failed", run_id=run_id, error=str(exc))
                outcome = None
            if outcome is not None:
                return outcome
            if source == "client":
                await _post_none(
                    gateway, payload, "client", model_ref, "no client reviewer took the review"
                )
                return {"status": "unreviewed", "why": "no client reviewer"}
        review: dict[str, Any] = {
            "phase": str(phase.get("id") or ""),
            "attempt": payload.get("attempt"),
            "gate": str(payload.get("gate") or ""),
            "source": "server",
            "model": model_ref or "default",
        }
        try:
            brief = await build_review_brief(payload, gateway)
            run = reviewer or (lambda b: server_review(b, model_ref=model_ref))
            review.update(await run(brief))
        except Exception as exc:  # noqa: BLE001 - a failed review still says so on the gate
            span.record_exception(exc)
            logger.warning("gate_review_failed", run_id=run_id, error=str(exc))
            review.update({"recommendation": "none", "error": str(exc)[:500]})
        try:
            stored = await gateway.request(
                "POST", f"/v1/runs/{run_id}/gate-review", json_body=review
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("gate_review_post_failed", run_id=run_id, error=str(exc))
            return {"status": "failed", "why": str(exc)}
    logger.info(
        "gate_review_posted",
        run_id=run_id,
        phase=review["phase"],
        recommendation=review.get("recommendation"),
        applied=(stored or {}).get("applied"),
    )
    return {"status": "reviewed", "applied": (stored or {}).get("applied")}


# ── a gate that opened without a review (FORGE-743) ─────────────────────────

#: ``run_id:phase:attempt`` of reviews asked for and not finished, so the
#: supervisor's next tick and a policy change do not ask twice for one gate.
_in_flight: set[str] = set()
_tasks: set[Any] = set()


async def review_open_gate(run_id: str) -> dict[str, Any]:
    """Review the gate ``run_id`` is parked at, if it has no review yet (FORGE-743).

    A review used to happen only when a gate opened. A gate that opened while
    the project's policy was ``off``, or whose review failed, stayed unreviewed
    for good: turning the policy to ``auto`` afterwards changed nothing, and the
    run waited for a person the policy said was not needed. The policy still
    decides: ``review_gate`` skips when it is ``off``.
    """
    from api_gateway.approvals import service
    from api_gateway.runs import routes as run_routes
    from api_gateway.runs.schemas import project_of

    run = run_routes.get_run_store().get(run_id)
    item = await service.get_item(None, f"gate:{run_id}")
    if item.status != "pending":
        return {"status": "skipped", "why": "the gate is not open"}
    if item.review is not None:
        return {"status": "skipped", "why": "the gate already has a review"}
    phase_id = str(item.detail.get("phase") or "")
    definition = run_routes._run_definition(run)  # noqa: SLF001
    phase = next(
        (p for p in (definition.phases if definition is not None else []) if p.id == phase_id),
        None,
    )
    payload = {
        "run_id": run_id,
        "project_id": project_of(run.request),
        "goal": str(run.request.get("goal") or ""),
        "phase": {
            "id": phase_id,
            "title": getattr(phase, "title", phase_id),
            "objective": getattr(phase, "objective", ""),
            "required_deliverables": list(getattr(phase, "required_deliverables", []) or []),
            "disciplines": list(getattr(phase, "disciplines", []) or []),
        },
        "gate": str(item.detail.get("gate") or ""),
        "attempt": item.detail.get("attempt"),
        "ready": "approve" in item.allowed_decisions,
        "findings": [f.message for f in item.findings],
        "reason": run.approval_reason or "",
    }
    logger.info("gate_review_requested_for_open_gate", run_id=run_id, phase=phase_id)
    return await review_gate(payload)


def request_open_gate_review(run_id: str, phase: str, attempt: Any) -> bool:
    """Start :func:`review_open_gate` in the background, once per gate. True if started."""
    import asyncio

    key = f"{run_id}:{phase}:{attempt}"
    if key in _in_flight:
        return False
    _in_flight.add(key)

    async def _run() -> None:
        try:
            outcome = await review_open_gate(run_id)
            logger.info("gate_review_open_gate_done", run_id=run_id, phase=phase, **outcome)
        except Exception as exc:  # noqa: BLE001 - a review is advice; it never breaks a run
            logger.warning("gate_review_open_gate_failed", run_id=run_id, error=str(exc))
        finally:
            _in_flight.discard(key)

    task = asyncio.ensure_future(_run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


async def request_reviews_for_project(project_id: str) -> int:
    """Ask for a review of every open, unreviewed gate in a project (FORGE-743).

    Called when a person changes the project's approval policy, so a gate
    already waiting is reviewed under the new policy rather than only gates
    that open later. Returns how many reviews were started.
    """
    from api_gateway.approvals import service
    from api_gateway.runs import routes as run_routes
    from api_gateway.runs.schemas import project_of
    from orchestrator.harness.runs import RunStatus

    started = 0
    for run in list(run_routes.get_run_store().list()):
        if run.status is not RunStatus.AWAITING_APPROVAL:
            continue
        if project_of(run.request) != project_id or not run_routes.is_design_flow_run(run):
            continue
        try:
            item = await service.get_item(None, f"gate:{run.id}")
        except Exception as exc:  # noqa: BLE001 - one unreadable gate must not stop the rest
            logger.info("gate_review_open_gate_unreadable", run_id=run.id, error=str(exc))
            continue
        if item.review is None and request_open_gate_review(
            run.id, str(item.detail.get("phase") or ""), item.detail.get("attempt")
        ):
            started += 1
    logger.info("gate_review_project_requested", project_id=project_id, started=started)
    return started
