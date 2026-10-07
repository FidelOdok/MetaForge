"""Gateway authentication: the seam, not any particular provider (FORGE-540).

The load-bearing assertions here are the *refusals*. MetaForge already carries
several fail-open auth paths — a guard that returns silently when its token is
unset, an MCP key check that reports "open mode" when unconfigured, unscoped
calls granted full access. Every one was a sensible local-dev convenience that
became a permanent state because nothing failed when it engaged.

So the tests that matter most are the ones proving this gateway refuses to
start rather than coming up silently open, and that a route nobody remembered
to annotate is protected anyway.

Since the hosted provider moved to its own repository, one refusal carries more
weight than it used to: naming a provider that is not installed. That is no
longer a typo-only scenario — it is what a gateway image built without the
cloud package looks like, and it must stop the process rather than quietly
serve every route to anyone.
"""

from __future__ import annotations

import sys

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from api_gateway.auth import (
    AuthConfigurationError,
    AuthMiddleware,
    AuthUnavailable,
    InvalidToken,
    Principal,
    load_auth_settings,
    load_provider,
)

SUBJECT = "3f9a2c14-0000-4000-8000-000000000001"


# ---------------------------------------------------------------------------
# A stand-in provider, so these tests exercise the seam and nothing behind it
# ---------------------------------------------------------------------------


class FakeProvider:
    """Accepts one token and rejects everything else."""

    name = "fake"

    def __init__(self, *, good_token: str = "good") -> None:
        self._good = good_token
        self.closed = False

    async def verify(self, token: str) -> Principal:
        if token != self._good:
            raise InvalidToken("Token rejected")
        return Principal(subject=SUBJECT, email="engineer@example.com", role="authenticated")

    async def aclose(self) -> None:
        self.closed = True


class UnreachableProvider(FakeProvider):
    """The identity provider cannot be reached, so there is no verdict."""

    async def verify(self, token: str) -> Principal:
        raise AuthUnavailable("connection refused")


class _StubEntryPoint:
    """The two attributes :func:`load_provider` actually uses.

    ``load()`` returns the object the entry point names — a class or factory —
    which ``load_provider`` then calls, exactly as ``importlib.metadata`` does.
    """

    def __init__(self, name: str, target, value: str = "tests:FakeProvider") -> None:
        self.name = name
        self.value = value
        self._target = target

    def load(self):
        return self._target


class _UnimportableEntryPoint(_StubEntryPoint):
    """An entry point whose module is not in this environment."""

    def load(self):
        raise ModuleNotFoundError("No module named 'metaforge_cloud'")


def _register(monkeypatch, **providers) -> None:
    """Make ``providers`` look installed on the ``metaforge.auth`` group.

    Each value is the object the entry point resolves to, so a test registers
    ``fake=FakeProvider`` — the class — not an instance of it.
    """
    entries = {
        name: target if isinstance(target, _StubEntryPoint) else _StubEntryPoint(name, target)
        for name, target in providers.items()
    }
    monkeypatch.setattr("api_gateway.auth.provider.available_providers", lambda: entries)


# ---------------------------------------------------------------------------
# Configuration: the refusals
# ---------------------------------------------------------------------------


class TestAuthConfigRefuses:
    def test_naming_an_uninstalled_provider_refuses_to_start(self, monkeypatch):
        """The whole point: no silent downgrade to an open gateway."""
        _register(monkeypatch)
        with pytest.raises(AuthConfigurationError) as exc:
            load_auth_settings(env={"METAFORGE_AUTH_MODE": "supabase"})
        assert "Refusing to start" in str(exc.value)
        assert "silently open" in str(exc.value)

    def test_refusal_names_what_is_installed(self, monkeypatch):
        """An operator needs to know what they can actually choose."""
        _register(monkeypatch, fake=FakeProvider)
        with pytest.raises(AuthConfigurationError) as exc:
            load_auth_settings(env={"METAFORGE_AUTH_MODE": "supbase"})
        assert "Installed providers: fake" in str(exc.value)

    def test_refusal_with_nothing_installed_says_where_to_get_one(self, monkeypatch):
        _register(monkeypatch)
        with pytest.raises(AuthConfigurationError) as exc:
            load_auth_settings(env={"METAFORGE_AUTH_MODE": "supabase"})
        assert "MetaForge Cloud" in str(exc.value)

    def test_a_provider_that_fails_to_import_is_fatal(self, monkeypatch):
        _register(monkeypatch, broken=_UnimportableEntryPoint("broken", None))
        with pytest.raises(AuthConfigurationError, match="failed to import"):
            load_provider("broken")

    def test_a_provider_that_cannot_verify_is_fatal(self, monkeypatch):
        """A plug-in resolving to the wrong object must not pass for one."""

        class NotAProvider:
            name = "wrong"

        _register(monkeypatch, wrong=lambda: NotAProvider())
        with pytest.raises(AuthConfigurationError, match="no verify"):
            load_provider("wrong")


class TestAuthConfigResolution:
    def test_default_is_off_and_needs_nothing(self):
        settings = load_auth_settings(env={})
        assert settings.mode == "off"
        assert settings.enabled is False

    def test_an_installed_provider_resolves(self, monkeypatch):
        _register(monkeypatch, fake=FakeProvider)
        settings = load_auth_settings(env={"METAFORGE_AUTH_MODE": "fake"})
        assert settings.mode == "fake"
        assert settings.enabled is True

    def test_mode_is_case_insensitive(self, monkeypatch):
        _register(monkeypatch, fake=FakeProvider)
        assert load_auth_settings(env={"METAFORGE_AUTH_MODE": "FAKE"}).mode == "fake"

    def test_load_provider_returns_the_registered_implementation(self, monkeypatch):
        _register(monkeypatch, fake=FakeProvider)
        assert isinstance(load_provider("fake"), FakeProvider)


# ---------------------------------------------------------------------------
# Middleware: default-deny
# ---------------------------------------------------------------------------


def _app(provider) -> FastAPI:
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

    app.add_middleware(AuthMiddleware, provider=provider)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(FakeProvider()))


class TestAuthMiddleware:
    def test_unauthenticated_request_is_challenged(self, client: TestClient):
        response = client.get("/v1/projects")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"].startswith("Bearer")

    def test_valid_token_passes(self, client: TestClient):
        response = client.get("/v1/projects", headers={"Authorization": "Bearer good"})
        assert response.status_code == 200

    def test_rejected_token_is_401(self, client: TestClient):
        response = client.get("/v1/projects", headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401

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

    def test_the_verified_principal_reaches_the_route(self):
        app = FastAPI()

        @app.get("/v1/whoami")
        async def whoami(request: Request) -> dict[str, str]:
            return {"actor": request.state.principal.actor_id}

        app.add_middleware(AuthMiddleware, provider=FakeProvider())
        response = TestClient(app).get("/v1/whoami", headers={"Authorization": "Bearer good"})
        assert response.json() == {"actor": f"user:{SUBJECT}"}

    def test_unreachable_identity_provider_is_503_not_401(self):
        """ "Cannot verify" must not be reported as "your credentials are bad"."""
        client = TestClient(_app(UnreachableProvider()))
        response = client.get("/v1/projects", headers={"Authorization": "Bearer good"})
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
        assert app.state.auth_settings.mode == "off"
        # No auth middleware installed at all, so local pays nothing for this.
        assert not any(m.cls is AuthMiddleware for m in app.user_middleware)

    def test_health_reports_the_live_auth_mode(self, monkeypatch):
        monkeypatch.delenv("METAFORGE_AUTH_MODE", raising=False)
        from api_gateway.health import get_reported_auth_mode
        from api_gateway.server import create_app

        create_app()
        assert get_reported_auth_mode() == "off"

    def test_wildcard_cors_with_auth_on_refuses_to_start(self, monkeypatch):
        """Wildcard origin + credentials is the one combination that must be
        impossible to deploy once tokens exist."""
        _register(monkeypatch, fake=FakeProvider)
        monkeypatch.setenv("METAFORGE_AUTH_MODE", "fake")
        monkeypatch.setenv("METAFORGE_CORS_ORIGINS", "*")
        from api_gateway.server import create_app

        with pytest.raises(AuthConfigurationError, match="wildcard"):
            create_app()

    def test_an_installed_provider_gets_the_middleware(self, monkeypatch):
        _register(monkeypatch, fake=FakeProvider)
        monkeypatch.setenv("METAFORGE_AUTH_MODE", "fake")
        monkeypatch.setenv("METAFORGE_CORS_ORIGINS", "https://app.metaforge.uk")
        from api_gateway.server import create_app

        app = create_app()
        assert app.state.auth_settings.enabled is True
        assert isinstance(app.state.auth_provider, FakeProvider)
        assert any(m.cls is AuthMiddleware for m in app.user_middleware)

    def test_a_gateway_image_missing_the_provider_will_not_start(self, monkeypatch):
        """The deployment mistake the entry-point indirection exists to catch."""
        _register(monkeypatch)
        monkeypatch.setenv("METAFORGE_AUTH_MODE", "supabase")
        monkeypatch.setenv("METAFORGE_CORS_ORIGINS", "https://app.metaforge.uk")
        from api_gateway.server import create_app

        with pytest.raises(AuthConfigurationError, match="not installed"):
            create_app()


class TestMinimalInstall:
    """A crypto stack stays the provider's dependency, never the gateway's.

    ``api_gateway/__init__.py`` imports ``server``, which imports the auth
    package, so anything touching ``api_gateway`` loads it — including a local
    gateway that will never verify a token. An eager ``import jwt`` therefore
    made PyJWT a hard dependency of the entire package. That regression shipped
    once and was caught by CI rather than locally, because the library happened
    to be present transitively in the dev environment.

    Moving verification behind an entry point is what makes this structural:
    there is no longer any import path from the gateway to a JWT library at
    all. These tests keep it that way.
    """

    @staticmethod
    def _hide_pyjwt(monkeypatch) -> None:
        """Make ``jwt`` unimportable for the duration of one test.

        Blocking ``meta_path`` alone is not enough: ``find_spec`` consults
        ``sys.modules`` first and returns a cached module's spec without ever
        reaching a finder, so cached entries are evicted too.
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

        assert create_app().state.auth_settings.mode == "off"

    def test_the_auth_package_imports_no_jwt_library(self, monkeypatch):
        """Not merely lazy — absent. The seam has no crypto behind it."""
        self._hide_pyjwt(monkeypatch)
        for module in [m for m in sys.modules if m.startswith("api_gateway")]:
            monkeypatch.delitem(sys.modules, module, raising=False)

        import api_gateway.auth as auth_pkg

        assert auth_pkg.MODE_OFF == "off"
