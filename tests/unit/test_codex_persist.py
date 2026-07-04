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
