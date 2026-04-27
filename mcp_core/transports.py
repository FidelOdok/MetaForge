"""Additional transport implementations for the MCP client."""

from __future__ import annotations

import asyncio
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


class StdioTransport(Transport):
    """Spawn a subprocess MCP server and frame JSON-RPC line-delimited (MET-306).

    Symmetric counterpart to MET-337's stdio listener: lets the gateway
    (or any other client) launch ``python -m metaforge.mcp --transport
    stdio`` (or any other stdio MCP server) and talk to it as if it were
    a local in-process adapter.

    Wire format: one JSON-RPC request per line on stdin, one JSON-RPC
    response per line on stdout. Stderr is captured but otherwise
    ignored (servers are expected to log there without affecting the
    protocol channel).

    The ``ready_signal`` parameter lets the caller wait for a known
    line on stderr before sending the first request — MET-337's server
    emits ``metaforge-mcp ready`` on launch for exactly this purpose.

    Example::

        transport = StdioTransport(
            command=["python", "-m", "metaforge.mcp", "--transport", "stdio"],
            ready_signal="metaforge-mcp ready",
        )
        client = McpClient()
        await client.connect("metaforge", transport)
    """

    def __init__(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        ready_signal: str | None = None,
        ready_timeout: float = 30.0,
    ) -> None:
        self._command = list(command)
        self._env = env
        self._ready_signal = ready_signal
        self._ready_timeout = ready_timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._connected = False
        self._send_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Spawn the subprocess and wait for the ready signal (if given)."""
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
        )
        if self._ready_signal is not None:
            await self._wait_for_ready()
        self._connected = True

    async def _wait_for_ready(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        deadline = asyncio.get_event_loop().time() + self._ready_timeout
        signal = self._ready_signal or ""
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Subprocess did not signal ready within {self._ready_timeout}s "
                    f"(expected {signal!r} on stderr)"
                )
            try:
                line = await asyncio.wait_for(self._proc.stderr.readline(), timeout=remaining)
            except TimeoutError as exc:
                raise TimeoutError(
                    f"Subprocess stderr timed out before ready signal {signal!r}"
                ) from exc
            if not line:
                raise RuntimeError("Subprocess exited before ready signal")
            if signal.encode() in line:
                return

    async def send(self, message: str) -> str:
        """Send a JSON-RPC request line, return the response line."""
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("Stdio transport is not connected")
        async with self._send_lock:
            self._proc.stdin.write(message.encode("utf-8") + b"\n")
            await self._proc.stdin.drain()
            response_line = await self._proc.stdout.readline()
        if not response_line:
            raise RuntimeError("Subprocess closed stdout (process likely exited)")
        return response_line.decode("utf-8").rstrip("\n")

    async def disconnect(self) -> None:
        """Close stdin so the subprocess exits, then await teardown."""
        if self._proc is None:
            self._connected = False
            return
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except TimeoutError:
            self._proc.kill()
            await self._proc.wait()
        self._proc = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._proc is not None and self._proc.returncode is None
