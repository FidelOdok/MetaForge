"""Unit tests for the harness provider pipeline (MET-547, Phase 1)."""

from __future__ import annotations

import pytest

from observability.metrics import MetricsCollector
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


class _FakeMetrics(MetricsCollector):
    """Records calls without a real OTel meter (production-harness audit
    follow-up: this pipeline previously had no duration field anywhere)."""

    def __init__(self) -> None:
        super().__init__()  # no meter -- the base class's own no-op instruments
        self.calls: list[tuple[str, str, str]] = []

    def record_harness_provider_call(
        self, provider: str, model: str, role: str, duration: float
    ) -> None:
        self.calls.append((provider, model, role))


@pytest.mark.asyncio
async def test_records_provider_call_duration_on_success() -> None:
    metrics = _FakeMetrics()
    slots = RoleModelSlots(slots={"generator": [PRIMARY]})
    pipeline = ProviderPipeline(slots, metrics=metrics)

    async def invoke(spec: ProviderSpec, request: object) -> str:
        return "ok"

    await pipeline.complete("generator", {}, invoke)
    assert metrics.calls == [("anthropic", "claude-opus-4-8", "generator")]


@pytest.mark.asyncio
async def test_records_provider_call_duration_per_attempt_including_failures() -> None:
    metrics = _FakeMetrics()
    pipeline, _ = _pipeline(PRIMARY, FALLBACK, retries=0)
    pipeline._metrics = metrics  # noqa: SLF001 - _pipeline() helper doesn't take metrics

    calls = {"n": 0}

    async def invoke(spec: ProviderSpec, request: object) -> str:
        calls["n"] += 1
        if spec is PRIMARY:
            raise ProviderError("down", status_code=500, retryable=False)
        return "ok"

    await pipeline.complete("generator", {}, invoke)
    assert metrics.calls == [
        ("anthropic", "claude-opus-4-8", "generator"),
        ("openai", "gpt-5", "generator"),
    ]


def test_resolve_returns_ordered_candidates() -> None:
    pipeline, _ = _pipeline(PRIMARY, FALLBACK)
    assert pipeline.resolve("generator") == [PRIMARY, FALLBACK]


def test_unknown_role_raises() -> None:
    pipeline, _ = _pipeline(PRIMARY)
    with pytest.raises(KeyError, match="evaluator"):
        pipeline.resolve("evaluator")


# ---------------------------------------------------------------------------
# MET-655 remainder: context-window-aware fallback. Live-caught: openai-codex
# rejected a 137k-token request as too long, and the pipeline blindly fell
# through to openrouter/gpt-4o (128k window) -- smaller than the window that
# JUST rejected the same request, guaranteed to fail identically.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_fallback_whose_window_is_no_larger_than_a_failed_one() -> None:
    big = ProviderSpec(name="openai-codex", model="gpt-5.5", max_context_tokens=100_000)
    smaller = ProviderSpec(name="openrouter", model="gpt-4o", max_context_tokens=80_000)
    pipeline, _ = _pipeline(big, smaller, retries=0)

    calls: list[str] = []

    async def invoke(spec: ProviderSpec, request: object) -> str:
        calls.append(spec.name)
        raise ProviderError("prompt exceeds the context window", context_length_exceeded=True)

    with pytest.raises(AllProvidersFailedError) as excinfo:
        await pipeline.complete("generator", {}, invoke)

    # The smaller fallback was never actually invoked -- only the first was.
    assert calls == ["openai-codex"]
    skipped_spec, skipped_err = excinfo.value.attempts[1]
    assert skipped_spec is smaller
    assert isinstance(skipped_err, ProviderError)
    assert skipped_err.context_length_exceeded is True


@pytest.mark.asyncio
async def test_still_tries_a_fallback_with_a_larger_window() -> None:
    small = ProviderSpec(name="openai-codex", model="gpt-5.5", max_context_tokens=100_000)
    bigger = ProviderSpec(name="anthropic", model="claude-opus-4-8", max_context_tokens=200_000)
    pipeline, _ = _pipeline(small, bigger, retries=0)

    calls: list[str] = []

    async def invoke(spec: ProviderSpec, request: object) -> str:
        calls.append(spec.name)
        if spec is small:
            raise ProviderError("context_length_exceeded", context_length_exceeded=True)
        return "ok"

    result = await pipeline.complete("generator", {}, invoke)
    assert result == "ok"
    assert calls == ["openai-codex", "anthropic"]  # the larger-window fallback WAS tried


@pytest.mark.asyncio
async def test_unset_max_context_tokens_never_skips_anything() -> None:
    """Default (no caller opted in): behavior is completely unchanged."""
    pipeline, _ = _pipeline(PRIMARY, FALLBACK, retries=0)
    calls: list[str] = []

    async def invoke(spec: ProviderSpec, request: object) -> str:
        calls.append(spec.name)
        if spec is PRIMARY:
            raise ProviderError("context length exceeded", context_length_exceeded=True)
        return "ok"

    result = await pipeline.complete("generator", {}, invoke)
    assert result == "ok"
    assert calls == ["anthropic", "openai"]  # fallback was invoked, not skipped


def test_context_length_marker_is_not_retryable() -> None:
    """adapters._classify_error: a context-length rejection must never be
    retried against the SAME provider -- the same oversized request will
    fail again identically."""
    from orchestrator.harness.providers.adapters import _classify_error

    class _FakeSdkError(Exception):
        status_code = 400

    err = _classify_error(_FakeSdkError("This model's maximum context length is 128000 tokens"))
    assert err.context_length_exceeded is True
    assert err.retryable is False


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
