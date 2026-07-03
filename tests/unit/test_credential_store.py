"""Unit tests for the MetaForge credential store (MET-551)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from orchestrator.harness.providers import Credential, CredentialStore
from orchestrator.harness.providers.credentials import default_credentials_path


def _store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(tmp_path / "credentials.json")


def test_add_and_query(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add(Credential(provider="openrouter", name="a", api_key_env="KEY_A"))
    store.add(Credential(provider="openrouter", name="b", api_key_env="KEY_B"))
    assert [c.name for c in store.credentials("openrouter")] == ["a", "b"]
    assert store.providers() == ["openrouter"]


def test_add_replaces_by_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add(Credential(provider="p", name="a", api_key_env="OLD"))
    store.add(Credential(provider="p", name="a", api_key_env="NEW"))
    creds = store.credentials("p")
    assert len(creds) == 1 and creds[0].api_key_env == "NEW"


def test_mark_dead_excluded_from_healthy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add(Credential(provider="p", name="a"))
    store.add(Credential(provider="p", name="b"))
    store.mark_dead("p", "a", reason="revoked")
    assert [c.name for c in store.healthy("p")] == ["b"]
    assert store.credentials("p")[0].dead is True
    assert store.credentials("p")[0].dead_reason == "revoked"


def test_revive_clears_dead(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add(Credential(provider="p", name="a"))
    store.mark_dead("p", "a")
    store.revive("p", "a")
    assert [c.name for c in store.healthy("p")] == ["a"]


def test_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    first = CredentialStore(path)
    first.add(Credential(provider="p", name="a", api_key_env="K", base_url="https://h/v1"))
    first.mark_dead("p", "a", reason="x")

    reopened = CredentialStore(path)
    creds = reopened.credentials("p")
    assert creds[0].api_key_env == "K"
    assert creds[0].base_url == "https://h/v1"
    assert creds[0].dead is True


def test_file_written_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    store = CredentialStore(path)
    store.add(Credential(provider="p", name="a"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_default_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "custom" / "creds.json"
    monkeypatch.setenv("METAFORGE_CREDENTIALS_PATH", str(target))
    assert default_credentials_path() == target
