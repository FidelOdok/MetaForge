"""Client-agnostic agent-session capture core (MET-497).

A tiny, dependency-light (stdlib + httpx only — no MetaForge imports) library
+ CLI that pushes an agent's narrative into the MetaForge session API
(``/v1/sessions``, MET-493). Per-client adapters (Claude Code hooks, Cursor,
OpenCode, …) translate their native hook events into calls on this core.

Hard guarantee: capture must never break the host agent. Every operation is
best-effort — failures are logged locally and swallowed; the CLI always exits 0.
"""

from tools.session_capture.metaforge_capture import CaptureClient, assistant_texts

__all__ = ["CaptureClient", "assistant_texts"]
