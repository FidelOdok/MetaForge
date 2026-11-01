"""Gateway authentication configuration.

MetaForge runs in two shapes from one codebase:

* **local** (``METAFORGE_AUTH_MODE=off``, the default) — a single-user gateway
  on your own machine. No accounts, no tokens, behaviour byte-identical to what
  the gateway did before authentication existed.
* **authenticated** (``METAFORGE_AUTH_MODE=<provider>``) — the same gateway
  reachable by more than one person, with an installed provider deciding who
  each caller is. The hosted provider ships separately as MetaForge Cloud; see
  :mod:`api_gateway.auth.provider` for the seam it plugs into.

The whole point of this module is the transition between those two, and the one
rule that matters is: **a gateway that was meant to be authenticated must never
silently end up open.**

That failure mode is not hypothetical here. The gateway already carries four
fail-open paths — ``_require_admin`` returns silently when its token is unset
(``api_gateway/harness/routes.py``), ``mcp_core.auth.verify_api_key`` returns
"open_mode" when no key is configured, and unscoped MCP calls are granted *full*
access rather than none. Each was a reasonable local-dev convenience. Together
they are a pattern, and the pattern is what this module refuses to extend.

So configuration here is deliberately brittle in one direction:

* ``off`` is the default, and needs nothing.
* Any other mode must name a provider registered on the ``metaforge.auth``
  entry-point group. One that is absent, or installed but misconfigured, raises
  :class:`AuthConfigurationError` **at application construction**. The process
  does not start. It does not warn and continue, and it does not fall back to
  ``off`` — a gateway that downgrades itself to open on a typo, or because an
  image was built without the provider in it, is the exact outcome this design
  exists to prevent.

The resolved mode is reported on ``GET /health`` so operators can confirm what a
running gateway is actually doing, rather than inferring it from the config they
believe they deployed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "MODE_OFF",
    "AuthConfigurationError",
    "AuthSettings",
    "load_auth_settings",
    "missing_provider_message",
]

#: The mode meaning "no authentication at all". The default, and the only one
#: that needs nothing installed.
MODE_OFF = "off"


class AuthConfigurationError(RuntimeError):
    """Raised when authentication is requested but cannot be configured safely.

    Always fatal. Callers must not catch this to fall back to an open gateway;
    that is the bug this exception exists to make impossible.
    """


@dataclass(frozen=True, slots=True)
class AuthSettings:
    """Resolved authentication settings.

    Deliberately thin. Everything a particular provider needs — issuer,
    audience, signing keys — belongs to that provider and is read by it, so
    that adding one never means editing this file. All the gateway itself has
    to know is whether callers must present a credential, and which provider
    answers for them.

    Construct via :func:`load_auth_settings` rather than directly; the
    validation is the point, and this dataclass does none of it.
    """

    #: ``off``, or the entry-point name of a registered provider.
    mode: str = MODE_OFF

    @property
    def enabled(self) -> bool:
        """True when callers must present a credential."""
        return self.mode != MODE_OFF


def load_auth_settings(env: dict[str, str] | None = None) -> AuthSettings:
    """Resolve :class:`AuthSettings` from the environment.

    Parameters
    ----------
    env:
        Optional override mapping, for tests. Defaults to ``os.environ``.

    Raises
    ------
    AuthConfigurationError
        If the mode names a provider that is not installed. Never returns a
        downgraded ``off`` result in that case.
    """
    if env is not None:
        raw_mode = (env.get("METAFORGE_AUTH_MODE") or "").strip()
    else:
        raw_mode = os.environ.get("METAFORGE_AUTH_MODE", "").strip()

    mode = raw_mode.lower() or MODE_OFF

    if mode == MODE_OFF:
        logger.info("gateway_auth_disabled", mode=mode)
        return AuthSettings(mode=mode)

    # Resolved here, before anything is imported, so that a typo or an image
    # built without the provider fails while the message can still name both
    # what was asked for and what is actually available.
    from api_gateway.auth.provider import available_providers

    registered = available_providers()
    if mode not in registered:
        raise AuthConfigurationError(missing_provider_message(mode, sorted(registered)))

    logger.info("gateway_auth_enabled", mode=mode)
    return AuthSettings(mode=mode)


def missing_provider_message(mode: str, installed: list[str]) -> str:
    """Say what is wrong and what the operator can actually choose instead.

    Shared with :mod:`api_gateway.auth.provider`, which can reach the same
    conclusion later than this module does, so that both refusals read alike.
    """
    if installed:
        have = f"Installed providers: {', '.join(installed)}."
    else:
        have = (
            "No authentication provider is installed in this environment. The hosted "
            "one ships separately as MetaForge Cloud; see "
            "docs/deployment/authentication.md for that, or for writing your own."
        )
    return (
        f"METAFORGE_AUTH_MODE={mode!r} names an authentication provider that is not "
        f"installed. {have} Refusing to start rather than falling back to 'off' — a "
        "gateway meant to be authenticated must never come up silently open."
    )
