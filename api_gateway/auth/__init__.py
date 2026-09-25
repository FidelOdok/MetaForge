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
"""

from __future__ import annotations

from api_gateway.auth.config import (
    AuthConfigurationError,
    AuthMode,
    AuthSettings,
    load_auth_settings,
)
from api_gateway.auth.dependencies import current_principal, require_principal
from api_gateway.auth.jwks import JwksCache, JwksError
from api_gateway.auth.middleware import PUBLIC_PATHS, AuthMiddleware
from api_gateway.auth.principal import Principal
from api_gateway.auth.verifier import InvalidToken, TokenVerifier

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
