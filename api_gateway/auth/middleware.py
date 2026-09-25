"""Gateway authentication middleware.

Applied as middleware rather than as a per-route ``Depends`` on purpose.

The gateway has seventeen routers and well over a hundred routes, and the
pattern this module exists to break is precisely that of a protection someone
forgot to apply. A route-level dependency is opt-in: every new endpoint is
unprotected until an author remembers to annotate it, and nothing fails when
they don't. Middleware inverts that. Everything is closed, a short explicit
allow-list is open, and a route added tomorrow is protected by default because
its author did nothing.

When ``auth_mode`` is ``off`` this middleware is never installed at all, so the
local single-user gateway carries no added work and no behavioural difference.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from api_gateway.auth.jwks import JwksError
from api_gateway.auth.verifier import InvalidToken, TokenVerifier

logger = structlog.get_logger(__name__)

__all__ = ["AuthMiddleware", "PUBLIC_PATHS"]

#: Paths reachable without a credential.
#:
#: Kept deliberately tiny. ``/health`` is here because liveness probes cannot
#: hold tokens and because it is the endpoint operators use to confirm the auth
#: mode itself; it exposes no project data. The rest is API documentation.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
    }
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Requires a verified bearer token on every non-public request."""

    def __init__(self, app: Callable[..., Awaitable[None]], *, verifier: TokenVerifier) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._verifier = verifier

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if self._is_public(request):
            return await call_next(request)

        token = self._bearer(request)
        if token is None:
            return self._challenge("Missing bearer token")

        try:
            principal = await self._verifier.verify(token)
        except InvalidToken as exc:
            logger.info("gateway_auth_rejected", path=request.url.path, reason=str(exc))
            return self._challenge(str(exc))
        except JwksError as exc:
            # We could not reach a verdict. Saying 401 here would blame the
            # user's credentials for our own outage.
            logger.error("gateway_auth_unavailable", path=request.url.path, error=str(exc))
            return JSONResponse(
                {"detail": "Authentication is temporarily unavailable"},
                status_code=503,
            )

        request.state.principal = principal
        return await call_next(request)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _is_public(request: Request) -> bool:
        # CORS preflight carries no Authorization header by design, so it must
        # pass through to the CORS middleware or every cross-origin call fails
        # with an opaque error rather than a usable one.
        if request.method == "OPTIONS":
            return True
        return request.url.path.rstrip("/") in PUBLIC_PATHS or request.url.path in PUBLIC_PATHS

    @staticmethod
    def _bearer(request: Request) -> str | None:
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer":
            return None
        token = value.strip()
        return token or None

    @staticmethod
    def _challenge(detail: str) -> JSONResponse:
        return JSONResponse(
            {"detail": detail},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="metaforge"'},
        )
