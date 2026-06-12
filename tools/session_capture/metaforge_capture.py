"""metaforge-capture — client-agnostic agent-session capture core (MET-497).

stdlib + httpx ONLY (no MetaForge imports), so any client environment can run
it. Pushes a normalized event stream into the MetaForge session API:

    ensure-session        POST  /v1/sessions            (lazy, cached)
    push-event            POST  /v1/sessions/{id}/events
    push-transcript-delta (reads a .jsonl from a byte cursor → thought events)
    complete              PATCH /v1/sessions/{id}

State (session id + transcript byte cursor) lives at
``~/.metaforge/capture/<client>/<client_session_id>.json``.

Config (env):
    METAFORGE_GATEWAY_URL    default http://localhost:8000
    METAFORGE_MCP_API_KEY    sent as X-API-Key when set
    METAFORGE_SESSION_CAPTURE=off   global kill-switch

The normalized event contract is exactly MET-493's ``SessionEventCreateRequest``:
``{type, message, data}`` where type ∈ thought|action|decision|observation|error|result.

Hard guarantee: never raise out of the CLI; always exit 0. Capture failures
must not break the host agent's turn.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

DEFAULT_GATEWAY = "http://localhost:8000"
_DEFAULT_STATE_ROOT = Path.home() / ".metaforge" / "capture"
_MAX_TEXT = 8000
_EVENT_TYPES = {"thought", "action", "decision", "observation", "error", "result"}


def capture_enabled() -> bool:
    return os.environ.get("METAFORGE_SESSION_CAPTURE", "").strip().lower() != "off"


def _gateway_url() -> str:
    return os.environ.get("METAFORGE_GATEWAY_URL", DEFAULT_GATEWAY).rstrip("/")


def _auth_headers() -> dict[str, str]:
    key = os.environ.get("METAFORGE_MCP_API_KEY")
    return {"X-API-Key": key} if key else {}


def assistant_texts(entry: Any) -> list[str]:
    """Extract assistant text blocks from one transcript JSONL entry.

    Handles the Claude Code shape (``{type:"assistant", message:{content:[...]}}``)
    plus a couple of common variants. Returns [] for anything else so unknown
    formats are simply ignored rather than erroring.
    """
    if not isinstance(entry, dict):
        return []
    if entry.get("type") != "assistant":
        return []
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    out: list[str] = []
    if isinstance(content, str):
        if content.strip():
            out.append(content.strip()[:_MAX_TEXT])
    elif isinstance(content, list):
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].strip()
            ):
                out.append(block["text"].strip()[:_MAX_TEXT])
    return out


class CaptureClient:
    """Pushes capture events to the MetaForge session API for one client.

    ``http`` is injectable (an ``httpx.Client``) so tests can supply a
    MockTransport. ``state_root`` is injectable so tests don't touch ``$HOME``.
    """

    def __init__(
        self,
        client_name: str,
        *,
        http: httpx.Client | None = None,
        gateway: str | None = None,
        state_root: Path | None = None,
    ) -> None:
        self.client_name = client_name
        self.gateway = (gateway or _gateway_url()).rstrip("/")
        self.http = http or httpx.Client(
            base_url=self.gateway, timeout=2.0, headers=_auth_headers()
        )
        self.state_root = state_root or _DEFAULT_STATE_ROOT

    # -- state --------------------------------------------------------------

    def _state_path(self, client_session_id: str) -> Path:
        d = self.state_root / self.client_name
        d.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in client_session_id)
        return d / f"{safe or 'session'}.json"

    def _load_state(self, client_session_id: str) -> dict[str, Any]:
        path = self._state_path(client_session_id)
        if path.exists():
            try:
                data: Any = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
            return data if isinstance(data, dict) else {}
        return {}

    def _save_state(self, client_session_id: str, state: dict[str, Any]) -> None:
        # Atomic-ish write so concurrent hook firings don't corrupt the file.
        path = self._state_path(client_session_id)
        tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(state))
        tmp.replace(path)

    # -- operations ---------------------------------------------------------

    def ensure_session(
        self,
        client_session_id: str,
        *,
        agent_code: str,
        task_type: str,
        title: str | None = None,
        project_id: str | None = None,
    ) -> str | None:
        """Return the MetaForge session id, creating it on first use."""
        state = self._load_state(client_session_id)
        sid = state.get("session_id")
        if isinstance(sid, str) and sid:
            return sid
        body: dict[str, Any] = {"agent_code": agent_code, "task_type": task_type}
        if title:
            body["title"] = title
        if project_id:
            body["project_id"] = project_id
        resp = self.http.post("/v1/sessions", json=body)
        resp.raise_for_status()
        sid = str(resp.json()["id"])
        state["session_id"] = sid
        state.setdefault("cursor", 0)
        self._save_state(client_session_id, state)
        return sid

    def push_event(
        self,
        client_session_id: str,
        *,
        type: str,
        message: str,
        data: dict[str, Any] | None = None,
        agent_code: str = "agent",
        task_type: str = "session",
    ) -> dict[str, Any] | None:
        if type not in _EVENT_TYPES:
            type = "observation"
        sid = self.ensure_session(client_session_id, agent_code=agent_code, task_type=task_type)
        if sid is None:
            return None
        payload = {"type": type, "message": message[:_MAX_TEXT], "data": data or {}}
        resp = self.http.post(f"/v1/sessions/{sid}/events", json=payload)
        resp.raise_for_status()
        result: Any = resp.json()
        return result if isinstance(result, dict) else {}

    def push_transcript_delta(
        self,
        client_session_id: str,
        transcript_path: str,
        *,
        agent_code: str = "agent",
        task_type: str = "session",
    ) -> int:
        """Read new transcript bytes since the cached cursor; push assistant
        text as ``thought`` events. Returns the number of events pushed."""
        path = Path(transcript_path)
        if not path.exists():
            return 0
        state = self._load_state(client_session_id)
        cursor = int(state.get("cursor", 0) or 0)
        size = path.stat().st_size
        if size <= cursor:
            return 0
        with path.open("rb") as fh:
            fh.seek(cursor)
            chunk = fh.read()
        pushed = 0
        for raw in chunk.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            for text in assistant_texts(entry):
                self.push_event(
                    client_session_id,
                    type="thought",
                    message=text,
                    data={"origin": "transcript", "source": self.client_name},
                    agent_code=agent_code,
                    task_type=task_type,
                )
                pushed += 1
        # Advance the cursor even if no thoughts were extracted, so we don't
        # re-scan the same bytes next time.
        state = self._load_state(client_session_id)
        state["cursor"] = cursor + len(chunk)
        self._save_state(client_session_id, state)
        return pushed

    def complete(
        self,
        client_session_id: str,
        *,
        status: str = "completed",
        summary: str | None = None,
    ) -> bool:
        state = self._load_state(client_session_id)
        sid = state.get("session_id")
        if not isinstance(sid, str) or not sid:
            return False
        body: dict[str, Any] = {"status": status}
        if summary:
            body["summary"] = summary[:_MAX_TEXT]
        resp = self.http.patch(f"/v1/sessions/{sid}", json=body)
        resp.raise_for_status()
        return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="metaforge-capture", description="Agent-session capture.")
    p.add_argument("--client", default="agent", help="Client name (e.g. claude-code).")
    p.add_argument("--session", required=True, help="Client-side session id (binding key).")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("ensure-session")
    s.add_argument("--agent-code", default="agent")
    s.add_argument("--task-type", default="session")
    s.add_argument("--title", default=None)
    s.add_argument("--project-id", default=None)

    e = sub.add_parser("push-event")
    e.add_argument("--type", required=True)
    e.add_argument("--message", required=True)
    e.add_argument("--data", default=None, help="JSON object string.")
    e.add_argument("--agent-code", default="agent")
    e.add_argument("--task-type", default="session")

    t = sub.add_parser("push-transcript-delta")
    t.add_argument("--transcript", required=True)
    t.add_argument("--agent-code", default="agent")
    t.add_argument("--task-type", default="session")

    c = sub.add_parser("complete")
    c.add_argument("--status", default="completed")
    c.add_argument("--summary", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry. ALWAYS returns 0 — capture must never break the host."""
    try:
        if not capture_enabled():
            return 0
        args = _build_parser().parse_args(argv)
        client = CaptureClient(args.client)
        if args.command == "ensure-session":
            client.ensure_session(
                args.session,
                agent_code=args.agent_code,
                task_type=args.task_type,
                title=args.title,
                project_id=args.project_id,
            )
        elif args.command == "push-event":
            data = None
            if args.data:
                try:
                    data = json.loads(args.data)
                except (json.JSONDecodeError, ValueError):
                    data = {"raw": args.data}
            client.push_event(
                args.session,
                type=args.type,
                message=args.message,
                data=data,
                agent_code=args.agent_code,
                task_type=args.task_type,
            )
        elif args.command == "push-transcript-delta":
            client.push_transcript_delta(
                args.session,
                args.transcript,
                agent_code=args.agent_code,
                task_type=args.task_type,
            )
        elif args.command == "complete":
            client.complete(args.session, status=args.status, summary=args.summary)
    except SystemExit:
        # argparse usage error — still exit 0 so a misconfigured hook is silent.
        return 0
    except Exception as exc:  # noqa: BLE001 — capture is best-effort
        _log_error(exc)
        return 0
    return 0


def _log_error(exc: Exception) -> None:
    try:
        log_dir = _DEFAULT_STATE_ROOT
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "errors.log").open("a") as fh:
            fh.write(f"{type(exc).__name__}: {exc}\n")
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    sys.exit(main())
