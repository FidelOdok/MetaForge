"""File watcher — polls linked files for changes and triggers auto-sync.

Runs as a background asyncio task started in the Gateway lifespan.
Polls every FILE_WATCH_INTERVAL_SECONDS (default 5) for any FileLink
with watch=True. When a file's hash changes, calls sync_linked_file
and emits a structured log event.

Design notes:
- Pure asyncio polling (no watchdog/inotify — WSL2 compatible)
- Reads from the module-level link_store singleton
- Requires a twin reference (set via set_twin() before start)
- Single asyncio.Task; cancel() to stop
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from api_gateway.twin.file_link import check_sync_status, link_store, sync_linked_file
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.file_watcher")


class FileWatcher:
    """Background asyncio task that polls linked files for changes.

    Usage::

        file_watcher.set_twin(twin)
        await file_watcher.start()
        # ... application runs ...
        await file_watcher.stop()
    """

    def __init__(self, interval: float = 5.0) -> None:
        self.interval = interval
        self._twin: Any = None
        self._task: asyncio.Task[None] | None = None

    def set_twin(self, twin: Any) -> None:
        """Inject the Twin API reference used for sync operations."""
        self._twin = twin

    async def start(self) -> None:
        """Start the background polling task."""
        self._task = asyncio.get_event_loop().create_task(self._run())
        logger.info("file_watcher_started", interval=self.interval)

    async def stop(self) -> None:
        """Cancel the polling task and wait for it to finish."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("file_watcher_stopped")

    async def _run(self) -> None:
        """Main polling loop — runs until cancelled."""
        while True:
            try:
                await asyncio.sleep(self.interval)
                await self._poll()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("file_watcher_poll_error", error=str(exc))

    async def _poll(self) -> None:
        """Single poll cycle — check all watched links for changes."""
        with tracer.start_as_current_span("file_watcher.poll") as span:
            all_links = link_store.list_all()
            watched = [lnk for lnk in all_links if lnk.watch]
            span.set_attribute("watcher.links_checked", len(watched))

            for link in watched:
                try:
                    status = check_sync_status(link)

                    if status in ("changed", "disconnected") and status != link.sync_status:
                        link_store.update(link.work_product_id, sync_status=status)

                        if status == "changed" and self._twin is not None:
                            await sync_linked_file(link, self._twin)
                            logger.info(
                                "file_watcher_synced",
                                wp_id=link.work_product_id,
                                source_path=link.source_path,
                            )
                        elif status == "disconnected":
                            logger.warning(
                                "file_watcher_disconnected",
                                wp_id=link.work_product_id,
                                source_path=link.source_path,
                            )
                except Exception as exc:
                    logger.warning(
                        "file_watcher_link_error",
                        wp_id=link.work_product_id,
                        source_path=link.source_path,
                        error=str(exc),
                    )


# Module-level singleton
file_watcher = FileWatcher()
