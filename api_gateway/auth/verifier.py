"""Access-token verification.

Turns a bearer token into a :class:`~api_gateway.auth.principal.Principal`, or
raises. There is deliberately no third outcome — no "probably fine", no
degraded mode where an unverifiable token is allowed through with a warning.

Two failure classes are kept apart because they mean different things to a
caller:

* :class:`InvalidToken` — we verified and the answer is no. HTTP 401.
* :class:`~api_gateway.auth.jwks.JwksError` — we could not determine an answer,
  because the identity provider is unreachable or is publishing something we
  cannot parse. HTTP 503. Returning 401 here would tell a legitimate user their
  credentials are bad when the real fault is ours.
"""

from __future__ import annotations

import jwt
import structlog

from api_gateway.auth.config import AuthSettings
from api_gateway.auth.jwks import JwksCache
from api_gateway.auth.principal import Principal
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.auth.verifier")

__all__ = ["InvalidToken", "TokenVerifier"]


class InvalidToken(Exception):
    """The token was examined and rejected."""


class TokenVerifier:
    """Verifies Supabase access tokens against the configured signing scheme."""

    def __init__(self, settings: AuthSettings, *, jwks: JwksCache | None = None) -> None:
        if not settings.enabled:
            raise ValueError("TokenVerifier requires an enabled AuthSettings")
        self._settings = settings
        self._jwks = jwks
        if settings.jwks_url and self._jwks is None:
            self._jwks = JwksCache(settings.jwks_url)

    @property
    def settings(self) -> AuthSettings:
        return self._settings

    async def aclose(self) -> None:
        if self._jwks is not None:
            await self._jwks.aclose()

    async def verify(self, token: str) -> Principal:
        """Verify ``token`` and return the caller it identifies.

        Raises
        ------
        InvalidToken
            Malformed, expired, wrongly-signed, or failing an audience/issuer
            check.
        JwksError
            The signing key could not be resolved.
        """
        with tracer.start_as_current_span("auth.verify_token") as span:
            settings = self._settings

            if settings.jwt_secret:
                key: object = settings.jwt_secret
            else:
                try:
                    header = jwt.get_unverified_header(token)
                except jwt.PyJWTError as exc:
                    raise InvalidToken(f"Unreadable token header: {exc}") from exc
                # get_key may raise JwksError, which deliberately propagates —
                # "cannot check" must not collapse into "rejected".
                assert self._jwks is not None  # guaranteed by __init__
                key = await self._jwks.get_key(header.get("kid"))

            try:
                claims = jwt.decode(
                    token,
                    key,  # type: ignore[arg-type]
                    algorithms=settings.algorithms,
                    audience=settings.audience or None,
                    issuer=settings.issuer,
                    # Inline rather than hoisted to a local: PyJWT types this
                    # as a TypedDict, which only narrows from a literal.
                    options={
                        "require": ["exp", "sub"],
                        "verify_aud": bool(settings.audience),
                    },
                )
            except jwt.ExpiredSignatureError as exc:
                span.set_attribute("auth.failure", "expired")
                raise InvalidToken("Token has expired") from exc
            except jwt.InvalidAudienceError as exc:
                span.set_attribute("auth.failure", "audience")
                raise InvalidToken("Token audience does not match this gateway") from exc
            except jwt.InvalidIssuerError as exc:
                span.set_attribute("auth.failure", "issuer")
                raise InvalidToken("Token was not issued by the configured provider") from exc
            except jwt.PyJWTError as exc:
                span.set_attribute("auth.failure", "invalid")
                raise InvalidToken(f"Token rejected: {exc}") from exc

            subject = str(claims.get("sub") or "").strip()
            if not subject:
                span.set_attribute("auth.failure", "no_subject")
                raise InvalidToken("Token has no subject")

            span.set_attribute("auth.subject", subject)
            return Principal(
                subject=subject,
                email=claims.get("email"),
                role=claims.get("role"),
                claims=claims,
            )
