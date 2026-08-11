# Chat Harness & SSE

How dashboard/TUI chat actually wires to the backend harness, and where it breaks.

## Request flow

Dashboard chat is request/response + refetch, not SSE-driven for message *delivery*. `POST /v1/chat/threads/{id}/messages` runs the agent turn **synchronously**, persists the reply, and returns the user message; the frontend invalidates the thread query and refetches to show the agent's reply. A reply appears even with no SSE connected at all.

**Token streaming** is a separate layer on top: the backend broadcasts `message.delta` (+ `agent.typing`/`agent.done`) over `/v1/chat/threads/{id}/stream`. Because the send POST runs the agent turn synchronously and broadcasts all deltas during that single request, **the SSE `EventSource` must already be connected before you send**, or the deltas hit zero subscribers and the message just appears whole on refetch with no incremental render. Pages that auto-open the stream on mount (before the user types) get this right; anything with a create-then-send flow has a cold-start race on the very first message.

**Live steps (MET-590)**: `agent.step` SSE events stream during the loop now, not just in a burst at `agent.done` — long turns have per-tool-call liveness.

## The envelope gotcha (bit twice, in two different clients)

The gateway wraps stream payloads in an envelope: `{"data": {"delta": ...}, "thread_id": ..., "timestamp": ...}`. Any client-side SSE parser that reads `data.delta` directly instead of unwrapping `envelope.data` first will silently drop every token — showing "(no reply)" while the server logs the turn as a success (structlog → OTel → Loki, not docker stdout, so `docker logs` looks clean too). This exact bug hit the Python CLI first, then had to be independently re-fixed in the TypeScript TUI later — check both clients' parse paths when touching the stream format.

## Debugging the TUI specifically

The Ink-based `forge` TUI owns the terminal and cannot log to stdout. Logs go to `~/.forge/logs/session.log` (JSONL, always on; `FORGE_LOG_FILE` overrides the path). `--debug` or `FORGE_LOG=1` adds raw SSE frames. The key line per turn is `chat.turn_done {events, deltas, chars, reason}` — `reason` is only set when a turn came back empty, and names the cause (SSE parse mismatch, stream error, turn never reached the stream, or genuinely empty output).

A "(no reply)" plus a timeout error around the 5-minute idle mark usually means a **stale installed binary** (an old undici default timeout), not a real backend issue — check `forge --version` against the repo and rebuild (`cd tui && npm run bundle:bin`). Replace a busy binary with `mv`, not `cp` — `cp` fails with "Text file busy" while a session is running.

## Tools in chat

`run_chat_turn` takes an optional MCP bridge; if the gateway's bridge is empty (no adapters wired into the gateway's lifespan), chat has zero real tools even though the harness itself is fine. A live deployment with a real bridge wired in has dozens of real tools available to chat (cadquery/freecad/calculix/kicad/twin/etc.) — check which situation you're in before assuming "chat can't use tools" is a harness bug rather than a wiring gap.

## Known frontend gotchas

- `crypto.randomUUID()` requires a secure context; over plain HTTP it's `undefined` and crashes. Use a fallback-aware ID helper, not a bare call.
- The Vite dashboard dev server needs a manual restart after any file *deletion* — HMR serves a stale/broken bundle otherwise, and a browser hard-reload doesn't fix it.

Related: [Dashboard](dashboard.md), [CLI & TUI](cli-and-tui.md).
