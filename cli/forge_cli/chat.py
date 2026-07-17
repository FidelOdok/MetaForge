"""`forge chat` — interactive assistant REPL (MET-556).

A Claude-Code-style terminal front-end for the MetaForge assistant, built as a
*thin client* over the gateway's ``/v1/chat`` surface (harness-backed). It
creates or reuses an ``assistant``-scope thread, sends each line you type, and
prints the agent's reply.

This is the MVP foundation (parent MET-555): non-streaming request/response.
Token streaming + a live tool-call timeline (from the SSE ``/stream`` endpoint)
and inline approval for gated tools land in follow-up slices. Output is clean
ANSI, stdlib only — no third-party TUI dependency yet.
"""

from __future__ import annotations

import argparse
import sys
import threading
import uuid
from collections.abc import Iterable
from typing import Any

from cli.forge_cli.client import ForgeClient, ForgeClientError, ForgeClientNotFound

# The main assistant chat lives on the "assistant" scope (Design Assistant channel).
_SCOPE_KIND = "assistant"

# ANSI styling — only emitted to a TTY (and never when --no-color).
_DIM = "\033[2m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_RESET = "\033[0m"


def _c(text: str, code: str, *, enabled: bool) -> str:
    """Wrap ``text`` in an ANSI ``code`` when styling is enabled."""
    return f"{code}{text}{_RESET}" if enabled else text


def _agent_replies_after(messages: list[dict[str, Any]], user_msg_id: str) -> list[dict[str, Any]]:
    """Agent messages that appear after the just-sent user message.

    The gateway appends the agent reply synchronously during the message POST,
    so by the time we refetch the thread it is already present. Messages are in
    chronological order; we collect agent-authored ones after our user message.
    Falls back to the trailing agent messages if the id isn't found.
    """
    out: list[dict[str, Any]] = []
    seen = False
    for m in messages:
        if m.get("id") == user_msg_id:
            seen = True
            continue
        if seen and m.get("actor_kind") == "agent":
            out.append(m)
    if not out:  # id not found (e.g. paginated) — take trailing agent messages
        trailing: list[dict[str, Any]] = []
        for m in reversed(messages):
            if m.get("actor_kind") == "agent":
                trailing.append(m)
            else:
                break
        out = list(reversed(trailing))
    return out


def _render_step(step: dict[str, Any], *, color: bool) -> None:
    """Render one ReAct step as a tool-timeline line (Claude-Code style)."""
    if step.get("final") or not step.get("tool"):
        return  # the final step's answer arrives via message.delta
    tool = step.get("tool")
    arguments = step.get("arguments") or {}
    error = step.get("error")
    shown = ", ".join(f"{k}={v!r}" for k, v in list(arguments.items())[:3])
    if len(arguments) > 3:
        shown += ", …"
    mark = _c("✗", _RED, enabled=color) if error else _c("✓", _GREEN, enabled=color)
    print(_c(f"⏺ {tool}({shown})", _CYAN, enabled=color) + f"  {mark}")
    if error:
        print(_c(f"  {error}", _DIM, enabled=color))


def _render_stream(events: Iterable[dict[str, Any]], *, color: bool) -> str:
    """Consume chat SSE events, render timeline + streamed answer, return the text.

    Pure over an event iterable so it unit-tests without threads or a network.
    Stops on ``agent.done`` or ``error``.
    """
    parts: list[str] = []
    answering = False
    for evt in events:
        etype = evt.get("event")
        data = evt.get("data") or {}
        if etype == "agent.step":
            _render_step(data.get("step") or {}, color=color)
        elif etype == "message.delta":
            if not answering:
                sys.stdout.write("\n" + _c("assistant  ", _GREEN, enabled=color))
                answering = True
            delta = str(data.get("delta", ""))
            sys.stdout.write(delta)
            sys.stdout.flush()
            parts.append(delta)
        elif etype == "agent.done":
            break
        elif etype == "error":
            msg = data.get("message") or data.get("error") or data
            print(_c(f"\nError: {msg}", _RED, enabled=color), file=sys.stderr)
            break
    if answering:
        sys.stdout.write("\n\n")
        sys.stdout.flush()
    return "".join(parts)


def _turn_streaming(
    args: argparse.Namespace,
    client: ForgeClient,
    thread_id: str,
    content: str,
    *,
    color: bool,
) -> None:
    """Run one turn with live SSE rendering.

    Subscribe to the thread stream in a background thread, wait until it's
    connected, then POST the message (which drives the harness to broadcast
    deltas/steps). The consumer renders live and stops on agent.done.
    """
    connected = threading.Event()
    rendered = threading.Event()

    def _consume() -> None:
        try:
            _render_stream(
                client.stream_thread_events(thread_id, on_connect=connected.set),
                color=color,
            )
        except ForgeClientError as exc:
            print(_c(f"\nError: stream failed: {exc}", _RED, enabled=color), file=sys.stderr)
        finally:
            connected.set()  # never leave the main thread blocked
            rendered.set()

    consumer = threading.Thread(target=_consume, daemon=True)
    consumer.start()

    if not connected.wait(timeout=10.0):
        print(_c("  (could not open stream; falling back)", _DIM, enabled=color))
        _turn(args, client, thread_id, content, color=color)
        return

    try:
        client.send_message(
            thread_id,
            content,
            provider=args.provider,
            model=args.model,
            timeout=args.timeout,
        )
    except ForgeClientNotFound as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return
    except ForgeClientError as exc:
        print(f"Error: send failed: {exc}", file=sys.stderr)
        return

    # Give the stream a moment to flush the final events after the turn returns.
    rendered.wait(timeout=5.0)


def _turn(
    args: argparse.Namespace,
    client: ForgeClient,
    thread_id: str,
    content: str,
    *,
    color: bool,
) -> None:
    """Run one chat turn (non-streaming): send the message, print the agent reply."""
    try:
        user_msg = client.send_message(
            thread_id,
            content,
            provider=args.provider,
            model=args.model,
            timeout=args.timeout,
        )
    except ForgeClientNotFound as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return
    except ForgeClientError as exc:
        print(f"Error: send failed: {exc}", file=sys.stderr)
        return

    try:
        thread = client.get_thread(thread_id)
    except ForgeClientError as exc:
        print(f"Error: could not read reply: {exc}", file=sys.stderr)
        return

    replies = _agent_replies_after(thread.get("messages", []), user_msg.get("id", ""))
    if not replies:
        print(_c("  (no reply — is an LLM configured on the gateway?)", _DIM, enabled=color))
        return
    for m in replies:
        label = _c("assistant", _GREEN, enabled=color)
        print(f"\n{label}  {m.get('content', '')}\n")


def _resolve_thread(args: argparse.Namespace, client: ForgeClient) -> str | None:
    """Reuse ``--thread`` if given, else create a fresh assistant-scope thread."""
    if args.thread:
        try:
            client.get_thread(args.thread)
        except ForgeClientNotFound as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return None
        except ForgeClientError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return None
        return args.thread

    entity = args.session or f"cli-{uuid.uuid4().hex[:8]}"
    try:
        thread = client.create_thread(_SCOPE_KIND, entity, title=args.title or "CLI session")
    except ForgeClientError as exc:
        print(f"Error: could not start chat: {exc}", file=sys.stderr)
        return None
    return str(thread["id"])


def handle_chat(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Dispatch `forge chat` — one-shot (``--message``) or interactive REPL."""
    color = sys.stdout.isatty() and not getattr(args, "no_color", False)

    thread_id = _resolve_thread(args, client)
    if thread_id is None:
        return None

    turn = _turn if getattr(args, "no_stream", False) else _turn_streaming

    # One-shot mode: send a single message and exit (scriptable).
    if args.message:
        turn(args, client, thread_id, args.message, color=color)
        return None

    banner = _c("MetaForge assistant", _BOLD, enabled=color)
    banner += _c(f"  (thread {thread_id})", _DIM, enabled=color)
    print(banner)
    print(_c("Type your message. /exit or Ctrl-D to quit.", _DIM, enabled=color))
    prompt = _c("› ", _CYAN, enabled=color)

    while True:
        try:
            line = input(prompt)
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            break
        line = line.strip()
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        turn(args, client, thread_id, line, color=color)

    return None
