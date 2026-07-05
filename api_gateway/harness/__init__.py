"""Harness capability HTTP surface (MET-548).

Read-only endpoints that power the chat UI's model + tools/connectors selector:
which providers are registered/configured, what models a provider offers, and
what MCP tools/connectors are reachable through the gateway's bridge.
"""

from api_gateway.harness.routes import router

__all__ = ["router"]
