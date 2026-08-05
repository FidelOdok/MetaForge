"""Resolved-provider path selection for chat turns (MET-575). Network-free.

Live-caught on fidel-dev by the MET-570 evals: the native-vs-ReAct decision
consulted only the per-turn arg and ``METAFORGE_LLM_PROVIDER``, while the
provider chain honored the auth store's durable ``selection``. With env
``openrouter`` (OpenAI family → native path) and selection ``openai-codex``
(adapter cannot forward native tool schemas), every turn sent tools the
serving adapter dropped — the model saw zero tools, refused or fabricated
twin writes, and the context meter honestly counted 87 registered. The path
decision and the invoke chain must resolve the SAME provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import api_gateway.chat.harness_backend as hb
from orchestrator.harness.providers import CredentialStore, ProviderSpec
from orchestrator.harness.providers.auth_store import Selection

_REACT_MARKER = "Reply ONLY with a JSON"


class _FakeAuthStore:
    """AuthStore stand-in with a class-level programmable selection."""

    selection: Selection | None = None

    def __init__(self, *args: object, **kwargs: object) -> None: ...

    def get_selection(self) -> Selection | None:
        return type(self).selection

    def get_credential(self, name: str) -> None:
        return None

    def configured_providers(self) -> set[str]:
        return set()


@pytest.fixture()
def fake_store(monkeypatch: pytest.MonkeyPatch) -> type[_FakeAuthStore]:
    monkeypatch.setattr(hb, "AuthStore", _FakeAuthStore)
    _FakeAuthStore.selection = None
    return _FakeAuthStore


# --- precedence ---------------------------------------------------------------
def test_arg_beats_selection_and_env(
    fake_store: type[_FakeAuthStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.selection = Selection("openai-codex", "gpt-5.5")
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    assert hb.resolve_active_provider("gemini") == "gemini"


def test_selection_beats_env(
    fake_store: type[_FakeAuthStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.selection = Selection("openai-codex", "gpt-5.5")
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    assert hb.resolve_active_provider(None) == "openai-codex"


def test_env_when_no_selection(
    fake_store: type[_FakeAuthStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    assert hb.resolve_active_provider(None) == "openrouter"


def test_default_is_anthropic(
    fake_store: type[_FakeAuthStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("METAFORGE_LLM_PROVIDER", raising=False)
    assert hb.resolve_active_provider(None) == "anthropic"


# --- the live defect: path must follow the resolved provider ---------------------
@pytest.mark.asyncio
async def test_codex_selection_routes_turn_to_react_path(
    fake_store: type[_FakeAuthStore], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env says openrouter (native family) but the store selected codex —
    the turn must take the ReAct path (tools travel as text), never the
    native path whose schemas the codex adapter drops."""
    fake_store.selection = Selection("openai-codex", "gpt-5.5")
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("METAFORGE_NATIVE_TOOLS", raising=False)
    seen: dict = {}

    async def invoke(spec: ProviderSpec, request: dict) -> dict:
        seen["system"] = request.get("system", "")
        seen["has_native_tools"] = bool(request.get("tools"))
        seen["provider"] = spec.name
        return {"text": '{"thought": "t", "final": "ok"}', "model": spec.model}

    out = await hb.run_chat_turn(
        "hi", invoke=invoke, credentials=CredentialStore(tmp_path / "c.json")
    )
    assert out == "ok"
    assert seen["provider"] == "openai-codex"  # selection served the turn
    assert _REACT_MARKER in seen["system"]  # …on the ReAct protocol
    assert not seen["has_native_tools"]  # …with no native schemas to drop


@pytest.mark.asyncio
async def test_native_family_without_selection_keeps_native_path(
    fake_store: type[_FakeAuthStore], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("METAFORGE_NATIVE_TOOLS", raising=False)
    seen: dict = {}

    async def invoke(spec: ProviderSpec, request: dict) -> dict:
        seen["system"] = request.get("system", "")
        return {"text": "ok", "model": spec.model}

    out = await hb.run_chat_turn(
        "hi", invoke=invoke, credentials=CredentialStore(tmp_path / "c.json")
    )
    assert out == "ok"
    assert _REACT_MARKER not in seen["system"]  # native path retained
