"""Where an authentication implementation plugs into the gateway (FORGE-540).

MetaForge is open core, and authentication is the seam. This repository knows
that a caller *may* have to prove who they are, what a proven caller looks like
(:class:`~api_gateway.auth.principal.Principal`), and what has to happen when
the proof is absent or cannot be checked. It does not know how to check one.

An implementation registers itself on the ``metaforge.auth`` entry-point group::

    # pyproject.toml of the implementing distribution
    [project.entry-points."metaforge.auth"]
    supabase = "metaforge_cloud.auth:SupabaseAuthProvider"

``METAFORGE_AUTH_MODE`` then names the registered provider to use, and ``off``
— the default — means none. Installing nothing leaves the gateway exactly as it
was before authentication existed: no middleware, no crypto stack, no token.

**Naming a provider that is not installed is fatal.** That is the whole reason
this indirection exists rather than an ``if mode == "supabase"``. The failure
mode worth engineering against is a gateway that was *meant* to be
authenticated and silently is not, and "the plugin isn't in this image" is
precisely the deployment mistake that produces one. So an unresolvable mode
stops the process at construction; it never degrades to ``off``.

Two failure classes, kept apart because they mean different things to a caller:

* :class:`InvalidToken` — we checked, and the answer is no. HTTP 401.
* :class:`AuthUnavailable` — we could not reach an answer, because the identity
  provider is unreachable or is publishing something unparseable. HTTP 503.
  Returning 401 here would blame a legitimate user's credentials for our outage.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint, entry_points
from typing import Protocol

import structlog

from api_gateway.auth.config import AuthConfigurationError, missing_provider_message
from api_gateway.auth.principal import Principal

logger = structlog.get_logger(__name__)

__all__ = [
    "ENTRY_POINT_GROUP",
    "AuthProvider",
    "AuthUnavailable",
    "InvalidToken",
    "available_providers",
    "load_provider",
]

#: Entry-point group an authentication implementation registers on. The name it
#: registers under is the value ``METAFORGE_AUTH_MODE`` takes.
ENTRY_POINT_GROUP = "metaforge.auth"


class InvalidToken(Exception):
    """The credential was examined and rejected."""


class AuthUnavailable(RuntimeError):
    """No verdict could be reached.

    Distinct from a credential being *invalid*: this means we could not
    determine whether it is, which is a 503 rather than a 401.
    """


class AuthProvider(Protocol):
    """Turns a bearer token into a :class:`Principal`, or raises.

    There is deliberately no third outcome — no "probably fine", no degraded
    mode where an unverifiable token passes with a warning logged.
    """

    #: The mode name this provider answers to, matching its entry-point name.
    name: str

    async def verify(self, token: str) -> Principal:
        """Return the caller ``token`` identifies.

        Raises
        ------
        InvalidToken
            The token is malformed, expired, wrongly signed, or fails a
            provider-specific check.
        AuthUnavailable
            The verdict could not be reached.
        """
        ...

    async def aclose(self) -> None:
        """Release any resources held, such as an HTTP client."""
        ...


def available_providers() -> dict[str, EntryPoint]:
    """Registered providers by mode name, without importing any of them."""
    return {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}


def load_provider(mode: str) -> AuthProvider:
    """Import and construct the provider registered as ``mode``.

    The provider reads its own configuration when constructed, so a missing or
    contradictory setting surfaces here as :class:`AuthConfigurationError` —
    at application construction, where it stops the process.

    Raises
    ------
    AuthConfigurationError
        No provider is registered under ``mode``, the entry point fails to
        import, or what it yields is not usable as a provider.
    """
    registered = available_providers()
    entry = registered.get(mode)
    if entry is None:
        raise AuthConfigurationError(missing_provider_message(mode, sorted(registered)))

    try:
        factory = entry.load()
    except Exception as exc:  # noqa: BLE001 - re-raised as a fatal config error
        raise AuthConfigurationError(
            f"METAFORGE_AUTH_MODE={mode!r} is registered by {entry.value!r}, which "
            f"failed to import: {exc}. Refusing to start rather than leaving every "
            "route open."
        ) from exc

    provider = factory()
    if not callable(getattr(provider, "verify", None)):
        raise AuthConfigurationError(
            f"METAFORGE_AUTH_MODE={mode!r} resolved to {type(provider).__name__}, which "
            "has no verify() and so cannot authenticate anything. Refusing to start."
        )

    logger.info("gateway_auth_provider_loaded", mode=mode, implementation=entry.value)
    return provider
