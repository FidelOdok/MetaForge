"""Native ChatGPT/Codex OAuth login (MET-550).

MetaForge performs its own OAuth login instead of depending on an
externally-installed ``codex`` CLI. It writes ``CODEX_HOME/auth.json`` in the
exact shape :func:`codex_auth.parse_credentials` reads, so ``codex_invoke`` and
``get_valid_credentials`` keep working unchanged.

Two flows are supported:

* **PKCE + loopback redirect** — the guaranteed path (what the official codex
  CLI does): open ``/oauth/authorize`` in a browser, catch the redirect on
  ``http://localhost:1455/auth/callback``, and exchange the code. On a headless
  box, forward the port (``ssh -L 1455:localhost:1455``) or use ``--mode manual``
  to paste the redirect URL back.
* **Device-code** — a best-effort, beta path (must be enabled on the ChatGPT
  account). Any failure cleanly falls back to loopback.

Every network call goes through the injectable :data:`codex_auth.RefreshPost`
transport, and randomness / time / stdin are injectable, so this unit-tests
without touching the network, a socket, or a browser.

CAVEAT: the OAuth client and backend are undocumented and can change; this
reuses the same client id the codex CLI uses.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import time
import urllib.parse
import webbrowser
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

from orchestrator.harness.providers import codex_auth
from orchestrator.harness.providers.codex_auth import CodexAuthError, CodexCredentials

logger = structlog.get_logger(__name__)

# Login-only constants (the refresh path's constants live in codex_auth).
CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CODEX_DEVICE_AUTH_URL = "https://auth.openai.com/oauth/device/code"  # best-effort
CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
CODEX_SCOPE = "openid profile email offline_access"
CODEX_ORIGINATOR = "codex_cli_rs"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

Entropy = Callable[[int], bytes]
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]
ReadInput = Callable[[str], str]


# ---------------------------------------------------------------------------
# PKCE + URL helpers (pure, testable)
# ---------------------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_pkce(*, entropy: Entropy = os.urandom) -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge_S256)`` for the PKCE flow."""
    verifier = _b64url(entropy(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _random_state(entropy: Entropy) -> str:
    return _b64url(entropy(16))


def build_authorize_url(code_challenge: str, state: str) -> str:
    """Compose the ``/oauth/authorize`` URL with PKCE + OpenAI-specific params."""
    params = {
        "response_type": "code",
        "client_id": codex_auth.CODEX_CLIENT_ID,
        "redirect_uri": CODEX_REDIRECT_URI,
        "scope": CODEX_SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": CODEX_ORIGINATOR,
    }
    return f"{CODEX_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def parse_callback_request(request_line: str) -> dict[str, str]:
    """Extract query params from an HTTP request line (the loopback callback).

    ``request_line`` looks like ``GET /auth/callback?code=abc&state=xyz HTTP/1.1``.
    """
    parts = request_line.split(" ")
    if len(parts) < 2:
        return {}
    query = urllib.parse.urlsplit(parts[1]).query
    return dict(urllib.parse.parse_qsl(query))


def _extract_code_state(pasted: str) -> tuple[str, str | None]:
    """Parse a pasted full redirect URL or a bare authorization code."""
    pasted = pasted.strip()
    if pasted.startswith("http") or "?" in pasted:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(pasted).query))
        return q.get("code", ""), q.get("state")
    return pasted, None


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------


def _creds_from_token_response(resp: dict[str, Any], *, now: float | None) -> CodexCredentials:
    access_token = resp.get("access_token")
    if not access_token:
        raise CodexAuthError("token response had no access_token")
    id_token = resp.get("id_token")
    expires_in = resp.get("expires_in")
    expires_at = codex_auth._token_exp(str(access_token))
    if expires_at is None and isinstance(expires_in, (int, float)):
        expires_at = (now if now is not None else time.time()) + float(expires_in)
    return CodexCredentials(
        access_token=str(access_token),
        refresh_token=resp.get("refresh_token"),
        account_id=codex_auth._account_id_from_id_token(id_token),
        id_token=id_token,
        expires_at=expires_at,
    )


async def exchange_code(
    code: str,
    code_verifier: str,
    *,
    redirect_uri: str = CODEX_REDIRECT_URI,
    post: codex_auth.RefreshPost,
    now: float | None = None,
) -> CodexCredentials:
    """Exchange an authorization code for tokens (``grant_type=authorization_code``)."""
    body = {
        "grant_type": "authorization_code",
        "client_id": codex_auth.CODEX_CLIENT_ID,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    resp = await post(codex_auth.CODEX_TOKEN_URL, body)
    return _creds_from_token_response(resp, now=now)


# ---------------------------------------------------------------------------
# Loopback flow
# ---------------------------------------------------------------------------


def _try_open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - headless boxes have no browser; ignore
        pass


async def run_loopback_login(
    *,
    port: int = 1455,
    open_browser: bool = True,
    post: codex_auth.RefreshPost,
    timeout_s: float = 300.0,
    now: float | None = None,
    entropy: Entropy = os.urandom,
) -> CodexCredentials:
    """Run the PKCE + loopback flow: serve the callback, exchange the code.

    The redirect URI is fixed at the registered ``localhost:1455`` value; ``port``
    controls only the local bind (forward it with ``ssh -L`` on a remote box).
    """
    verifier, challenge = generate_pkce(entropy=entropy)
    state = _random_state(entropy)
    authorize_url = build_authorize_url(challenge, state)
    captured: dict[str, str] = {}
    done = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = (await reader.readline()).decode("latin-1", "replace")
            captured.update(parse_callback_request(request_line))
            body = b"<html><body>Login complete. You may close this window.</body></html>"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
            )
            await writer.drain()
        finally:
            writer.close()
            done.set()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    logger.info("codex_loopback_login_started", port=port)
    try:
        print(f"Open this URL to authorize Codex/ChatGPT:\n\n  {authorize_url}\n")
        if open_browser:
            _try_open_browser(authorize_url)
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout_s)
        except TimeoutError as exc:
            raise CodexAuthError("timed out waiting for the OAuth callback") from exc
    finally:
        server.close()
        await server.wait_closed()

    if captured.get("state") != state:
        raise CodexAuthError("state mismatch on OAuth callback — aborting")
    code = captured.get("code")
    if not code:
        raise CodexAuthError(f"OAuth callback failed: {captured.get('error', 'no code')}")
    return await exchange_code(code, verifier, post=post, now=now)


# ---------------------------------------------------------------------------
# Manual (paste) flow
# ---------------------------------------------------------------------------


def print_manual_instructions(authorize_url: str) -> None:
    """Print instructions for the no-inbound-connectivity manual flow."""
    print(
        "Manual login — open this URL in any browser, complete the login, then "
        "copy the FULL URL you are redirected to (it will start with "
        f"'{CODEX_REDIRECT_URI}?code=...') and paste it back here:\n\n  {authorize_url}\n"
    )


async def complete_manual_login(
    pasted: str,
    code_verifier: str,
    state: str | None,
    *,
    post: codex_auth.RefreshPost,
    now: float | None = None,
) -> CodexCredentials:
    """Complete a manual login from a pasted redirect URL (or bare code)."""
    code, got_state = _extract_code_state(pasted)
    if not code:
        raise CodexAuthError("no authorization code found in the pasted value")
    if got_state is not None and state is not None and got_state != state:
        raise CodexAuthError("state mismatch on pasted redirect — aborting")
    return await exchange_code(code, code_verifier, post=post, now=now)


# ---------------------------------------------------------------------------
# Device-code flow (best-effort)
# ---------------------------------------------------------------------------


async def run_device_login(
    *,
    post: codex_auth.RefreshPost,
    sleep: Sleep = asyncio.sleep,
    now: Clock = time.time,
    poll_cap_s: float = 900.0,
) -> CodexCredentials:
    """Run the OAuth device-code flow. Raises :class:`CodexAuthError` if the
    device endpoint is unavailable/disabled so the caller can fall back."""
    try:
        init = await post(
            CODEX_DEVICE_AUTH_URL, {"client_id": codex_auth.CODEX_CLIENT_ID, "scope": CODEX_SCOPE}
        )
    except Exception as exc:  # noqa: BLE001 - unavailable → clean fall-back signal
        raise CodexAuthError(f"device-code flow unavailable: {exc}") from exc

    device_code = init.get("device_code")
    verification_uri = init.get("verification_uri_complete") or init.get("verification_uri")
    if not device_code or not verification_uri:
        raise CodexAuthError("device-code flow unavailable (no device_code)")
    interval = float(init.get("interval", 5.0))
    print(f"To authorize, visit:\n\n  {verification_uri}\n")
    if init.get("user_code"):
        print(f"and enter the code: {init['user_code']}\n")

    deadline = now() + poll_cap_s
    while now() < deadline:
        await sleep(interval)
        try:
            resp = await post(
                codex_auth.CODEX_TOKEN_URL,
                {
                    "client_id": codex_auth.CODEX_CLIENT_ID,
                    "grant_type": DEVICE_GRANT,
                    "device_code": device_code,
                },
            )
        except Exception as exc:  # noqa: BLE001 - transport raised on 4xx pending/slow_down
            msg = str(exc)
            if "slow_down" in msg:
                interval += 5.0
                continue
            if "pending" in msg:
                continue
            raise CodexAuthError(f"device-code polling failed: {exc}") from exc
        err = resp.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5.0
            continue
        if err:
            raise CodexAuthError(f"device-code error: {err}")
        return _creds_from_token_response(resp, now=now())
    raise CodexAuthError("device-code login timed out")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _resolve_target(codex_home: Path | None) -> Path:
    if codex_home is not None:
        return codex_home / "auth.json"
    env = os.environ.get("CODEX_HOME") or os.environ.get("CHATGPT_LOCAL_HOME")
    if env:
        return Path(env) / "auth.json"
    return Path.home() / ".codex" / "auth.json"


async def login(
    *,
    mode: str = "auto",
    port: int = 1455,
    codex_home: Path | None = None,
    post: codex_auth.RefreshPost | None = None,
    open_browser: bool = True,
    entropy: Entropy = os.urandom,
    now: Clock = time.time,
    read_input: ReadInput = input,
) -> Path:
    """Run the chosen OAuth flow and persist credentials to ``CODEX_HOME/auth.json``.

    ``mode`` is ``auto`` (device → loopback), ``loopback``, ``device``, or ``manual``.
    Returns the path written.
    """
    if post is None:
        from orchestrator.harness.providers.adapters import _codex_refresh_post

        post = _codex_refresh_post

    if mode == "device":
        creds = await run_device_login(post=post, now=now)
    elif mode == "loopback":
        creds = await run_loopback_login(
            port=port, open_browser=open_browser, post=post, now=now(), entropy=entropy
        )
    elif mode == "manual":
        verifier, challenge = generate_pkce(entropy=entropy)
        state = _random_state(entropy)
        print_manual_instructions(build_authorize_url(challenge, state))
        pasted = read_input("Paste the full redirect URL (or the code): ")
        creds = await complete_manual_login(pasted, verifier, state, post=post, now=now())
    elif mode == "auto":
        try:
            creds = await run_device_login(post=post, now=now)
        except CodexAuthError:
            logger.info("codex_device_login_unavailable_falling_back_to_loopback")
            creds = await run_loopback_login(
                port=port, open_browser=open_browser, post=post, now=now(), entropy=entropy
            )
    else:
        raise CodexAuthError(f"unknown login mode: {mode!r}")

    target = _resolve_target(codex_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not codex_auth.save_credentials(target, creds):
        raise CodexAuthError(f"failed to write credentials to {target}")
    logger.info("codex_login_complete", path=str(target), account_id=creds.account_id)
    return target
