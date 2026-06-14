# Claude Code session-capture adapter (MET-497)

Captures this Claude Code session's **reasoning + actions** into MetaForge
`/sessions` (the digital thread), via the client-agnostic core in
`../metaforge_capture.py`.

`.claude/` is git-ignored, so the hook itself lives at the tracked path
`tools/session_capture/claude_code_adapter.py`; you only need to register it
in your local `.claude/settings.json`.

## Install

**Easiest (no clone):**

```
pipx install "git+https://github.com/FidelOdok/MetaForge.git#subdirectory=tools/session_capture"
metaforge-capture install --user --gateway-url http://fidel-dev:8000
```

**From a clone:** `python -m tools.session_capture.metaforge_capture install --user --gateway-url …`

**Manual:** merge `settings.snippet.json` (in this directory) into
`.claude/settings.json` at the repo root. It registers three hooks, all
pointing at the tracked adapter:

- `PostToolUse` matcher `mcp__metaforge__.*` → `action` event
- `Stop` → push new assistant text from the transcript as `thought` events
- `SessionEnd` → complete the session

That's it — the adapter shells to the core, which talks to the gateway at
`METAFORGE_GATEWAY_URL` (default `http://localhost:8000`).

## Config / safety

- `METAFORGE_GATEWAY_URL` — gateway base URL (e.g. `http://fidel-dev:8000`).
- `METAFORGE_MCP_API_KEY` — sent as `X-API-Key` when set.
- `METAFORGE_SESSION_CAPTURE=off` — global kill-switch.

Capture is best-effort: every failure is swallowed and the hook always exits 0,
so it can never break a turn.

## Other clients

The core is client-neutral. Cursor / OpenCode / Codex adapters and a
transcript-tailer fallback are tracked in MET-498; each is just a thin
translation into the same core calls.
