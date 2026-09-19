---
updated: 2026-08-11
---

# Repository Structure Drift

Places where the actual repo tree (verified 2026-08-11) diverges from CLAUDE.md's "canonical layout" diagram. None of these are bugs by themselves — they're just places a newcomer (human or agent) following the diagram literally will get confused.

- **Two twin directories**: `twin_core/` (current, real implementation — see [Digital Twin](digital-twin.md)) and an older `digital_twin/` still exist side by side at repo root. Only `twin_core/` is wired into the gateway.
- **A stray `metaforge/` package** exists alongside `cli/`, `api_gateway/`, etc. — not mentioned in CLAUDE.md's layout diagram, but it's where `metaforge/mcp/server.py` (the MCP server implementation referenced in [MCP HTTP Sidecar](mcp-http-sidecar.md)) actually lives.
- **CLI vs TUI**: CLAUDE.md's layout section doesn't distinguish these; in practice `cli/forge_cli/` is the mature, working CLI and `tui/` is the newer TypeScript client intended to eventually supersede it. See [CLI & TUI](cli-and-tui.md).
- **"Adapter exists" vs "adapter is wired in"**: the KiCad tool adapter existed in `tool_registry/tools/kicad/` for a while before it was actually registered in the unified MCP server. Directory presence is not proof of reachability — check `tool_registry/bootstrap.py`.
- **CLAUDE.md's Agent Session Capture section names a hook file that was never committed** (`.claude/hooks/metaforge_session_push.py` — only a stale `.pyc` remains, no git history). The real hook is `tools/session_capture/claude_code_adapter.py`. See [Agent Session Capture](agent-session-capture.md).

**Pattern to watch for**: CLAUDE.md's layout diagram describes the target modular-monorepo structure (from MetaForge-Planner), not necessarily what exists today at any given moment. When in doubt, `ls` the actual directory and check what's wired into `bootstrap.py` / `server.py`, don't trust the diagram alone. If you find a new instance of this kind of drift, add it here — and if it's egregious enough to mislead someone, fix CLAUDE.md itself in the same PR.
