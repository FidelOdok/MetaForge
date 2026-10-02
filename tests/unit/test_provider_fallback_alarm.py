"""Provider/model mismatch, fallback alarm and proposal provenance (FORGE-468).

The live bug: fidel-dev's primary was ``openai-codex`` + ``claude-opus-4-8``,
a pair Codex cannot serve. Every call 400'd and the pipeline quietly fell back
to OpenRouter, and the flow proposals it produced said nothing about which
model wrote them. Each test here pins one of the three things that hid it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from observability.metrics import MetricsCollector
from orchestrator.harness.providers import (
    InvalidModelError,
    ProviderError,
    ProviderPipeline,
    ProviderSpec,
    RetryPolicy,
    RoleModelSlots,
    load_provider_config,
    model_family_mismatch,
    validate_model,
)
from orchestrator.harness.providers.config import ConfigError
from orchestrator.harness.providers.provenance import (
    ServedBy,
    capture_served,
    last_fallback,
    note_served,
    reset_fallback_state,
)


@pytest.fixture(autouse=True)
def _clean_fallback_state() -> None:
    reset_fallback_state()


class _FakeMetrics(MetricsCollector):
    def __init__(self) -> None:
        super().__init__()
        self.fallbacks: list[tuple[str, str, str, str]] = []

    def record_harness_provider_call(
        self, provider: str, model: str, role: str, duration: float
    ) -> None:
        pass

    def record_harness_provider_fallback(
        self, primary: str, fallback: str, role: str, reason: str
    ) -> None:
        self.fallbacks.append((primary, fallback, role, reason))


# ── 1. a pair the family cannot serve is flagged before any call ─────────


class TestFamilyMismatch:
    @pytest.mark.parametrize(
        ("provider", "model"),
        [
            ("openai-codex", "claude-opus-4-8"),  # the live fidel-dev pair
            ("codex", "claude-sonnet-4-5"),  # alias resolves the same way
            ("anthropic", "gpt-4o"),
            ("anthropic", "o3-mini"),
            ("gemini", "claude-opus-4-8"),
        ],
    )
    def test_a_cross_vendor_pair_is_rejected(self, provider: str, model: str) -> None:
        reason = model_family_mismatch(provider, model)
        assert reason is not None and model in reason
        with pytest.raises(InvalidModelError):
            validate_model(provider, model)

    @pytest.mark.parametrize(
        ("provider", "model"),
        [
            ("openai-codex", "gpt-5.5"),
            ("openai-codex", "codex-mini-latest"),  # vendor not plain from prefix: allowed
            ("anthropic", "claude-opus-4-8"),
            ("gemini", "gemini-2.5-pro"),
            # Multi-vendor gateways are never judged.
            ("openrouter", "anthropic/claude-opus-4"),
            ("openrouter", "claude-opus-4-8"),
            ("bedrock", "anthropic.claude-3-5-sonnet"),
        ],
    )
    def test_a_servable_or_unknowable_pair_passes(self, provider: str, model: str) -> None:
        assert model_family_mismatch(provider, model) is None

    def test_a_config_file_with_a_mismatched_pair_fails_at_load(self) -> None:
        with pytest.raises(ConfigError, match=r"roles\['generator'\]\[0\].*claude-opus-4-8"):
            load_provider_config(
                {"roles": {"generator": [{"provider": "openai-codex", "model": "claude-opus-4-8"}]}}
            )

    def test_provider_config_drops_the_unservable_primary_and_raises_the_alarm(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The fidel-dev shape: store selects openai-codex with no model, env
        is openrouter + openai/gpt-4o, so the primary got the built-in
        claude-* default. It must not be sent, and dropping it is an alarm."""
        from api_gateway.chat.harness_backend import provider_config_from_env
        from orchestrator.harness.providers.auth_store import AuthStore

        monkeypatch.setenv("METAFORGE_HARNESS_AUTH_PATH", str(tmp_path / "auth.json"))
        monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
        monkeypatch.setenv("METAFORGE_LLM_MODEL", "openai/gpt-4o")
        monkeypatch.delenv("METAFORGE_LLM_BASE_URL", raising=False)
        AuthStore().set_selection("openai-codex", None)
        metrics = _FakeMetrics()

        with capture_logs() as logs:
            cfg = provider_config_from_env(metrics=metrics)

        candidates = cfg.slots.candidates("generator")
        assert [(c.name, c.model) for c in candidates] == [("openrouter", "openai/gpt-4o")]
        events = {e["event"] for e in logs}
        assert "harness_provider_model_mismatch" in events
        assert "harness_provider_fallback" in events
        assert metrics.fallbacks == [("openai-codex", "openrouter", "generator", "model_mismatch")]
        event, count = last_fallback()
        assert count == 1 and event is not None
        assert (event.primary, event.primary_model) == ("openai-codex", "claude-opus-4-8")
        assert event.reason == "model_mismatch"

    def test_no_usable_candidate_is_an_error_not_a_silent_call(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from api_gateway.chat.harness_backend import provider_config_from_env

        monkeypatch.setenv("METAFORGE_HARNESS_AUTH_PATH", str(tmp_path / "auth.json"))
        monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("METAFORGE_LLM_MODEL", "gpt-4o")
        with pytest.raises(InvalidModelError, match="no usable provider"):
            provider_config_from_env()


# ── 2. a fallback emits the metric and the warning ───────────────────────

PRIMARY = ProviderSpec(name="openai-codex", model="gpt-5.5")
FALLBACK = ProviderSpec(name="openrouter", model="openai/gpt-4o")


def _pipeline(metrics: MetricsCollector | None) -> ProviderPipeline:
    async def no_sleep(_: float) -> None:
        return None

    return ProviderPipeline(
        RoleModelSlots(slots={"generator": [PRIMARY, FALLBACK]}),
        retry_policy=RetryPolicy(api_max_retries=0),
        sleep=no_sleep,
        metrics=metrics,
    )


async def _primary_400s(spec: ProviderSpec, request: Any) -> str:
    if spec is PRIMARY:
        raise ProviderError("400 model not supported", status_code=400)
    return "answer"


class TestFallbackIsAnAlarm:
    @pytest.mark.asyncio
    async def test_falling_back_records_metric_warning_and_status(self) -> None:
        metrics = _FakeMetrics()
        with capture_logs() as logs, capture_served() as served:
            assert await _pipeline(metrics).complete("generator", {}, _primary_400s) == "answer"

        warning = next(e for e in logs if e["event"] == "harness_provider_fallback")
        assert warning["log_level"] == "warning"
        assert warning["primary"] == "openai-codex"
        assert warning["fallback"] == "openrouter"
        assert "400 model not supported" in warning["primary_error"]
        assert metrics.fallbacks == [("openai-codex", "openrouter", "generator", "call_failed")]

        event, count = last_fallback()
        assert count == 1 and event is not None and event.fallback_model == "openai/gpt-4o"
        last = served.last()
        assert last is not None
        assert (last.provider, last.model) == ("openrouter", "openai/gpt-4o")
        assert last.fell_back_from == "openai-codex:gpt-5.5"

    @pytest.mark.asyncio
    async def test_the_primary_answering_is_not_a_fallback(self) -> None:
        metrics = _FakeMetrics()

        async def ok(spec: ProviderSpec, request: Any) -> str:
            return "answer"

        with capture_served() as served:
            await _pipeline(metrics).complete("generator", {}, ok)
        assert metrics.fallbacks == []
        assert last_fallback() == (None, 0)
        last = served.last()
        assert last is not None and last.provider == "openai-codex" and last.fell_back_from is None

    @pytest.mark.asyncio
    async def test_a_streamed_fallback_is_an_alarm_too(self) -> None:
        metrics = _FakeMetrics()

        async def stream(spec: ProviderSpec, request: Any):  # type: ignore[no-untyped-def]
            if spec is PRIMARY:
                raise ProviderError("400", status_code=400)
            yield "tok"

        out = [t async for t in _pipeline(metrics).stream_complete("generator", {}, stream)]
        assert out == ["tok"]
        assert metrics.fallbacks == [("openai-codex", "openrouter", "generator", "call_failed")]

    @pytest.mark.asyncio
    async def test_a_by_design_streaming_decline_is_recorded_but_not_an_alarm(self) -> None:
        """Codex has no event-streaming adapter, so stream_events_complete
        always skips it. That must not fire the alert or mask a real failure
        in the status route, though provenance still says who answered."""
        from orchestrator.harness.providers.adapters import StreamingUnsupported

        metrics = _FakeMetrics()

        async def events(spec: ProviderSpec, request: Any):  # type: ignore[no-untyped-def]
            if spec is PRIMARY:
                raise StreamingUnsupported("openai-codex")
            yield {"type": "text_delta", "text": "tok"}

        with capture_logs() as logs, capture_served() as served:
            pipeline = _pipeline(metrics)
            out = [e async for e in pipeline.stream_events_complete("generator", {}, events)]
        assert len(out) == 1
        assert metrics.fallbacks == [
            ("openai-codex", "openrouter", "generator", "capability_unsupported")
        ]
        fallback_log = next(e for e in logs if e["event"] == "harness_provider_fallback")
        assert fallback_log["log_level"] == "info"
        assert last_fallback() == (None, 0)
        last = served.last()
        assert last is not None and last.fell_back_from == "openai-codex:gpt-5.5"

    def test_the_harness_status_route_reports_the_last_fallback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from api_gateway.server import create_app
        from orchestrator.harness.providers.provenance import record_fallback

        monkeypatch.setenv("METAFORGE_HARNESS_AUTH_PATH", str(tmp_path / "auth.json"))
        client = TestClient(create_app())
        assert client.get("/v1/harness/providers").json()["last_fallback"] is None

        record_fallback(
            role="generator",
            primary="openai-codex",
            primary_model="claude-opus-4-8",
            fallback="openrouter",
            fallback_model="openai/gpt-4o",
            error="400 not supported",
        )
        body = client.get("/v1/harness/providers").json()
        assert body["fallback_count"] == 1
        assert body["last_fallback"]["primary"] == "openai-codex"
        assert body["last_fallback"]["fallback_model"] == "openai/gpt-4o"
        assert body["last_fallback"]["error"] == "400 not supported"


# ── 3. a proposal carries the provider/model that produced it ────────────

_CONTEXT = {
    "manufacturingContext": {
        "route": "in_house",
        "processes": ["woodworking"],
        "machines": ["table saw"],
        "stockMaterials": ["18 mm birch plywood"],
    },
    "targetMaturity": "physically_validated",
    "loadsAndUse": "40 kg of dishes per shelf, indoors",
}


class TestProposalProvenance:
    def test_propose_response_and_held_approval_name_the_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi.testclient import TestClient

        import api_gateway.chat.harness_backend as backend
        import api_gateway.design_flows.generate as gen
        from api_gateway.chat.tool_approvals import get_approval_store
        from api_gateway.server import create_app

        async def no_questions(*args: Any, **kwargs: Any) -> list[Any]:
            return []

        async def fake_turn(prompt: str, **kwargs: Any) -> str:
            # What the real pipeline records when a fallback answered.
            note_served(
                ServedBy(
                    provider="openrouter",
                    model="openai/gpt-4o",
                    role="generator",
                    fell_back_from="openai-codex:claude-opus-4-8",
                )
            )
            return json.dumps({"template": "hardware_v1", "rationale": "fits", "operations": []})

        monkeypatch.setattr(gen, "suggest_extra_questions", no_questions)
        monkeypatch.setattr(backend, "run_chat_turn", fake_turn)

        client = TestClient(create_app())
        response = client.post(
            "/v1/design-flows/propose", json={"intent": "a kitchen cabinet", **_CONTEXT}
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["generatedBy"] == {
            "provider": "openrouter",
            "model": "openai/gpt-4o",
            "fellBackFrom": "openai-codex:claude-opus-4-8",
        }
        held = get_approval_store().get(body["approvalId"]).request
        assert held["generated_by"] == {
            "provider": "openrouter",
            "model": "openai/gpt-4o",
            "fell_back_from": "openai-codex:claude-opus-4-8",
        }

    @pytest.mark.asyncio
    async def test_a_dropped_primary_shows_up_as_the_fallback_source(self) -> None:
        """When the primary is dropped at config time the pipeline never sees
        it, so the capture has to fold it in."""
        from orchestrator.harness.providers.provenance import record_fallback

        with capture_served() as served:
            record_fallback(
                role="generator",
                primary="openai-codex",
                primary_model="claude-opus-4-8",
                fallback="openrouter",
                fallback_model="openai/gpt-4o",
                error="mismatch",
                reason="model_mismatch",
            )
            note_served(ServedBy(provider="openrouter", model="openai/gpt-4o", role="generator"))
        last = served.last()
        assert last is not None and last.fell_back_from == "openai-codex:claude-opus-4-8"
