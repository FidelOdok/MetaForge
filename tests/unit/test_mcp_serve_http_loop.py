"""Unit tests for the MCP HTTP serve path (MET-477 G3).

The MET-477 smoke surfaced that ``memory.list_insights`` failed with
"another operation is in progress" because ``asyncio.run(_bootstrap)``
created asyncpg pools bound to a one-shot event loop, then
``run_http`` started uvicorn's loop and tried to query against pools
whose backing connections were tied to the now-dead loop.

These tests assert the post-G3 contract: ``serve_http_async`` is a
proper coroutine that can be awaited inside an already-running loop,
and the sync ``run_http`` wrapper delegates to it via a single
``asyncio.run`` call so the bootstrap-then-serve sequence shares one
loop end to end.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import patch

import pytest

from metaforge.mcp.__main__ import run_http, serve_http_async


def test_serve_http_async_is_a_coroutine_function():
    """``serve_http_async`` must be awaitable; if it accidentally
    becomes synchronous again, the G3 fix has regressed."""
    assert inspect.iscoroutinefunction(serve_http_async), (
        "serve_http_async must be async — the whole point of G3 is that "
        "the bootstrap event loop and uvicorn share one loop"
    )


def test_serve_http_async_signature_takes_host_port_enable_sse():
    """Signature pin so callers don't accidentally break the contract."""
    sig = inspect.signature(serve_http_async)
    params = set(sig.parameters)
    assert {"server", "host", "port", "enable_sse"} <= params


class _StubServer:
    """Minimal stand-in for uvicorn.Server — captures the ``serve()`` call."""

    serve_called = False

    async def serve(self) -> None:
        type(self).serve_called = True


@pytest.mark.asyncio
async def test_serve_http_async_awaits_uvicorn_serve(monkeypatch):
    """``serve_http_async`` MUST call the async ``Server.serve()`` —
    not the blocking ``Server.run()``. The blocking variant creates
    a fresh event loop, which is exactly the pool-binding bug G3
    fixed.
    """

    class _FakeServer:
        adapters: list[Any] = []
        tool_ids: list[str] = []

    # Stub out the HTTP app builder so we don't pull in the FastAPI
    # init cost during a unit test.
    monkeypatch.setattr("metaforge.mcp.__main__.build_http_app", lambda *a, **k: object())

    captured: dict[str, Any] = {}

    class _StubUvicornServer:
        def __init__(self, config: Any) -> None:
            captured["config"] = config

        async def serve(self) -> None:
            captured["served"] = True

        def run(self) -> None:  # pragma: no cover — must NOT be invoked
            captured["ran_sync"] = True

    import uvicorn

    monkeypatch.setattr(uvicorn, "Server", _StubUvicornServer)

    await serve_http_async(_FakeServer(), host="127.0.0.1", port=8765, enable_sse=False)  # type: ignore[arg-type]

    assert captured.get("served") is True
    assert "ran_sync" not in captured, (
        "serve_http_async invoked Server.run() — that creates a new "
        "event loop and reintroduces the G3 pool-binding bug"
    )


def test_run_http_delegates_to_serve_http_async(monkeypatch):
    """The sync wrapper exists for back-compat. It must funnel through
    ``asyncio.run(serve_http_async(...))`` — exactly one event loop,
    not the old multi-loop pattern."""

    called: dict[str, Any] = {}

    async def _fake_async(server: Any, host: str, port: int, *, enable_sse: bool) -> None:
        called["host"] = host
        called["port"] = port
        called["enable_sse"] = enable_sse

    monkeypatch.setattr("metaforge.mcp.__main__.serve_http_async", _fake_async)

    # Track asyncio.run to confirm the wrapper calls it exactly once.
    import asyncio as _asyncio

    run_calls: list[Any] = []

    def _spy_run(coro: Any) -> None:
        run_calls.append(coro)
        # Execute the coroutine so the assertions see the propagated args.
        loop = _asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

    with patch.object(_asyncio, "run", _spy_run):
        run_http(server=object(), host="127.0.0.1", port=8766, enable_sse=True)  # type: ignore[arg-type]

    assert len(run_calls) == 1, "run_http must call asyncio.run exactly once (single loop)"
    assert called == {"host": "127.0.0.1", "port": 8766, "enable_sse": True}
