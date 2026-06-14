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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx  # only needed for type hints; the runtime import is lazy so
    # admin commands (install/uninstall) and parsers don't require httpx.

DEFAULT_GATEWAY = "http://localhost:8000"
_DEFAULT_STATE_ROOT = Path.home() / ".metaforge" / "capture"
_MAX_TEXT = 8000
_EVENT_TYPES = {"thought", "action", "decision", "observation", "error", "result"}


def capture_enabled() -> bool:
    return os.environ.get("METAFORGE_SESSION_CAPTURE", "").strip().lower() != "off"


def _config() -> dict[str, Any]:
    """Read ~/.metaforge/capture/config.json (written by ``install``).

    A fallback for ``METAFORGE_GATEWAY_URL`` / ``METAFORGE_MCP_API_KEY`` so the
    hook reaches the gateway without editing shell profiles (MET-499). Env wins.
    """
    path = Path.home() / ".metaforge" / "capture" / "config.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _gateway_url() -> str:
    env = os.environ.get("METAFORGE_GATEWAY_URL")
    url = env or _config().get("gateway_url") or DEFAULT_GATEWAY
    return str(url).rstrip("/")


def _auth_headers() -> dict[str, str]:
    key = os.environ.get("METAFORGE_MCP_API_KEY") or _config().get("api_key")
    return {"X-API-Key": str(key)} if key else {}


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
        if http is None:
            import httpx  # lazy — only the actual push path needs httpx

            http = httpx.Client(base_url=self.gateway, timeout=2.0, headers=_auth_headers())
        self.http = http
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

    def push_delta(
        self,
        client_session_id: str,
        transcript_path: str,
        parser: Any,
        *,
        agent_code: str = "agent",
        task_type: str = "session",
    ) -> int:
        """Parser-driven transcript delta (MET-498).

        Reads new bytes since the cached cursor and runs ``parser(entry)`` →
        list of ``(type, message, data)`` per JSONL line, pushing each as an
        event. The byte cursor is keyed per ``(client, client_session_id)`` so
        a restarted tailer never re-emits old lines. Returns events pushed.
        """
        path = Path(transcript_path)
        if not path.exists():
            return 0
        cursor_key = f"cursor::{path.name}"
        state = self._load_state(client_session_id)
        cursor = int(state.get(cursor_key, 0) or 0)
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
            if not isinstance(entry, dict):
                continue
            for event_type, message, data in parser(entry):
                self.push_event(
                    client_session_id,
                    type=event_type,
                    message=message,
                    data=data,
                    agent_code=agent_code,
                    task_type=task_type,
                )
                pushed += 1
        state = self._load_state(client_session_id)
        state[cursor_key] = cursor + len(chunk)
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


def _load_parsers() -> Any:
    """Load the sibling ``parsers`` module by path (works as a plain script)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "metaforge_capture_parsers", Path(__file__).resolve().parent / "parsers.py"
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="metaforge-capture", description="Agent-session capture.")
    p.add_argument("--client", default="agent", help="Client name (e.g. claude-code).")
    p.add_argument("--session", default=None, help="Client-side session id (binding key).")
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

    # MET-498: parser-driven tailer — one MetaForge session per transcript file.
    tl = sub.add_parser("tail")
    tl.add_argument("--path", required=True, help="Glob of transcript files to tail.")
    tl.add_argument("--parser", default=None, help="Parser name (defaults to --client).")
    tl.add_argument("--follow", action="store_true", help="Keep watching (else one-shot).")
    tl.add_argument("--interval", type=float, default=2.0, help="Follow poll interval (s).")
    tl.add_argument("--agent-code", default="agent")
    tl.add_argument("--task-type", default="session")

    # MET-499: install/uninstall the Claude Code capture hook.
    for name in ("install", "uninstall"):
        a = sub.add_parser(name)
        scope = a.add_mutually_exclusive_group()
        scope.add_argument("--user", action="store_true", help="~/.claude/settings.json (default).")
        scope.add_argument("--project", action="store_true", help="./.claude/settings.json.")
        if name == "install":
            a.add_argument("--mode", choices=("copy", "link"), default="copy")
            a.add_argument("--gateway-url", default=None)
            a.add_argument("--api-key", default=None)
    return p


def _settings_path(args: argparse.Namespace) -> Path:
    if getattr(args, "project", False):
        return Path.cwd() / ".claude" / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def _run_admin(args: argparse.Namespace) -> int:
    """Handle install/uninstall — user-invoked, so surface errors (not silent)."""
    from tools.session_capture import installer

    settings_path = _settings_path(args)
    try:
        if args.command == "install":
            summary = installer.install(
                source_dir=Path(__file__).resolve().parent,
                settings_path=settings_path,
                metaforge_home=Path.home() / ".metaforge",
                mode=args.mode,
                gateway_url=args.gateway_url,
                api_key=args.api_key,
            )
            print("metaforge-capture installed:")
            print(f"  settings : {summary['settings_path']}")
            print(f"  adapter  : {summary['adapter_path']} ({summary['mode']})")
            print(f"  hooks    : {', '.join(summary['events'])}")
            if summary["gateway_url"]:
                print(f"  gateway  : {summary['gateway_url']}")
            print("Restart Claude Code to load the hooks.")
        else:
            installer.uninstall(settings_path)
            print(f"metaforge-capture hooks removed from {settings_path}")
        return 0
    except Exception as exc:  # noqa: BLE001 — admin command: show the failure
        print(f"metaforge-capture {args.command} failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Capture commands ALWAYS return 0 (never break the host);
    install/uninstall are user-invoked and surface errors."""
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit:
        return 0

    # Admin commands run regardless of the capture kill-switch and report errors.
    if args.command in ("install", "uninstall"):
        return _run_admin(args)

    try:
        if not capture_enabled():
            return 0
        client = CaptureClient(args.client)
        # The tail command derives a session per transcript file; everything
        # else needs an explicit --session binding key.
        if args.command != "tail" and not args.session:
            return 0
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
        elif args.command == "tail":
            _run_tail(client, args)
    except SystemExit:
        # argparse usage error — still exit 0 so a misconfigured hook is silent.
        return 0
    except Exception as exc:  # noqa: BLE001 — capture is best-effort
        _log_error(exc)
        return 0
    return 0


def _run_tail(client: CaptureClient, args: Any) -> None:
    """Tail transcript files matching a glob, one session per file (MET-498)."""
    import glob as _glob
    import time as _time

    parsers = _load_parsers()
    if parsers is None:
        return
    parser = parsers.get_parser(args.parser or args.client)
    if parser is None:
        return
    while True:
        for filepath in sorted(_glob.glob(args.path)):
            # One MetaForge session per transcript file; its name is the
            # stable binding key, so a restarted tailer resumes the cursor.
            session_key = Path(filepath).stem or filepath
            client.push_delta(
                session_key,
                filepath,
                parser,
                agent_code=args.agent_code,
                task_type=args.task_type,
            )
        if not args.follow:
            return
        _time.sleep(args.interval)


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
