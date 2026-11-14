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

What it does *not* know is how a token is checked. That belongs to whichever
:class:`~api_gateway.auth.provider.AuthProvider` is installed — this class only
decides what each of the provider's two failure classes means over HTTP.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from api_gateway.auth.provider import AuthProvider, AuthUnavailable, InvalidToken
from api_gateway.auth.scope import decide, supports_project_scope
from twin_core.library.viewer import NO_TENANT, library_viewer

logger = structlog.get_logger(__name__)

__all__ = ["AuthMiddleware", "PUBLIC_GET_PATTERNS", "PUBLIC_PATHS", "PUBLIC_PREFIXES"]

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


#: Path prefixes whose requests carry their own credential instead of a
#: bearer token: a signed link (``api_gateway.files.links``) is checked by
#: its route, which refuses anything forged or expired. Without this a link
#: handed to a person's browser, or to ``curl``, would need the token the
#: link exists to avoid.
PUBLIC_PREFIXES: tuple[str, ...] = ("/v1/links/",)

#: GET paths that carry their own credential, by pattern (FORGE-821). The
#: Imagine engine page is framed in a sandboxed iframe, which cannot send a
#: bearer token: its route checks the short-lived signed ``t`` that
#: ``GET .../imagine/studio`` put in ``engine_url``
#: (``api_gateway.imagine.engine_token``). Its assets are the engine's own
#: scripts and stylesheet and carry no project data.
PUBLIC_GET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/v1/twin/projects/[0-9a-fA-F-]{36}/imagine/engine(/assets/[A-Za-z0-9_.-]+)?$"),
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Requires a verified bearer token on every non-public request."""

    def __init__(self, app: Callable[..., Awaitable[None]], *, provider: AuthProvider) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._provider = provider

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if self._is_public(request):
            return await call_next(request)

        token = self._bearer(request)
        if token is None:
            return self._challenge("Missing bearer token")

        try:
            principal = await self._provider.verify(token)
        except InvalidToken as exc:
            logger.info("gateway_auth_rejected", path=request.url.path, reason=str(exc))
            return self._challenge(str(exc))
        except AuthUnavailable as exc:
            # We could not reach a verdict. Saying 401 here would blame the
            # user's credentials for our own outage.
            logger.error("gateway_auth_unavailable", path=request.url.path, error=str(exc))
            return JSONResponse(
                {"detail": "Authentication is temporarily unavailable"},
                status_code=503,
            )

        request.state.principal = principal

        # FORGE-849: on a gateway shared by several accounts the provider also
        # confines the caller to their projects. Fail closed: a route that is
        # not confined to a project is refused, not answered across accounts.
        if supports_project_scope(self._provider):
            try:
                scope = await self._provider.project_scope(principal)  # type: ignore[attr-defined]
            except AuthUnavailable as exc:
                logger.error("gateway_scope_unavailable", path=request.url.path, error=str(exc))
                return JSONResponse(
                    {"detail": "Authorization is temporarily unavailable"}, status_code=503
                )
            decision = await decide(request.app, request, scope)
            if not decision.allowed:
                logger.info(
                    "gateway_scope_refused",
                    path=request.url.path,
                    method=request.method,
                    status=decision.status,
                    tenancy=decision.tenancy.value if decision.tenancy else None,
                    subject=principal.subject,
                )
                return JSONResponse({"detail": decision.detail}, status_code=decision.status)
            request.state.project_scope = scope
            # FORGE-876: every library read and write in this request sees the
            # shared entries and this caller's own, wherever it is made from.
            view = None if scope.unrestricted else (scope.tenant or NO_TENANT)
            with library_viewer(view):
                return await call_next(request)

        return await call_next(request)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _is_public(request: Request) -> bool:
        # CORS preflight carries no Authorization header by design, so it must
        # pass through to the CORS middleware or every cross-origin call fails
        # with an opaque error rather than a usable one.
        if request.method == "OPTIONS":
            return True
        path = request.url.path
        if path.startswith(PUBLIC_PREFIXES):
            return True
        if request.method in ("GET", "HEAD") and any(p.match(path) for p in PUBLIC_GET_PATTERNS):
            return True
        return path.rstrip("/") in PUBLIC_PATHS or path in PUBLIC_PATHS

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
