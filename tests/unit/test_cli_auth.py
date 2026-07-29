"""``forge auth`` CLI handlers (MET-10). No network — a fake client records calls
and prompts are injected."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cli.forge_cli import auth


class FakeClient:
    def __init__(self, providers: list[dict[str, Any]], active: str | None = None) -> None:
        self._providers = providers
        self._active = active
        self.calls: list[tuple[str, tuple, dict]] = []

    def list_harness_providers(self) -> dict[str, Any]:
        return {"active_provider": self._active, "active_model": None, "providers": self._providers}

    def list_harness_models(self, provider: str) -> dict[str, Any]:
        return {"provider": provider, "models": [], "source": "none"}

    def set_credential(self, provider: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("set_credential", (provider,), kwargs))
        return {"ok": True}

    def set_selection(self, provider: str, model: str | None = None) -> dict[str, Any]:
        self.calls.append(("set_selection", (provider, model), {}))
        return {"ok": True}

    def delete_credential(self, provider: str) -> dict[str, Any]:
        self.calls.append(("delete_credential", (provider,), {}))
        return {"ok": True}


_PROVIDERS = [
    {"id": "openai", "family": "openai", "configured": True, "base_url": None},
    {"id": "openai-codex", "family": "codex", "configured": False, "base_url": None},
]


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep _mirror_local_config off the real ~/.forge/config.json.
    monkeypatch.setenv("FORGE_CONFIG", str(tmp_path / "config.json"))


def _scripted(answers: list[str]):
    it = iter(answers)
    return lambda _prompt: next(it, "")


def test_cmd_list_marks_active_and_configured() -> None:
    rows = auth._cmd_list(FakeClient(_PROVIDERS, active="openai"))
    by = {r["provider"]: r for r in rows}
    assert by["openai"]["configured"] == "✓" and by["openai"]["active"] == "✓"
    assert by["openai-codex"]["configured"] == "" and by["openai-codex"]["active"] == ""


def test_login_api_key_stores_and_activates() -> None:
    client = FakeClient(_PROVIDERS)
    args = SimpleNamespace(
        provider="openai",
        method=None,
        model=None,
        no_activate=False,
        mode="auto",
        port=1455,
        no_browser=False,
    )
    auth._cmd_login(
        client,
        args,
        input_fn=_scripted(["", "gpt-4o"]),  # base_url (blank), then model free-text
        getpass_fn=lambda _p: "sk-secret",
    )
    kinds = [c[0] for c in client.calls]
    assert "set_credential" in kinds and "set_selection" in kinds
    cred = next(c for c in client.calls if c[0] == "set_credential")
    assert (
        cred[1] == ("openai",)
        and cred[2]["method"] == "api_key"
        and cred[2]["api_key"] == "sk-secret"
    )
    sel = next(c for c in client.calls if c[0] == "set_selection")
    assert sel[1] == ("openai", "gpt-4o")


def test_login_oauth_pushes_tokens() -> None:
    client = FakeClient(_PROVIDERS)
    args = SimpleNamespace(
        provider="openai-codex",
        method=None,
        model=None,
        no_activate=True,
        mode="auto",
        port=1455,
        no_browser=False,
    )
    tokens = {"tokens": {"access_token": "a", "refresh_token": "r"}}
    auth._cmd_login(
        client,
        args,
        input_fn=_scripted([]),
        getpass_fn=lambda _p: "unused",
        oauth_login=lambda _a: tokens,
    )
    cred = next(c for c in client.calls if c[0] == "set_credential")
    assert cred[1] == ("openai-codex",)
    assert cred[2]["method"] == "oauth" and cred[2]["tokens"] == tokens
    # --no-activate → no selection set.
    assert not any(c[0] == "set_selection" for c in client.calls)


def test_use_and_logout() -> None:
    client = FakeClient(_PROVIDERS)
    auth._cmd_use(client, SimpleNamespace(provider="OpenAI", model="gpt-4o"))
    auth._cmd_logout(client, SimpleNamespace(provider="OpenAI"))
    assert ("set_selection", ("openai", "gpt-4o"), {}) in client.calls
    assert ("delete_credential", ("openai",), {}) in client.calls


def test_handle_auth_dispatch_list() -> None:
    rows = auth.handle_auth(
        SimpleNamespace(auth_command="list"), FakeClient(_PROVIDERS, active="openai")
    )
    assert isinstance(rows, list) and rows[0]["provider"] == "openai"
