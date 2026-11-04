"""Reporting live assembly placements from the sidecar to the gateway (FORGE-647).

The viewer's stream lives in the gateway. A tool running in this process posts
each placement to ``POST /v1/twin/projects/{id}/live-events``, using the same
gateway address and key as the other callbacks from this container.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from mcp_core.live_events import LiveEventPublisher
from mcp_core.operation_events import OperationEventPublisher

__all__ = ["http_live_publisher", "http_operation_publisher"]

_TIMEOUT_SECONDS = 5.0


def _gateway_url() -> str:
    return (os.environ.get("METAFORGE_GATEWAY_URL") or "").strip().rstrip("/")


def http_live_publisher(client: httpx.AsyncClient | None = None) -> LiveEventPublisher | None:
    """A publisher that posts to the gateway, or None when no gateway is set.

    None leaves the tool reporting ``live: false``, which is the truth: with
    no gateway to post to, no viewer could have seen the placement.
    """
    base = _gateway_url()
    if not base:
        return None

    async def publish(project_id: str, event: dict[str, Any]) -> None:
        headers = {}
        key = (os.environ.get("METAFORGE_GATEWAY_API_KEY") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        url = f"{base}/v1/twin/projects/{project_id}/live-events"
        if client is not None:
            resp = await client.post(url, json=event, headers=headers, timeout=_TIMEOUT_SECONDS)
        else:
            async with httpx.AsyncClient() as c:
                resp = await c.post(url, json=event, headers=headers, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()

    return publish


def http_operation_publisher(
    client: httpx.AsyncClient | None = None,
) -> OperationEventPublisher | None:
    """Operation events (FORGE-785) posted to the gateway's ingest route.

    ``POST /v1/projects/{id}/operations/events``. The event carries its own
    ``event_id``, so a retried post is answered as a duplicate. None when no
    gateway is set: nobody could see the event.
    """
    base = _gateway_url()
    if not base:
        return None

    async def publish(project_id: str, event: dict[str, Any]) -> None:
        headers = {}
        key = (os.environ.get("METAFORGE_GATEWAY_API_KEY") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        url = f"{base}/v1/projects/{project_id}/operations/events"
        if client is not None:
            resp = await client.post(url, json=event, headers=headers, timeout=_TIMEOUT_SECONDS)
        else:
            async with httpx.AsyncClient() as c:
                resp = await c.post(url, json=event, headers=headers, timeout=_TIMEOUT_SECONDS)
        # 409 is the gateway refusing an event that is out of order or after
        # the operation ended: not a delivery failure.
        if resp.status_code != 409:
            resp.raise_for_status()

    return publish
