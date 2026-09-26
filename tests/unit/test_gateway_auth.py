"""Gateway authentication (MetaForge Cloud, Phase 1).

The load-bearing assertions here are the *refusals*. MetaForge already carries
several fail-open auth paths — a guard that returns silently when its token is
unset, an MCP key check that reports "open mode" when unconfigured, unscoped
calls granted full access. Every one was a sensible local-dev convenience that
became a permanent state because nothing failed when it engaged.

So the tests that matter most are the ones proving this gateway refuses to
start rather than coming up silently open, and that a route nobody remembered
to annotate is protected anyway.
"""

from __future__ import annotations

import sys
import time

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.auth import (
    AuthConfigurationError,
    AuthMiddleware,
    AuthMode,
    InvalidToken,
    JwksError,
    Principal,
    TokenVerifier,
    load_auth_settings,
)
from api_gateway.auth.config import DEFAULT_AUDIENCE

# At least 32 bytes: PyJWT warns below the RFC 7518 §3.2 minimum for HS256.
SECRET = "test-signing-secret-at-least-32-bytes-long"
ISSUER = "https://proj.supabase.co/auth/v1"


def _settings(**overrides):
    env = {
        "METAFORGE_AUTH_MODE": "supabase",
        "METAFORGE_SUPABASE_JWT_SECRET": SECRET,
        **overrides,
    }
    return load_auth_settings(env=env)


def _token(**claims) -> str:
    payload = {
        "sub": "3f9a2c14-0000-4000-8000-000000000001",
        "aud": DEFAULT_AUDIENCE,
        "email": "engineer@example.com",
        "role": "authenticated",
        "exp": int(time.time()) + 3600,
        **claims,
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# Configuration: the refusals
# ---------------------------------------------------------------------------


class TestAuthConfigRefuses:
    def test_cloud_mode_without_any_verification_method_refuses_to_start(self):
        """The whole point: no silent downgrade to an open gateway."""
        with pytest.raises(AuthConfigurationError) as exc:
            load_auth_settings(env={"METAFORGE_AUTH_MODE": "supabase"})
        assert "Refusing to start" in str(exc.value)

    def test_unrecognised_mode_refuses_rather_than_meaning_off(self):
        with pytest.raises(AuthConfigurationError) as exc:
            load_auth_settings(env={"METAFORGE_AUTH_MODE": "supbase"})
        assert "not a valid mode" in str(exc.value)

    def test_contradictory_signing_schemes_refuse(self):
        with pytest.raises(AuthConfigurationError):
            load_auth_settings(
                env={
                    "METAFORGE_AUTH_MODE": "supabase",
                    "METAFORGE_SUPABASE_JWKS_URL": "https://x/jwks",
                    "METAFORGE_SUPABASE_JWT_SECRET": "s",
                }
            )

    def test_plaintext_supabase_url_refuses(self):
        with pytest.raises(AuthConfigurationError) as exc:
            load_auth_settings(
                env={
                    "METAFORGE_AUTH_MODE": "supabase",
                    "METAFORGE_SUPABASE_URL": "http://proj.supabase.co",
                }
            )
        assert "https" in str(exc.value)


class TestAuthConfigResolution:
    def test_default_is_off_and_needs_nothing(self):
        settings = load_auth_settings(env={})
        assert settings.mode is AuthMode.OFF
        assert settings.enabled is False

    def test_project_url_derives_jwks_and_issuer(self):
        settings = load_auth_settings(
            env={
                "METAFORGE_AUTH_MODE": "supabase",
                "METAFORGE_SUPABASE_URL": "https://proj.supabase.co/",
            }
        )
        assert settings.jwks_url == "https://proj.supabase.co/auth/v1/.well-known/jwks.json"
        assert settings.issuer == ISSUER
        assert settings.algorithms == ["RS256", "ES256"]

    def test_algorithms_are_pinned_to_the_configured_scheme(self):
        """A symmetric-secret gateway must not also accept asymmetric tokens."""
        assert _settings().algorithms == ["HS256"]


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


class TestTokenVerifier:
    async def test_valid_token_yields_a_principal(self):
        principal = await TokenVerifier(_settings()).verify(_token())
        assert isinstance(principal, Principal)
        assert principal.subject == "3f9a2c14-0000-4000-8000-000000000001"
        assert principal.email == "engineer@example.com"
        assert principal.actor_id == "user:3f9a2c14-0000-4000-8000-000000000001"

    async def test_expired_token_is_rejected(self):
        with pytest.raises(InvalidToken, match="expired"):
            await TokenVerifier(_settings()).verify(_token(exp=int(time.time()) - 10))

    async def test_wrong_audience_is_rejected(self):
        with pytest.raises(InvalidToken, match="audience"):
            await TokenVerifier(_settings()).verify(_token(aud="some-other-service"))

    async def test_token_signed_with_another_key_is_rejected(self):
        forged = jwt.encode(
            {"sub": "x", "aud": DEFAULT_AUDIENCE, "exp": int(time.time()) + 60},
            "a-different-secret-also-at-least-32-bytes",
            algorithm="HS256",
        )
        with pytest.raises(InvalidToken):
            await TokenVerifier(_settings()).verify(forged)

    async def test_alg_none_token_is_rejected(self):
        """The classic JWT downgrade attack."""
        unsigned = jwt.encode(
            {"sub": "x", "aud": DEFAULT_AUDIENCE, "exp": int(time.time()) + 60},
            key="",
            algorithm="none",
        )
        with pytest.raises(InvalidToken):
            await TokenVerifier(_settings()).verify(unsigned)

    async def test_token_without_subject_is_rejected(self):
        payload = {"aud": DEFAULT_AUDIENCE, "exp": int(time.time()) + 60}
        with pytest.raises(InvalidToken):
            await TokenVerifier(_settings()).verify(jwt.encode(payload, SECRET, algorithm="HS256"))


# ---------------------------------------------------------------------------
# Middleware: default-deny
# ---------------------------------------------------------------------------


def _app(verifier: TokenVerifier) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/v1/projects")
    async def projects() -> dict[str, list[str]]:
        return {"projects": []}

    # Deliberately un-annotated: nothing here opts into protection. It must be
    # protected regardless, because that is what middleware buys over a
    # per-route dependency.
    @app.get("/v1/some/route/nobody/remembered")
    async def forgotten() -> dict[str, bool]:
        return {"reached": True}

    app.add_middleware(AuthMiddleware, verifier=verifier)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(TokenVerifier(_settings())))


class TestAuthMiddleware:
    def test_unauthenticated_request_is_challenged(self, client: TestClient):
        response = client.get("/v1/projects")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"].startswith("Bearer")

    def test_valid_token_passes(self, client: TestClient):
        response = client.get("/v1/projects", headers={"Authorization": f"Bearer {_token()}"})
        assert response.status_code == 200

    def test_a_route_nobody_annotated_is_still_protected(self, client: TestClient):
        assert client.get("/v1/some/route/nobody/remembered").status_code == 401

    def test_health_stays_public(self, client: TestClient):
        """Probes cannot hold tokens, and this is how auth_mode is inspected."""
        assert client.get("/health").status_code == 200

    def test_cors_preflight_is_not_challenged(self, client: TestClient):
        response = client.options(
            "/v1/projects",
            headers={
                "Origin": "https://app.metaforge.uk",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code != 401

    @pytest.mark.parametrize(
        "header",
        ["", "Bearer", "Bearer   ", "Basic abc123", "Token abc123", "bearer"],
    )
    def test_malformed_authorization_headers_are_challenged(self, client: TestClient, header: str):
        response = client.get("/v1/projects", headers={"Authorization": header})
        assert response.status_code == 401

    def test_unreachable_identity_provider_is_503_not_401(self):
        """ "Cannot verify" must not be reported as "your credentials are bad"."""

        class Unreachable(TokenVerifier):
            async def verify(self, token: str) -> Principal:
                raise JwksError("connection refused")

        client = TestClient(_app(Unreachable(_settings())))
        response = client.get("/v1/projects", headers={"Authorization": f"Bearer {_token()}"})
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# Application wiring
# ---------------------------------------------------------------------------


class TestCreateApp:
    """``create_app`` must carry the refusals, not just the config loader."""

    def test_local_gateway_builds_with_no_configuration(self, monkeypatch):
        monkeypatch.delenv("METAFORGE_AUTH_MODE", raising=False)
        from api_gateway.server import create_app

        app = create_app()
        assert app.state.auth_settings.mode is AuthMode.OFF
        # No auth middleware installed at all, so local pays nothing for this.
        assert not any(m.cls is AuthMiddleware for m in app.user_middleware)

    def test_health_reports_the_live_auth_mode(self, monkeypatch):
        monkeypatch.delenv("METAFORGE_AUTH_MODE", raising=False)
        from api_gateway.health import get_reported_auth_mode
        from api_gateway.server import create_app

        create_app()
        assert get_reported_auth_mode() == "off"

    def test_cloud_mode_with_wildcard_cors_refuses_to_start(self, monkeypatch):
        """Wildcard origin + credentials is the one combination that must be
        impossible to deploy once tokens exist."""
        monkeypatch.setenv("METAFORGE_AUTH_MODE", "supabase")
        monkeypatch.setenv("METAFORGE_SUPABASE_URL", "https://proj.supabase.co")
        monkeypatch.setenv("METAFORGE_CORS_ORIGINS", "*")
        from api_gateway.server import create_app

        with pytest.raises(AuthConfigurationError, match="wildcard"):
            create_app()

    def test_cloud_mode_installs_the_middleware(self, monkeypatch):
        monkeypatch.setenv("METAFORGE_AUTH_MODE", "supabase")
        monkeypatch.setenv("METAFORGE_SUPABASE_URL", "https://proj.supabase.co")
        monkeypatch.setenv("METAFORGE_CORS_ORIGINS", "https://app.metaforge.uk")
        from api_gateway.server import create_app

        app = create_app()
        assert app.state.auth_settings.enabled is True
        assert any(m.cls is AuthMiddleware for m in app.user_middleware)


class TestMinimalInstall:
    """PyJWT is a cloud-only dependency and must stay one.

    ``api_gateway/__init__.py`` imports ``server``, which imports the auth
    package, so anything touching ``api_gateway`` loads it — including a local
    gateway that will never verify a token. An eager ``import jwt`` therefore
    makes PyJWT a hard dependency of the entire package. That regression
    shipped once and was caught by CI rather than locally, because the library
    happened to be present transitively in the dev environment.
    """

    @staticmethod
    def _hide_pyjwt(monkeypatch) -> None:
        """Make ``jwt`` unimportable for the duration of one test.

        Blocking ``meta_path`` alone is not enough: ``find_spec`` consults
        ``sys.modules`` first and returns a cached module's spec without ever
        reaching a finder. This module imports ``jwt`` at the top, so it is
        always cached — the eviction below is what makes the check honest.
        """

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name == "jwt" or name.startswith("jwt."):
                    raise ImportError("No module named 'jwt'")
                return None

        for module in [m for m in sys.modules if m == "jwt" or m.startswith("jwt.")]:
            monkeypatch.delitem(sys.modules, module, raising=False)
        monkeypatch.setattr(sys, "meta_path", [Blocker(), *sys.meta_path])

    def test_local_gateway_builds_without_pyjwt(self, monkeypatch):
        monkeypatch.delenv("METAFORGE_AUTH_MODE", raising=False)
        self._hide_pyjwt(monkeypatch)
        for module in [m for m in sys.modules if m.startswith("api_gateway")]:
            monkeypatch.delitem(sys.modules, module, raising=False)

        from api_gateway.server import create_app

        # Compared by value, not identity: purging the modules above means the
        # freshly imported AuthMode is a different class object from the one
        # this test module imported.
        assert create_app().state.auth_settings.mode.value == "off"

    def test_cloud_mode_without_pyjwt_refuses_by_name(self, monkeypatch):
        """Named at startup, not discovered as a 500 on the first request."""
        self._hide_pyjwt(monkeypatch)
        with pytest.raises(AuthConfigurationError, match="PyJWT"):
            load_auth_settings(
                env={
                    "METAFORGE_AUTH_MODE": "supabase",
                    "METAFORGE_SUPABASE_URL": "https://proj.supabase.co",
                }
            )
