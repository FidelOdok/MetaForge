"""Publish twin work-product events onto the event bus (MET-567).

``KnowledgeConsumer`` has subscribed to ``WORK_PRODUCT_CREATED`` /
``WORK_PRODUCT_UPDATED`` since MET-307 — and until now nothing in the codebase
ever published one. The event type existed, the Kafka topic mapping existed,
the consumer existed, and the whole path was dead: a decision recorded through
``twin.record_decision`` became a graph node and a MinIO blob, but never a
knowledge chunk, so ``knowledge.search(knowledge_type=DESIGN_DECISION)`` could
not find the very decisions the agent had just been told to record.

This module is the publisher side, wired from a bootstrap the same way the
other layer-4 singletons are (``init_twin`` / ``init_mcp_bridge`` /
``init_context_assembler``). With no bus wired it is a no-op, so recording a
work product behaves exactly as before.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from orchestrator.event_bus.events import Event, EventType

logger = structlog.get_logger(__name__)

_bus: Any = None


def init_work_product_events(bus: Any) -> None:
    """Wire the event bus work-product events publish onto (``None`` disables)."""
    global _bus  # noqa: PLW0603
    _bus = bus
    logger.info("work_product_events_initialized", enabled=bus is not None)


def work_product_events_enabled() -> bool:
    return _bus is not None


async def publish_work_product_created(
    *,
    work_product_id: str,
    work_product_type: str,
    name: str,
    content: str,
    project_id: str | None = None,
    source: str = "twin",
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Publish one ``WORK_PRODUCT_CREATED`` event. Returns whether it was published.

    ``content`` is the text the knowledge layer will chunk and index, and
    ``work_product_type`` is what it classifies on (``design_decision`` →
    ``KnowledgeType.DESIGN_DECISION``) — see
    ``digital_twin/knowledge/consumer.py``. Best-effort by contract: a bus
    failure is logged, never raised, because indexing must not be able to fail
    a write the twin has already committed.
    """
    if _bus is None:
        return False
    event = Event(
        id=str(uuid4()),
        type=EventType.WORK_PRODUCT_CREATED,
        timestamp=datetime.now(UTC).isoformat(),
        source=source,
        data={
            "work_product_id": work_product_id,
            "work_product_type": work_product_type,
            "name": name,
            "content": content,
            "project_id": project_id,
            **(metadata or {}),
        },
    )
    try:
        await _bus.publish(event)
    except Exception as exc:  # noqa: BLE001 — indexing never breaks a write
        logger.warning(
            "work_product_event_publish_failed",
            work_product_id=work_product_id,
            error=str(exc),
        )
        return False
    logger.info(
        "work_product_event_published",
        work_product_id=work_product_id,
        work_product_type=work_product_type,
        project_id=project_id,
    )
    return True
