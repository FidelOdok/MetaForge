"""The Temporal worker that actually runs design-flow phases (FORGE-401).

Lives in ``api_gateway`` rather than ``orchestrator`` because of the layering
rule, and the rule is right here: a phase runs the agent brain, which needs
the MCP bridge, the project backend and the twin — all layer-4 things.
``orchestrator`` owns the workflow and the activity *shapes*; this binds real
implementations to them.

Run it with ``python -m api_gateway.runs.flow_worker``.

A note on what a phase activity is. It is one agent loop, minutes to hours,
and it is *not* idempotent in the usual sense: re-running it after a crash
will produce work products again. That is deliberate and it is why the retry
policy is small (3 attempts) rather than generous — the twin tolerates a
duplicate proposal far better than a run tolerates being abandoned halfway,
but neither is free, so retries are bounded and heartbeats are what
distinguish a slow phase from a dead worker.

FORGE-475: this process is not the gateway, so nothing it imports wires the
two things a phase brain needs from the gateway's lifespan. Tools come from an
MCP client to the ``mcp-http`` sidecar (``METAFORGE_MCP_URL``), installed with
the same ``init_mcp_bridge`` seam the gateway uses so ``build_phase_brain`` is
reused unchanged. The model comes from the same ``METAFORGE_LLM_*`` env and
durable ``~/.metaforge`` selection the gateway reads (compose gives this
service both). Without either, a phase used to "run" with zero tools against
a provider it had no key for, and the run only showed the symptom.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog
from temporalio.exceptions import ApplicationError

from mcp_core.context import McpCallContext, with_context
from observability.tracing import get_tracer
from orchestrator.design_flow.grounding import phase_status
from orchestrator.design_flow.temporal_activities import DesignFlowActivities
from orchestrator.design_flow.temporal_flow import GateCheck, PhaseRequest, PhaseResult
from orchestrator.design_flow.worker import build_design_flow_worker

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.runs.flow_worker")

__all__ = [
    "DEFAULT_MCP_URL",
    "build_activities",
    "ensure_gate_stores",
    "ensure_mcp_bridge",
    "main",
    "mcp_server_url",
    "run_worker",
]

#: Where the sidecar lives inside compose. Overridden by ``METAFORGE_MCP_URL``.
DEFAULT_MCP_URL = "http://mcp-http:8765/mcp"

#: The actor every phase's tool calls are attributed to on the sidecar.
_ACTOR = "agent:design-flow"

_stores_ready = False
_stores_lock: asyncio.Lock | None = None
_bridge: Any = None
_bridge_lock: asyncio.Lock | None = None


async def ensure_gate_stores() -> None:
    """Point the gate evaluator at the real project store and twin (FORGE-484).

    This process is not the gateway, so nothing runs the lifespan that swaps
    the route modules' in-memory defaults for the Postgres project backend and
    the Neo4j twin. Without it the gate check read an empty in-memory project,
    reported "0 deliverable type(s) recorded", and judged a phase that had
    recorded its deliverable. A failure here raises a retryable activity error
    rather than falling back to the empty store: an unreadable store must not
    be read as "nothing recorded" or as "all clear".
    """
    global _stores_ready, _stores_lock  # noqa: PLW0603
    if _stores_ready:
        return
    if _stores_lock is None:
        _stores_lock = asyncio.Lock()
    async with _stores_lock:
        if _stores_ready:
            return
        from api_gateway.projects import routes as project_routes
        from api_gateway.projects.backend import create_project_backend
        from api_gateway.server import _init_database
        from api_gateway.twin import routes as twin_routes
        from twin_core.api import InMemoryTwinAPI

        try:
            await _init_database()
            backend = await create_project_backend()
            twin = await InMemoryTwinAPI.create_from_env()
        except Exception as exc:
            logger.error("design_flow_worker_stores_unavailable", error=str(exc))
            raise ApplicationError(
                f"design-flow worker cannot reach the project store or twin: {exc}",
                type="StoreUnavailable",
            ) from exc
        project_routes.init_project_backend(backend)
        project_routes.init_twin(twin)
        twin_routes.init_twin(twin)
        _stores_ready = True
        logger.info(
            "design_flow_worker_stores_ready",
            backend=type(backend).__name__,
            graph=type(getattr(twin, "_graph", None)).__name__,
        )


def mcp_server_url() -> str:
    """The sidecar's base URL, as ``HttpTransport`` wants it.

    ``METAFORGE_MCP_URL`` is written the way clients see it (``.../mcp``);
    the transport appends ``/mcp`` itself, so a trailing one is dropped
    rather than doubled into ``/mcp/mcp``.
    """
    url = (os.environ.get("METAFORGE_MCP_URL") or DEFAULT_MCP_URL).strip().rstrip("/")
    if url.endswith("/mcp"):
        url = url[: -len("/mcp")]
    return url


async def ensure_mcp_bridge() -> Any:
    """Connect to the sidecar once and install it as the phase brain's bridge.

    Lazy, so a worker started before the sidecar is up still comes up, and a
    failed connect is retried on the next phase instead of being cached. A
    failure raises a *retryable* activity error carrying the reason: the
    alternative, the empty in-memory bridge, is the silent zero-tools state
    this exists to end.
    """
    global _bridge, _bridge_lock  # noqa: PLW0603
    if _bridge is not None:
        return _bridge
    if _bridge_lock is None:
        _bridge_lock = asyncio.Lock()
    async with _bridge_lock:
        if _bridge is not None:
            return _bridge
        from api_gateway.chat.routes import init_mcp_bridge
        from skill_registry.bridge_factory import connect_http_bridge

        url = mcp_server_url()
        api_key = (
            os.environ.get("METAFORGE_MCP_CLIENT_KEY")
            or os.environ.get("METAFORGE_MCP_API_KEY")
            or None
        )
        # FORGE-487: the worker's service credential, shared only with the
        # sidecar. Absent, the sidecar keeps treating this process as an
        # untrusted caller and holds its writes; that is the safe default, so
        # a missing key is logged, not fatal.
        service_key = (os.environ.get("METAFORGE_MCP_SERVICE_KEY") or "").strip() or None
        if service_key is None:
            logger.warning(
                "design_flow_worker_no_service_key",
                detail="METAFORGE_MCP_SERVICE_KEY is not set: the sidecar will hold every "
                "phase write for a dashboard approval",
            )
        with tracer.start_as_current_span("design_flow_worker.connect_mcp") as span:
            span.set_attribute("mcp.url", url)
            try:
                bridge = await connect_http_bridge(
                    url, api_key=api_key, require=True, service_key=service_key
                )
                tools = await bridge.list_tools()
            except Exception as exc:
                span.record_exception(exc)
                logger.error("design_flow_worker_mcp_unreachable", url=url, error=str(exc))
                raise ApplicationError(
                    f"design-flow worker cannot reach the MCP sidecar at {url}: {exc}",
                    type="McpUnavailable",
                ) from exc
            span.set_attribute("mcp.tool_count", len(tools))
        if not tools:
            logger.error("design_flow_worker_mcp_no_tools", url=url)
            raise ApplicationError(
                f"MCP sidecar at {url} lists no tools; a phase would run with none",
                type="McpUnavailable",
            )
        init_mcp_bridge(bridge)
        _bridge = bridge
        logger.info("design_flow_worker_mcp_connected", url=url, tool_count=len(tools))
        return bridge


def _reset_mcp_bridge() -> None:
    """Forget the cached bridge (tests)."""
    global _bridge, _bridge_lock  # noqa: PLW0603
    _bridge = None
    _bridge_lock = None


def _project_uuid(project_id: str | None) -> uuid.UUID | None:
    if not project_id:
        return None
    try:
        return uuid.UUID(str(project_id))
    except ValueError:
        logger.warning("design_flow_phase_project_id_not_uuid", project_id=project_id)
        return None


@contextmanager
def _phase_scope(request: PhaseRequest) -> Iterator[McpCallContext]:
    """Scope every MCP call the phase makes to the run's project.

    ``HttpTransport`` forwards the active context as ``X-MetaForge-*``
    headers, which the sidecar turns back into the same context, so twin and
    project tools see the run's project and its session capture groups the
    phase's calls under one session per run.
    """
    ctx = McpCallContext(
        project_id=_project_uuid(request.project_id),
        session_id=uuid.uuid5(uuid.NAMESPACE_URL, f"metaforge:design-flow:{request.run_id}"),
        actor_id=_ACTOR,
        # FORGE-487: what the sidecar records against every write. Claims here;
        # the sidecar only believes them after the service key checks out and
        # the gateway confirms the run.
        run_id=request.run_id,
        phase=request.phase.id,
        model=request.phase.model,
    )
    with with_context(ctx):
        yield ctx


def _log_phase_skills(request: PhaseRequest) -> None:
    """Say which discipline skills the phase's brain will load.

    The brain loads them itself (``ReActPhaseBrain`` for the procedural
    overlay, ``mcp_tools_from_bridge`` for tool scoping); this only makes an
    empty set visible, since it reads like a working phase otherwise.
    """
    from skill_registry.skill_context import cards_for_domains, load_skill_cards

    disciplines = tuple(request.phase.disciplines)
    cards = cards_for_domains(load_skill_cards(), disciplines) if disciplines else []
    log = logger.warning if disciplines and not cards else logger.info
    log(
        "design_flow_phase_skills",
        phase=request.phase.id,
        disciplines=list(disciplines),
        skills=[c.name for c in cards],
    )


def _model_failure(exc: BaseException) -> ApplicationError | None:
    """Turn a phase that could not reach any model into a visible run failure.

    Without this the activity error was generic and retried as if the next
    attempt might find a key that is not there. A chain whose every attempt
    failed for a non-retryable reason (a missing key, a model the provider
    cannot serve) is non-retryable here too, so the run fails once, with the
    reason, instead of three times.
    """
    from orchestrator.harness.providers.pipeline import AllProvidersFailedError, ProviderError
    from orchestrator.harness.providers.registry import InvalidModelError
    from orchestrator.harness.providers.routing import RoutingConfigError

    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, AllProvidersFailedError):
            permanent = all(
                isinstance(err, ProviderError) and not err.retryable for _, err in cur.attempts
            )
            return ApplicationError(
                f"no model provider could serve this phase: {cur}",
                type="ProviderUnavailable",
                non_retryable=permanent,
            )
        if isinstance(cur, (InvalidModelError, RoutingConfigError)):
            return ApplicationError(
                f"no usable model provider for this phase: {cur}",
                type="ProviderUnavailable",
                non_retryable=True,
            )
        cur = cur.__cause__ or cur.__context__
    return None


async def _run_phase(request: PhaseRequest) -> PhaseResult:
    """Drive one phase's agent loop.

    Reuses the same ``HybridBrain`` the in-process executor builds, so the
    two engines run identical phase logic and a difference between them is a
    difference in durability only — not in what the agent does.
    """
    from api_gateway.runs.routes import build_phase_brain
    from orchestrator.design_flow.executor import FlowContext
    from orchestrator.design_flow.spec import Phase

    await ensure_mcp_bridge()
    _log_phase_skills(request)
    brain = await build_phase_brain(request.run_id, request.flow_id)
    phase = Phase(
        id=request.phase.id,
        title=request.phase.title,
        objective=request.phase.objective,
        expected_artifacts=tuple(request.phase.expected_artifacts),
        required_deliverables=tuple(request.phase.required_deliverables),
        enforce_deliverables=request.phase.enforce_deliverables,
        disciplines=tuple(request.phase.disciplines),
        model=request.phase.model,
    )
    ctx = FlowContext(
        goal=request.goal,
        project_id=request.project_id,
        session_id=request.session_id,
        flow_context=request.flow_context,
    )
    with tracer.start_as_current_span("design_flow_worker.run_phase") as span:
        span.set_attribute("run.id", request.run_id)
        span.set_attribute("phase.id", request.phase.id)
        try:
            with _phase_scope(request):
                outcome = await brain.run_phase(goal=request.goal, phase=phase, context=ctx)
        except Exception as exc:
            span.record_exception(exc)
            failure = _model_failure(exc)
            if failure is None:
                raise
            logger.error(
                "design_flow_phase_no_model",
                run_id=request.run_id,
                phase=request.phase.id,
                non_retryable=failure.non_retryable,
                error=str(failure),
            )
            raise failure from exc
    return PhaseResult(
        summary=outcome.summary,
        artifacts=list(outcome.artifacts),
        status=phase_status(outcome.summary, outcome.status),
    )


async def _check_gate(payload: dict[str, Any]) -> GateCheck:
    """Evaluate a gate's preconditions against the twin.

    Returns ``checked=False`` when an evaluator is not wired rather than a
    clean result. "Nothing was wrong" and "nothing was looked at" must not
    render the same (FORGE-361).
    """
    from api_gateway.runs.routes import build_gate_checkers

    await ensure_gate_stores()
    checkers = await build_gate_checkers()
    if checkers is None:
        return GateCheck(checked=False, constraints_checked=False, reason="no evaluators wired")

    return await checkers.evaluate(payload.get("phase"), payload.get("project_id"))


def build_activities() -> DesignFlowActivities:
    return DesignFlowActivities(phase_runner=_run_phase, gate_checker=_check_gate)


async def run_worker() -> None:
    from api_gateway.runs.engine import temporal_target
    from orchestrator.design_flow.launcher import connect_temporal

    target = temporal_target()
    client = await connect_temporal(target)
    worker = build_design_flow_worker(client, build_activities())
    logger.info("design_flow_worker_starting", target=target)
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":  # pragma: no cover
    main()
