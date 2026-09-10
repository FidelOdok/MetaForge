"""Provider-agnostic model pipeline (MET-547, Phase 1).

The harness's Planner/Generator/Evaluator agents call models through a
:class:`ProviderPipeline` instead of a hard-wired SDK client. The pipeline
resolves the ordered provider candidates for a *role*, retries each up to
``api_max_retries`` on retryable failures, and falls through to the next
provider when one is exhausted -- satisfying the MET-547 criteria "same loop
runs against any provider with zero code change" and "automatic failover on
429: fall to the next model, session preserved".

The actual API call is an injected ``invoke`` coroutine
``(ProviderSpec, request) -> response``. Keeping the SDK binding out of this
module means the retry/fallback logic is pure and fully unit-testable with a
fake ``invoke`` -- no network, no real backoff sleeps.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog

from observability.metrics import MetricsCollector
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.harness.providers.pipeline")

# One of: "planner" | "generator" | "evaluator" | "vision" | "compression".
Role = str

# Injected transport: perform one model call for a resolved provider.
Invoke = Callable[["ProviderSpec", Any], Awaitable[Any]]
# Injected streaming transport: yield text deltas for a resolved provider.
StreamInvoke = Callable[["ProviderSpec", Any], AsyncIterator[str]]
# MET-591: event streaming — yields {"type": "text_delta"|"response", ...} dicts.
StreamEvents = Callable[["ProviderSpec", Any], AsyncIterator[dict[str, Any]]]
# Injected sleep, so tests can assert backoff without real delays.
Sleep = Callable[[float], Awaitable[None]]


class ProviderError(Exception):
    """A model call failed against one provider.

    ``status_code`` mirrors an HTTP status when the transport has one;
    ``retryable`` lets a transport mark a failure retryable independent of
    status (e.g. a connection reset with no status).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        context_length_exceeded: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        # MET-655 remainder: set by a transport that recognized the SDK's
        # "prompt exceeds context window" error shape, so the pipeline can
        # skip any later candidate whose window is provably too small too
        # (see ProviderSpec.max_context_tokens) instead of resending the
        # same oversized request and getting the identical rejection.
        self.context_length_exceeded = context_length_exceeded


class AllProvidersFailedError(Exception):
    """Every provider configured for a role was exhausted.

    Carries each ``(ProviderSpec, last_error)`` so callers can report exactly
    what was tried and why the whole chain failed.
    """

    def __init__(self, role: Role, attempts: list[tuple[ProviderSpec, Exception]]) -> None:
        self.role = role
        self.attempts = attempts
        detail = "; ".join(f"{spec.name}:{spec.model} -> {err}" for spec, err in attempts)
        super().__init__(f"all providers failed for role '{role}': {detail}")


@dataclass(frozen=True)
class ProviderSpec:
    """A single provider+model target in a role's fallback chain."""

    name: str
    model: str
    api_key_env: str | None = None
    base_url: str | None = None
    # A raw API key resolved from the gateway auth store (`forge auth login`),
    # preferred over reading ``api_key_env`` from the environment when set.
    api_key: str | None = None
    weight: int = 1
    extra: Mapping[str, str] = field(default_factory=dict)
    # MET-655 remainder: this provider+model's real context window in tokens,
    # if the caller knows it (e.g. via api_gateway.chat.harness_backend's
    # context_window_for). Optional and purely advisory -- when unset (the
    # default), the pipeline behaves exactly as before. When set, it lets
    # the pipeline skip a later candidate that is guaranteed to fail the
    # same way a context-length rejection just did.
    max_context_tokens: int | None = None


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to retry a single provider before falling through."""

    api_max_retries: int = 2
    backoff_base_seconds: float = 0.5
    retryable_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})

    def is_retryable(self, error: ProviderError) -> bool:
        if error.status_code is not None and error.status_code in self.retryable_statuses:
            return True
        return error.retryable


@dataclass
class RoleModelSlots:
    """Ordered provider candidates per role (primary first, then fallbacks).

    Role-based slots let the Evaluator run on a different provider than the
    Generator (bias independence, a MET-547 success criterion).
    """

    slots: dict[Role, list[ProviderSpec]] = field(default_factory=dict)

    def candidates(self, role: Role) -> list[ProviderSpec]:
        specs = self.slots.get(role)
        if not specs:
            raise KeyError(f"no provider configured for role '{role}'")
        return list(specs)


class ProviderPipeline:
    """Resolve a role to providers and call the first one that succeeds."""

    def __init__(
        self,
        slots: RoleModelSlots,
        *,
        retry_policy: RetryPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self._slots = slots
        self._retry = retry_policy or RetryPolicy()
        self._sleep = sleep
        self._metrics = metrics

    def resolve(self, role: Role) -> list[ProviderSpec]:
        """Ordered candidate providers for ``role`` (raises if none)."""
        return self._slots.candidates(role)

    async def complete(self, role: Role, request: Any, invoke: Invoke) -> Any:
        """Run ``request`` against the role's providers with retry + fallback.

        Tries each provider in order. A retryable failure is retried up to
        ``api_max_retries`` with exponential backoff; a non-retryable failure
        moves straight to the next provider. Raises
        :class:`AllProvidersFailedError` only when the whole chain is spent.
        """
        candidates = self.resolve(role)
        attempts: list[tuple[ProviderSpec, Exception]] = []
        known_insufficient_window: int | None = None

        with tracer.start_as_current_span("provider.complete") as span:
            span.set_attribute("provider.role", role)
            span.set_attribute("provider.candidate_count", len(candidates))

            for spec in candidates:
                skip_reason = self._context_window_skip_reason(spec, known_insufficient_window)
                if skip_reason is not None:
                    logger.warning(
                        "provider_skipped_context_window_too_small",
                        role=role,
                        provider=spec.name,
                        model=spec.model,
                        max_context_tokens=spec.max_context_tokens,
                        known_insufficient_window=known_insufficient_window,
                    )
                    skip_err = ProviderError(skip_reason, context_length_exceeded=True)
                    attempts.append((spec, skip_err))
                    continue

                last_exc: Exception | None = None
                for attempt in range(self._retry.api_max_retries + 1):
                    call_start = time.monotonic()
                    try:
                        result = await invoke(spec, request)
                    except ProviderError as exc:
                        self._record_call_duration(spec, role, time.monotonic() - call_start)
                        last_exc = exc
                        if exc.context_length_exceeded and spec.max_context_tokens is not None:
                            known_insufficient_window = max(
                                known_insufficient_window or 0, spec.max_context_tokens
                            )
                        retryable = self._retry.is_retryable(exc)
                        logger.warning(
                            "provider_attempt_failed",
                            role=role,
                            provider=spec.name,
                            model=spec.model,
                            attempt=attempt,
                            status_code=exc.status_code,
                            retryable=retryable,
                            context_length_exceeded=exc.context_length_exceeded,
                            error=str(exc),
                        )
                        if not retryable or attempt >= self._retry.api_max_retries:
                            break
                        await self._sleep(self._retry.backoff_base_seconds * (2**attempt))
                        continue
                    except Exception as exc:  # noqa: BLE001 - non-provider failure: try next spec
                        self._record_call_duration(spec, role, time.monotonic() - call_start)
                        last_exc = exc
                        logger.warning(
                            "provider_attempt_error",
                            role=role,
                            provider=spec.name,
                            model=spec.model,
                            error=str(exc),
                        )
                        break
                    else:
                        self._record_call_duration(spec, role, time.monotonic() - call_start)
                        logger.info(
                            "provider_complete_ok",
                            role=role,
                            provider=spec.name,
                            model=spec.model,
                            attempt=attempt,
                        )
                        span.set_attribute("provider.chosen", spec.name)
                        return result

                assert last_exc is not None  # loop only exits the try via break/exhaust
                attempts.append((spec, last_exc))

            span.set_attribute("provider.failed", True)

        logger.error("all_providers_failed", role=role, tried=len(attempts))
        raise AllProvidersFailedError(role, attempts)

    @staticmethod
    def _context_window_skip_reason(
        spec: ProviderSpec, known_insufficient_window: int | None
    ) -> str | None:
        """MET-655 remainder: a reason string if ``spec`` is guaranteed to
        fail the same way a prior candidate already failed -- its context
        window is no larger than one that already rejected this exact
        request for being too long. ``None`` when there's no such evidence
        yet, or ``spec`` didn't declare a window (behavior unchanged)."""
        if known_insufficient_window is None or spec.max_context_tokens is None:
            return None
        if spec.max_context_tokens > known_insufficient_window:
            return None
        return (
            f"skipped {spec.name}:{spec.model} -- its context window "
            f"({spec.max_context_tokens} tokens) is no larger than a window "
            f"that already rejected this request as too long "
            f"({known_insufficient_window} tokens)"
        )

    def _record_call_duration(self, spec: ProviderSpec, role: Role, duration: float) -> None:
        """Best-effort per-attempt latency metric (production-harness audit
        follow-up — this pipeline previously logged retries/outcomes with no
        timing field anywhere)."""
        if self._metrics is None:
            return
        try:
            self._metrics.record_harness_provider_call(spec.name, spec.model, role, duration)
        except Exception:  # noqa: BLE001 - metrics must never break a provider call
            pass

    async def stream_complete(
        self, role: Role, request: Any, stream_invoke: StreamInvoke
    ) -> AsyncIterator[str]:
        """Stream a role's response with retry + fallback *before the first token*.

        Each candidate is tried like :meth:`complete`, but the transport yields
        text deltas. Retries and provider fallback happen only until the first
        delta arrives; once a token is yielded the provider is committed and a
        mid-stream failure is surfaced (no mid-stream failover — you can't
        un-send tokens). Raises :class:`AllProvidersFailedError` if no provider
        produces a first token.
        """
        candidates = self.resolve(role)
        attempts: list[tuple[ProviderSpec, Exception]] = []
        known_insufficient_window: int | None = None

        for spec in candidates:
            skip_reason = self._context_window_skip_reason(spec, known_insufficient_window)
            if skip_reason is not None:
                logger.warning(
                    "provider_skipped_context_window_too_small",
                    role=role,
                    provider=spec.name,
                    model=spec.model,
                )
                attempts.append((spec, ProviderError(skip_reason, context_length_exceeded=True)))
                continue

            last_exc: Exception | None = None
            for attempt in range(self._retry.api_max_retries + 1):
                agen = stream_invoke(spec, request)
                try:
                    first = await agen.__anext__()
                except StopAsyncIteration:
                    return  # empty but successful stream
                except ProviderError as exc:
                    last_exc = exc
                    if exc.context_length_exceeded and spec.max_context_tokens is not None:
                        known_insufficient_window = max(
                            known_insufficient_window or 0, spec.max_context_tokens
                        )
                    if not self._retry.is_retryable(exc) or attempt >= self._retry.api_max_retries:
                        break
                    await self._sleep(self._retry.backoff_base_seconds * (2**attempt))
                    continue
                except Exception as exc:  # noqa: BLE001 - non-provider failure: try next spec
                    last_exc = exc
                    break
                # First token obtained → commit to this provider, no more failover.
                logger.info("provider_stream_ok", role=role, provider=spec.name, model=spec.model)
                yield first
                async for delta in agen:
                    yield delta
                return

            assert last_exc is not None
            attempts.append((spec, last_exc))

        logger.error("all_providers_failed_stream", role=role, tried=len(attempts))
        raise AllProvidersFailedError(role, attempts)

    async def stream_events_complete(
        self, role: Role, request: Any, stream_events: StreamEvents
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a role's response as structured events (MET-591).

        Same contract as :meth:`stream_complete` — retry + provider fallback
        happen only until the FIRST event arrives, then the provider is
        committed. Adapters for unsupported families raise a non-retryable
        error before their first event, so those candidates are skipped and
        callers fall back to the non-streaming invoke when every candidate
        declines (:class:`AllProvidersFailedError`).
        """
        candidates = self.resolve(role)
        attempts: list[tuple[ProviderSpec, Exception]] = []
        known_insufficient_window: int | None = None

        for spec in candidates:
            skip_reason = self._context_window_skip_reason(spec, known_insufficient_window)
            if skip_reason is not None:
                logger.warning(
                    "provider_skipped_context_window_too_small",
                    role=role,
                    provider=spec.name,
                    model=spec.model,
                )
                attempts.append((spec, ProviderError(skip_reason, context_length_exceeded=True)))
                continue

            last_exc: Exception | None = None
            for attempt in range(self._retry.api_max_retries + 1):
                agen = stream_events(spec, request)
                try:
                    first = await agen.__anext__()
                except StopAsyncIteration:
                    return  # empty but successful stream
                except ProviderError as exc:
                    last_exc = exc
                    if exc.context_length_exceeded and spec.max_context_tokens is not None:
                        known_insufficient_window = max(
                            known_insufficient_window or 0, spec.max_context_tokens
                        )
                    if not self._retry.is_retryable(exc) or attempt >= self._retry.api_max_retries:
                        break
                    await self._sleep(self._retry.backoff_base_seconds * (2**attempt))
                    continue
                except Exception as exc:  # noqa: BLE001 - non-provider failure: try next spec
                    last_exc = exc
                    break
                logger.info(
                    "provider_stream_events_ok", role=role, provider=spec.name, model=spec.model
                )
                yield first
                async for event in agen:
                    yield event
                return

            assert last_exc is not None
            attempts.append((spec, last_exc))

        logger.error("all_providers_failed_stream_events", role=role, tried=len(attempts))
        raise AllProvidersFailedError(role, attempts)
