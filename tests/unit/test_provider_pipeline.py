"""Unit tests for the harness provider pipeline (MET-547, Phase 1)."""

from __future__ import annotations

import pytest

from orchestrator.harness.providers import (
    AllProvidersFailedError,
    ProviderError,
    ProviderPipeline,
    ProviderSpec,
    RetryPolicy,
    RoleModelSlots,
)

PRIMARY = ProviderSpec(name="anthropic", model="claude-opus-4-8")
FALLBACK = ProviderSpec(name="openai", model="gpt-5")


def _pipeline(*specs: ProviderSpec, retries: int = 2) -> tuple[ProviderPipeline, list[float]]:
    """A pipeline for role 'generator' with a recording fake sleep."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    slots = RoleModelSlots(slots={"generator": list(specs)})
    policy = RetryPolicy(api_max_retries=retries, backoff_base_seconds=0.5)
    return ProviderPipeline(slots, retry_policy=policy, sleep=fake_sleep), slept


def test_resolve_returns_ordered_candidates() -> None:
    pipeline, _ = _pipeline(PRIMARY, FALLBACK)
    assert pipeline.resolve("generator") == [PRIMARY, FALLBACK]


def test_unknown_role_raises() -> None:
    pipeline, _ = _pipeline(PRIMARY)
    with pytest.raises(KeyError, match="evaluator"):
        pipeline.resolve("evaluator")


@pytest.mark.asyncio
async def test_first_provider_success() -> None:
    pipeline, slept = _pipeline(PRIMARY, FALLBACK)
    calls: list[str] = []

    async def invoke(spec: ProviderSpec, request: object) -> str:
        calls.append(spec.name)
        return f"ok:{spec.name}"

    result = await pipeline.complete("generator", {"prompt": "hi"}, invoke)
    assert result == "ok:anthropic"
    assert calls == ["anthropic"]  # fallback never touched
    assert slept == []


@pytest.mark.asyncio
async def test_retries_then_succeeds_on_same_provider() -> None:
    pipeline, slept = _pipeline(PRIMARY, retries=2)
    attempts = {"n": 0}

    async def invoke(spec: ProviderSpec, request: object) -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ProviderError("rate limited", status_code=429)
        return "ok"

    result = await pipeline.complete("generator", {}, invoke)
    assert result == "ok"
    assert attempts["n"] == 3  # 1 initial + 2 retries
    assert slept == [0.5, 1.0]  # exponential backoff base*2**attempt


@pytest.mark.asyncio
async def test_falls_over_to_next_provider_after_retries_exhausted() -> None:
    pipeline, _ = _pipeline(PRIMARY, FALLBACK, retries=1)
    calls: list[str] = []

    async def invoke(spec: ProviderSpec, request: object) -> str:
        calls.append(spec.name)
        if spec.name == "anthropic":
            raise ProviderError("overloaded", status_code=503)
        return "ok:openai"

    result = await pipeline.complete("generator", {}, invoke)
    assert result == "ok:openai"
    # primary tried twice (1 + 1 retry), then fallback once
    assert calls == ["anthropic", "anthropic", "openai"]


@pytest.mark.asyncio
async def test_non_retryable_error_skips_to_next_without_retry() -> None:
    pipeline, slept = _pipeline(PRIMARY, FALLBACK, retries=3)
    calls: list[str] = []

    async def invoke(spec: ProviderSpec, request: object) -> str:
        calls.append(spec.name)
        if spec.name == "anthropic":
            raise ProviderError("bad request", status_code=400)  # not retryable
        return "ok:openai"

    result = await pipeline.complete("generator", {}, invoke)
    assert result == "ok:openai"
    assert calls == ["anthropic", "openai"]  # primary tried once only
    assert slept == []  # no backoff for a non-retryable failure


@pytest.mark.asyncio
async def test_all_providers_failed_raises_with_attempts() -> None:
    pipeline, _ = _pipeline(PRIMARY, FALLBACK, retries=0)

    async def invoke(spec: ProviderSpec, request: object) -> str:
        raise ProviderError(f"{spec.name} down", status_code=500)

    with pytest.raises(AllProvidersFailedError) as excinfo:
        await pipeline.complete("generator", {}, invoke)

    err = excinfo.value
    assert err.role == "generator"
    assert [spec.name for spec, _ in err.attempts] == ["anthropic", "openai"]


@pytest.mark.asyncio
async def test_non_provider_exception_is_captured_as_attempt() -> None:
    pipeline, _ = _pipeline(PRIMARY, retries=2)

    async def invoke(spec: ProviderSpec, request: object) -> str:
        raise ValueError("boom")  # not a ProviderError

    with pytest.raises(AllProvidersFailedError) as excinfo:
        await pipeline.complete("generator", {}, invoke)
    # non-provider error is not retried: exactly one attempt recorded
    assert len(excinfo.value.attempts) == 1
    assert isinstance(excinfo.value.attempts[0][1], ValueError)
