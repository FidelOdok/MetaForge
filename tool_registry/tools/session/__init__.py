"""Session MCP adapter (MET-494).

Exposes ``session.start`` / ``session.log_event`` / ``session.complete``
over MCP so external agents can record their own narrative + decision
timeline into the agent-session store. Completes the takeover loop with
the server-side auto-capture middleware (MET-496).
"""
