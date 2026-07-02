"""Unit tests for Codex-subscription credential handling (MET-550). Network-free."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from orchestrator.harness.providers.codex_auth import (
    CODEX_CLIENT_ID,
    CODEX_TOKEN_URL,
    CodexAuthError,
    CodexCredentials,
    auth_json_path,
    load_credentials,
    parse_credentials,
    refresh_credentials,
)


def _jwt(payload: dict) -> str:
    def _seg(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{_seg({'alg': 'none'})}.{_seg(payload)}.sig"


def test_parse_tokens_shape() -> None:
    creds = parse_credentials(
        {"tokens": {"access_token": "a", "refresh_token": "r", "account_id": "acct-1"}}
    )
    assert creds.access_token == "a"
    assert creds.refresh_token == "r"
    assert creds.account_id == "acct-1"


def test_account_id_from_id_token_claim() -> None:
    id_token = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-jwt"}})
    creds = parse_credentials({"tokens": {"access_token": "a", "id_token": id_token}})
    assert creds.account_id == "acct-jwt"


def test_missing_access_token_raises() -> None:
    with pytest.raises(CodexAuthError, match="no access_token"):
        parse_credentials({"tokens": {}})


def test_is_expired_from_access_token_exp() -> None:
    access = _jwt({"exp": 1000})
    creds = parse_credentials({"tokens": {"access_token": access}})
    assert creds.expires_at == 1000
    assert creds.is_expired(now=1000, skew=0)
    assert not creds.is_expired(now=800, skew=0)


def test_load_from_file_and_missing(tmp_path: Path) -> None:
    p = tmp_path / "auth.json"
    p.write_text(
        json.dumps({"tokens": {"access_token": "a", "refresh_token": "r"}}), encoding="utf-8"
    )
    assert load_credentials(p).access_token == "a"
    with pytest.raises(CodexAuthError, match="run `npx @openai/codex login`"):
        load_credentials(tmp_path / "nope.json")


def test_auth_json_path_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert auth_json_path() == tmp_path / "auth.json"


@pytest.mark.asyncio
async def test_refresh_posts_correct_body() -> None:
    seen: dict = {}

    async def fake_post(url: str, body: dict) -> dict:
        seen["url"] = url
        seen["body"] = body
        return {"access_token": _jwt({"exp": 5000}), "refresh_token": "r2"}

    creds = CodexCredentials(access_token="old", refresh_token="r1", account_id="acct")
    new = await refresh_credentials(creds, post=fake_post, now=1.0)
    assert seen["url"] == CODEX_TOKEN_URL
    assert seen["body"]["client_id"] == CODEX_CLIENT_ID
    assert seen["body"]["grant_type"] == "refresh_token"
    assert seen["body"]["refresh_token"] == "r1"
    assert new.refresh_token == "r2"
    assert new.expires_at == 5000
    assert new.account_id == "acct"  # preserved


@pytest.mark.asyncio
async def test_refresh_without_refresh_token_raises() -> None:
    with pytest.raises(CodexAuthError, match="no refresh_token"):
        await refresh_credentials(CodexCredentials(access_token="a"), post=_unused)


@pytest.mark.asyncio
async def test_get_valid_refreshes_when_expired(tmp_path: Path) -> None:
    from orchestrator.harness.providers import codex_auth as ca

    expired = _jwt({"exp": 100})
    p = tmp_path / "auth.json"
    p.write_text(
        json.dumps({"tokens": {"access_token": expired, "refresh_token": "r"}}), encoding="utf-8"
    )

    async def fake_post(url: str, body: dict) -> dict:
        return {"access_token": _jwt({"exp": 9999})}

    creds = await ca.get_valid_credentials(path=p, post=fake_post, now=1000.0)
    assert creds.expires_at == 9999  # refreshed


async def _unused(url: str, body: dict) -> dict:  # pragma: no cover - never called
    return {}
