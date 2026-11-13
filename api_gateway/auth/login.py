"""Signing a person in with their own credentials (FORGE-859).

The gateway verifies tokens its provider issued; it never sees a password.
The MCP sidecar is different: claude.ai and Claude Code sign in to it through
its own OAuth server (``metaforge/mcp/oauth.py``), whose login page has to
establish *who* is signing in. Without a provider that can answer, that page
is one shared secret, and every caller holding it is the same person, which
is fine for a single team and wrong for a gateway shared by several accounts.

A provider that can check an email and password implements
:class:`PasswordLogin`. The sidecar then signs people in with it and binds
each MCP token to the :class:`~api_gateway.auth.principal.Principal` it
returns, so the same project scope applies over MCP as over HTTP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from api_gateway.auth.principal import Principal

__all__ = ["PasswordLogin", "supports_password_login"]


@runtime_checkable
class PasswordLogin(Protocol):
    """Optional provider capability: verify a person's email and password."""

    async def verify_password(self, email: str, password: str) -> Principal:
        """The principal these credentials belong to.

        Raises :class:`~api_gateway.auth.provider.InvalidToken` for wrong
        credentials and :class:`~api_gateway.auth.provider.AuthUnavailable`
        when the answer cannot be reached; the login page tells the two apart.
        """
        ...


def supports_password_login(provider: object) -> bool:
    """True when ``provider`` can sign a person in with email and password."""
    return callable(getattr(provider, "verify_password", None))
