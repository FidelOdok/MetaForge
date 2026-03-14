"""HTTP client for the MetaForge Gateway API.

``ForgeClient`` wraps httpx to provide typed access to assistant,
twin, and proposal endpoints.  The base URL is read from the
``METAFORGE_GATEWAY_URL`` environment variable (default
``http://localhost:8000``).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

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
