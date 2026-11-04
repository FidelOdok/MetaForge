"""Cancel and retry for jobs the MCP sidecar runs (FORGE-788, RT-028).

A slow tool job runs in the process that hosts the MCP server it was called
on. A job called through the ``mcp-http`` sidecar therefore lives there, and
only that process can stop it or run it again. The gateway relays the request
to the sidecar's ``POST /v1/jobs/{job_id}/cancel`` and ``.../retry``, which
take the same credential as its MCP route.

The sidecar's address is ``METAFORGE_MCP_URL`` (written the way clients see
it, ``.../mcp``) and its key ``METAFORGE_MCP_CLIENT_KEY`` (else
``METAFORGE_MCP_API_KEY``). With no address set there is no sidecar to relay
to, and the operation is not cancellable from here.

Best effort: an unreachable sidecar is reported as such and never retried.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.operations.sidecar")

__all__ = ["RelayResult", "relay_job_action", "sidecar_base_url"]

_TIMEOUT_SECONDS = 10.0

JobAction = Literal["cancel", "retry"]


@dataclass(frozen=True)
class RelayResult:
    """What the sidecar answered. ``status`` 0 means it was not reached."""

    status: int
    body: dict[str, Any]

    @property
    def reached(self) -> bool:
        return self.status != 0


def sidecar_base_url(env: Mapping[str, str] | None = None) -> str | None:
    """The sidecar's base URL from ``METAFORGE_MCP_URL``, or None when unset."""
    env = os.environ if env is None else env
    url = (env.get("METAFORGE_MCP_URL") or "").strip().rstrip("/")
    if not url:
        return None
    if url.endswith("/mcp"):
        url = url[: -len("/mcp")]
    return url


def _headers(env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = os.environ if env is None else env
    key = (env.get("METAFORGE_MCP_CLIENT_KEY") or env.get("METAFORGE_MCP_API_KEY") or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


async def relay_job_action(
    job_id: str,
    action: JobAction,
    *,
    client: httpx.AsyncClient | None = None,
) -> RelayResult | None:
    """Ask the sidecar to cancel or retry ``job_id``. None when no sidecar is set."""
    base = sidecar_base_url()
    if base is None:
        return None
    url = f"{base}/v1/jobs/{job_id}/{action}"
    with tracer.start_as_current_span("operations.sidecar_relay") as span:
        span.set_attribute("job_id", job_id)
        span.set_attribute("action", action)
        try:
            if client is not None:
                resp = await client.post(url, headers=_headers(), timeout=_TIMEOUT_SECONDS)
            else:
                async with httpx.AsyncClient() as http:
                    resp = await http.post(url, headers=_headers(), timeout=_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            span.record_exception(exc)
            logger.warning(
                "operation_sidecar_unreachable", job_id=job_id, action=action, error=str(exc)
            )
            return RelayResult(0, {"detail": f"the MCP sidecar could not be reached ({exc})"})
        span.set_attribute("status", resp.status_code)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    logger.info("operation_sidecar_relayed", job_id=job_id, action=action, status=resp.status_code)
    return RelayResult(resp.status_code, body if isinstance(body, dict) else {})


async def relay_job_cancel(job_id: str) -> RelayResult | None:
    """The supersede path's relay (``publisher.set_job_cancel_relay``)."""
    return await relay_job_action(job_id, "cancel")
