"""File change watcher for Assistant Mode.

Monitors directories for file changes using the ``watchfiles`` library
and emits debounced ``FileChangeEvent`` objects.  Falls back gracefully
when ``watchfiles`` is not installed.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.assistant.watcher")

# Try to import watchfiles; degrade gracefully if unavailable.
try:
    import watchfiles

    HAS_WATCHFILES = True
except ImportError:
    watchfiles = None  # type: ignore[assignment]
    HAS_WATCHFILES = False


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ChangeType(StrEnum):
    """Type of file-system change detected."""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


class FileChangeEvent(BaseModel):
    """Immutable record of a single file change."""

    path: str
    change_type: ChangeType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    file_hash: str = ""


# ---------------------------------------------------------------------------
# Default extensions recognised by the watcher
# ---------------------------------------------------------------------------

DEFAULT_EXTENSIONS: set[str] = {
    ".kicad_sch",
    ".kicad_pcb",
    ".FCStd",
    ".step",
    ".stp",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".py",
}


# ---------------------------------------------------------------------------
# FileWatcher
# ---------------------------------------------------------------------------


class FileWatcher:
    """Watch directories for file changes and invoke registered callbacks.

    Parameters
    ----------
    watch_dirs:
        Directories to monitor recursively.
    extensions:
        Only emit events for files whose suffix is in this set.
    debounce_ms:
        Minimum interval (in milliseconds) between events for the same
        file path.  Rapid saves are collapsed into a single event.
    """

    def __init__(
        self,
        watch_dirs: list[str],
        extensions: set[str] | None = None,
        debounce_ms: int = 500,
    ) -> None:
        self._watch_dirs = watch_dirs
        self._extensions = extensions if extensions is not None else DEFAULT_EXTENSIONS
        self._debounce_ms = debounce_ms
        self._callbacks: list[Callable[[FileChangeEvent], Awaitable[None]]] = []
        self._running = False
        self._task: asyncio.Task[None] | None = None
        # Debounce bookkeeping: path -> last event timestamp (monotonic ns)
        self._last_event_ns: dict[str, int] = {}

    # -- public API ---------------------------------------------------------

    def on_change(self, callback: Callable[[FileChangeEvent], Awaitable[None]]) -> None:
        """Register an async callback to receive change events."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        """Begin watching directories.

        Raises ``RuntimeError`` if ``watchfiles`` is not installed.
        """
        if not HAS_WATCHFILES:
            logger.warning(
                "watchfiles_not_installed",
                hint="Install watchfiles>=0.21 to enable file watching",
            )
            raise RuntimeError(
                "watchfiles is required for file watching. "
                "Install it with: pip install 'metaforge[assistant]'"
            )

        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(
            "watcher_started",
            watch_dirs=self._watch_dirs,
            extensions=sorted(self._extensions),
            debounce_ms=self._debounce_ms,
        )

    async def stop(self) -> None:
        """Stop watching directories."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("watcher_stopped")

    # -- internal -----------------------------------------------------------

    async def _watch_loop(self) -> None:
        """Core loop that reads watchfiles changes and dispatches events."""
        assert watchfiles is not None  # guaranteed by start()

        try:
            async for changes in watchfiles.awatch(
                *self._watch_dirs,
                step=self._debounce_ms,
            ):
                if not self._running:
                    break
                await self._process_changes(changes)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("watcher_loop_error")

    async def _process_changes(self, changes: set[Any]) -> None:
        """Convert raw watchfiles changes to ``FileChangeEvent`` objects."""
        with tracer.start_as_current_span("watcher.process_changes") as span:
            span.set_attribute("watcher.raw_change_count", len(changes))
            emitted = 0

            for change_kind, path_str in changes:
                path = Path(path_str)

                # Extension filter
                if not self._matches_extension(path):
                    continue

                # Debounce
                if self._is_debounced(path_str):
                    continue

                change_type = self._map_change_type(change_kind)
                file_hash = (
                    await self._compute_hash(path) if change_type != ChangeType.DELETED else ""
                )

                event = FileChangeEvent(
                    path=path_str,
                    change_type=change_type,
                    file_hash=file_hash,
                )

                self._last_event_ns[path_str] = _monotonic_ns()
                await self._dispatch(event)
                emitted += 1

            span.set_attribute("watcher.emitted_count", emitted)

    def _matches_extension(self, path: Path) -> bool:
        """Check whether the file suffix matches the allowed extensions."""
        return path.suffix in self._extensions

    def _is_debounced(self, path_str: str) -> bool:
        """Return True if we should suppress this event (too soon)."""
        last = self._last_event_ns.get(path_str)
        if last is None:
            return False
        elapsed_ms = (_monotonic_ns() - last) / 1_000_000
        return elapsed_ms < self._debounce_ms

    @staticmethod
    def _map_change_type(change_kind: Any) -> ChangeType:
        """Map watchfiles change enum to our ``ChangeType``."""
        # watchfiles.Change: added=1, modified=2, deleted=3
        kind_value = int(change_kind)
        if kind_value == 1:
            return ChangeType.CREATED
        if kind_value == 3:
            return ChangeType.DELETED
        return ChangeType.MODIFIED

    @staticmethod
    async def _compute_hash(path: Path) -> str:
        """Compute SHA-256 hex digest of a file.  Returns '' on error."""
        try:
            data = await asyncio.to_thread(path.read_bytes)
            return hashlib.sha256(data).hexdigest()
        except OSError:
            logger.warning("hash_computation_failed", path=str(path))
            return ""

    async def _dispatch(self, event: FileChangeEvent) -> None:
        """Invoke all registered callbacks for an event."""
        for cb in self._callbacks:
            try:
                await cb(event)
            except Exception:
                logger.exception(
                    "callback_error",
                    path=event.path,
                    change_type=str(event.change_type),
                )


def _monotonic_ns() -> int:
    """Return monotonic time in nanoseconds."""
    import time

    return time.monotonic_ns()
