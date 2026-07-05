"""Unit tests for the MetaForge credential store (MET-551)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from orchestrator.harness.providers import Credential, CredentialStore
from orchestrator.harness.providers.credentials import default_credentials_path, next_cooldown


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


def test_cooldown_excludes_then_auto_revives(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add(Credential(provider="p", name="a"))
    store.mark_cooldown("p", "a", now=1000.0)  # 1st failure → 30s
    # within the cooldown window it's excluded from healthy...
    assert [c.name for c in store.healthy("p", now=1010.0)] == []
    # ...and auto-revives once it expires (no background timer needed)
    assert [c.name for c in store.healthy("p", now=1031.0)] == ["a"]


def test_record_success_resets_failures_and_counts_usage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add(Credential(provider="p", name="a"))
    store.mark_cooldown("p", "a", now=0.0)
    store.record_success("p", "a")
    cred = store.get("p", "a")
    assert cred is not None
    assert cred.failure_count == 0
    assert cred.cooldown_until is None
    assert cred.usage_count == 1


def test_cooldown_escalates_across_failures(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add(Credential(provider="p", name="a"))
    store.mark_cooldown("p", "a", now=0.0)  # 30s
    assert store.get("p", "a").cooldown_until == 30.0
    store.mark_cooldown("p", "a", now=100.0)  # 2nd → 60s
    assert store.get("p", "a").cooldown_until == 160.0
    store.mark_cooldown("p", "a", now=200.0)  # 3rd → 300s
    assert store.get("p", "a").cooldown_until == 500.0
    store.mark_cooldown("p", "a", now=1000.0)  # capped at 300s
    assert store.get("p", "a").cooldown_until == 1300.0


def test_next_cooldown_ladder() -> None:
    assert next_cooldown(0) == 0.0
    assert next_cooldown(1) == 30.0
    assert next_cooldown(2) == 60.0
    assert next_cooldown(3) == 300.0
    assert next_cooldown(9) == 300.0  # capped


def test_old_json_without_new_fields_loads(tmp_path: Path) -> None:
    # A credentials.json written before the cooldown fields existed still loads.
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {"providers": {"p": [{"provider": "p", "name": "a", "api_key_env": "K"}]}}
        ),
        encoding="utf-8",
    )
    store = CredentialStore(path)
    cred = store.get("p", "a")
    assert cred is not None
    assert cred.cooldown_until is None
    assert cred.failure_count == 0
    assert cred.usage_count == 0
