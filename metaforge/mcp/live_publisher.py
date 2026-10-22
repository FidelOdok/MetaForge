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

__all__ = ["http_live_publisher"]

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
