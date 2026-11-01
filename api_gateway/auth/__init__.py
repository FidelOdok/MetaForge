"""Gateway authentication.

This package was an empty ``.gitkeep`` placeholder from the repository's first
commit until MetaForge Cloud needed it. Everything the gateway serves — the
digital twin, projects, chat, file download — was open to anything that could
reach the port, which is why the dashboard's Settings page and
``docs/deployment/vercel.md`` both tell operators to keep the gateway on a
private network.

That stays true for local use. This package adds a second mode rather than
replacing the first:

* ``METAFORGE_AUTH_MODE=off`` (default) — unchanged. No middleware is
  installed, no credential is required, no behaviour differs.
* ``METAFORGE_AUTH_MODE=<provider>`` — every route except a short public
  allow-list requires a bearer token that the named provider accepts.

What lives here is the half of that which belongs to every MetaForge gateway:
the shape of a verified caller, default-deny enforcement, and the refusal to
start when authentication was asked for and cannot be delivered. What does
*not* live here is any particular way of checking a credential. Providers
register on the ``metaforge.auth`` entry-point group and are installed
alongside the gateway — the hosted one ships separately as MetaForge Cloud
(FORGE-540).

Layout:

* :mod:`~api_gateway.auth.config` — mode resolution, and the refusal to start
  when authentication is requested but no provider can serve it
* :mod:`~api_gateway.auth.provider` — the plug-in seam: the protocol, the two
  failure classes, and entry-point discovery
* :mod:`~api_gateway.auth.middleware` — default-deny enforcement
* :mod:`~api_gateway.auth.principal` — the verified caller
* :mod:`~api_gateway.auth.dependencies` — reading the principal in a route
* :mod:`~api_gateway.auth.approver` — attributing a decision to a human.
  Imported directly rather than re-exported here: it reaches into
  :mod:`mcp_core.guardrails`, and ``import api_gateway`` should not drag that in

Everything exported here is stdlib, Pydantic, Starlette and FastAPI only. The
crypto stack a provider needs is that provider's dependency, so a local
single-user gateway installs none of it — which is the property CI caught us
breaking when the JWT imports were eager.
"""

from __future__ import annotations

from api_gateway.auth.config import (
    MODE_OFF,
    AuthConfigurationError,
    AuthSettings,
    load_auth_settings,
)
from api_gateway.auth.dependencies import current_principal, require_principal
from api_gateway.auth.middleware import PUBLIC_PATHS, AuthMiddleware
from api_gateway.auth.principal import Principal
from api_gateway.auth.provider import (
    ENTRY_POINT_GROUP,
    AuthProvider,
    AuthUnavailable,
    InvalidToken,
    available_providers,
    load_provider,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "MODE_OFF",
    "PUBLIC_PATHS",
    "AuthConfigurationError",
    "AuthMiddleware",
    "AuthProvider",
    "AuthSettings",
    "AuthUnavailable",
    "InvalidToken",
    "Principal",
    "available_providers",
    "current_principal",
    "load_auth_settings",
    "load_provider",
    "require_principal",
]
