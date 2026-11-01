"""Announcing an open design-flow gate to the people who can answer it (FORGE-489).

A run parked at a gate used to wait silently: the worker that opened it had no
announcer wired, so nobody was told until someone polled ``flow.status``. The
dashboard's Approvals page reads the gateway's run list, and that list is only
as fresh as the gateway's record of the run. This module makes the gateway's
record say "awaiting approval" the moment the workflow reaches the gate, which
is what puts the run on the Approvals page and on the run's SSE stream.

Announcing never approves. It moves the *record* to ``awaiting_approval`` and
nothing else; the gate is answered only by ``POST /v1/runs/{id}/approval``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

__all__ = ["DEFAULT_GATEWAY_URL", "gateway_url", "http_gate_announcer"]

#: Where the gateway lives inside compose. Overridden by ``METAFORGE_GATEWAY_URL``.
DEFAULT_GATEWAY_URL = "http://gateway:8000"

_TIMEOUT_SECONDS = 10.0


def gateway_url() -> str:
    return (os.environ.get("METAFORGE_GATEWAY_URL") or DEFAULT_GATEWAY_URL).strip().rstrip("/")


def http_gate_announcer(client: httpx.AsyncClient | None = None) -> Any:
    """A ``GateAnnouncer`` that tells the gateway a run has opened a gate.

    The worker is a separate process with no run store of its own, so the
    gateway is the one place the Approvals page can learn this from. The call
    carries the gateway API key when one is configured. A non-2xx answer
    raises, so the activity counts it as a failed announcement instead of a
    delivered one.
    """

    async def announce(run_id: str, gate: str, reason: str) -> None:
        headers = {}
        key = (os.environ.get("METAFORGE_GATEWAY_API_KEY") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        url = f"{gateway_url()}/v1/runs/{run_id}/gate-opened"
        body = {"gate": gate, "reason": reason}
        if client is not None:
            resp = await client.post(url, json=body, headers=headers, timeout=_TIMEOUT_SECONDS)
        else:
            async with httpx.AsyncClient() as c:
                resp = await c.post(url, json=body, headers=headers, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        logger.info("design_flow_gate_announced", run_id=run_id, gate=gate)

    return announce
