"""Drift reconciler — keeps file state and Digital Twin graph in sync.

Tracks ``StateLink`` mappings between file paths and graph nodes,
detects drift when files are modified outside the graph pipeline,
and reconciles by re-ingesting changed files.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.assistant.reconciler")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DriftDirection(StrEnum):
    """Direction of drift between a file and its graph representation."""

    FILE_NEWER = "file_newer"
    GRAPH_NEWER = "graph_newer"
    FILE_MISSING = "file_missing"
    IN_SYNC = "in_sync"


class StateLink(BaseModel):
    """Mapping between a file on disk and a node in the Digital Twin graph."""

    file_path: str
    file_hash: str
    graph_node_id: UUID
    last_synced: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DriftResult(BaseModel):
    """Outcome of a drift check for a single file."""

    file_path: str
    direction: DriftDirection
    details: str


# ---------------------------------------------------------------------------
# Event types used by the reconciler
# ---------------------------------------------------------------------------

# We reuse EventType from the event bus, but the reconciler also needs
# custom event types.  We add them to the Event.data dict with a
# "drift_event" key to avoid modifying the core EventType enum.

_DRIFT_DETECTED = "assistant.drift.detected"
_DRIFT_RESOLVED = "assistant.drift.resolved"


# ---------------------------------------------------------------------------
# DriftReconciler
# ---------------------------------------------------------------------------


class DriftReconciler:
    """Detect and reconcile drift between files and the Digital Twin graph.

    Parameters
    ----------
    twin:
        TwinAPI instance for reading/writing graph state.
    event_bus:
        EventBus for publishing drift events.
    scan_interval_s:
        Interval (in seconds) between periodic full scans.
    """

    def __init__(
        self,
        twin: Any,  # TwinAPI — use Any to avoid circular import
        event_bus: Any,  # EventBus
        scan_interval_s: float = 60.0,
    ) -> None:
        self._twin = twin
        self._event_bus = event_bus
        self._scan_interval_s = scan_interval_s
        self._links: dict[str, StateLink] = {}

    # -- Link management ----------------------------------------------------

    async def register_link(
        self,
        file_path: str,
        node_id: UUID,
        file_hash: str,
    ) -> StateLink:
        """Track a file <-> graph node mapping."""
        link = StateLink(
            file_path=file_path,
            file_hash=file_hash,
            graph_node_id=node_id,
        )
        self._links[file_path] = link
        logger.info(
            "link_registered",
            file_path=file_path,
            node_id=str(node_id),
        )
        return link

    def get_link(self, file_path: str) -> StateLink | None:
        """Return the link for *file_path*, or ``None``."""
        return self._links.get(file_path)

    @property
    def link_count(self) -> int:
        return len(self._links)

    # -- Drift detection ----------------------------------------------------

    async def check_drift(self, file_path: str) -> DriftResult:
        """Compare file hash on disk against stored hash."""
        with tracer.start_as_current_span("reconciler.check_drift") as span:
            span.set_attribute("file.path", file_path)

            link = self._links.get(file_path)
            if link is None:
                return DriftResult(
                    file_path=file_path,
                    direction=DriftDirection.IN_SYNC,
                    details="No link registered for this file",
                )

            path = Path(file_path)
            if not path.exists():
                result = DriftResult(
                    file_path=file_path,
                    direction=DriftDirection.FILE_MISSING,
                    details="File no longer exists on disk",
                )
                span.set_attribute("drift.direction", str(result.direction))
                return result

            current_hash = self._compute_hash(path)
            if current_hash == link.file_hash:
                result = DriftResult(
                    file_path=file_path,
                    direction=DriftDirection.IN_SYNC,
                    details="File hash matches stored hash",
                )
            else:
                result = DriftResult(
                    file_path=file_path,
                    direction=DriftDirection.FILE_NEWER,
                    details=(
                        f"File hash changed: stored={link.file_hash[:12]}... "
                        f"current={current_hash[:12]}..."
                    ),
                )

            span.set_attribute("drift.direction", str(result.direction))
            return result

    # -- Reconciliation -----------------------------------------------------

    async def reconcile(self, drift: DriftResult) -> None:
        """Resolve a detected drift.

        For ``FILE_NEWER`` drift: re-hash the file and update the link.
        For ``FILE_MISSING`` drift: remove the link.
        """
        with tracer.start_as_current_span("reconciler.reconcile") as span:
            span.set_attribute("file.path", drift.file_path)
            span.set_attribute("drift.direction", str(drift.direction))

            if drift.direction == DriftDirection.FILE_NEWER:
                await self._reconcile_file_newer(drift)
            elif drift.direction == DriftDirection.FILE_MISSING:
                await self._reconcile_file_missing(drift)
            elif drift.direction == DriftDirection.IN_SYNC:
                logger.debug("no_reconciliation_needed", file_path=drift.file_path)
                return

            # Publish DRIFT_RESOLVED event
            await self._publish_drift_event(_DRIFT_RESOLVED, drift)

    async def _reconcile_file_newer(self, drift: DriftResult) -> None:
        """Update link hash after file changed on disk."""
        link = self._links.get(drift.file_path)
        if link is None:
            return

        path = Path(drift.file_path)
        new_hash = self._compute_hash(path)

        # Update the stored link
        self._links[drift.file_path] = StateLink(
            file_path=link.file_path,
            file_hash=new_hash,
            graph_node_id=link.graph_node_id,
        )

        logger.info(
            "drift_reconciled_file_newer",
            file_path=drift.file_path,
            old_hash=link.file_hash[:12],
            new_hash=new_hash[:12],
        )

    async def _reconcile_file_missing(self, drift: DriftResult) -> None:
        """Remove link when file is gone."""
        removed = self._links.pop(drift.file_path, None)
        if removed:
            logger.info(
                "drift_reconciled_file_missing",
                file_path=drift.file_path,
                node_id=str(removed.graph_node_id),
            )

    # -- Full scan ----------------------------------------------------------

    async def run_full_scan(self) -> list[DriftResult]:
        """Check all tracked files for drift."""
        with tracer.start_as_current_span("reconciler.full_scan") as span:
            results: list[DriftResult] = []
            drifted = 0

            for file_path in list(self._links.keys()):
                result = await self.check_drift(file_path)
                results.append(result)
                if result.direction != DriftDirection.IN_SYNC:
                    drifted += 1
                    await self._publish_drift_event(_DRIFT_DETECTED, result)

            span.set_attribute("reconciler.total_files", len(results))
            span.set_attribute("reconciler.drifted_files", drifted)

            logger.info(
                "full_scan_complete",
                total_files=len(results),
                drifted_files=drifted,
            )
            return results

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _compute_hash(path: Path) -> str:
        """Compute SHA-256 hex digest of a file."""
        try:
            data = path.read_bytes()
            return hashlib.sha256(data).hexdigest()
        except OSError:
            return ""

    async def _publish_drift_event(self, event_kind: str, drift: DriftResult) -> None:
        """Publish a drift event on the event bus."""
        from orchestrator.event_bus.events import Event, EventType

        event = Event(
            id=str(uuid.uuid4()),
            type=EventType.WORK_PRODUCT_UPDATED,
            timestamp=datetime.now(UTC).isoformat(),
            source="assistant.reconciler",
            data={
                "drift_event": event_kind,
                "file_path": drift.file_path,
                "direction": str(drift.direction),
                "details": drift.details,
            },
        )
        try:
            await self._event_bus.publish(event)
        except Exception:
            logger.exception(
                "drift_event_publish_failed",
                event_kind=event_kind,
                file_path=drift.file_path,
            )
