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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("orchestrator.harness.providers.pipeline")

# One of: "planner" | "generator" | "evaluator" | "vision" | "compression".
Role = str

# Injected transport: perform one model call for a resolved provider.
Invoke = Callable[["ProviderSpec", Any], Awaitable[Any]]
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
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


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
    weight: int = 1
    extra: Mapping[str, str] = field(default_factory=dict)


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
    ) -> None:
        self._slots = slots
        self._retry = retry_policy or RetryPolicy()
        self._sleep = sleep

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

        with tracer.start_as_current_span("provider.complete") as span:
            span.set_attribute("provider.role", role)
            span.set_attribute("provider.candidate_count", len(candidates))

            for spec in candidates:
                last_exc: Exception | None = None
                for attempt in range(self._retry.api_max_retries + 1):
                    try:
                        result = await invoke(spec, request)
                    except ProviderError as exc:
                        last_exc = exc
                        retryable = self._retry.is_retryable(exc)
                        logger.warning(
                            "provider_attempt_failed",
                            role=role,
                            provider=spec.name,
                            model=spec.model,
                            attempt=attempt,
                            status_code=exc.status_code,
                            retryable=retryable,
                            error=str(exc),
                        )
                        if not retryable or attempt >= self._retry.api_max_retries:
                            break
                        await self._sleep(self._retry.backoff_base_seconds * (2**attempt))
                        continue
                    except Exception as exc:  # noqa: BLE001 - non-provider failure: try next spec
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
