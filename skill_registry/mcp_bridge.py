"""Bridge from skill execution to MCP tool calls."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class McpToolError(Exception):
    """Raised when an MCP tool call fails."""

    def __init__(self, tool_id: str, details: str) -> None:
        self.tool_id = tool_id
        self.details = details
        super().__init__(f"MCP tool '{tool_id}' failed: {details}")


class McpTimeoutError(McpToolError):
    """Raised when an MCP tool call times out."""

    def __init__(self, tool_id: str, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(tool_id, f"Exceeded timeout of {timeout_seconds}s")


class McpBridge(ABC):
    """Abstract bridge for MCP tool invocation.

    Skills use this interface to call external tools through the MCP protocol.
    The concrete implementation will delegate to McpClient (mcp_core).
    """

    @abstractmethod
    async def invoke(
        self,
        tool_id: str,
        params: dict[str, Any],
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Invoke an MCP tool.

        Args:
            tool_id: Tool identifier (e.g., "calculix.run_fea").
            params: Tool parameters (will be JSON-serialized).
            timeout: Override timeout in seconds.

        Returns:
            Tool result as a dictionary.

        Raises:
            McpToolError: If the tool call fails.
            McpTimeoutError: If the tool call exceeds the timeout.
        """
        ...

    @abstractmethod
    async def is_available(self, tool_id: str) -> bool:
        """Check if a tool is available and healthy."""
        ...

    @abstractmethod
    async def list_tools(self, capability: str | None = None) -> list[dict[str, Any]]:
        """List available tools, optionally filtered by capability."""
        ...


class InMemoryMcpBridge(McpBridge):
    """In-memory MCP bridge for testing.

    Allows registering mock tool responses for unit testing skills
    without requiring actual MCP infrastructure.
    """

    def __init__(self) -> None:
        self._responses: dict[str, dict[str, Any]] = {}
        self._available: set[str] = set()
        self._tools: list[dict[str, Any]] = []

    def register_tool_response(self, tool_id: str, response: dict[str, Any]) -> None:
        """Register a mock response for a tool."""
        self._responses[tool_id] = response
        self._available.add(tool_id)

    def register_tool(self, tool_id: str, capability: str, name: str = "") -> None:
        """Register a tool as available."""
        self._available.add(tool_id)
        self._tools.append({"tool_id": tool_id, "capability": capability, "name": name or tool_id})

    async def invoke(
        self,
        tool_id: str,
        params: dict[str, Any],
        timeout: int | None = None,
    ) -> dict[str, Any]:
        if tool_id not in self._responses:
            raise McpToolError(tool_id, f"No mock response registered for {tool_id}")
        return self._responses[tool_id]

    async def is_available(self, tool_id: str) -> bool:
        return tool_id in self._available

    async def list_tools(self, capability: str | None = None) -> list[dict[str, Any]]:
        if capability is None:
            return list(self._tools)
        return [t for t in self._tools if t.get("capability") == capability]
