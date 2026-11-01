"""Confirming a client's claimed phase task before trusting its writes (FORGE-584).

Off unless the owner sets ``METAFORGE_CLIENT_TASK_WRITES``. When on, a session
that took a phase task with ``phase.claim`` may write inside that run's
project without each write being held, within the same bounds as the
design-flow worker (no project, flow or run administration, no human
authority, nothing destructive).

The session's binding is only a claim. Before every grant the sidecar asks
the gateway, which holds the truth:

* ``GET /v1/client-tasks/{id}``: the task exists and is still ``claimed``
  (submitted or cancelled ends the grant);
* ``GET /v1/runs/{id}``: the run is ``running``, was started in client mode,
  and belongs to the project the session is bound to.

A grant is cached for a few seconds so a phase's burst of writes is not a
burst of gateway requests; a refusal is never cached. Anything the gateway
cannot confirm fails closed: the call is held like any other, never trusted.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import structlog

from mcp_core.context import TaskBinding

logger = structlog.get_logger(__name__)

__all__ = [
    "CLIENT_TASK_WRITES_ENV",
    "ClientTaskNotConfirmedError",
    "GatewayClientTaskVerifier",
    "client_task_writes_enabled",
]

#: The owner's switch. Off by default.
CLIENT_TASK_WRITES_ENV = "METAFORGE_CLIENT_TASK_WRITES"

DEFAULT_GRANT_TTL_SECONDS = 5.0
_REQUEST_TIMEOUT_SECONDS = 5.0


def client_task_writes_enabled() -> bool:
    return os.environ.get(CLIENT_TASK_WRITES_ENV, "").strip().lower() in ("1", "true", "yes", "on")


class ClientTaskNotConfirmedError(RuntimeError):
    """The gateway did not confirm the claim; the reason says why."""


def _same_project(a: Any, b: uuid.UUID | None) -> bool:
    if b is None or not a:
        return False
    try:
        return uuid.UUID(str(a)) == b
    except ValueError:
        return False


class GatewayClientTaskVerifier:
    """Confirms a :class:`TaskBinding` against the gateway's task and run stores."""

    def __init__(
        self,
        gateway_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        ttl_seconds: float = DEFAULT_GRANT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = gateway_url.rstrip("/")
        self._client = client
        self._ttl = ttl_seconds
        self._clock = clock
        self._granted: dict[str, float] = {}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS)
        return self._client

    async def confirm(self, binding: TaskBinding) -> None:
        """Return if the claim holds; raise :class:`ClientTaskNotConfirmedError` if not."""
        until = self._granted.get(binding.task_id)
        if until is not None and self._clock() < until:
            return
        self._granted.pop(binding.task_id, None)

        http = self._http()
        task_resp = await http.get(f"{self._base}/v1/client-tasks/{binding.task_id}")
        if task_resp.status_code == 404:
            raise ClientTaskNotConfirmedError(f"task {binding.task_id} does not exist")
        task_resp.raise_for_status()
        task = task_resp.json()
        if task.get("status") != "claimed":
            raise ClientTaskNotConfirmedError(
                f"task {binding.task_id} is {task.get('status')}, not claimed"
            )
        if task.get("run_id") != binding.run_id:
            raise ClientTaskNotConfirmedError(f"task {binding.task_id} is for another run")

        run_resp = await http.get(f"{self._base}/v1/runs/{binding.run_id}")
        if run_resp.status_code == 404:
            raise ClientTaskNotConfirmedError(f"run {binding.run_id} does not exist")
        run_resp.raise_for_status()
        run = run_resp.json()
        request = run.get("request") or {}
        if run.get("status") != "running":
            raise ClientTaskNotConfirmedError(
                f"run {binding.run_id} is {run.get('status')}, not running"
            )
        if request.get("intelligence") != "client":
            raise ClientTaskNotConfirmedError(f"run {binding.run_id} is not a client-mode run")
        if not _same_project(request.get("project_id"), binding.project_id):
            raise ClientTaskNotConfirmedError(
                f"run {binding.run_id} does not belong to the session's project"
            )
        self._granted[binding.task_id] = self._clock() + self._ttl
