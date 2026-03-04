"""Audit event logger with in-memory buffering and JSONL export.

MET-126
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

import structlog

from observability.audit.models import AuditEvent, AuditEventType, ExportConfig

logger = structlog.get_logger(__name__)


class AuditLogger:
    """Buffers audit events and flushes them to an export destination.

    Parameters
    ----------
    config:
        Export configuration (destination, batch size, etc.).
        If ``None`` the logger stores events but does not auto-flush.
    """

    def __init__(self, config: ExportConfig | None = None) -> None:
        self._config = config
        self._buffer: list[AuditEvent] = []
        self._flushed: list[AuditEvent] = []

    # ── Core logging ──────────────────────────────────────────────────

    def log_event(self, event: AuditEvent) -> None:
        """Append *event* to the buffer.  Auto-flush when the batch size
        is reached (if config is set)."""
        self._buffer.append(event)
        if (
            self._config is not None
            and len(self._buffer) >= self._config.batch_size
        ):
            self.flush()

    # ── Convenience helpers ───────────────────────────────────────────

    def log_policy_decision(
        self,
        actor: str,
        policy: str,
        result: str,
        details: dict | None = None,
    ) -> None:
        """Log an OPA policy decision event."""
        self.log_event(
            AuditEvent(
                event_id=uuid4(),
                event_type=AuditEventType.policy_decision,
                actor=actor,
                action=f"policy_evaluate:{policy}",
                resource_type="policy",
                resource_id=policy,
                details={"result": result, **(details or {})},
            )
        )

    def log_graph_mutation(
        self,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict | None = None,
    ) -> None:
        """Log a Digital Twin graph mutation event."""
        self.log_event(
            AuditEvent(
                event_id=uuid4(),
                event_type=AuditEventType.graph_mutation,
                actor=actor,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details or {},
            )
        )

    def log_approval_action(
        self,
        actor: str,
        action: str,
        change_id: str,
        decision: str,
        details: dict | None = None,
    ) -> None:
        """Log an approval workflow action."""
        self.log_event(
            AuditEvent(
                event_id=uuid4(),
                event_type=AuditEventType.approval_action,
                actor=actor,
                action=action,
                resource_type="change_request",
                resource_id=change_id,
                details={"decision": decision, **(details or {})},
            )
        )

    # ── Flush / query / export ────────────────────────────────────────

    def flush(self) -> list[AuditEvent]:
        """Move buffered events to the flushed list and return them.

        In a production implementation this would write to the configured
        export destination (S3, SIEM, etc.).  For now the events are kept
        in ``_flushed`` for query access.
        """
        events = list(self._buffer)
        self._flushed.extend(events)
        self._buffer.clear()
        logger.info("audit_flush", count=len(events))
        return events

    def get_events(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        event_type: AuditEventType | None = None,
        actor: str | None = None,
    ) -> list[AuditEvent]:
        """Query flushed + buffered events with optional filters."""
        all_events = self._flushed + self._buffer
        results: list[AuditEvent] = []
        for ev in all_events:
            if start is not None and ev.timestamp < start:
                continue
            if end is not None and ev.timestamp > end:
                continue
            if event_type is not None and ev.event_type != event_type:
                continue
            if actor is not None and ev.actor != actor:
                continue
            results.append(ev)
        return results

    @staticmethod
    def export_jsonl(events: list[AuditEvent]) -> str:
        """Serialize *events* to JSON Lines format.

        Each line is a JSON object with ``event_id`` serialized as a string
        and ``timestamp`` as an ISO-8601 string.
        """
        lines: list[str] = []
        for ev in events:
            obj = ev.model_dump()
            obj["event_id"] = str(obj["event_id"])
            obj["event_type"] = obj["event_type"]
            obj["timestamp"] = ev.timestamp.isoformat()
            lines.append(json.dumps(obj, default=str))
        return "\n".join(lines)
