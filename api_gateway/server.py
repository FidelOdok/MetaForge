"""MetaForge API Gateway server.

FastAPI application factory that wires together all routers, middleware,
and lifecycle hooks.  Run with ``uvicorn api_gateway.server:app``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_gateway.assistant.routes import router as assistant_router
from api_gateway.chat.routes import router as chat_router
from api_gateway.health import health_router
from observability.middleware import ObservabilityMiddleware
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.server")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle handler."""
    logger.info("gateway_starting", version="0.1.0")
    yield
    logger.info("gateway_stopping")


def create_app(
    *,
    cors_origins: list[str] | None = None,
    collector: Any | None = None,
) -> FastAPI:
    """Create and configure the MetaForge Gateway FastAPI application.

    Parameters
    ----------
    cors_origins:
        Allowed CORS origins.  Defaults to ``["*"]`` for development.
    collector:
        Optional ``MetricsCollector`` for the observability middleware.
    """
    app = FastAPI(
        title="MetaForge Gateway",
        version="0.1.0",
        description="HTTP/WebSocket front door for the MetaForge platform",
        lifespan=lifespan,
    )

    # -- CORS --------------------------------------------------------------
    origins = cors_origins if cors_origins is not None else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Observability middleware -------------------------------------------
    app.add_middleware(ObservabilityMiddleware, collector=collector)

    # -- Routers -----------------------------------------------------------
    app.include_router(health_router)
    app.include_router(assistant_router)
    app.include_router(chat_router)

    logger.info(
        "gateway_configured",
        cors_origins=origins,
        routers=["health", "assistant", "chat"],
    )

    return app


# Module-level app for ``uvicorn api_gateway.server:app``
app = create_app()


def main() -> None:
    """Run the gateway with uvicorn (development entry point)."""
    import uvicorn

    logger.info("gateway_main_starting", host="0.0.0.0", port=8000)
    uvicorn.run(
        "api_gateway.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
