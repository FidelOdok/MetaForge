"""Where a process reports an operation's progress (FORGE-785).

A slow tool job runs in whichever process hosts the MCP server: the gateway,
or the ``mcp-http`` sidecar. Only the gateway holds the durable operation log
and the project stream the dashboard reads, so a job cannot write there
directly. It calls :func:`publish_operation_event` instead, and each process
installs the publisher that fits it at start-up: a direct call inside the
gateway, an authenticated HTTP post from the sidecar (the same shape as
:mod:`mcp_core.live_events`).

With no publisher installed the call does nothing and says so. Publishing is
best effort: a lost status event is a stale progress line, never a failed
tool call.

This module also names the operation domains, so producers in any layer can
derive one from a tool id without importing the gateway.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "DOMAINS",
    "OperationEventPublisher",
    "domain_for_text",
    "domain_for_tool",
    "publish_operation_event",
    "set_operation_event_publisher",
]

#: Takes ``(project_id, event)`` and delivers it. May raise; the caller catches.
OperationEventPublisher = Callable[[str, dict[str, Any]], Awaitable[Any]]

#: The domains an operation or stage can belong to (the contract's enum).
DOMAINS: tuple[str, ...] = (
    "concept",
    "sketch",
    "mechanical",
    "electronics",
    "firmware",
    "simulation",
    "manufacturing",
    "cross_domain",
    "general",
)

#: Tool id prefixes (the part before the first dot) and the domain they work in.
_TOOL_PREFIX_DOMAIN: dict[str, str] = {
    "cadquery": "mechanical",
    "freecad": "mechanical",
    "cad": "mechanical",
    "occt": "mechanical",
    "kicad": "electronics",
    "firmware": "firmware",
    "calculix": "simulation",
    "spice": "simulation",
    "openfoam": "simulation",
    "gazebo": "simulation",
    "isaac_sim": "simulation",
    "manufacture": "manufacturing",
    "dfm": "manufacturing",
}

#: Words that name a domain, checked in order (the first match wins).
_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("concept", "concept"),
    ("sketch", "sketch"),
    ("firmware", "firmware"),
    ("mech", "mechanical"),
    ("cad", "mechanical"),
    ("enclosure", "mechanical"),
    ("electr", "electronics"),
    ("pcb", "electronics"),
    ("schematic", "electronics"),
    ("simulat", "simulation"),
    ("thermal", "simulation"),
    ("manufactur", "manufacturing"),
    ("gerber", "manufacturing"),
    ("hardware", "cross_domain"),
)

_publisher: OperationEventPublisher | None = None


def domain_for_text(text: str | None) -> str:
    """A domain named by a flow, phase or discipline id, else ``general``."""
    lowered = (text or "").lower()
    if lowered in DOMAINS:
        return lowered
    for word, domain in _KEYWORDS:
        if word in lowered:
            return domain
    return "general"


def domain_for_tool(tool_id: str | None) -> str:
    """The domain a tool works in, from its id prefix (``kicad.run_drc``)."""
    raw = (tool_id or "").strip()
    prefix, _, name = raw.partition(".")
    if prefix in _TOOL_PREFIX_DOMAIN:
        return _TOOL_PREFIX_DOMAIN[prefix]
    # ``twin.generate_concept_mesh`` and similar: the name carries the domain.
    return domain_for_text(name) if name else "general"


def set_operation_event_publisher(publisher: OperationEventPublisher | None) -> None:
    """Install (or, with ``None``, remove) this process's publisher."""
    global _publisher  # noqa: PLW0603
    _publisher = publisher


def operation_event_publisher() -> OperationEventPublisher | None:
    """This process's publisher, if one is installed."""
    return _publisher


async def publish_operation_event(project_id: str, event: dict[str, Any]) -> bool:
    """Deliver ``event`` for ``project_id``. True when it was handed on.

    Best effort: a publisher that raises is logged and reported as False.
    """
    if _publisher is None:
        return False
    try:
        await _publisher(project_id, event)
    except Exception as exc:  # noqa: BLE001 - a status line never fails the work it describes
        logger.info(
            "operation_event_dropped",
            project_id=project_id,
            operation_id=event.get("operation_id"),
            error=str(exc),
        )
        return False
    return True
