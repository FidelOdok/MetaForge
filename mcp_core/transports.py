"""Additional transport implementations for the MCP client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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


class HttpTransport(Transport):
    """HTTP transport that forwards JSON-RPC messages to a remote MCP server.

    Used when tool adapters run in separate containers (e.g. Docker Compose)
    and expose an HTTP endpoint for MCP communication.

    The ``aiohttp`` dependency is imported lazily inside methods to comply
    with mcp_core's zero-side-effect-on-import policy.

    Example::

        transport = HttpTransport("http://cadquery-adapter:8100")
        client = McpClient()
        await client.connect("cadquery", transport)
    """

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session: Any = None
        self._connected = False

    async def send(self, message: str) -> str:
        """Post a JSON-RPC message to the remote MCP endpoint and return the response."""
        import aiohttp  # lazy import — not in mcp_core's required deps

        if self._session is None:
            self._session = aiohttp.ClientSession()
        async with self._session.post(
            f"{self._base_url}/mcp",
            data=message,
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=self._timeout),
        ) as resp:
            return await resp.text()

    async def connect(self) -> None:
        """Mark transport as connected."""
        self._connected = True

    async def disconnect(self) -> None:
        """Close the HTTP session and mark transport as disconnected."""
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._connected = False

    def is_connected(self) -> bool:
        """Check if transport is connected."""
        return self._connected
