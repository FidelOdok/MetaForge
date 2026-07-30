"""Gateway auth store + credential resolution + write endpoints (MET-10).

Covers: the 0600 AuthStore, `_require_key` preferring a raw key, the auth store
overriding env in `provider_config_from_env`, and the POST/PUT/DELETE harness
routes. Network-free; each test points the store at a temp file.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest


@pytest.fixture()
def store_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "harness-auth.json"
    monkeypatch.setenv("METAFORGE_HARNESS_AUTH_PATH", str(p))
    return p


# --- AuthStore -------------------------------------------------------------


def test_auth_store_roundtrip_and_0600(store_path: Path) -> None:
    from orchestrator.harness.providers.auth_store import AuthStore

    s = AuthStore()
    s.set_credential("openai", "sk-secret", base_url="https://x/v1")
    s.set_selection("openai", "gpt-4o")

    # Persisted 0600 (owner rw only).
    assert store_path.is_file()
    assert stat.S_IMODE(store_path.stat().st_mode) == 0o600

    # A fresh store reads it back.
    s2 = AuthStore()
    cred = s2.get_credential("openai")
    assert cred is not None and cred.api_key == "sk-secret" and cred.base_url == "https://x/v1"
    sel = s2.get_selection()
    assert sel is not None and sel.provider == "openai" and sel.model == "gpt-4o"
    assert s2.configured_providers() == {"openai"}


def test_auth_store_delete_clears_matching_selection(store_path: Path) -> None:
    from orchestrator.harness.providers.auth_store import AuthStore

    s = AuthStore()
    s.set_credential("openai", "sk-1")
    s.set_selection("openai", "gpt-4o")
    assert s.delete_credential("openai") is True
    assert s.get_credential("openai") is None
    assert s.get_selection() is None  # selection pointed at the deleted provider
    assert s.delete_credential("openai") is False  # idempotent


def test_auth_store_missing_file_is_empty(store_path: Path) -> None:
    from orchestrator.harness.providers.auth_store import AuthStore

    assert not store_path.exists()
    s = AuthStore()
    assert s.get_credential("openai") is None
    assert s.get_selection() is None


# --- _require_key prefers a raw spec key -----------------------------------


def test_require_key_prefers_raw_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.harness.providers.adapters import _require_key
    from orchestrator.harness.providers.pipeline import ProviderSpec

    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    spec = ProviderSpec(name="openai", model="gpt-4o", api_key_env="OPENAI_API_KEY", api_key="raw")
    assert _require_key(spec, "OPENAI_API_KEY") == "raw"
    # Without a raw key it falls back to env.
    spec2 = ProviderSpec(name="openai", model="gpt-4o", api_key_env="OPENAI_API_KEY")
    assert _require_key(spec2, "OPENAI_API_KEY") == "from-env"


# --- provider_config_from_env consults the store --------------------------


def test_provider_config_uses_store_selection_and_key(
    store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_gateway.chat.harness_backend import provider_config_from_env
    from orchestrator.harness.providers.auth_store import AuthStore

    # Env says one thing; the store selection + key must win.
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("METAFORGE_LLM_MODEL", "claude-opus-4-8")
    AuthStore().set_credential("openai", "sk-live", base_url="https://api.openai.com/v1")
    AuthStore().set_selection("openai", "gpt-4o")

    spec = provider_config_from_env().slots.candidates("generator")[0]
    assert spec.name == "openai"
    assert spec.model == "gpt-4o"
    assert spec.api_key == "sk-live"
    assert spec.base_url == "https://api.openai.com/v1"


def test_provider_config_explicit_arg_wins_over_store(
    store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_gateway.chat.harness_backend import provider_config_from_env
    from orchestrator.harness.providers.auth_store import AuthStore

    AuthStore().set_selection("openai", "gpt-4o")
    spec = provider_config_from_env(provider="anthropic", model="claude-opus-4-8").slots.candidates(
        "generator"
    )[0]
    assert spec.name == "anthropic" and spec.model == "claude-opus-4-8"


# --- write endpoints -------------------------------------------------------


@pytest.mark.asyncio
async def test_routes_set_api_key_and_selection(store_path: Path) -> None:
    from api_gateway.harness import routes
    from orchestrator.harness.providers.auth_store import AuthStore

    await routes.set_credential(
        routes.SetCredentialRequest(provider="openai", method="api_key", api_key="sk-abc")
    )
    await routes.set_selection(routes.SetSelectionRequest(provider="openai", model="gpt-4o"))

    s = AuthStore()
    assert s.get_credential("openai").api_key == "sk-abc"  # type: ignore[union-attr]
    assert s.get_selection().model == "gpt-4o"  # type: ignore[union-attr]

    # GET /providers reflects it as configured + active.
    resp = await routes.list_providers()
    assert resp.active_provider == "openai" and resp.active_model == "gpt-4o"
    assert any(p.id == "openai" and p.configured for p in resp.providers)

    await routes.delete_credential("openai")
    assert AuthStore().get_credential("openai") is None


@pytest.mark.asyncio
async def test_routes_reject_unknown_provider_and_missing_key(store_path: Path) -> None:
    from fastapi import HTTPException

    from api_gateway.harness import routes

    with pytest.raises(HTTPException) as ei:
        await routes.set_credential(routes.SetCredentialRequest(provider="nope", method="api_key"))
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException) as ei2:
        await routes.set_credential(
            routes.SetCredentialRequest(provider="openai", method="api_key", api_key="")
        )
    assert ei2.value.status_code == 400


# --- _is_configured: the real family is "openai-codex", never bare "codex" -
#
# `profile.api_family == "codex"` never matches (registry.CODEX == "openai-
# codex"), so this fell through past the intended auth_json_path() check to
# the "active provider borrows METAFORGE_LLM_API_KEY" fallback below it and
# reported configured=True from an unrelated env key (e.g. an openrouter key)
# even with no Codex login at all. Live-caught: `forge auth login
# --provider openai-codex` looked configured+active while the OAuth token had
# actually failed to persist (see test_codex_auth.py's save_credentials fix).


def _codex_profile():  # noqa: ANN201 - returns registry.ProviderProfile (imported locally)
    from orchestrator.harness.providers import registry

    return registry.ProviderProfile(id="openai-codex", api_family=registry.CODEX, api_key_env="X")


def test_is_configured_true_only_via_auth_json_for_codex(
    store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_gateway.harness import routes

    monkeypatch.setattr(routes, "auth_json_path", lambda: Path("/some/auth.json"))
    assert routes._is_configured(_codex_profile()) is True


def test_is_configured_false_for_codex_without_auth_json_even_if_active_and_env_key_set(
    store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_gateway.harness import routes
    from orchestrator.harness.providers.auth_store import AuthStore

    # Exactly the live scenario: openai-codex is the active selection, and an
    # unrelated METAFORGE_LLM_API_KEY (e.g. an openrouter key) is set — neither
    # should make codex look configured when there's no auth.json.
    AuthStore().set_selection("openai-codex")
    monkeypatch.setenv("METAFORGE_LLM_API_KEY", "sk-unrelated-openrouter-key")
    monkeypatch.setattr(routes, "auth_json_path", lambda: None)

    assert routes._is_configured(_codex_profile()) is False


@pytest.mark.asyncio
async def test_set_credential_oauth_raises_when_persist_fails(store_path: Path) -> None:
    """A write that fails to persist must 500, not silently report success —
    the exact bug that dropped a completed OAuth login on the floor."""
    from fastapi import HTTPException

    from api_gateway.harness import routes

    orig = routes.save_credentials
    routes.save_credentials = lambda *_a, **_k: False  # simulate a failed write
    try:
        with pytest.raises(HTTPException) as ei:
            await routes.set_credential(
                routes.SetCredentialRequest(
                    provider="openai-codex",
                    method="oauth",
                    tokens={"access_token": "a", "refresh_token": "r"},
                )
            )
        assert ei.value.status_code == 500
        assert "auth.json" in ei.value.detail or "persist" in ei.value.detail
    finally:
        routes.save_credentials = orig


def test_require_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from api_gateway.harness import routes

    # No token configured → allowed (dev).
    monkeypatch.delenv("METAFORGE_HARNESS_ADMIN_TOKEN", raising=False)
    routes._require_admin(None)

    # Token configured → header must match.
    monkeypatch.setenv("METAFORGE_HARNESS_ADMIN_TOKEN", "s3cret")
    routes._require_admin("s3cret")
    with pytest.raises(HTTPException) as ei:
        routes._require_admin("wrong")
    assert ei.value.status_code == 401
