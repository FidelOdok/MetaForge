"""Per-session FreeCAD worker subprocess pool (FORGE-221).

FreeCAD's PartDesign/OCCT native code can segfault on certain geometry
(confirmed live: ``pocket_sketch`` on a ``Revolution``-typed PartDesign body).
Every stateful ``operations.py`` function takes *live* FreeCAD objects
(``document``, ``body``, ``sketch``, ``obj``), not IDs, so a session's entire
live state — document + object registry — has to live wherever its calls
execute; there is no way to keep object lookup in the gateway process and
only ship raw geometry ops elsewhere. Isolating a crash therefore means
isolating the *whole session* into its own OS process.

This pool spawns one ``python -m tool_registry.tools.freecad.entrypoint``
subprocess per session, in stdio mode (``FREECAD_TRANSPORT=stdio``,
``FREECAD_MAX_SESSIONS=1``) — i.e. each worker is just an ordinary,
unmodified :class:`~tool_registry.tools.freecad.adapter.FreecadServer`
running the exact same handler bodies this adapter already has, talking over
the exact same JSON-RPC-over-stdio transport (:mod:`mcp_core.transports`)
every other stdio MCP adapter in this codebase uses. No new wire protocol,
no new handler logic — only the container the existing logic runs in.

A crash in one worker (segfault, kills that one OS process) never touches
any other session's worker: they're separate processes with their own
transport and lock. :meth:`FreecadWorkerPool.call` detects the crash via
:class:`~mcp_core.transports.StdioTransport`'s existing "subprocess closed
stdout" ``RuntimeError``, recovers whatever diagnostic landed on the dead
worker's stderr (a ``faulthandler`` dump, if the crash was a trapped signal —
armed in ``entrypoint.py``), logs it, drops the session, and raises a clear,
structured :class:`FreecadWorkerCrashedError` — replacing the previous
"Server disconnected" opacity that used to take the *entire* adapter process
(and every concurrent session in it) down with it.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from mcp_core.protocol import (
    ToolExecutionError,
    create_request,
    deserialize_response,
    serialize_message,
)
from mcp_core.schemas import JsonRpcErrorResponse
from mcp_core.transports import StdioTransport
from observability.tracing import get_tracer
from tool_registry.tools.freecad.config import FreecadConfig
from tool_registry.tools.freecad.session import SessionNotFoundError

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.freecad.worker_pool")

# entrypoint.py prints this exact line to stderr once its FreecadServer is
# constructed and about to serve -- StdioTransport waits for it before
# sending the first request, so a call can't race FreeCAD's own (non-trivial)
# import time.
READY_SIGNAL = "freecad-worker-ready"

# Stderr tail kept on a crash log line -- generous enough for a real
# faulthandler dump (a Python traceback is rarely more than a few KB), capped
# so a runaway print loop in a crashing worker can't flood the log.
_STDERR_LOG_TAIL = 4000


class FreecadWorkerCrashedError(RuntimeError):
    """A session's live FreeCAD worker process died mid-call.

    Mirrors :class:`~tool_registry.tools.freecad.session.SessionNotFoundError`'s
    style: a clear, structured, actionable message rather than an opaque
    disconnect. ``session_id`` is ``None`` only for a crash during
    :meth:`FreecadWorkerPool.open_session` itself, before any session_id
    existed to lose.
    """

    def __init__(self, session_id: str | None, tool: str) -> None:
        self.session_id = session_id
        self.tool = tool
        if session_id is None:
            message = (
                f"FreeCAD crashed while starting a new session (during {tool!r}) "
                "-- no session was created. This is a native-code failure, not a "
                "scripting error; other sessions are unaffected. Try again."
            )
        else:
            message = (
                f"FreeCAD crashed while executing {tool!r} -- session {session_id!r} "
                "was lost and must be reopened. This is a native-code failure, not "
                "a scripting error; other sessions are unaffected, and the crash "
                "has been logged for investigation."
            )
        super().__init__(message)


@dataclass
class _Worker:
    """One session's live subprocess + the bookkeeping the pool needs."""

    transport: StdioTransport
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_access: float = 0.0


def _worker_env(config: FreecadConfig) -> dict[str, str]:
    """Environment for a spawned worker: inherit everything, then pin it to
    exactly one session (FORGE-221) using the same env-var names
    ``entrypoint.py`` already reads for the in-process path."""
    return {
        **os.environ,
        "FREECAD_TRANSPORT": "stdio",
        "FREECAD_MAX_SESSIONS": "1",
        "FREECAD_WORK_DIR": config.work_dir,
        "FREECAD_BINARY": config.freecad_binary,
    }


class FreecadWorkerPool:
    """Spawns and tracks one FreeCAD worker subprocess per open session."""

    def __init__(
        self,
        config: FreecadConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        worker_ready_timeout: float = 30.0,
        worker_command: list[str] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._worker_ready_timeout = worker_ready_timeout
        # Injectable for tests (a fake worker script) -- production callers
        # never pass this, so they always get the real entrypoint below.
        self._worker_command = worker_command or [
            sys.executable,
            "-m",
            "tool_registry.tools.freecad.entrypoint",
        ]
        self._workers: dict[str, _Worker] = {}
        self._registry_lock = asyncio.Lock()  # guards self._workers mutations only

    # ---- lifecycle ------------------------------------------------------

    async def open_session(self, name: str) -> dict[str, Any]:
        """Spawn a fresh worker, ask it to open a session, remember the mapping."""
        now = self._clock()
        async with self._registry_lock:
            await self._evict_idle_locked(now)
            await self._enforce_capacity_locked()

        with tracer.start_as_current_span("freecad.worker_pool.open_session"):
            transport = StdioTransport(
                command=self._worker_command,
                env=_worker_env(self._config),
                ready_signal=READY_SIGNAL,
                ready_timeout=self._worker_ready_timeout,
            )
            await transport.connect()
            try:
                result = await self._send(transport, "open_session", {"name": name})
            except Exception:
                # Nothing was ever registered in self._workers for this
                # attempt -- just tear down the transport before re-raising,
                # whether it crashed, timed out, or answered with a normal
                # tool error.
                await transport.disconnect()
                raise

        session_id = str(result["session_id"])
        async with self._registry_lock:
            self._workers[session_id] = _Worker(transport=transport, last_access=now)
        logger.info("freecad_worker_opened", session_id=session_id, name=name)
        return result

    async def call(self, session_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Forward one tool call to the worker that owns ``session_id``."""
        worker = self._workers.get(session_id)
        if worker is None:
            raise SessionNotFoundError(session_id)
        worker.last_access = self._clock()

        with tracer.start_as_current_span(f"freecad.worker_pool.call.{name}") as span:
            span.set_attribute("session_id", session_id)
            async with worker.lock:
                try:
                    return await self._send(
                        worker.transport, name, arguments, session_id=session_id
                    )
                except FreecadWorkerCrashedError:
                    async with self._registry_lock:
                        self._workers.pop(session_id, None)
                    raise

    async def close_session(self, session_id: str) -> bool:
        """Close a session's worker. Returns False if unknown."""
        async with self._registry_lock:
            worker = self._workers.pop(session_id, None)
        if worker is None:
            return False
        await worker.transport.disconnect()
        logger.info("freecad_worker_closed", session_id=session_id)
        return True

    def session_ids(self) -> list[str]:
        return list(self._workers)

    # ---- internals --------------------------------------------------------

    async def _send(
        self,
        transport: StdioTransport,
        tool: str,
        arguments: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Send one ``freecad.<tool>`` call, unwrap the result, or raise.

        Distinguishes three failure shapes: the worker answered with a normal
        MCP error (``ToolExecutionError`` -- a scripting mistake, a bad
        argument, ordinary geometry-kernel rejection: the worker is still
        alive and other calls to this same session are still fine); the
        worker's process died (``FreecadWorkerCrashedError`` -- the actual
        FORGE-221 fix: a native crash, not a scripting error, session lost);
        or a hang past ``max_operation_time`` (treated the same as a crash --
        an operation stuck this long can't be trusted to leave the worker in
        a usable state for future calls either).
        """
        tool_id = f"freecad.{tool}"
        request = create_request(
            method="tool/call", params={"tool_id": tool_id, "arguments": arguments}
        )
        try:
            raw = await asyncio.wait_for(
                transport.send(serialize_message(request)),
                timeout=self._config.max_operation_time,
            )
        except TimeoutError as exc:
            stderr = await transport.read_stderr()
            logger.error(
                "freecad_worker_timed_out",
                session_id=session_id,
                tool=tool_id,
                timeout_s=self._config.max_operation_time,
                stderr=stderr.decode("utf-8", errors="replace")[-_STDERR_LOG_TAIL:],
            )
            raise FreecadWorkerCrashedError(session_id, tool_id) from exc
        except RuntimeError as exc:
            stderr = await transport.read_stderr()
            logger.error(
                "freecad_worker_crashed",
                session_id=session_id,
                tool=tool_id,
                stderr=stderr.decode("utf-8", errors="replace")[-_STDERR_LOG_TAIL:],
            )
            raise FreecadWorkerCrashedError(session_id, tool_id) from exc

        response = deserialize_response(raw)
        if isinstance(response, JsonRpcErrorResponse):
            raise ToolExecutionError(tool_id=tool_id, details=str(response.error))
        result = response.result.get("data")
        return result if isinstance(result, dict) else {}

    async def _evict_idle_locked(self, now: float) -> None:
        """Drop workers idle beyond the TTL. Caller holds ``_registry_lock``."""
        ttl = self._config.session_ttl_seconds
        if ttl <= 0:
            return
        expired = [sid for sid, w in self._workers.items() if (now - w.last_access) > ttl]
        for sid in expired:
            worker = self._workers.pop(sid, None)
            if worker is not None:
                await worker.transport.disconnect()
                logger.info("freecad_worker_evicted", session_id=sid, reason="idle_ttl")

    async def _enforce_capacity_locked(self) -> None:
        """Evict the least-recently-used worker if at capacity. Caller holds lock."""
        while len(self._workers) >= self._config.max_workers:
            oldest_id = min(self._workers, key=lambda sid: self._workers[sid].last_access)
            worker = self._workers.pop(oldest_id, None)
            if worker is not None:
                await worker.transport.disconnect()
                logger.info("freecad_worker_evicted", session_id=oldest_id, reason="capacity")
