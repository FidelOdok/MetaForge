"""MCP sign-in with the person's own account (FORGE-859, part A).

When the gateway's auth provider can check a password, the MCP OAuth server
signs people in with it and binds each token to the verified subject. The
shared secret is then refused: holding it would mean acting outside any
account. Everything else about OAuth (registration, PKCE, refresh) is the
existing flow in ``test_mcp_oauth.py``.
"""

from __future__ import annotations

import pytest

from api_gateway.auth.login import supports_password_login
from api_gateway.auth.principal import Principal
from api_gateway.auth.provider import AuthUnavailable, InvalidToken
from metaforge.mcp.oauth import OAuthConfig, OAuthProvider, subject_of
from tests.unit.test_mcp_oauth import _pkce

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
ALICE = "6f1c2b4e-0000-4000-8000-000000000001"


class Login:
    def __init__(self) -> None:
        self.down = False

    async def verify_password(self, email: str, password: str) -> Principal:
        if self.down:
            raise AuthUnavailable("identity provider unreachable")
        if (email, password) != ("alice@example.com", "correct-horse"):
            raise InvalidToken("wrong credentials")
        return Principal(subject=ALICE, email=email)


# ── config and provider ──────────────────────────────────────────────


def test_identity_login_turns_oauth_on_without_a_secret_and_ignores_one() -> None:
    cfg = OAuthConfig.from_env({}, identity_login=True)
    assert cfg is not None and cfg.enabled and cfg.verified_identity
    with_secret = OAuthConfig.from_env({"METAFORGE_OAUTH_LOGIN_SECRET": "s"}, identity_login=True)
    assert with_secret is not None and with_secret.login_secret == ""
    assert OAuthProvider(with_secret).verify_login("s") is False


def test_shared_secret_login_is_unchanged() -> None:
    cfg = OAuthConfig.from_env({"METAFORGE_OAUTH_LOGIN_SECRET": "s"})
    assert cfg is not None and not cfg.identity_login and not cfg.verified_identity
    assert OAuthProvider(cfg).verify_login("s") is True
    assert OAuthConfig.from_env({}) is None


def test_a_typed_name_can_never_pass_for_a_verified_subject() -> None:
    provider = OAuthProvider(OAuthConfig(login_secret="s"))
    client = provider.register_client({"redirect_uris": [CALLBACK]})
    _, challenge = _pkce()
    registered = provider.validate_authorize(
        client_id=str(client["client_id"]),
        redirect_uri=CALLBACK,
        response_type="code",
        code_challenge=challenge,
        code_challenge_method="S256",
        scope=None,
    )
    code = provider.issue_code(registered, CALLBACK, challenge, None, operator=f"sub:{ALICE}")
    actor = provider._store.codes[code].actor_id  # noqa: SLF001
    assert subject_of(actor) is None
    assert subject_of(f"sub:{ALICE}") == ALICE
    assert subject_of("oauth:web") is None


def test_a_provider_with_verify_password_supports_login() -> None:
    assert supports_password_login(Login())
    assert not supports_password_login(object())


# ── through the HTTP app ─────────────────────────────────────────────


@pytest.fixture
def login() -> Login:
    return Login()


@pytest.fixture
def client(login: Login):  # type: ignore[no-untyped-def]
    starlette_testclient = pytest.importorskip("starlette.testclient")
    from metaforge.mcp.__main__ import build_http_app

    server = _fake_server()
    provider = OAuthProvider(OAuthConfig.from_env({}, identity_login=True))  # type: ignore[arg-type]
    app = build_http_app(server, enable_sse=False, oauth=provider, login=login)
    test_client = starlette_testclient.TestClient(app)
    test_client.oauth = provider  # type: ignore[attr-defined]
    return test_client


def _fake_server():  # type: ignore[no-untyped-def]
    class _FakeServer:
        adapters: dict = {}
        tool_ids: list = []

        def declare_auth_posture(self, posture) -> None:  # type: ignore[no-untyped-def]
            self.posture = posture

        def declare_caller(self, caller) -> None:  # type: ignore[no-untyped-def]
            self.caller = caller

        def attach_elicitor(self, elicitor) -> None:  # type: ignore[no-untyped-def]
            pass

        def attach_notifier(self, notifier) -> None:  # type: ignore[no-untyped-def]
            pass

        def attach_service_auth(self, key, verifier) -> bool:  # type: ignore[no-untyped-def]
            return False

        def attach_client_task_grant(self, verifier) -> bool:  # type: ignore[no-untyped-def]
            return False

        async def authenticate_service_caller(self, ctx, presented_key, inbound):  # type: ignore[no-untyped-def]
            return ctx

        async def handle_request(self, raw: str) -> str:
            return '{"jsonrpc":"2.0","id":"x","result":{}}'

    return _FakeServer()


def _authorize(client, **fields):  # type: ignore[no-untyped-def]
    _, challenge = _pkce()
    client_id = client.post("/register", json={"redirect_uris": [CALLBACK]}).json()["client_id"]
    data = {
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **fields,
    }
    return client_id, client.post("/authorize", data=data, follow_redirects=False)


def test_the_login_page_asks_for_an_email_and_password(client) -> None:  # type: ignore[no-untyped-def]
    _, challenge = _pkce()
    client_id = client.post("/register", json={"redirect_uris": [CALLBACK]}).json()["client_id"]
    page = client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert page.status_code == 200
    assert 'name="email"' in page.text and 'name="password"' in page.text
    assert "login_secret" not in page.text


def test_signing_in_binds_the_token_to_the_person(client) -> None:  # type: ignore[no-untyped-def]
    verifier, _ = _pkce()
    client_id, authz = _authorize(client, email="alice@example.com", password="correct-horse")
    assert authz.status_code == 302
    code = authz.headers["location"].split("code=")[1].split("&")[0]
    tok = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CALLBACK,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    access = tok.json()["access_token"]
    assert subject_of(client.oauth.validate_token(access)) == ALICE
    refreshed = client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": tok.json()["refresh_token"]},
    ).json()["access_token"]
    assert subject_of(client.oauth.validate_token(refreshed)) == ALICE


def test_wrong_credentials_and_the_shared_secret_are_refused(client) -> None:  # type: ignore[no-untyped-def]
    _, wrong = _authorize(client, email="alice@example.com", password="nope")
    assert wrong.status_code == 401 and "Wrong email or password" in wrong.text
    _, secret = _authorize(client, login_secret="anything")
    assert secret.status_code == 401


def test_an_unreachable_identity_provider_is_503_not_wrong_password(  # type: ignore[no-untyped-def]
    client, login: Login
) -> None:
    login.down = True
    _, resp = _authorize(client, email="alice@example.com", password="correct-horse")
    assert resp.status_code == 503
    assert "unavailable" in resp.text
