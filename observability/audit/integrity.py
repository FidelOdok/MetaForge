"""Tamper-evident hash chain for audit events.

Produces a SHA-256 hash chain where each entry includes the hash of the
previous entry, making it straightforward to detect modifications to
historical events.

MET-126
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from observability.audit.models import AuditEvent


class AuditIntegrity:
    """Computes and verifies hash chains over audit events."""

    @staticmethod
    def compute_hash(event: AuditEvent, previous_hash: str = "") -> str:
        """Return the SHA-256 hex digest of *event* combined with
        *previous_hash*.

        The canonical form is a JSON serialisation of the event fields
        that matter for integrity (event_id, event_type, actor, action,
        resource_type, resource_id, details, timestamp, tenant_id) plus
        the previous hash.
        """
        canonical: dict[str, Any] = {
            "event_id": str(event.event_id),
            "event_type": event.event_type.value,
            "actor": event.actor,
            "action": event.action,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "details": event.details,
            "timestamp": event.timestamp.isoformat(),
            "tenant_id": event.tenant_id,
            "previous_hash": previous_hash,
        }
        payload = json.dumps(canonical, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def build_hash_chain(cls, events: list[AuditEvent]) -> list[str]:
        """Build a tamper-evident hash chain over *events*.

        Returns a list of hex digests of the same length as *events*.
        """
        chain: list[str] = []
        previous = ""
        for event in events:
            h = cls.compute_hash(event, previous)
            chain.append(h)
            previous = h
        return chain

    @classmethod
    def verify_chain(cls, events: list[AuditEvent], chain: list[str]) -> bool:
        """Verify that *chain* is a valid hash chain for *events*.

        Returns ``True`` if every hash matches; ``False`` otherwise.
        """
        if len(events) != len(chain):
            return False
        expected = cls.build_hash_chain(events)
        return expected == chain
