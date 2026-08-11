# Agent Session Capture

How an external agent's tool calls and reasoning get recorded into the digital thread (MET-492).

- `tools/session_capture/` is the real capture core: `metaforge_capture.py` (27KB, main engine), `parsers.py`, `installer.py`.
- **The live Claude Code hook is `tools/session_capture/claude_code_adapter.py`**, wired via `.claude/settings.json`'s `PostToolUse` hooks (matcher `mcp__metaforge__.*`).
- **Documentation drift found 2026-08-11**: CLAUDE.md names the hook as `.claude/hooks/metaforge_session_push.py`. That file does not exist as tracked source — only a stale compiled `.pyc` remains under `.claude/hooks/__pycache__/`, and `git log --all` shows zero history for that filename. It was likely a locally-generated or renamed artifact, never committed. Point people at `claude_code_adapter.py` instead; CLAUDE.md's Agent Session Capture section needs a correction.
- Store: Postgres `agent_sessions` / `agent_session_events`, shared by gateway and the MCP sidecar via `DATABASE_URL`.
- Kill-switch: `METAFORGE_SESSION_CAPTURE=off`. Capture is always best-effort — it never fails or blocks a tool call.

See `docs/session-capture.md` for the layered design (server-side auto-capture, client hooks, explicit `session.*` tools).
