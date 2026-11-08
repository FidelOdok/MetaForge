"""``files.*`` from the sidecar, through the gateway.

Uploads, files and the link secret live in the gateway; this calls its
``/v1/uploads``, ``/v1/files`` routes and returns their shapes. No
``api_gateway`` import.

A link the gateway issues is a path (``/v1/links/<token>``). The person or
client using it reaches this sidecar, not the gateway, so the sidecar serves
``/v1/links/*`` itself (proxied to the gateway) and links are made absolute
on the address the MCP request came in on (:data:`PUBLIC_BASE`), or on
``METAFORGE_MCP_PUBLIC_URL`` when set.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import Any
from urllib.parse import quote as urlquote
from urllib.parse import urlencode

import httpx

from metaforge.mcp.remote_flows import _Gateway

__all__ = ["PUBLIC_BASE", "RemoteFilesService", "public_base"]

#: An upload of several megabytes through the gateway takes a while.
DEFAULT_TIMEOUT_SECONDS = 120.0

#: The address the current MCP request reached this sidecar on, set by the
#: ``/mcp`` route for the length of the call.
PUBLIC_BASE: ContextVar[str | None] = ContextVar("mcp_public_base", default=None)


def public_base() -> str:
    """Where a person reaches this sidecar: the env, else the current request's address."""
    configured = os.environ.get("METAFORGE_MCP_PUBLIC_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return (PUBLIC_BASE.get() or "http://localhost:8765").rstrip("/")


def _agent_header(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str] | None]:
    """The request without ``agent``, and the header that names it to the gateway."""
    body = {k: v for k, v in request.items() if k != "agent"}
    agent = str(request.get("agent") or "").strip()
    return body, ({"X-MetaForge-Agent": agent[:100]} if agent else None)


class RemoteFilesService:
    supports_local_paths = False

    def __init__(
        self,
        gateway_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._gateway = _Gateway(gateway_url, client, timeout_seconds)

    def absolute(self, path: str) -> str:
        return f"{public_base()}{path}"

    async def stage_inline(self, request: dict[str, Any]) -> dict[str, Any]:
        body, headers = _agent_header(request)
        result: dict[str, Any] = await self._gateway.request(
            "POST", "/v1/uploads/inline", json=body, headers=headers
        )
        return result

    async def request_upload(self, request: dict[str, Any]) -> dict[str, Any]:
        body, headers = _agent_header(request)
        result: dict[str, Any] = await self._gateway.request(
            "POST", "/v1/uploads/link", json=body, headers=headers
        )
        return result

    async def decide_upload(
        self, upload_id: str, decision: str, reason: str, agent: str, on_behalf_of: str
    ) -> dict[str, Any]:
        """Decide through ``/v1/approvals`` as the agent, for the person.

        The gateway applies its own rule: an agent decides an upload only when
        it too delegates human authority (403 otherwise).
        """
        result: dict[str, Any] = await self._gateway.request(
            "POST",
            f"/v1/approvals/{urlquote(f'upload:{upload_id}', safe=':')}/decision",
            json={"decision": decision, "reason": reason},
            headers={
                "X-MetaForge-Surface": "agent",
                "X-MetaForge-Agent": agent[:100],
                "X-MetaForge-On-Behalf-Of": on_behalf_of[:200],
            },
        )
        return result

    async def upload_status(self, upload_id: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "GET", f"/v1/uploads/{urlquote(upload_id, safe='')}"
        )
        return result

    async def list_files(self, work_product_id: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "GET", f"/v1/files/{urlquote(work_product_id, safe='')}"
        )
        return result

    async def read(self, work_product_id: str, path: str, max_bytes: int) -> dict[str, Any]:
        query = urlencode({"path": path, "max_bytes": max_bytes})
        result: dict[str, Any] = await self._gateway.request(
            "GET", f"/v1/files/{urlquote(work_product_id, safe='')}/read?{query}"
        )
        return result

    async def link(self, work_product_id: str, path: str, lifetime_s: int | None) -> dict[str, Any]:
        result: dict[str, Any] = await self._gateway.request(
            "POST",
            f"/v1/files/{urlquote(work_product_id, safe='')}/link",
            json={"path": path, "lifetime_s": lifetime_s},
        )
        return result

    async def stage_local(self, request: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(
            "local paths are read only by the local plugin (metaforge-local), which runs "
            "on the same machine; use files.import or files.request_upload here"
        )

    async def save_local(self, request: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(
            "saving into a local folder is only for the local plugin (metaforge-local); "
            "use files.link for a download link here"
        )
