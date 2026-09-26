"""Gateway authentication (MetaForge Cloud).

This package was an empty ``.gitkeep`` placeholder from the repository's first
commit until MetaForge Cloud needed it. Everything the gateway serves — the
digital twin, projects, chat, file download — was open to anything that could
reach the port, which is why the dashboard's Settings page and
``docs/deployment/vercel.md`` both tell operators to keep the gateway on a
private network.

That stays true for local use. This package adds a second mode rather than
replacing the first:

* ``METAFORGE_AUTH_MODE=off`` (default) — unchanged. No middleware is
  installed, no token is required, no behaviour differs.
* ``METAFORGE_AUTH_MODE=supabase`` — every route except a short public
  allow-list requires a Supabase-issued bearer token.

Layout:

* :mod:`~api_gateway.auth.config` — mode resolution, and the refusal to start
  when cloud auth is requested but not configured
* :mod:`~api_gateway.auth.jwks` — signing-key fetch and cache
* :mod:`~api_gateway.auth.verifier` — token to principal, or an exception
* :mod:`~api_gateway.auth.middleware` — default-deny enforcement
* :mod:`~api_gateway.auth.dependencies` — reading the principal in a route

**On the lazy imports below.** ``api_gateway/__init__.py`` imports ``server``,
which imports this package, so anything that touches ``api_gateway`` at all
loads this module — including a local single-user gateway that will never
verify a token. Importing the JWT stack eagerly would therefore make PyJWT a
hard dependency of the whole package and break every minimal install, which is
exactly how CI caught it.

So the four names that need PyJWT resolve through :pep:`562` module
``__getattr__`` instead: they cost nothing until something asks for them, and
in ``auth_mode=off`` nothing ever does. ``config``, ``principal`` and
``dependencies`` stay eager — they are stdlib, Pydantic and FastAPI only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from api_gateway.auth.config import (
    AuthConfigurationError,
    AuthMode,
    AuthSettings,
    load_auth_settings,
)
from api_gateway.auth.dependencies import current_principal, require_principal
from api_gateway.auth.principal import Principal

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from api_gateway.auth.jwks import JwksCache, JwksError
    from api_gateway.auth.middleware import PUBLIC_PATHS, AuthMiddleware
    from api_gateway.auth.verifier import InvalidToken, TokenVerifier

#: Attribute name -> defining submodule, for the PyJWT-dependent exports.
_LAZY: dict[str, str] = {
    "JwksCache": "api_gateway.auth.jwks",
    "JwksError": "api_gateway.auth.jwks",
    "TokenVerifier": "api_gateway.auth.verifier",
    "InvalidToken": "api_gateway.auth.verifier",
    "AuthMiddleware": "api_gateway.auth.middleware",
    "PUBLIC_PATHS": "api_gateway.auth.middleware",
}


def __getattr__(name: str) -> Any:
    """Resolve the PyJWT-dependent exports on first use."""
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "PUBLIC_PATHS",
    "AuthConfigurationError",
    "AuthMiddleware",
    "AuthMode",
    "AuthSettings",
    "InvalidToken",
    "JwksCache",
    "JwksError",
    "Principal",
    "TokenVerifier",
    "current_principal",
    "load_auth_settings",
    "require_principal",
]
