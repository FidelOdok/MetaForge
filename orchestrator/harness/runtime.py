"""Harness runtime composition root (MET-547, Phase 2/3 seam).

One object that bundles the services a run needs — the provider pipeline
(model access), the tool registry (native + MCP), and the run store — so the
Planner/Generator/Evaluator agents receive a single ``HarnessRuntime`` instead
of reaching for globals. Gate enforcement is centralized here: ``call_tool``
always threads the runtime's ``gate_check`` into the registry, so a consequential
tool can't be invoked through the runtime without its preconditions being
evaluated.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from observability.metrics import MetricsCollector
from observability.tracing import get_tracer
from orchestrator.harness.providers import (
    CredentialStore,
    HarnessProviderConfig,
    ProviderPipeline,
    RetryPolicy,
    RoleModelSlots,
    RotationStrategy,
    store_backed_invoke,
)
from orchestrator.harness.providers.pipeline import (
    Invoke,
    ProviderSpec,
    StreamEvents,
    StreamInvoke,
)
from orchestrator.harness.runs import (
    ApprovalDecision,
    InMemoryRunStore,
    InvalidTransition,
    RunNotFoundError,
    RunStatus,
)
from orchestrator.harness.tools import ApprovalDeniedError, GateCheck, ToolRegistry, ToolSpec

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.harness.runtime")

# Notified once a gated tool call is paused awaiting a human decision:
# (run_id, tool_name, arguments).
OnApprovalRequest = Callable[[str, str, dict[str, Any]], Awaitable[None]]


@dataclass
class HarnessRuntime:
    """The services a harness run is given: models, tools, run state."""

    providers: ProviderPipeline
    tools: ToolRegistry
    runs: InMemoryRunStore
    gate_check: GateCheck | None = None
    # Optional multi-credential store: when set, model calls rotate a provider's
    # stored credentials per session and blacklist any that fail terminally.
    credentials: CredentialStore | None = None
    session_id: str = "default"
    # Clock seam — threaded into credential cooldown checks so time is injectable.
    clock: Callable[[], float] = time.time
    # How stored credentials are rotated across a provider's profiles.
    rotation_strategy: RotationStrategy = RotationStrategy.ROUND_ROBIN
    # Optional metrics sink (production-harness audit follow-up) — every
    # public call on this runtime is best-effort instrumented when set; None
    # (the default, and every existing test) keeps this a pure no-op.
    metrics: MetricsCollector | None = None
    # Three-tier permissions, "ask" (production-harness audit follow-up):
    # notified when a `requires_approval` tool call pauses; None means no
    # live notification (the call still waits/times out, just silently).
    on_approval_request: OnApprovalRequest | None = None
    approval_timeout_seconds: float = 120.0
    approval_poll_interval: float = 1.0
    # Injectable so tests don't wait real wall-clock time (same seam as
    # ProviderPipeline's `sleep`).
    approval_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    @classmethod
    def build(
        cls,
        provider_config: HarnessProviderConfig | None = None,
        *,
        tools: ToolRegistry | None = None,
        gate_check: GateCheck | None = None,
        credentials: CredentialStore | None = None,
        session_id: str = "default",
        clock: Callable[[], float] = time.time,
        rotation_strategy: RotationStrategy = RotationStrategy.ROUND_ROBIN,
        metrics: MetricsCollector | None = None,
        runs: InMemoryRunStore | None = None,
        on_approval_request: OnApprovalRequest | None = None,
        approval_timeout_seconds: float = 120.0,
        approval_poll_interval: float = 1.0,
        approval_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> HarnessRuntime:
        """Assemble a runtime from an optional provider config.

        With no config the provider pipeline has no role slots (resolving a
        role raises) — useful for tool-only runtimes in tests.

        ``runs``, when given, is used as-is instead of a fresh per-turn store
        — pass a process-level, shared :class:`InMemoryRunStore` so a
        separate HTTP request (an approval decision) can reach the same live
        run a paused ``call_tool`` is polling (production-harness audit
        follow-up).
        """
        slots = provider_config.slots if provider_config else RoleModelSlots()
        retry = provider_config.retry if provider_config else RetryPolicy()
        return cls(
            providers=ProviderPipeline(slots, retry_policy=retry, metrics=metrics),
            tools=tools or ToolRegistry(),
            runs=runs if runs is not None else InMemoryRunStore(clock=clock),
            gate_check=gate_check,
            credentials=credentials,
            session_id=session_id,
            clock=clock,
            rotation_strategy=rotation_strategy,
            metrics=metrics,
            on_approval_request=on_approval_request,
            approval_timeout_seconds=approval_timeout_seconds,
            approval_poll_interval=approval_poll_interval,
            approval_sleep=approval_sleep,
        )

    def _effective_invoke(self, base_invoke: Invoke) -> Invoke:
        """Wrap ``base_invoke`` with credential rotation when a store is set.

        For each provider the pipeline tries, if the store has healthy
        credentials for that provider id, calls rotate through them (and dead
        ones are blacklisted); otherwise the base invoke is used unchanged.
        """
        store = self.credentials
        if store is None:
            return base_invoke
        session = self.session_id

        async def invoke(spec: ProviderSpec, request: Any) -> Any:
            if store.healthy(spec.name, now=self.clock()):
                rotated = store_backed_invoke(
                    base_invoke,
                    store,
                    spec.name,
                    session,
                    now=self.clock,
                    strategy=self.rotation_strategy,
                )
                return await rotated(spec, request)
            return await base_invoke(spec, request)

        return invoke

    async def complete(self, role: str, request: Any, invoke: Invoke) -> Any:
        """Run a model request for a role through the provider pipeline."""
        return await self.providers.complete(role, request, self._effective_invoke(invoke))

    async def stream_complete(
        self, role: str, request: Any, stream_invoke: StreamInvoke
    ) -> AsyncIterator[str]:
        """Stream a role's response through the pipeline (retry/fallback before
        the first token, then committed).

        Used for the final user-facing chat answer only — the rotation-protected
        :meth:`complete` path drives the ReAct loop that does the real work, so
        the stream uses the spec's own credentials (no per-token rotation).
        """
        async for delta in self.providers.stream_complete(role, request, stream_invoke):
            yield delta

    async def stream_events(
        self, role: str, request: Any, stream_events: StreamEvents
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a role's response as structured events (MET-591) — text deltas
        while the model generates, terminated by the full response object.
        Failover happens only before the first event (see the pipeline)."""
        async for event in self.providers.stream_events_complete(role, request, stream_events):
            yield event

    async def _await_approval(self, spec: ToolSpec, arguments: dict[str, Any]) -> None:
        """Pause until a ``requires_approval`` tool's call is approved.

        Production-harness audit follow-up — the third permission tier,
        "ask". Fails safe on every non-approved path: no ``runs`` store at
        all, an explicit rejection, or a timeout all raise
        :class:`ApprovalDeniedError` rather than ever silently proceeding.
        """
        run = self.runs.create({"tool": spec.name, "arguments": arguments})
        self.runs.start(run.id)
        self.runs.request_approval(run.id, reason=f"approval required for tool '{spec.name}'")
        if self.on_approval_request is not None:
            try:
                await self.on_approval_request(run.id, spec.name, arguments)
            except Exception as exc:  # noqa: BLE001 - notification is best-effort
                logger.warning(
                    "approval_notify_failed", run_id=run.id, tool=spec.name, error=str(exc)
                )
        deadline = time.monotonic() + self.approval_timeout_seconds
        while time.monotonic() < deadline:
            status = self.runs.get(run.id).status
            if status is RunStatus.RUNNING:
                return
            if status is RunStatus.REJECTED:
                raise ApprovalDeniedError(spec.name, "rejected")
            await self.approval_sleep(self.approval_poll_interval)
        # Timed out — deny by default. A decision landing in the exact
        # instant between the last poll and here is still honored (checked
        # once more) rather than clobbered by a race with submit_approval.
        try:
            self.runs.submit_approval(run.id, ApprovalDecision.REJECT)
        except (InvalidTransition, RunNotFoundError):
            pass
        if self.runs.get(run.id).status is RunStatus.RUNNING:
            return
        logger.warning("approval_timed_out", run_id=run.id, tool=spec.name)
        raise ApprovalDeniedError(spec.name, "timed out waiting for approval (denied by default)")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a registered tool, enforcing this runtime's gate + approval policy."""
        start = time.monotonic()
        status = "ok"
        with tracer.start_as_current_span("harness.tool_call") as span:
            span.set_attribute("tool.name", name)
            try:
                spec = self.tools.get(name)
                if spec.requires_approval:
                    await self._await_approval(spec, arguments)
                result = await self.tools.invoke(name, arguments, gate_check=self.gate_check)
            except Exception as exc:
                status = "error"
                span.record_exception(exc)
                raise
            finally:
                span.set_attribute("tool.status", status)
                if self.metrics is not None:
                    try:
                        self.metrics.record_harness_tool_call(
                            name, status, time.monotonic() - start
                        )
                    except Exception:  # noqa: BLE001 - metrics must never break a tool call
                        pass
            return result
