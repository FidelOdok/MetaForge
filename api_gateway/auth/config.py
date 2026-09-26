"""Gateway authentication configuration.

MetaForge runs in two shapes from one codebase:

* **local** (``METAFORGE_AUTH_MODE=off``, the default) — a single-user gateway on
  your own machine. No accounts, no tokens, behaviour byte-identical to what the
  gateway did before authentication existed.
* **cloud** (``METAFORGE_AUTH_MODE=supabase``) — the same gateway reachable by more
  than one person, with Supabase as the identity provider.

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
* ``supabase`` with missing or contradictory settings raises
  :class:`AuthConfigurationError` **at application construction**. The process
  does not start. It does not warn and continue, and it does not fall back to
  ``off`` — a gateway that downgrades itself to open on a typo is the exact
  outcome this design exists to prevent.
* An unrecognised mode is an error too, so ``METAFORGE_AUTH_MODE=supbase`` fails
  loudly instead of quietly meaning ``off``.

The resolved mode is reported on ``GET /health`` so operators can confirm what a
running gateway is actually doing, rather than inferring it from the config they
believe they deployed.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "AuthConfigurationError",
    "AuthMode",
    "AuthSettings",
    "load_auth_settings",
]

#: Supabase mints access tokens with this audience for a signed-in end user.
DEFAULT_AUDIENCE = "authenticated"

#: Path, relative to the Supabase project URL, of the JWKS document used to
#: verify asymmetrically-signed access tokens.
JWKS_PATH = "/auth/v1/.well-known/jwks.json"

#: Path of the token issuer, which must match the ``iss`` claim.
ISSUER_PATH = "/auth/v1"


class AuthConfigurationError(RuntimeError):
    """Raised when authentication is requested but cannot be configured safely.

    Always fatal. Callers must not catch this to fall back to an open gateway;
    that is the bug this exception exists to make impossible.
    """


class AuthMode(StrEnum):
    """How the gateway authenticates callers."""

    #: Single-user local gateway. No credentials required or checked.
    OFF = "off"

    #: Verify Supabase-issued JWTs on every request.
    SUPABASE = "supabase"


@dataclass(frozen=True, slots=True)
class AuthSettings:
    """Resolved, validated authentication settings.

    Construct via :func:`load_auth_settings` rather than directly — the
    validation is the point, and this dataclass does none of it.
    """

    mode: AuthMode

    #: JWKS document URL. Set when ``mode`` is SUPABASE and asymmetric signing
    #: keys are in use. Mutually exclusive with ``jwt_secret``.
    jwks_url: str | None = None

    #: Shared HMAC secret, for Supabase projects still on the legacy symmetric
    #: signing key. Mutually exclusive with ``jwks_url``.
    jwt_secret: str | None = None

    #: Expected ``iss`` claim. ``None`` skips the issuer check.
    issuer: str | None = None

    #: Expected ``aud`` claim.
    audience: str = DEFAULT_AUDIENCE

    @property
    def enabled(self) -> bool:
        """True when callers must present a credential."""
        return self.mode is not AuthMode.OFF

    @property
    def algorithms(self) -> list[str]:
        """Signature algorithms to accept.

        Pinned to the signing scheme actually configured. Accepting both
        families at once would let a token signed with the symmetric secret
        satisfy a gateway that believes it is verifying against a public key.
        """
        return ["HS256"] if self.jwt_secret else ["RS256", "ES256"]


def _clean(name: str) -> str:
    return os.environ.get(name, "").strip()


def load_auth_settings(env: dict[str, str] | None = None) -> AuthSettings:
    """Resolve :class:`AuthSettings` from the environment.

    Parameters
    ----------
    env:
        Optional override mapping, for tests. Defaults to ``os.environ``.

    Raises
    ------
    AuthConfigurationError
        If the mode is unrecognised, or is ``supabase`` and the settings are
        absent, incomplete or contradictory. Never returns a downgraded
        ``off`` result in these cases.
    """
    get = (lambda k: (env.get(k) or "").strip()) if env is not None else _clean

    raw_mode = get("METAFORGE_AUTH_MODE") or AuthMode.OFF.value
    try:
        mode = AuthMode(raw_mode.lower())
    except ValueError:
        valid = ", ".join(m.value for m in AuthMode)
        raise AuthConfigurationError(
            f"METAFORGE_AUTH_MODE={raw_mode!r} is not a valid mode (expected one of: {valid}). "
            "Refusing to start rather than guess — an unrecognised mode must not be "
            "read as 'off'."
        ) from None

    if mode is AuthMode.OFF:
        logger.info("gateway_auth_disabled", mode=mode.value)
        return AuthSettings(mode=mode)

    # PyJWT is a cloud-only dependency, so that a local gateway needs no crypto
    # stack at all. Checked here rather than at import so the failure names the
    # cause: a missing library must not be discovered as a 500 on the first
    # authenticated request.
    try:
        pyjwt_missing = importlib.util.find_spec("jwt") is None
    except (ImportError, ValueError):
        # find_spec does not only return None for an absent module — it also
        # raises when the name is unimportable for another reason. Either way
        # we cannot verify tokens, and that must be the loud answer.
        pyjwt_missing = True
    if pyjwt_missing:
        raise AuthConfigurationError(
            "METAFORGE_AUTH_MODE=supabase needs PyJWT, which is not installed. "
            "Install the gateway extra: pip install -e '.[gateway]' (or "
            "pip install 'pyjwt[crypto]>=2.8'). Refusing to start rather than "
            "leaving every route open."
        )

    project_url = get("METAFORGE_SUPABASE_URL").rstrip("/")
    explicit_jwks = get("METAFORGE_SUPABASE_JWKS_URL")
    secret = get("METAFORGE_SUPABASE_JWT_SECRET")
    audience = get("METAFORGE_SUPABASE_JWT_AUDIENCE") or DEFAULT_AUDIENCE

    if secret and explicit_jwks:
        raise AuthConfigurationError(
            "Both METAFORGE_SUPABASE_JWT_SECRET and METAFORGE_SUPABASE_JWKS_URL are set. "
            "These select different signature schemes (symmetric vs asymmetric) and only "
            "one can be correct — unset whichever does not match your Supabase project."
        )

    if project_url:
        parsed = urlparse(project_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AuthConfigurationError(
                f"METAFORGE_SUPABASE_URL={project_url!r} is not a valid http(s) URL."
            )
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise AuthConfigurationError(
                f"METAFORGE_SUPABASE_URL={project_url!r} must use https — bearer tokens "
                "verified over plain HTTP are interceptable."
            )

    issuer = f"{project_url}{ISSUER_PATH}" if project_url else None

    if secret:
        # Legacy Supabase projects sign with a symmetric key. Supported because
        # plenty of projects are still on it, but it is strictly worse: the
        # gateway holds a secret that can *mint* tokens, not merely verify them.
        logger.warning(
            "gateway_auth_symmetric_secret",
            detail=(
                "Verifying with a shared HMAC secret. This key can forge tokens as well "
                "as check them. Migrate the Supabase project to asymmetric signing keys "
                "and use METAFORGE_SUPABASE_URL instead."
            ),
        )
        settings = AuthSettings(mode=mode, jwt_secret=secret, issuer=issuer, audience=audience)
    else:
        jwks_url = explicit_jwks or (f"{project_url}{JWKS_PATH}" if project_url else "")
        if not jwks_url:
            raise AuthConfigurationError(
                "METAFORGE_AUTH_MODE=supabase requires a way to verify tokens, and none "
                "is configured. Set METAFORGE_SUPABASE_URL (recommended), or "
                "METAFORGE_SUPABASE_JWKS_URL, or METAFORGE_SUPABASE_JWT_SECRET for a "
                "legacy project. Refusing to start: continuing would leave every route "
                "on this gateway open while appearing to be authenticated."
            )
        settings = AuthSettings(mode=mode, jwks_url=jwks_url, issuer=issuer, audience=audience)

    logger.info(
        "gateway_auth_enabled",
        mode=settings.mode.value,
        scheme="hs256_secret" if settings.jwt_secret else "jwks",
        jwks_url=settings.jwks_url,
        issuer=settings.issuer,
        audience=settings.audience,
    )
    return settings
