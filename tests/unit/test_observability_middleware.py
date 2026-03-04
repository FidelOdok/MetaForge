"""Unit tests for MET-102: FastAPI observability middleware.

Uses a lightweight ASGI test harness to verify that the
``ObservabilityMiddleware`` correctly records request metrics, handles
non-HTTP scopes, measures duration, and degrades gracefully when no
collector is configured.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from observability.metrics import MetricsCollector
from observability.middleware import ObservabilityMiddleware

# ---------------------------------------------------------------------------
# ASGI test helpers
# ---------------------------------------------------------------------------


def _make_http_scope(
    method: str = "GET",
    path: str = "/test",
) -> dict[str, Any]:
    """Return a minimal ASGI HTTP scope."""
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
    }


def _make_lifespan_scope() -> dict[str, Any]:
    return {"type": "lifespan"}


def _make_websocket_scope() -> dict[str, Any]:
    return {"type": "websocket", "path": "/ws"}


async def _noop_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b""}


async def _noop_send(message: dict[str, Any]) -> None:
    pass


def _simple_app(status: int = 200):
    """Return an ASGI app that responds with *status*."""

    async def app(scope: dict, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


def _error_app():
    """Return an ASGI app that raises an exception after sending headers."""

    async def app(scope: dict, receive: Any, send: Any) -> None:
        raise RuntimeError("boom")

    return app


def _error_after_headers_app(status: int = 503):
    """ASGI app that sends headers, then raises."""

    async def app(scope: dict, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": status, "headers": []})
        raise RuntimeError("boom after headers")

    return app


# ===================================================================
# Tests
# ===================================================================


class TestObservabilityMiddleware:
    """Core middleware behaviour."""

    @pytest.fixture()
    def collector(self) -> MagicMock:
        return MagicMock(spec=MetricsCollector)

    # -- basic recording ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_records_request_metrics(self, collector: MagicMock) -> None:
        mw = ObservabilityMiddleware(_simple_app(200), collector=collector)
        await mw(_make_http_scope("GET", "/api/data"), _noop_receive, _noop_send)

        collector.record_request.assert_called_once()
        args = collector.record_request.call_args
        assert args[0][0] == "GET"
        assert args[0][1] == "/api/data"
        assert args[0][2] == 200
        assert isinstance(args[0][3], float)
        assert args[0][3] >= 0

    @pytest.mark.asyncio
    async def test_captures_status_code(self, collector: MagicMock) -> None:
        mw = ObservabilityMiddleware(_simple_app(404), collector=collector)
        await mw(_make_http_scope(), _noop_receive, _noop_send)

        _, kwargs = collector.record_request.call_args[0], collector.record_request.call_args
        status = kwargs[0][2]
        assert status == 404

    @pytest.mark.asyncio
    async def test_measures_positive_duration(self, collector: MagicMock) -> None:
        mw = ObservabilityMiddleware(_simple_app(), collector=collector)
        await mw(_make_http_scope(), _noop_receive, _noop_send)

        duration = collector.record_request.call_args[0][3]
        assert duration > 0

    @pytest.mark.asyncio
    async def test_records_method_and_path(self, collector: MagicMock) -> None:
        mw = ObservabilityMiddleware(_simple_app(), collector=collector)
        await mw(
            _make_http_scope("POST", "/api/v1/chat"),
            _noop_receive,
            _noop_send,
        )
        method, path = (
            collector.record_request.call_args[0][0],
            collector.record_request.call_args[0][1],
        )
        assert method == "POST"
        assert path == "/api/v1/chat"

    # -- non-HTTP scopes ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_passes_through_lifespan_scope(self, collector: MagicMock) -> None:
        inner_called = False

        async def inner(scope: dict, receive: Any, send: Any) -> None:
            nonlocal inner_called
            inner_called = True

        mw = ObservabilityMiddleware(inner, collector=collector)
        await mw(_make_lifespan_scope(), _noop_receive, _noop_send)

        assert inner_called
        collector.record_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_through_websocket_scope(self, collector: MagicMock) -> None:
        inner_called = False

        async def inner(scope: dict, receive: Any, send: Any) -> None:
            nonlocal inner_called
            inner_called = True

        mw = ObservabilityMiddleware(inner, collector=collector)
        await mw(_make_websocket_scope(), _noop_receive, _noop_send)

        assert inner_called
        collector.record_request.assert_not_called()

    # -- graceful no-collector mode ----------------------------------------

    @pytest.mark.asyncio
    async def test_works_without_collector(self) -> None:
        """Middleware with collector=None must not raise."""
        mw = ObservabilityMiddleware(_simple_app(), collector=None)
        await mw(_make_http_scope(), _noop_receive, _noop_send)

    @pytest.mark.asyncio
    async def test_no_collector_still_forwards_request(self) -> None:
        sent_messages: list[dict] = []

        async def capture_send(msg: dict) -> None:
            sent_messages.append(msg)

        mw = ObservabilityMiddleware(_simple_app(200), collector=None)
        await mw(_make_http_scope(), _noop_receive, capture_send)

        assert any(m.get("status") == 200 for m in sent_messages)

    # -- exception handling ------------------------------------------------

    @pytest.mark.asyncio
    async def test_records_metrics_on_app_exception(
        self, collector: MagicMock
    ) -> None:
        mw = ObservabilityMiddleware(_error_app(), collector=collector)
        with pytest.raises(RuntimeError, match="boom"):
            await mw(_make_http_scope(), _noop_receive, _noop_send)

        # Metrics should still be recorded (in the finally block)
        collector.record_request.assert_called_once()
        # Default status is 500 because no response.start was sent
        assert collector.record_request.call_args[0][2] == 500

    @pytest.mark.asyncio
    async def test_records_actual_status_on_exception_after_headers(
        self, collector: MagicMock
    ) -> None:
        mw = ObservabilityMiddleware(
            _error_after_headers_app(503), collector=collector
        )
        with pytest.raises(RuntimeError, match="boom after headers"):
            await mw(_make_http_scope(), _noop_receive, _noop_send)

        # Status should be 503 since headers were sent before the crash
        assert collector.record_request.call_args[0][2] == 503

    @pytest.mark.asyncio
    async def test_duration_measured_even_on_exception(
        self, collector: MagicMock
    ) -> None:
        mw = ObservabilityMiddleware(_error_app(), collector=collector)
        with pytest.raises(RuntimeError):
            await mw(_make_http_scope(), _noop_receive, _noop_send)

        duration = collector.record_request.call_args[0][3]
        assert duration >= 0

    # -- scope defaults ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_defaults_for_missing_scope_fields(
        self, collector: MagicMock
    ) -> None:
        """If scope lacks method/path keys, fall back to defaults."""
        scope = {"type": "http"}  # no method or path
        mw = ObservabilityMiddleware(_simple_app(), collector=collector)
        await mw(scope, _noop_receive, _noop_send)

        method = collector.record_request.call_args[0][0]
        path = collector.record_request.call_args[0][1]
        assert method == "UNKNOWN"
        assert path == "/"

    @pytest.mark.asyncio
    async def test_multiple_requests_recorded_independently(
        self, collector: MagicMock
    ) -> None:
        mw = ObservabilityMiddleware(_simple_app(200), collector=collector)
        await mw(_make_http_scope("GET", "/a"), _noop_receive, _noop_send)
        await mw(_make_http_scope("POST", "/b"), _noop_receive, _noop_send)

        assert collector.record_request.call_count == 2
        first_path = collector.record_request.call_args_list[0][0][1]
        second_path = collector.record_request.call_args_list[1][0][1]
        assert first_path == "/a"
        assert second_path == "/b"
