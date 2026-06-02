"""Self-issued OAuth 2.1 + PKCE authorization server for HTTP MCP (MET-480).

claude.ai's web connector authenticates **only** via OAuth 2.1 + PKCE
against an issuer the MCP server implements itself — the static bearer
key (MET-338) is enough for Claude Code but the web UI rejects it. This
module is the minimal authorization server that makes the claude.ai
connector work:

* Dynamic Client Registration — RFC 7591 (``/register``)
* Authorization Server Metadata — RFC 8414
  (``/.well-known/oauth-authorization-server``)
* Protected Resource Metadata — RFC 9728
  (``/.well-known/oauth-protected-resource``)
* ``/authorize`` gated by a single shared login secret, issuing a PKCE
  ``S256``-bound authorization code
* ``/token`` exchanging the code (or a refresh token) for an opaque
  access token
* opaque-token validation for ``/mcp``

**Identity is dev-grade** (the "self-issued + shared secret" choice in
MET-480): one ``METAFORGE_OAUTH_LOGIN_SECRET`` gates ``/authorize`` and
every token maps to one configurable actor. **Storage is in-memory** —
tokens reset on restart, which claude.ai recovers from transparently via
the refresh / re-auth flow. Federating to a real IdP and persisting
clients/tokens to postgres are tracked follow-ups.

The module is framework-agnostic: it returns plain dicts and raises
:class:`OAuthError`; the HTTP routes in ``metaforge.mcp.__main__`` map
those onto FastAPI responses. This keeps the protocol logic unit-testable
without binding a port.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = [
    "OAuthConfig",
    "OAuthError",
    "OAuthProvider",
    "verify_pkce_s256",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OAuthError(Exception):
    """An OAuth protocol failure.

    ``error`` is an RFC 6749 / 7591 error code (e.g. ``invalid_request``,
    ``invalid_grant``, ``invalid_client``). ``status`` is the HTTP status
    the route should return. ``redirectable`` marks errors that, per
    RFC 6749 §4.1.2.1, should be delivered back to the client's
    ``redirect_uri`` rather than rendered directly — but only once the
    ``redirect_uri`` itself is validated.
    """

    def __init__(
        self,
        error: str,
        description: str = "",
        *,
        status: int = 400,
        redirectable: bool = False,
    ) -> None:
        super().__init__(f"{error}: {description}" if description else error)
        self.error = error
        self.description = description
        self.status = status
        self.redirectable = redirectable

    def as_dict(self) -> dict[str, str]:
        body = {"error": self.error}
        if self.description:
            body["error_description"] = self.description
        return body


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def verify_pkce_s256(verifier: str, challenge: str) -> bool:
    """Return True iff ``BASE64URL(SHA256(verifier)) == challenge`` (RFC 7636).

    Only the ``S256`` method is supported — ``plain`` is rejected at the
    ``/authorize`` boundary, so this is the only transform we need.
    """
    if not verifier or not challenge:
        return False
    expected = _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())
    return hmac.compare_digest(expected, challenge)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthConfig:
    """OAuth server settings, sourced from the environment.

    OAuth is **enabled only when ``login_secret`` is set** — otherwise the
    provider stays dormant and the HTTP transport falls back to static
    API-key / open-mode behaviour.
    """

    login_secret: str
    issuer: str | None = None
    actor_id: str = "oauth:web"
    scope: str = "mcp"
    code_ttl: int = 600
    access_ttl: int = 3600
    refresh_ttl: int = 60 * 60 * 24 * 30

    @property
    def enabled(self) -> bool:
        return bool(self.login_secret)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> OAuthConfig | None:
        """Build from env, or ``None`` when OAuth isn't configured."""
        src = env if env is not None else dict(os.environ)
        secret = (src.get("METAFORGE_OAUTH_LOGIN_SECRET") or "").strip()
        if not secret:
            return None

        def _int(name: str, default: int) -> int:
            raw = src.get(name)
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        return cls(
            login_secret=secret,
            issuer=(src.get("METAFORGE_OAUTH_ISSUER") or "").strip() or None,
            actor_id=(src.get("METAFORGE_OAUTH_ACTOR_ID") or "oauth:web").strip(),
            scope=(src.get("METAFORGE_OAUTH_SCOPE") or "mcp").strip(),
            code_ttl=_int("METAFORGE_OAUTH_CODE_TTL", 600),
            access_ttl=_int("METAFORGE_OAUTH_ACCESS_TTL", 3600),
            refresh_ttl=_int("METAFORGE_OAUTH_REFRESH_TTL", 60 * 60 * 24 * 30),
        )


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class _Client:
    client_id: str
    redirect_uris: tuple[str, ...]
    client_name: str
    issued_at: int


@dataclass
class _Code:
    client_id: str
    redirect_uri: str
    code_challenge: str
    scope: str
    expires_at: float


@dataclass
class _Token:
    actor_id: str
    scope: str
    client_id: str
    expires_at: float


@dataclass
class _Refresh:
    actor_id: str
    scope: str
    client_id: str
    expires_at: float


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


@dataclass
class _Store:
    clients: dict[str, _Client] = field(default_factory=dict)
    codes: dict[str, _Code] = field(default_factory=dict)
    access: dict[str, _Token] = field(default_factory=dict)
    refresh: dict[str, _Refresh] = field(default_factory=dict)


class OAuthProvider:
    """In-memory OAuth 2.1 + PKCE provider.

    One instance per running HTTP server. All protocol state (registered
    clients, live codes, issued tokens) lives in process memory.
    """

    def __init__(self, config: OAuthConfig, *, now: Callable[[], float] | None = None) -> None:
        self.config = config
        self._store = _Store()
        self._now = now or time.time

    # -- discovery metadata ------------------------------------------------

    def protected_resource_metadata(self, issuer: str) -> dict[str, object]:
        """RFC 9728 — tells the client which authorization server to use."""
        return {
            "resource": issuer,
            "authorization_servers": [issuer],
            "bearer_methods_supported": ["header"],
            "scopes_supported": [self.config.scope],
        }

    def authorization_server_metadata(self, issuer: str) -> dict[str, object]:
        """RFC 8414 — advertises the endpoints and supported parameters."""
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "registration_endpoint": f"{issuer}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [self.config.scope],
        }

    # -- dynamic client registration (RFC 7591) ----------------------------

    def register_client(self, metadata: dict[str, object]) -> dict[str, object]:
        raw_uris = metadata.get("redirect_uris")
        if not isinstance(raw_uris, list) or not raw_uris:
            raise OAuthError("invalid_redirect_uri", "redirect_uris is required")
        uris: list[str] = []
        for uri in raw_uris:
            if not isinstance(uri, str) or not uri.startswith(("https://", "http://")):
                raise OAuthError("invalid_redirect_uri", f"bad redirect_uri: {uri!r}")
            uris.append(uri)

        client_id = "mcp-" + secrets.token_urlsafe(24)
        name = str(metadata.get("client_name") or "claude-connector")
        issued = int(self._now())
        self._store.clients[client_id] = _Client(
            client_id=client_id,
            redirect_uris=tuple(uris),
            client_name=name,
            issued_at=issued,
        )
        # Public client + PKCE: no secret issued (token_endpoint_auth_method=none).
        return {
            "client_id": client_id,
            "client_id_issued_at": issued,
            "redirect_uris": uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": name,
            "scope": self.config.scope,
        }

    # -- authorization endpoint -------------------------------------------

    def validate_authorize(
        self,
        *,
        client_id: str | None,
        redirect_uri: str | None,
        response_type: str | None,
        code_challenge: str | None,
        code_challenge_method: str | None,
        scope: str | None,
    ) -> _Client:
        """Validate an ``/authorize`` request before showing the login page.

        Raises non-redirectable :class:`OAuthError` for failures that must
        not bounce to a (possibly attacker-controlled) ``redirect_uri`` —
        unknown client or mismatched ``redirect_uri``. Other failures are
        redirectable per RFC 6749 once the URI is trusted.
        """
        if not client_id:
            raise OAuthError("invalid_request", "client_id is required")
        client = self._store.clients.get(client_id)
        if client is None:
            raise OAuthError("invalid_client", "unknown client_id")
        if not redirect_uri or redirect_uri not in client.redirect_uris:
            raise OAuthError("invalid_request", "redirect_uri mismatch")
        if response_type != "code":
            raise OAuthError(
                "unsupported_response_type",
                "only response_type=code is supported",
                redirectable=True,
            )
        if not code_challenge:
            raise OAuthError(
                "invalid_request", "code_challenge is required (PKCE)", redirectable=True
            )
        if code_challenge_method != "S256":
            raise OAuthError(
                "invalid_request",
                "code_challenge_method must be S256",
                redirectable=True,
            )
        return client

    def verify_login(self, secret: str | None) -> bool:
        """Constant-time check of the shared login secret."""
        if not secret:
            return False
        return hmac.compare_digest(secret, self.config.login_secret)

    def issue_code(
        self, client: _Client, redirect_uri: str, code_challenge: str, scope: str | None
    ) -> str:
        code = secrets.token_urlsafe(32)
        self._store.codes[code] = _Code(
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scope=scope or self.config.scope,
            expires_at=self._now() + self.config.code_ttl,
        )
        return code

    # -- token endpoint ----------------------------------------------------

    def exchange(self, params: dict[str, str]) -> dict[str, object]:
        grant = params.get("grant_type")
        if grant == "authorization_code":
            return self._grant_auth_code(params)
        if grant == "refresh_token":
            return self._grant_refresh(params)
        raise OAuthError("unsupported_grant_type", f"unsupported grant_type: {grant!r}")

    def _grant_auth_code(self, params: dict[str, str]) -> dict[str, object]:
        code = params.get("code")
        record = self._store.codes.pop(code, None) if code else None
        if record is None:
            raise OAuthError("invalid_grant", "unknown or used authorization code")
        if record.expires_at < self._now():
            raise OAuthError("invalid_grant", "authorization code expired")
        if params.get("client_id") != record.client_id:
            raise OAuthError("invalid_grant", "client_id mismatch")
        if params.get("redirect_uri") != record.redirect_uri:
            raise OAuthError("invalid_grant", "redirect_uri mismatch")
        verifier = params.get("code_verifier")
        if not verifier or not verify_pkce_s256(verifier, record.code_challenge):
            raise OAuthError("invalid_grant", "PKCE verification failed")
        return self._issue_tokens(record.client_id, record.scope)

    def _grant_refresh(self, params: dict[str, str]) -> dict[str, object]:
        token = params.get("refresh_token")
        record = self._store.refresh.pop(token, None) if token else None
        if record is None:
            raise OAuthError("invalid_grant", "unknown refresh token")
        if record.expires_at < self._now():
            raise OAuthError("invalid_grant", "refresh token expired")
        if params.get("client_id") and params.get("client_id") != record.client_id:
            raise OAuthError("invalid_grant", "client_id mismatch")
        return self._issue_tokens(record.client_id, record.scope)

    def _issue_tokens(self, client_id: str, scope: str) -> dict[str, object]:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        now = self._now()
        self._store.access[access] = _Token(
            actor_id=self.config.actor_id,
            scope=scope,
            client_id=client_id,
            expires_at=now + self.config.access_ttl,
        )
        self._store.refresh[refresh] = _Refresh(
            actor_id=self.config.actor_id,
            scope=scope,
            client_id=client_id,
            expires_at=now + self.config.refresh_ttl,
        )
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": self.config.access_ttl,
            "refresh_token": refresh,
            "scope": scope,
        }

    # -- resource-server validation ---------------------------------------

    def validate_token(self, token: str | None) -> str | None:
        """Return the bound ``actor_id`` for a live access token, else None."""
        if not token:
            return None
        record = self._store.access.get(token)
        if record is None:
            return None
        if record.expires_at < self._now():
            self._store.access.pop(token, None)
            return None
        return record.actor_id
