"""Additional transport implementations for the MCP client."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_core.client import Transport

if TYPE_CHECKING:
    from tool_registry.mcp_server.server import McpToolServer


class LoopbackTransport(Transport):
    """In-process transport that connects an McpClient directly to an McpToolServer.

    Instead of going over stdio or HTTP, this transport dispatches JSON-RPC
    messages directly to the server's handle_request() method. Used for
    integration testing and in-process demos.

    Example::

        server = CalculixServer()
        transport = LoopbackTransport(server)
        client = McpClient()
        await client.connect("calculix", transport)
    """

    def __init__(self, server: McpToolServer) -> None:
        self._server = server
        self._connected = False

    async def send(self, message: str) -> str:
        """Dispatch a JSON-RPC request to the server and return the response."""
        return await self._server.handle_request(message)

    async def connect(self) -> None:
        """Mark transport as connected."""
        self._connected = True

    async def disconnect(self) -> None:
        """Mark transport as disconnected."""
        self._connected = False

    def is_connected(self) -> bool:
        """Check if transport is connected."""
        return self._connected
