"""Central registry that discovers, catalogs, and manages tool adapters."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from mcp_core.client import McpClient
from mcp_core.schemas import HealthStatus
from mcp_core.schemas import ToolManifest as ClientToolManifest
from mcp_core.transports import LoopbackTransport, Transport
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tool_metadata import AdapterInfo, AdapterStatus, ToolCapability

logger = structlog.get_logger()


class _RemoteAdapterServer:
    """Adapter-server shim wrapping a remote ``McpClient`` so it can plug
    into ``UnifiedMcpServer`` alongside in-process ``McpToolServer``s.

    Exposes the same surface ``UnifiedMcpServer`` consumes: ``adapter_id``,
    ``version``, ``tool_ids``, and ``handle_request(raw_text)``. Routes
    every request through the connected ``McpClient``'s underlying
    ``Transport`` so JSON-RPC payloads round-trip end-to-end (MET-373).
    """

    def __init__(
        self,
        adapter_id: str,
        version: str,
        tool_ids: list[str],
        transport: Transport,
    ) -> None:
        self.adapter_id = adapter_id
        self.version = version
        self._tool_ids = list(tool_ids)
        self._transport = transport

    @property
    def tool_ids(self) -> list[str]:
        return list(self._tool_ids)

    async def handle_request(self, raw_message: str) -> str:
        return await self._transport.send(raw_message)


class ToolRegistry:
    """Central registry that discovers, catalogs, and manages tool adapters.

    The registry:
    - Registers adapters (McpToolServer instances) and their tools
    - Tracks adapter health via periodic health checks
    - Routes tool calls to the appropriate adapter
    - Provides capability-based tool discovery
    """

    def __init__(self) -> None:
        self._adapters: dict[str, AdapterInfo] = {}
        self._tools: dict[str, ToolManifest] = {}  # tool_id -> manifest
        self._clients: dict[str, McpClient] = {}  # adapter_id -> connected client
        self._servers: dict[str, McpToolServer] = {}  # adapter_id -> server reference

    async def register_adapter(self, server: McpToolServer) -> AdapterInfo:
        """Register an adapter by connecting via LoopbackTransport and fetching its tools.

        If the adapter is already registered, it will be updated with fresh tool manifests.
        """
        adapter_id = server.adapter_id

        # Create transport and client
        transport = LoopbackTransport(server)
        client = McpClient()
        await client.connect(adapter_id, transport)

        # Fetch tool manifests from the server via tool/list
        tool_manifests: list[ToolManifest] = []
        for tool_id, reg in server._tools.items():
            manifest = reg.manifest
            tool_manifests.append(manifest)
            self._tools[tool_id] = manifest

            # Also register on the client side for call routing
            client.register_manifest(
                ClientToolManifest(
                    tool_id=manifest.tool_id,
                    adapter_id=manifest.adapter_id,
                    name=manifest.name,
                    description=manifest.description,
                    capability=manifest.capability,
                    input_schema=manifest.input_schema,
                    output_schema=manifest.output_schema,
                    phase=manifest.phase,
                )
            )

        # Create adapter info
        adapter_info = AdapterInfo(
            adapter_id=adapter_id,
            version=server.version,
            status=AdapterStatus.CONNECTED,
            tools=tool_manifests,
        )

        # Store everything
        self._adapters[adapter_id] = adapter_info
        self._clients[adapter_id] = client
        self._servers[adapter_id] = server

        logger.info(
            "Registered adapter",
            adapter_id=adapter_id,
            version=server.version,
            tool_count=len(tool_manifests),
        )

        return adapter_info

    async def register_remote_adapter(
        self, adapter_id: str, version: str, client: McpClient
    ) -> AdapterInfo:
        """Register a remote adapter that is already connected via an HttpTransport.

        Unlike ``register_adapter`` which creates a local LoopbackTransport,
        this method expects the caller to have already connected the client
        (e.g. via ``HttpTransport``).  It fetches the tool list through the
        MCP ``tool/list`` JSON-RPC call and registers the returned manifests.

        No ``_servers`` entry is created because the adapter runs remotely.
        """
        # Fetch tool manifests via the connected client's tool/list RPC
        client_manifests = await client.list_tools(adapter_id)

        # Mirror the manifests into the registry's internal tool map and
        # onto the client so that call routing works.
        handler_manifests: list[ToolManifest] = []
        for cm in client_manifests:
            hm = ToolManifest(
                tool_id=cm.tool_id,
                adapter_id=cm.adapter_id,
                name=cm.name,
                description=cm.description,
                capability=cm.capability,
                input_schema=cm.input_schema,
                output_schema=cm.output_schema,
                phase=cm.phase,
            )
            handler_manifests.append(hm)
            self._tools[cm.tool_id] = hm

        adapter_info = AdapterInfo(
            adapter_id=adapter_id,
            version=version,
            status=AdapterStatus.CONNECTED,
            tools=handler_manifests,
        )

        self._adapters[adapter_id] = adapter_info
        self._clients[adapter_id] = client
        # Wire the remote adapter into ``_servers`` via a thin shim so
        # ``list_adapter_servers()`` (consumed by UnifiedMcpServer) surfaces
        # it alongside in-process adapters. The shim re-uses the client's
        # transport for JSON-RPC pass-through (MET-373).
        client_transport = client._transports.get(adapter_id)  # noqa: SLF001
        if client_transport is not None:
            self._servers[adapter_id] = _RemoteAdapterServer(  # type: ignore[assignment]
                adapter_id=adapter_id,
                version=version,
                tool_ids=[m.tool_id for m in handler_manifests],
                transport=client_transport,
            )

        logger.info(
            "Registered remote adapter",
            adapter_id=adapter_id,
            version=version,
            tool_count=len(handler_manifests),
        )

        return adapter_info

    async def unregister_adapter(self, adapter_id: str) -> None:
        """Unregister an adapter and remove all its tools."""
        adapter_info = self._adapters.pop(adapter_id, None)
        if adapter_info is None:
            logger.warning("Adapter not found for unregister", adapter_id=adapter_id)
            return

        # Remove tools belonging to this adapter
        tool_ids_to_remove = [
            tool_id
            for tool_id, manifest in self._tools.items()
            if manifest.adapter_id == adapter_id
        ]
        for tool_id in tool_ids_to_remove:
            del self._tools[tool_id]

        # Disconnect client
        client = self._clients.pop(adapter_id, None)
        if client is not None:
            await client.disconnect(adapter_id)

        self._servers.pop(adapter_id, None)

        logger.info(
            "Unregistered adapter",
            adapter_id=adapter_id,
            tools_removed=len(tool_ids_to_remove),
        )

    def get_adapter(self, adapter_id: str) -> AdapterInfo | None:
        """Get adapter info by ID. Returns None if not found."""
        return self._adapters.get(adapter_id)

    def list_adapters(self) -> list[AdapterInfo]:
        """List all registered adapters."""
        return list(self._adapters.values())

    def get_adapter_server(self, adapter_id: str) -> McpToolServer | None:
        """Return the in-process ``McpToolServer`` for an adapter, if any.

        Remote adapters (registered via ``register_remote_adapter``)
        return ``None`` — they live in another process and aren't
        directly composable. Used by ``metaforge.mcp.server`` to
        aggregate every local adapter into one unified MCP entrypoint
        (MET-337).
        """
        return self._servers.get(adapter_id)

    def list_adapter_servers(self) -> list[McpToolServer]:
        """Return every local adapter server registered. Remote-only
        adapters are excluded — see ``get_adapter_server``."""
        return list(self._servers.values())

    def get_tool(self, tool_id: str) -> ToolManifest | None:
        """Get a tool manifest by tool ID. Returns None if not found."""
        return self._tools.get(tool_id)

    def list_tools(
        self,
        capability: str | None = None,
        phase: int | None = None,
    ) -> list[ToolManifest]:
        """List tools, optionally filtered by capability and/or phase."""
        tools = list(self._tools.values())

        if capability is not None:
            tools = [t for t in tools if t.capability == capability]

        if phase is not None:
            tools = [t for t in tools if t.phase == phase]

        return tools

    def find_tools_by_capability(self, capability: str) -> list[ToolManifest]:
        """Find all tools that provide a given capability."""
        return [t for t in self._tools.values() if t.capability == capability]

    def list_capabilities(self) -> list[ToolCapability]:
        """List all unique capabilities with the tools that provide them."""
        capability_map: dict[str, list[str]] = {}
        description_map: dict[str, str] = {}

        for manifest in self._tools.values():
            cap = manifest.capability
            if cap not in capability_map:
                capability_map[cap] = []
                description_map[cap] = manifest.description
            capability_map[cap].append(manifest.tool_id)

        return [
            ToolCapability(
                capability=cap,
                tool_ids=tool_ids,
                description=description_map.get(cap, ""),
            )
            for cap, tool_ids in sorted(capability_map.items())
        ]

    async def check_health(self, adapter_id: str) -> HealthStatus:
        """Check the health of a specific adapter via its MCP client."""
        client = self._clients.get(adapter_id)
        if client is None:
            return HealthStatus(
                adapter_id=adapter_id,
                status="unhealthy",
                version="unknown",
                tools_available=0,
                uptime_seconds=0,
            )

        health = await client.health_check(adapter_id)

        # Update adapter status based on health check
        adapter_info = self._adapters.get(adapter_id)
        if adapter_info is not None:
            if health.status == "healthy":
                adapter_info.status = AdapterStatus.CONNECTED
            elif health.status == "degraded":
                adapter_info.status = AdapterStatus.DEGRADED
            else:
                adapter_info.status = AdapterStatus.DISCONNECTED
            adapter_info.last_health_check = datetime.now(UTC)

        return health

    async def check_all_health(self) -> dict[str, HealthStatus]:
        """Check health of all registered adapters."""
        results: dict[str, HealthStatus] = {}
        for adapter_id in self._adapters:
            results[adapter_id] = await self.check_health(adapter_id)
        return results

    def get_client(self, adapter_id: str) -> McpClient | None:
        """Get the MCP client for a specific adapter. Used by ExecutionEngine."""
        return self._clients.get(adapter_id)

    def get_adapter_for_tool(self, tool_id: str) -> str | None:
        """Get the adapter ID that owns a given tool."""
        manifest = self._tools.get(tool_id)
        if manifest is None:
            return None
        return manifest.adapter_id
