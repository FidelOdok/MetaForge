"""Talking to a machine's controller to run a job (FORGE-451 step 4, FORGE-427/440).

A connector does three things, and only these: say whether the machine is
idle (``health``), send a file and start it (``start``), and report the job
on the machine (``status``). Starting a job is a physical action, so the MCP
tool that reaches ``start`` (``library.dispatch_job``) is held for the
person every time; nothing here decides to print.

States are normalised across controllers: ``idle``, ``printing``,
``paused``, ``complete``, ``cancelled``, ``failed``, ``offline``. Failures
are typed (:class:`ConnectorError` ``kind``) so a caller can say *why*: the
machine is unreachable, refused the key, is busy, or answered something this
code does not understand.

The key is read on the gateway from the connection's ``credential_env``
(``METAFORGE_MACHINE_*`` only, checked by ``parse_connection``).
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import httpx
import structlog

from observability.tracing import get_tracer
from twin_core.library.machines import MachineLibraryError, parse_connection

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.library.connectors")

DEFAULT_TIMEOUT_SECONDS = 30.0
#: Upload can be a large G-code file over a slow link.
UPLOAD_TIMEOUT_SECONDS = 300.0

TERMINAL_STATES = frozenset({"complete", "cancelled", "failed"})

__all__ = [
    "TERMINAL_STATES",
    "ConnectorError",
    "MachineConnector",
    "MoonrakerConnector",
    "OctoPrintConnector",
    "connector_for",
]


class ConnectorError(RuntimeError):
    """A controller call failed. ``kind``: unreachable, auth, busy, refused, protocol, config."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


class MachineConnector(Protocol):
    protocol: str

    async def health(self) -> dict[str, Any]: ...

    async def start(self, content: bytes, filename: str) -> dict[str, Any]: ...

    async def status(self) -> dict[str, Any]: ...


class _Http:
    def __init__(self, connection: dict[str, Any], client: httpx.AsyncClient | None) -> None:
        self.base = str(connection["url"])
        self._client = client
        env = connection.get("credential_env")
        self.headers: dict[str, str] = {}
        if env:
            key = os.environ.get(env, "").strip()
            if not key:
                raise ConnectorError("config", f"{env} is not set on the gateway")
            self.headers["X-Api-Key"] = key

    async def call(
        self,
        method: str,
        path: str,
        *,
        files: Any = None,
        data: Any = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        allow: tuple[int, ...] = (),
    ) -> tuple[int, Any]:
        owned = self._client is None
        http = self._client or httpx.AsyncClient(timeout=timeout)
        try:
            response = await http.request(
                method, f"{self.base}{path}", headers=self.headers, files=files, data=data
            )
        except httpx.HTTPError as exc:
            raise ConnectorError("unreachable", f"could not reach {self.base}: {exc}") from exc
        finally:
            if owned:
                await http.aclose()
        if response.status_code in (401, 403):
            raise ConnectorError("auth", f"{self.base}{path} refused the credential")
        if response.status_code in allow:
            return response.status_code, None
        if response.status_code == 409:
            raise ConnectorError("busy", f"{self.base}{path}: the machine is not ready (409)")
        if response.is_error:
            raise ConnectorError(
                "refused", f"{self.base}{path} answered HTTP {response.status_code}"
            )
        try:
            return response.status_code, response.json()
        except ValueError as exc:
            raise ConnectorError("protocol", f"{self.base}{path} did not answer JSON") from exc


class OctoPrintConnector:
    protocol = "octoprint"

    def __init__(self, connection: dict[str, Any], client: httpx.AsyncClient | None = None):
        self._http = _Http(connection, client)

    async def health(self) -> dict[str, Any]:
        code, body = await self._http.call("GET", "/api/printer", allow=(409,))
        if code == 409:
            return {"state": "offline"}
        flags = ((body or {}).get("state") or {}).get("flags") or {}
        if flags.get("printing"):
            state = "printing"
        elif flags.get("paused"):
            state = "paused"
        elif flags.get("error") or not flags.get("operational"):
            state = "offline"
        else:
            state = "idle"
        return {"state": state}

    async def start(self, content: bytes, filename: str) -> dict[str, Any]:
        _, body = await self._http.call(
            "POST",
            "/api/files/local",
            files={"file": (filename, content, "application/octet-stream")},
            data={"select": "true", "print": "true"},
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
        local = ((body or {}).get("files") or {}).get("local") or {}
        return {"remote_path": local.get("path") or local.get("name") or filename}

    async def status(self) -> dict[str, Any]:
        _, body = await self._http.call("GET", "/api/job")
        body = body or {}
        raw = str(body.get("state") or "")
        completion = (body.get("progress") or {}).get("completion")
        progress = float(completion) / 100.0 if completion is not None else None
        name = ((body.get("job") or {}).get("file") or {}).get("name")
        lowered = raw.lower()
        if lowered.startswith(("printing", "starting", "resuming")):
            state = "printing"
        elif lowered.startswith(("paused", "pausing")):
            state = "paused"
        elif lowered.startswith("cancelling"):
            state = "cancelled"
        elif lowered.startswith("operational"):
            # OctoPrint goes back to Operational after a job; the last job's
            # completion tells a finished print from a stopped one.
            if name and progress is not None:
                state = "complete" if progress >= 0.999 else "cancelled"
            else:
                state = "idle"
        elif "error" in lowered:
            state = "failed"
        else:
            state = "offline"
        return {"state": state, "filename": name, "progress": progress, "raw_state": raw}


class MoonrakerConnector:
    protocol = "moonraker"

    def __init__(self, connection: dict[str, Any], client: httpx.AsyncClient | None = None):
        self._http = _Http(connection, client)

    async def health(self) -> dict[str, Any]:
        _, info = await self._http.call("GET", "/printer/info")
        if ((info or {}).get("result") or {}).get("state") != "ready":
            return {"state": "offline"}
        current = await self.status()
        return {"state": "idle" if current["state"] in TERMINAL_STATES else current["state"]}

    async def start(self, content: bytes, filename: str) -> dict[str, Any]:
        _, body = await self._http.call(
            "POST",
            "/server/files/upload",
            files={"file": (filename, content, "application/octet-stream")},
            data={"print": "true"},
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
        result = (body or {}).get("result") or body or {}
        if result.get("print_started") is False:
            raise ConnectorError("refused", "Moonraker stored the file but did not start it")
        return {"remote_path": (result.get("item") or {}).get("path") or filename}

    async def status(self) -> dict[str, Any]:
        _, body = await self._http.call("GET", "/printer/objects/query?print_stats&virtual_sdcard")
        status = (((body or {}).get("result") or {}).get("status")) or {}
        stats = status.get("print_stats") or {}
        raw = str(stats.get("state") or "")
        state = {
            "standby": "idle",
            "printing": "printing",
            "paused": "paused",
            "complete": "complete",
            "cancelled": "cancelled",
            "error": "failed",
        }.get(raw, "offline")
        progress = (status.get("virtual_sdcard") or {}).get("progress")
        return {
            "state": state,
            "filename": stats.get("filename") or None,
            "progress": float(progress) if progress is not None else None,
            "raw_state": raw,
        }


_CONNECTORS = {"octoprint": OctoPrintConnector, "moonraker": MoonrakerConnector}


def connector_for(
    connection: dict[str, Any], client: httpx.AsyncClient | None = None
) -> MachineConnector:
    """The connector for a library connection, or a ``config`` error saying why not."""
    try:
        conn = parse_connection(connection)
    except MachineLibraryError as exc:
        raise ConnectorError("config", str(exc)) from exc
    cls = _CONNECTORS.get(str(conn.get("protocol")))
    if cls is None:
        raise ConnectorError(
            "config",
            "jobs can be sent to octoprint or moonraker machines; this one's connection is "
            f"{conn.get('protocol') or 'not set'}",
        )
    connector: MachineConnector = cls(conn, client)
    return connector
