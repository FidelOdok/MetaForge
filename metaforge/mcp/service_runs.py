"""Asking the gateway whether a design-flow service call is in scope (FORGE-487).

The sidecar holds the service key; the gateway holds the truth about runs. A
key says the caller is the design-flow worker. It does not say that the run the
call names is real, still running, on a flow version a person approved, or in
the project the call is aimed at. Those are answered here, per call, against
the gateway's own endpoints:

* ``GET /v1/runs/{id}``: the run exists, is ``running``, and its request names
  the same project as the call.
* ``GET /v1/design-flows/versions/{id}``: the flow version the run is pinned
  to is ``approved`` and valid.

A run with no flow version is a built-in template run. FORGE-721: it is
trusted when the content hash the gateway recorded for it at start equals the
shipped template's (``GET /v1/design-flows/{id}`` ``contentHash``), so the run
executes reviewed code nobody tailored. A tailored or patched flow, an unknown
template, or a run with no recorded hash returns ``None``: the caller stays
untrusted and its writes are held for a person, as before. Before this every
built-in template run held every write, and a person had to approve each
FreeCAD and twin call of an approved run.

Answers are cached for a few seconds so a phase making a burst of writes is
not a burst of gateway requests. Only a *grant* is cached, and briefly: a run
that is cancelled or finishes stops being writable within one window, and a
refusal is never remembered, so a run that becomes valid is not locked out.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import structlog

from mcp_core.service_auth import ServiceGrant, ServiceScopeError

logger = structlog.get_logger(__name__)

__all__ = ["DEFAULT_GRANT_TTL_SECONDS", "GatewayRunVerifier"]

#: How long a verified grant is reused. Short enough that cancelling a run is
#: felt by its next write; long enough to absorb a phase's burst of calls.
DEFAULT_GRANT_TTL_SECONDS = 5.0
_REQUEST_TIMEOUT_SECONDS = 5.0


def _same_project(a: Any, b: str) -> bool:
    if not a:
        return False
    try:
        return uuid.UUID(str(a)) == uuid.UUID(str(b))
    except ValueError:
        return str(a).strip().lower() == str(b).strip().lower()


class GatewayRunVerifier:
    """A :class:`~mcp_core.service_auth.ServiceRunVerifier` over the gateway's REST API."""

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
        self._grants: dict[tuple[str, str], tuple[float, ServiceGrant]] = {}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS)
        return self._client

    async def verify(self, run_id: str, project_id: str) -> ServiceGrant | None:
        key = (run_id, project_id.lower())
        cached = self._grants.get(key)
        if cached is not None and self._clock() < cached[0]:
            return cached[1]
        self._grants.pop(key, None)

        http = self._http()
        response = await http.get(f"{self._base}/v1/runs/{run_id}")
        if response.status_code == 404:
            raise ServiceScopeError("", f"run {run_id} does not exist")
        response.raise_for_status()
        run = response.json()

        status = run.get("status")
        if status != "running":
            raise ServiceScopeError("", f"run {run_id} is {status}, not running")
        request = run.get("request") or {}
        run_project = request.get("project_id")
        if not _same_project(run_project, project_id):
            # Do not echo the run's project: the caller learns only that this
            # is not theirs.
            raise ServiceScopeError("", f"run {run_id} does not belong to project {project_id}")

        version_id = run.get("flow_version_id") or request.get("flow_version_id")
        if not version_id:
            grant = await self._template_grant(http, run_id, project_id, request)
            if grant is not None:
                self._grants[key] = (self._clock() + self._ttl, grant)
            return grant

        version_response = await http.get(f"{self._base}/v1/design-flows/versions/{version_id}")
        if version_response.status_code == 404:
            raise ServiceScopeError("", f"flow version {version_id} does not exist")
        version_response.raise_for_status()
        version = version_response.json()
        if version.get("status") != "approved" or not version.get("valid", False):
            raise ServiceScopeError(
                "",
                f"flow version {version_id} is {version.get('status')}, "
                "not an approved, valid version",
            )

        grant = ServiceGrant(run_id=run_id, project_id=project_id, flow_version_id=str(version_id))
        self._grants[key] = (self._clock() + self._ttl, grant)
        return grant

    async def _template_grant(
        self, http: httpx.AsyncClient, run_id: str, project_id: str, request: dict[str, Any]
    ) -> ServiceGrant | None:
        """A grant for a run of an unmodified built-in template, else ``None``."""
        template_id = request.get("flow_template_id")
        recorded = request.get("flow_content_hash")
        if not template_id or not recorded:
            return None
        response = await http.get(f"{self._base}/v1/design-flows/{template_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        view = response.json()
        shipped = view.get("contentHash")
        if not view.get("valid", False) or not shipped or shipped != recorded:
            logger.info(
                "mcp_service_template_run_not_trusted",
                run_id=run_id,
                template=template_id,
                reason="tailored, changed or invalid template",
            )
            return None
        return ServiceGrant(
            run_id=run_id,
            project_id=project_id,
            flow_version_id=f"template:{template_id}@{str(shipped)[:12]}",
        )
