"""HTTP client for the MetaForge Gateway API.

``ForgeClient`` wraps httpx to provide typed access to assistant,
twin, and proposal endpoints.  The base URL is read from the
``METAFORGE_GATEWAY_URL`` environment variable (default
``http://localhost:8000``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import httpx


class ForgeClientError(Exception):
    """Raised by ``ForgeClient`` for non-transport errors callers should surface.

    ``status_code`` is set when the underlying HTTP response carried one
    so handlers can branch on 404 vs 5xx without re-parsing exception
    text.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ForgeClientNotFound(ForgeClientError):
    """Raised when the gateway returns 404 for a lookup-by-id endpoint."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


_DEFAULT_GATEWAY_URL = "http://localhost:8000"


class ForgeClient:
    """Thin wrapper around httpx for Gateway API calls.

    Parameters
    ----------
    base_url:
        Gateway base URL.  Falls back to ``METAFORGE_GATEWAY_URL`` env
        var, then ``http://localhost:8000``.
    timeout:
        Request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url or os.environ.get("METAFORGE_GATEWAY_URL") or _DEFAULT_GATEWAY_URL
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def _url(self, path: str) -> str:
        return f"/api/v1{path}"

    # ------------------------------------------------------------------
    # Skill invocation
    # ------------------------------------------------------------------

    def run_skill(
        self,
        skill_name: str,
        work_product_id: str,
        parameters: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Invoke a skill via ``POST /api/v1/assistant/request``."""
        payload: dict[str, Any] = {
            "action": skill_name,
            "target_id": work_product_id,
            "parameters": parameters or {},
        }
        if session_id:
            payload["session_id"] = session_id
        with self._client() as client:
            resp = client.post(self._url("/assistant/request"), json=payload)
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Session status
    # ------------------------------------------------------------------

    def get_status(self, session_id: str) -> dict[str, Any]:
        """Fetch session/agent status via ``GET /api/v1/assistant/sessions/{session_id}/status``.

        Note: this endpoint is a placeholder — returns a minimal object
        until the Orchestrator integration is built.
        """
        with self._client() as client:
            resp = client.get(self._url(f"/assistant/sessions/{session_id}/status"))
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Digital Twin queries
    # ------------------------------------------------------------------

    def twin_query(self, node_id: str) -> dict[str, Any]:
        """Query a single Digital Twin node via ``GET /api/v1/twin/nodes/{node_id}``."""
        with self._client() as client:
            resp = client.get(self._url(f"/twin/nodes/{node_id}"))
            resp.raise_for_status()
            return resp.json()

    def twin_list(
        self,
        domain: str | None = None,
        work_product_type: str | None = None,
    ) -> dict[str, Any]:
        """List Digital Twin work_products via ``GET /api/v1/twin/nodes``."""
        params: dict[str, str] = {}
        if domain:
            params["domain"] = domain
        if work_product_type:
            params["type"] = work_product_type
        with self._client() as client:
            resp = client.get(self._url("/twin/nodes"), params=params)
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Proposals
    # ------------------------------------------------------------------

    def list_proposals(self) -> dict[str, Any]:
        """List pending proposals via ``GET /api/v1/assistant/proposals``."""
        with self._client() as client:
            resp = client.get(self._url("/assistant/proposals"))
            resp.raise_for_status()
            return resp.json()

    def approve_proposal(
        self,
        change_id: str,
        reason: str,
        reviewer: str = "cli-user",
    ) -> dict[str, Any]:
        """Approve a proposal via ``POST /api/v1/assistant/proposals/{change_id}/decide``."""
        payload = {
            "change_id": change_id,
            "decision": "approve",
            "reason": reason,
            "reviewer": reviewer,
        }
        with self._client() as client:
            resp = client.post(self._url(f"/assistant/proposals/{change_id}/decide"), json=payload)
            resp.raise_for_status()
            return resp.json()

    def reject_proposal(
        self,
        change_id: str,
        reason: str,
        reviewer: str = "cli-user",
    ) -> dict[str, Any]:
        """Reject a proposal via ``POST /api/v1/assistant/proposals/{change_id}/decide``."""
        payload = {
            "change_id": change_id,
            "decision": "reject",
            "reason": reason,
            "reviewer": reviewer,
        }
        with self._client() as client:
            resp = client.post(self._url(f"/assistant/proposals/{change_id}/decide"), json=payload)
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Knowledge ingestion (MET-336)
    # ------------------------------------------------------------------

    def ingest_document(
        self,
        content: str,
        source_path: str,
        knowledge_type: str,
        source_work_product_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Ingest a document via ``POST /v1/knowledge/documents``.

        Larger payloads need a longer timeout than the 30s default
        because LightRAG's ingest pipeline runs synchronously inside
        the request — pass ``timeout=120`` for big PDFs.

        MET-451: knowledge routes moved off the ``/api/v1/`` prefix to
        align with the rest of the gateway, so this method hard-codes
        ``/v1/knowledge/...`` instead of going through ``self._url``
        (which still prepends ``/api/v1`` for legacy callers).
        """
        payload: dict[str, Any] = {
            "content": content,
            "sourcePath": source_path,
            "knowledgeType": knowledge_type,
            "metadata": metadata or {},
        }
        if source_work_product_id:
            payload["sourceWorkProductId"] = source_work_product_id
        eff_timeout = timeout if timeout is not None else self.timeout
        with httpx.Client(base_url=self.base_url, timeout=eff_timeout) as client:
            resp = client.post("/v1/knowledge/documents", json=payload)
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Knowledge sources (MET-411)
    # ------------------------------------------------------------------

    def list_sources(
        self,
        knowledge_type: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List ingested knowledge sources via ``GET /v1/knowledge/sources`` (MET-451).

        Returns the raw response envelope ``{"sources": [...], "total": N}``.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if knowledge_type:
            params["knowledgeType"] = knowledge_type
        if project_id:
            params["projectId"] = project_id
        with self._client() as client:
            resp = client.get("/v1/knowledge/sources", params=params)
            resp.raise_for_status()
            return resp.json()

    def get_source(self, source_path: str) -> dict[str, Any]:
        """Fetch one source via ``GET /v1/knowledge/sources/{path}`` (MET-451).

        Raises ``ForgeClientNotFound`` on 404 so the CLI can surface a
        clean message instead of a stack trace.
        """
        encoded = quote(source_path, safe="")
        with self._client() as client:
            resp = client.get(f"/v1/knowledge/sources/{encoded}")
            if resp.status_code == 404:
                raise ForgeClientNotFound(f"No knowledge source registered for {source_path!r}")
            resp.raise_for_status()
            return resp.json()

    def delete_source(self, source_path: str) -> dict[str, Any]:
        """Delete a source via ``DELETE /v1/knowledge/sources/{path}`` (MET-451).

        Returns ``{"sourcePath": ..., "deletedChunks": N}``.
        """
        encoded = quote(source_path, safe="")
        with self._client() as client:
            resp = client.delete(f"/v1/knowledge/sources/{encoded}")
            if resp.status_code == 404:
                raise ForgeClientNotFound(f"No knowledge source registered for {source_path!r}")
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Harness runs (MET-548) — served at /v1/runs (no /api prefix)
    # ------------------------------------------------------------------

    def create_run(
        self, request: dict[str, Any] | None = None, *, start: bool = True
    ) -> dict[str, Any]:
        """Create a run via ``POST /v1/runs``."""
        with self._client() as client:
            resp = client.post("/v1/runs", json={"request": request or {}, "start": start})
            resp.raise_for_status()
            return resp.json()

    def list_runs(self) -> dict[str, Any]:
        """List runs via ``GET /v1/runs``."""
        with self._client() as client:
            resp = client.get("/v1/runs")
            resp.raise_for_status()
            return resp.json()

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Fetch one run via ``GET /v1/runs/{id}``."""
        with self._client() as client:
            resp = client.get(f"/v1/runs/{run_id}")
            if resp.status_code == 404:
                raise ForgeClientNotFound(f"No run with id {run_id!r}")
            resp.raise_for_status()
            return resp.json()

    def submit_run_approval(self, run_id: str, decision: str) -> dict[str, Any]:
        """Approve or reject a paused run via ``POST /v1/runs/{id}/approval``."""
        with self._client() as client:
            resp = client.post(f"/v1/runs/{run_id}/approval", json={"decision": decision})
            if resp.status_code == 404:
                raise ForgeClientNotFound(f"No run with id {run_id!r}")
            resp.raise_for_status()
            return resp.json()

    def stream_run_events(self, run_id: str) -> Iterator[dict[str, Any]]:
        """Yield run status events from the SSE stream until it closes.

        Uses no read timeout since the stream is long-lived.
        """
        with httpx.Client(base_url=self.base_url, timeout=None) as client:
            with client.stream("GET", f"/v1/runs/{run_id}/events") as resp:
                if resp.status_code == 404:
                    raise ForgeClientNotFound(f"No run with id {run_id!r}")
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        yield json.loads(line[len("data:") :].strip())

    # ------------------------------------------------------------------
    # Chat (MET-556) — served at /v1/chat (no /api prefix)
    # ------------------------------------------------------------------

    def list_channels(self) -> dict[str, Any]:
        """List chat channels via ``GET /v1/chat/channels``."""
        with self._client() as client:
            resp = client.get("/v1/chat/channels")
            resp.raise_for_status()
            return resp.json()

    def create_thread(
        self,
        scope_kind: str,
        scope_entity_id: str,
        *,
        title: str | None = None,
        initial_message: str | None = None,
    ) -> dict[str, Any]:
        """Create a chat thread via ``POST /v1/chat/threads``.

        Raises ``ForgeClientError`` on 400 (no channel for the scope_kind).
        """
        payload: dict[str, Any] = {
            "scope_kind": scope_kind,
            "scope_entity_id": scope_entity_id,
        }
        if title is not None:
            payload["title"] = title
        if initial_message is not None:
            payload["initial_message"] = initial_message
        with self._client() as client:
            resp = client.post("/v1/chat/threads", json=payload)
            if resp.status_code == 400:
                raise ForgeClientError(
                    f"No chat channel for scope_kind={scope_kind!r}", status_code=400
                )
            resp.raise_for_status()
            return resp.json()

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        """Fetch a thread (with its messages) via ``GET /v1/chat/threads/{id}``."""
        with self._client() as client:
            resp = client.get(f"/v1/chat/threads/{thread_id}")
            if resp.status_code == 404:
                raise ForgeClientNotFound(f"No thread with id {thread_id!r}")
            resp.raise_for_status()
            return resp.json()

    def send_message(
        self,
        thread_id: str,
        content: str,
        *,
        actor_id: str = "cli-user",
        actor_kind: str = "user",
        provider: str | None = None,
        model: str | None = None,
        tools: list[str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Post a message via ``POST /v1/chat/threads/{id}/messages``.

        The gateway invokes the agent synchronously and appends its reply to the
        thread; this returns the *user* message envelope, so callers refetch the
        thread to read the agent's response. A longer timeout is allowed since
        the agent turn runs inside the request.
        """
        payload: dict[str, Any] = {
            "content": content,
            "actor_id": actor_id,
            "actor_kind": actor_kind,
        }
        if provider is not None:
            payload["provider"] = provider
        if model is not None:
            payload["model"] = model
        if tools is not None:
            payload["tools"] = tools
        eff_timeout = timeout if timeout is not None else self.timeout
        with httpx.Client(base_url=self.base_url, timeout=eff_timeout) as client:
            resp = client.post(f"/v1/chat/threads/{thread_id}/messages", json=payload)
            if resp.status_code == 404:
                raise ForgeClientNotFound(f"No thread with id {thread_id!r}")
            resp.raise_for_status()
            return resp.json()
