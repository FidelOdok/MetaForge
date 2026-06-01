"""OAuth 2.1 + PKCE tests for the HTTP MCP transport (MET-480).

Two layers:

* protocol-level unit tests on :class:`OAuthProvider` (PKCE, code/token
  exchange, metadata, DCR) — no HTTP;
* an end-to-end run of the claude.ai-style handshake through the FastAPI
  app built by ``build_http_app``, using Starlette's TestClient.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from metaforge.mcp.oauth import OAuthConfig, OAuthError, OAuthProvider, verify_pkce_s256


def _pkce() -> tuple[str, str]:
    verifier = "verifier-" + "a" * 50
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _provider() -> OAuthProvider:
    return OAuthProvider(OAuthConfig(login_secret="s3cret"))


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def test_pkce_s256_roundtrip() -> None:
    verifier, challenge = _pkce()
    assert verify_pkce_s256(verifier, challenge)


def test_pkce_rejects_wrong_verifier() -> None:
    _, challenge = _pkce()
    assert not verify_pkce_s256("not-the-verifier", challenge)


def test_pkce_rejects_empty() -> None:
    assert not verify_pkce_s256("", "x")
    assert not verify_pkce_s256("x", "")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_disabled_without_secret() -> None:
    assert OAuthConfig.from_env({}) is None


def test_config_from_env() -> None:
    cfg = OAuthConfig.from_env(
        {"METAFORGE_OAUTH_LOGIN_SECRET": "pw", "METAFORGE_OAUTH_ISSUER": "https://mcp.x/"}
    )
    assert cfg is not None and cfg.enabled
    assert cfg.issuer == "https://mcp.x/"


# ---------------------------------------------------------------------------
# Metadata + DCR
# ---------------------------------------------------------------------------


def test_authorization_server_metadata_shape() -> None:
    meta = _provider().authorization_server_metadata("https://mcp.x")
    assert meta["issuer"] == "https://mcp.x"
    assert meta["authorization_endpoint"] == "https://mcp.x/authorize"
    assert meta["token_endpoint"] == "https://mcp.x/token"
    assert meta["registration_endpoint"] == "https://mcp.x/register"
    assert meta["code_challenge_methods_supported"] == ["S256"]


def test_protected_resource_metadata_points_at_issuer() -> None:
    meta = _provider().protected_resource_metadata("https://mcp.x")
    assert meta["authorization_servers"] == ["https://mcp.x"]


def test_register_requires_redirect_uris() -> None:
    with pytest.raises(OAuthError) as exc:
        _provider().register_client({})
    assert exc.value.error == "invalid_redirect_uri"


def test_register_issues_public_client() -> None:
    reg = _provider().register_client({"redirect_uris": ["https://claude.ai/cb"]})
    assert reg["client_id"]
    assert reg["token_endpoint_auth_method"] == "none"


# ---------------------------------------------------------------------------
# authorize + token
# ---------------------------------------------------------------------------


def test_authorize_rejects_unknown_client() -> None:
    with pytest.raises(OAuthError) as exc:
        _provider().validate_authorize(
            client_id="nope",
            redirect_uri="https://claude.ai/cb",
            response_type="code",
            code_challenge="c",
            code_challenge_method="S256",
            scope=None,
        )
    assert exc.value.error == "invalid_client"


def test_authorize_rejects_redirect_uri_mismatch() -> None:
    p = _provider()
    reg = p.register_client({"redirect_uris": ["https://claude.ai/cb"]})
    with pytest.raises(OAuthError):
        p.validate_authorize(
            client_id=reg["client_id"],
            redirect_uri="https://evil.example/cb",
            response_type="code",
            code_challenge="c",
            code_challenge_method="S256",
            scope=None,
        )


def test_authorize_requires_s256() -> None:
    p = _provider()
    reg = p.register_client({"redirect_uris": ["https://claude.ai/cb"]})
    with pytest.raises(OAuthError) as exc:
        p.validate_authorize(
            client_id=reg["client_id"],
            redirect_uri="https://claude.ai/cb",
            response_type="code",
            code_challenge="c",
            code_challenge_method="plain",
            scope=None,
        )
    assert exc.value.redirectable is True


def test_full_code_exchange_with_pkce() -> None:
    p = _provider()
    verifier, challenge = _pkce()
    reg = p.register_client({"redirect_uris": ["https://claude.ai/cb"]})
    client = p.validate_authorize(
        client_id=reg["client_id"],
        redirect_uri="https://claude.ai/cb",
        response_type="code",
        code_challenge=challenge,
        code_challenge_method="S256",
        scope="mcp",
    )
    code = p.issue_code(client, "https://claude.ai/cb", challenge, "mcp")
    tokens = p.exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://claude.ai/cb",
            "client_id": reg["client_id"],
            "code_verifier": verifier,
        }
    )
    assert tokens["token_type"] == "Bearer"
    assert p.validate_token(tokens["access_token"]) == "oauth:web"


def test_code_is_single_use() -> None:
    p = _provider()
    verifier, challenge = _pkce()
    reg = p.register_client({"redirect_uris": ["https://claude.ai/cb"]})
    client = p.validate_authorize(
        client_id=reg["client_id"],
        redirect_uri="https://claude.ai/cb",
        response_type="code",
        code_challenge=challenge,
        code_challenge_method="S256",
        scope="mcp",
    )
    code = p.issue_code(client, "https://claude.ai/cb", challenge, "mcp")
    args = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "https://claude.ai/cb",
        "client_id": reg["client_id"],
        "code_verifier": verifier,
    }
    p.exchange(args)
    with pytest.raises(OAuthError) as exc:
        p.exchange(args)
    assert exc.value.error == "invalid_grant"


def test_token_exchange_rejects_bad_verifier() -> None:
    p = _provider()
    _, challenge = _pkce()
    reg = p.register_client({"redirect_uris": ["https://claude.ai/cb"]})
    client = p.validate_authorize(
        client_id=reg["client_id"],
        redirect_uri="https://claude.ai/cb",
        response_type="code",
        code_challenge=challenge,
        code_challenge_method="S256",
        scope="mcp",
    )
    code = p.issue_code(client, "https://claude.ai/cb", challenge, "mcp")
    with pytest.raises(OAuthError) as exc:
        p.exchange(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://claude.ai/cb",
                "client_id": reg["client_id"],
                "code_verifier": "wrong-verifier",
            }
        )
    assert exc.value.error == "invalid_grant"


def test_refresh_token_grant() -> None:
    p = _provider()
    verifier, challenge = _pkce()
    reg = p.register_client({"redirect_uris": ["https://claude.ai/cb"]})
    client = p.validate_authorize(
        client_id=reg["client_id"],
        redirect_uri="https://claude.ai/cb",
        response_type="code",
        code_challenge=challenge,
        code_challenge_method="S256",
        scope="mcp",
    )
    code = p.issue_code(client, "https://claude.ai/cb", challenge, "mcp")
    tokens = p.exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://claude.ai/cb",
            "client_id": reg["client_id"],
            "code_verifier": verifier,
        }
    )
    refreshed = p.exchange(
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": reg["client_id"],
        }
    )
    assert refreshed["access_token"] != tokens["access_token"]
    assert p.validate_token(refreshed["access_token"]) == "oauth:web"


def test_expired_token_is_invalid() -> None:
    clock = {"t": 1000.0}
    p = OAuthProvider(OAuthConfig(login_secret="s", access_ttl=10), now=lambda: clock["t"])
    verifier, challenge = _pkce()
    reg = p.register_client({"redirect_uris": ["https://claude.ai/cb"]})
    client = p.validate_authorize(
        client_id=reg["client_id"],
        redirect_uri="https://claude.ai/cb",
        response_type="code",
        code_challenge=challenge,
        code_challenge_method="S256",
        scope="mcp",
    )
    code = p.issue_code(client, "https://claude.ai/cb", challenge, "mcp")
    tokens = p.exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://claude.ai/cb",
            "client_id": reg["client_id"],
            "code_verifier": verifier,
        }
    )
    assert p.validate_token(tokens["access_token"]) == "oauth:web"
    clock["t"] += 11
    assert p.validate_token(tokens["access_token"]) is None


# ---------------------------------------------------------------------------
# End-to-end through the FastAPI app
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    starlette_testclient = pytest.importorskip("starlette.testclient")
    from metaforge.mcp.__main__ import build_http_app

    class _FakeServer:
        adapters: dict = {}
        tool_ids: list = []

        async def handle_request(self, raw: str) -> str:
            return '{"jsonrpc":"2.0","id":"health","result":{"status":"ok"}}'

    provider = OAuthProvider(OAuthConfig(login_secret="open-sesame"))
    app = build_http_app(_FakeServer(), enable_sse=False, oauth=provider)
    return starlette_testclient.TestClient(app)


def test_protected_resource_metadata_served(client) -> None:
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    assert "authorization_servers" in resp.json()


def test_mcp_401_advertises_oauth(client) -> None:
    resp = client.post("/mcp", content="{}")
    assert resp.status_code == 401
    assert "resource_metadata=" in resp.headers.get("www-authenticate", "")


def test_end_to_end_connector_flow(client) -> None:
    verifier, challenge = _pkce()

    # 1. Dynamic client registration.
    reg = client.post(
        "/register", json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}
    )
    assert reg.status_code == 201
    client_id = reg.json()["client_id"]

    # 2. Authorize (login with the shared secret), don't follow the redirect.
    authz = client.post(
        "/authorize",
        data={
            "login_secret": "open-sesame",
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp",
            "state": "xyz",
        },
        follow_redirects=False,
    )
    assert authz.status_code == 302
    location = authz.headers["location"]
    assert location.startswith("https://claude.ai/api/mcp/auth_callback")
    assert "state=xyz" in location
    code = location.split("code=")[1].split("&")[0]

    # 3. Exchange the code for a token.
    tok = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert tok.status_code == 200
    access = tok.json()["access_token"]

    # 4. Call /mcp with the bearer token.
    ok = client.post("/mcp", content="{}", headers={"Authorization": f"Bearer {access}"})
    assert ok.status_code == 200


def test_authorize_wrong_secret_rejected(client) -> None:
    verifier, challenge = _pkce()
    reg = client.post("/register", json={"redirect_uris": ["https://claude.ai/cb"]})
    client_id = reg.json()["client_id"]
    resp = client.post(
        "/authorize",
        data={
            "login_secret": "wrong",
            "client_id": client_id,
            "redirect_uri": "https://claude.ai/cb",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 401
