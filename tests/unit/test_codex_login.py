"""Unit tests for native Codex/ChatGPT OAuth login (MET-550). Network-free."""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from pathlib import Path

import pytest

from orchestrator.harness.providers import codex_auth, codex_login
from orchestrator.harness.providers.codex_auth import CodexAuthError

# Deterministic entropy so PKCE / state are reproducible in tests.
_ZEROS = lambda n: b"\x00" * n  # noqa: E731


def _jwt(payload: dict) -> str:
    def _seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{_seg({'alg': 'none'})}.{_seg(payload)}.sig"


def test_generate_pkce_is_valid_s256() -> None:
    verifier, challenge = codex_login.generate_pkce(entropy=_ZEROS)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.decode().rstrip("=")
    assert "=" not in verifier and "=" not in challenge


def test_build_authorize_url_has_required_params() -> None:
    url = codex_login.build_authorize_url("chal", "st4te")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert url.startswith(codex_login.CODEX_AUTHORIZE_URL)
    assert q["client_id"] == codex_auth.CODEX_CLIENT_ID
    assert q["code_challenge"] == "chal"
    assert q["code_challenge_method"] == "S256"
    assert q["state"] == "st4te"
    assert q["redirect_uri"] == codex_login.CODEX_REDIRECT_URI
    assert q["originator"] == codex_login.CODEX_ORIGINATOR


def test_parse_callback_request_extracts_query() -> None:
    line = "GET /auth/callback?code=abc&state=xyz HTTP/1.1"
    assert codex_login.parse_callback_request(line) == {"code": "abc", "state": "xyz"}
    assert codex_login.parse_callback_request("garbage") == {}


def test_extract_code_state_url_and_bare() -> None:
    url = "http://localhost:1455/auth/callback?code=C1&state=S1"
    assert codex_login._extract_code_state(url) == ("C1", "S1")
    assert codex_login._extract_code_state("bare-code") == ("bare-code", None)


@pytest.mark.asyncio
async def test_exchange_code_posts_authorization_grant() -> None:
    seen: dict = {}

    async def fake_post(url: str, body: dict) -> dict:
        seen["url"] = url
        seen["body"] = body
        return {
            "access_token": _jwt({"exp": 9999}),
            "refresh_token": "r-new",
            "id_token": _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-9"}}),
        }

    creds = await codex_login.exchange_code("the-code", "the-verifier", post=fake_post, now=1.0)
    assert seen["url"] == codex_auth.CODEX_TOKEN_URL
    assert seen["body"]["grant_type"] == "authorization_code"
    assert seen["body"]["code"] == "the-code"
    assert seen["body"]["code_verifier"] == "the-verifier"
    assert seen["body"]["client_id"] == codex_auth.CODEX_CLIENT_ID
    assert creds.refresh_token == "r-new"
    assert creds.account_id == "acct-9"
    assert creds.expires_at == 9999


@pytest.mark.asyncio
async def test_complete_manual_login_bare_code_and_state_mismatch() -> None:
    async def fake_post(url: str, body: dict) -> dict:
        return {"access_token": _jwt({"exp": 1}), "refresh_token": "r"}

    # bare code (no state) → exchanged
    creds = await codex_login.complete_manual_login("code1", "verifier", None, post=fake_post)
    assert creds.access_token

    # pasted redirect with a mismatched state → rejected
    bad = "http://localhost:1455/auth/callback?code=c&state=WRONG"
    with pytest.raises(CodexAuthError, match="state mismatch"):
        await codex_login.complete_manual_login(bad, "verifier", "EXPECTED", post=fake_post)


@pytest.mark.asyncio
async def test_run_device_login_polls_until_authorized() -> None:
    responses = [
        {
            "device_code": "dev",
            "verification_uri": "https://x/device",
            "interval": 5,
            "user_code": "AB-CD",
        },
        {"error": "authorization_pending"},
        {"error": "slow_down"},
        {"access_token": _jwt({"exp": 4242}), "refresh_token": "rd"},
    ]
    calls: list[dict] = []
    slept: list[float] = []

    async def fake_post(url: str, body: dict) -> dict:
        calls.append(body)
        return responses[len(calls) - 1]

    async def fake_sleep(sec: float) -> None:
        slept.append(sec)

    ticks = iter(range(0, 1000, 10))

    creds = await codex_login.run_device_login(
        post=fake_post, sleep=fake_sleep, now=lambda: next(ticks), poll_cap_s=900.0
    )
    assert creds.expires_at == 4242
    assert creds.refresh_token == "rd"
    # slow_down bumped the interval from 5 → 10 on the last poll
    assert slept[-1] == 10.0


@pytest.mark.asyncio
async def test_run_device_login_falls_back_on_unavailable() -> None:
    async def fake_post(url: str, body: dict) -> dict:
        raise RuntimeError("404 not found")

    with pytest.raises(CodexAuthError, match="unavailable"):
        await codex_login.run_device_login(post=fake_post)


@pytest.mark.asyncio
async def test_login_manual_writes_loadable_auth_json(tmp_path: Path) -> None:
    async def fake_post(url: str, body: dict) -> dict:
        return {
            "access_token": _jwt({"exp": 9999}),
            "refresh_token": "r-final",
            "id_token": _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-x"}}),
        }

    target = await codex_login.login(
        mode="manual",
        codex_home=tmp_path,
        post=fake_post,
        entropy=_ZEROS,
        read_input=lambda prompt: "authcode-123",  # bare code → no state check
    )
    assert target == tmp_path / "auth.json"
    # The written file round-trips through the existing loader (the key assertion).
    loaded = codex_auth.load_credentials(target)
    assert loaded.refresh_token == "r-final"
    assert loaded.account_id == "acct-x"
    # 0600 perms
    assert (target.stat().st_mode & 0o777) == 0o600


@pytest.mark.asyncio
async def test_login_auto_falls_back_to_manual_via_unknown_mode() -> None:
    async def fake_post(url: str, body: dict) -> dict:
        return {}

    with pytest.raises(CodexAuthError, match="unknown login mode"):
        await codex_login.login(mode="nonsense", post=fake_post)
