"""Unit tests for FreecadWorkerPool (FORGE-221).

Uses a small fake worker subprocess (a `python -c` one-liner, no real
FreeCAD needed) that speaks the same line-delimited JSON-RPC shape a real
``entrypoint.py --transport stdio`` worker would, injected via
``FreecadWorkerPool``'s ``worker_command`` test seam. The core regression
test proves the actual ticket requirement: a worker that dies mid-call is
detected and dropped without affecting any other session's worker.
"""

from __future__ import annotations

import sys

import pytest

from tool_registry.tools.freecad.config import FreecadConfig
from tool_registry.tools.freecad.session import SessionNotFoundError
from tool_registry.tools.freecad.worker_pool import FreecadWorkerCrashedError, FreecadWorkerPool

_FAKE_WORKER_SRC = """
import sys, json, os

def resp(id_, data):
    return json.dumps({"jsonrpc": "2.0", "id": id_, "result": {"data": data}})

sys.stderr.write("freecad-worker-ready\\n")
sys.stderr.flush()
for line in sys.stdin:
    req = json.loads(line)
    id_ = req["id"]
    params = req.get("params", {})
    tool_id = params.get("tool_id", "")
    name = tool_id.split(".", 1)[-1]
    arguments = params.get("arguments", {})
    if name == "open_session":
        # Each open_session spawns a brand-new worker process (one per
        # session, by design) -- key by pid so distinct workers in the same
        # test never collide on the same session_id.
        sys.stdout.write(resp(id_, {"session_id": "worker-session-%d" % os.getpid()}) + "\\n")
        sys.stdout.flush()
    elif name == "crash":
        sys.stderr.write("simulated native crash\\n")
        sys.stderr.flush()
        os._exit(139)
    else:
        sys.stdout.write(resp(id_, {"echo": arguments}) + "\\n")
        sys.stdout.flush()
"""

_FAKE_WORKER_COMMAND = [sys.executable, "-u", "-c", _FAKE_WORKER_SRC]


def _make_pool(**config_kwargs: object) -> FreecadWorkerPool:
    config = FreecadConfig(**config_kwargs)
    return FreecadWorkerPool(config, worker_command=_FAKE_WORKER_COMMAND, worker_ready_timeout=10.0)


class _FakeClock:
    """Manually-advanced clock so eviction tests don't depend on wall time."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_open_and_call_round_trip() -> None:
    pool = _make_pool()
    try:
        opened = await pool.open_session("part1")
        session_id = opened["session_id"]
        result = await pool.call(session_id, "echo", {"session_id": session_id, "x": 1})
        assert result == {"echo": {"session_id": session_id, "x": 1}}
    finally:
        for sid in pool.session_ids():
            await pool.close_session(sid)


@pytest.mark.asyncio
async def test_call_unknown_session_raises() -> None:
    pool = _make_pool()
    with pytest.raises(SessionNotFoundError):
        await pool.call("no-such-session", "echo", {"session_id": "no-such-session"})


@pytest.mark.asyncio
async def test_close_session_returns_false_for_unknown() -> None:
    pool = _make_pool()
    assert await pool.close_session("no-such-session") is False


@pytest.mark.asyncio
async def test_close_session_tears_down_worker() -> None:
    pool = _make_pool()
    opened = await pool.open_session("part1")
    session_id = opened["session_id"]
    assert await pool.close_session(session_id) is True
    assert session_id not in pool.session_ids()
    with pytest.raises(SessionNotFoundError):
        await pool.call(session_id, "echo", {"session_id": session_id})


@pytest.mark.asyncio
async def test_capacity_eviction_evicts_least_recently_used() -> None:
    clock = _FakeClock()
    config = FreecadConfig(max_workers=2)
    pool = FreecadWorkerPool(
        config, worker_command=_FAKE_WORKER_COMMAND, worker_ready_timeout=10.0, clock=clock
    )
    try:
        clock.now = 1.0
        first = (await pool.open_session("a"))["session_id"]
        clock.now = 2.0
        second = (await pool.open_session("b"))["session_id"]
        # Touch `second` so `first` is the least-recently-used entry.
        clock.now = 3.0
        await pool.call(second, "echo", {"session_id": second})

        clock.now = 4.0
        third = (await pool.open_session("c"))["session_id"]

        assert first not in pool.session_ids()
        assert second in pool.session_ids()
        assert third in pool.session_ids()
    finally:
        for sid in pool.session_ids():
            await pool.close_session(sid)


@pytest.mark.asyncio
async def test_idle_ttl_eviction() -> None:
    clock = _FakeClock()
    config = FreecadConfig(session_ttl_seconds=10.0)
    pool = FreecadWorkerPool(
        config, worker_command=_FAKE_WORKER_COMMAND, worker_ready_timeout=10.0, clock=clock
    )
    try:
        clock.now = 0.0
        stale = (await pool.open_session("a"))["session_id"]

        clock.now = 100.0  # far past the 10s TTL
        fresh = (await pool.open_session("b"))["session_id"]

        assert stale not in pool.session_ids()
        assert fresh in pool.session_ids()
    finally:
        for sid in pool.session_ids():
            await pool.close_session(sid)


@pytest.mark.asyncio
async def test_worker_crash_is_isolated_to_its_own_session() -> None:
    """Core FORGE-221 regression: a crash in one session's worker must not
    affect any other concurrently open session."""
    pool = _make_pool()
    try:
        doomed = (await pool.open_session("doomed"))["session_id"]
        survivor = (await pool.open_session("survivor"))["session_id"]

        with pytest.raises(FreecadWorkerCrashedError):
            await pool.call(doomed, "crash", {"session_id": doomed})

        # The crashed session is gone from the pool...
        assert doomed not in pool.session_ids()
        # ...but the other session's worker is completely unaffected.
        result = await pool.call(survivor, "echo", {"session_id": survivor, "still": "alive"})
        assert result == {"echo": {"session_id": survivor, "still": "alive"}}
    finally:
        for sid in pool.session_ids():
            await pool.close_session(sid)
