"""Codex token persistence across rotation (MET-550). Network-free."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from orchestrator.harness.providers.codex_auth import (
    CodexCredentials,
    get_valid_credentials,
    load_credentials,
    save_credentials,
)


def _jwt(payload: dict) -> str:
    seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{seg}.{seg}.sig"


def test_save_preserves_shape_and_updates_tokens(tmp_path: Path) -> None:
    p = tmp_path / "auth.json"
    p.write_text(
        json.dumps(
            {"OPENAI_API_KEY": None, "tokens": {"access_token": "old", "refresh_token": "r0"}}
        ),
        encoding="utf-8",
    )
    save_credentials(p, CodexCredentials(access_token="new", refresh_token="r1", account_id="acct"))
    data = json.loads(p.read_text())
    assert data["tokens"]["access_token"] == "new"
    assert data["tokens"]["refresh_token"] == "r1"
    assert data["tokens"]["account_id"] == "acct"
    assert "OPENAI_API_KEY" in data  # unrelated fields preserved
    assert "last_refresh" in data
    # round-trips back through the loader
    assert load_credentials(p).refresh_token == "r1"


def test_save_is_best_effort_on_unwritable_path(tmp_path: Path) -> None:
    # A directory path can't be written as a file → returns False, no raise.
    assert save_credentials(tmp_path, CodexCredentials(access_token="x")) is False


@pytest.mark.asyncio
async def test_get_valid_persists_rotated_refresh_token(tmp_path: Path) -> None:
    p = tmp_path / "auth.json"
    p.write_text(
        json.dumps({"tokens": {"access_token": _jwt({"exp": 100}), "refresh_token": "r0"}}),
        encoding="utf-8",
    )

    async def fake_post(url: str, body: dict) -> dict:
        assert body["refresh_token"] == "r0"
        return {"access_token": _jwt({"exp": 9999}), "refresh_token": "r1-rotated"}

    creds = await get_valid_credentials(path=p, post=fake_post, now=1000.0)
    assert creds.refresh_token == "r1-rotated"
    # persisted to disk → a fresh load sees the rotated token, not the dead one
    assert load_credentials(p).refresh_token == "r1-rotated"


# --- FORGE-475: the gateway and the design-flow worker share ~/.codex --------


def _expired_file(p: Path) -> None:
    p.write_text(
        json.dumps({"tokens": {"access_token": _jwt({"exp": 100}), "refresh_token": "r0"}}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_concurrent_refreshers_make_exactly_one_refresh_call(tmp_path: Path) -> None:
    """Refresh tokens are single-use: a second refresh of r0 would fail."""
    import asyncio

    p = tmp_path / "auth.json"
    _expired_file(p)
    calls: list[str] = []

    async def fake_post(url: str, body: dict) -> dict:
        calls.append(body["refresh_token"])
        # Hold the lock long enough that the other refresher is waiting on it.
        await asyncio.sleep(0.2)
        return {"access_token": _jwt({"exp": 9999}), "refresh_token": "r1"}

    a, b = await asyncio.gather(
        get_valid_credentials(path=p, post=fake_post, now=1000.0),
        get_valid_credentials(path=p, post=fake_post, now=1000.0),
    )

    assert calls == ["r0"]
    assert a.refresh_token == b.refresh_token == "r1"
    assert a.access_token == b.access_token
    assert load_credentials(p).refresh_token == "r1"


@pytest.mark.asyncio
async def test_a_forced_refresh_uses_tokens_another_process_already_rotated(
    tmp_path: Path,
) -> None:
    """The 401 path: our in-memory token is dead, but someone already rotated it."""
    from orchestrator.harness.providers.codex_auth import refresh_and_persist

    p = tmp_path / "auth.json"
    stale = CodexCredentials(access_token=_jwt({"exp": 9999}), refresh_token="r0")
    save_credentials(p, CodexCredentials(access_token=_jwt({"exp": 8888}), refresh_token="r1"))

    async def must_not_post(url: str, body: dict) -> dict:
        raise AssertionError("refreshed a token another process already rotated")

    got = await refresh_and_persist(p, stale, post=must_not_post, force=True)
    assert got.refresh_token == "r1"


@pytest.mark.asyncio
async def test_a_forced_refresh_still_refreshes_an_unrotated_token(tmp_path: Path) -> None:
    from orchestrator.harness.providers.codex_auth import refresh_and_persist

    p = tmp_path / "auth.json"
    stale = CodexCredentials(access_token=_jwt({"exp": 9999}), refresh_token="r0")
    save_credentials(p, stale)
    calls: list[str] = []

    async def fake_post(url: str, body: dict) -> dict:
        calls.append(body["refresh_token"])
        return {"access_token": _jwt({"exp": 99999}), "refresh_token": "r1"}

    got = await refresh_and_persist(p, stale, post=fake_post, force=True)
    assert calls == ["r0"]
    assert got.refresh_token == "r1"
    assert load_credentials(p).refresh_token == "r1"


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    p = tmp_path / "auth.json"
    assert save_credentials(p, CodexCredentials(access_token="a", refresh_token="r"))
    assert sorted(f.name for f in tmp_path.iterdir()) == ["auth.json"]
    assert (p.stat().st_mode & 0o777) == 0o600
    # A failed write leaves neither a partial file nor a stray temp file.
    assert save_credentials(tmp_path, CodexCredentials(access_token="x")) is False
    assert sorted(f.name for f in tmp_path.iterdir()) == ["auth.json"]
