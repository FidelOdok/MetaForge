"""Where a tool tells the viewer something happened in a project (FORGE-647).

A tool such as ``twin.place_part`` runs in whichever process hosts the MCP
server: the gateway, or the ``mcp-http`` sidecar. Only the gateway holds the
live stream the dashboard is subscribed to, so the tool cannot publish to it
directly. It calls :func:`publish_live_event` instead, and each process
installs the publisher that fits it at start-up: a direct call inside the
gateway, an authenticated HTTP post from the sidecar.

With no publisher installed the call does nothing and says so, so a tool can
report honestly that nobody could have seen the event.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["LiveEventPublisher", "publish_live_event", "set_live_event_publisher"]

#: Takes ``(project_id, event)`` and delivers it. May raise; the caller catches.
LiveEventPublisher = Callable[[str, dict[str, Any]], Awaitable[None]]

_publisher: LiveEventPublisher | None = None


def set_live_event_publisher(publisher: LiveEventPublisher | None) -> None:
    """Install (or, with ``None``, remove) this process's publisher."""
    global _publisher
    _publisher = publisher


async def publish_live_event(project_id: str, event: dict[str, Any]) -> bool:
    """Deliver ``event`` for ``project_id``. True when it was handed on.

    Best effort: a publisher that raises is logged and reported as False. A
    viewer missing an event is a lost moment, never a failed tool call.
    """
    if _publisher is None:
        return False
    try:
        await _publisher(project_id, event)
    except Exception as exc:  # noqa: BLE001 - the tool call must not fail on a viewer's behalf
        logger.info("live_event_dropped", project_id=project_id, error=str(exc))
        return False
    return True
