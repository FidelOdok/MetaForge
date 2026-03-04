"""MET-102: FastAPI observability middleware.

Automatically records request metrics (count, duration, status code) for
every HTTP request passing through the gateway.  Non-HTTP ASGI scopes
(e.g. ``lifespan``, ``websocket``) are passed through untouched.

The middleware is designed to work with or without a ``MetricsCollector``;
when *collector* is ``None`` it simply forwards requests unchanged.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from observability.metrics import MetricsCollector

# ASGI type aliases for readability
Scope = dict[str, Any]
Receive = Callable[..., Any]
Send = Callable[..., Any]


class ObservabilityMiddleware:
    """ASGI middleware that emits per-request Prometheus metrics.

    Parameters
    ----------
    app:
        The next ASGI application in the stack.
    collector:
        A ``MetricsCollector`` instance.  Pass *None* to disable metric
        recording while still keeping the middleware in the chain.
    """

    def __init__(
        self,
        app: Any,
        collector: MetricsCollector | None = None,
    ) -> None:
        self.app = app
        self.collector = collector

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only instrument HTTP requests — pass everything else through.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.monotonic()
        status_code = 500  # fallback in case of unhandled exception

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.monotonic() - start_time
            method: str = scope.get("method", "UNKNOWN")
            path: str = scope.get("path", "/")
            if self.collector is not None:
                self.collector.record_request(method, path, status_code, duration)
