"""Bootstrap tool adapters at application startup.

Discovers, instantiates, and registers all available tool adapters into
a ToolRegistry. Called by the API Gateway during lifespan initialization.

Adapter registration is config-driven via environment variables:
- METAFORGE_ADAPTERS: comma-separated list of adapter IDs to enable
  (default: all known adapters)
- METAFORGE_ADAPTER_{ID}_ENABLED: per-adapter toggle
  (e.g., METAFORGE_ADAPTER_CADQUERY_ENABLED=false)
- METAFORGE_ADAPTER_{ID}_URL: when set, connects to a remote adapter
  container via HTTP instead of creating a local server
  (e.g., METAFORGE_ADAPTER_CADQUERY_URL=http://cadquery-adapter:8100)
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from mcp_core.client import McpClient
from mcp_core.transports import HttpTransport
from observability.tracing import get_tracer
from tool_registry.registry import ToolRegistry

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.bootstrap")

# Known adapters and their factory functions (import path, class, config class)
_ADAPTER_REGISTRY: dict[str, dict[str, str]] = {
    "cadquery": {
        "module": "tool_registry.tools.cadquery.adapter",
        "class": "CadqueryServer",
        "config_module": "tool_registry.tools.cadquery.config",
        "config_class": "CadqueryConfig",
    },
    "freecad": {
        "module": "tool_registry.tools.freecad.adapter",
        "class": "FreecadServer",
        "config_module": "tool_registry.tools.freecad.config",
        "config_class": "FreecadConfig",
    },
    "calculix": {
        "module": "tool_registry.tools.calculix.adapter",
        "class": "CalculixServer",
        "config_module": "tool_registry.tools.calculix.config",
        "config_class": "CalculixConfig",
    },
    # MET-478 / MET-477 EE-vertical blocker: KiCad has lived under
    # tool_registry/tools/kicad/ since MET-336 but shipped as a
    # separate stdio entrypoint (kicad/entrypoint.py). The unified
    # MCP bootstrap never registered it, so kicad.* tools were absent
    # from tools/list and the EE vertical scenario in
    # tests/integration/test_mcp_e2e/test_vertical_electronics.py
    # was forced to skip steps 3-6 (run_erc / run_drc / export_bom /
    # export_gerber). Wiring KiCad here surfaces all 6 kicad.* tools
    # in the unified server. Production deploys still need the kicad
    # CLI binary in PATH for the tools to execute; without it the
    # adapter registers (tools/list contains them) but each handler
    # raises KicadCliNotFoundError, which the dispatcher surfaces as
    # -32001 TOOL_EXECUTION_ERROR — the EE vertical's _attempt() helper
    # already treats that as an acceptable outcome.
    "kicad": {
        "module": "tool_registry.tools.kicad.adapter",
        "class": "KicadServer",
        "config_module": "tool_registry.tools.kicad.config",
        "config_class": "KicadConfig",
    },
}


def _is_adapter_enabled(adapter_id: str) -> bool:
    """Check if an adapter is enabled via environment variables."""
    # Per-adapter toggle: METAFORGE_ADAPTER_CADQUERY_ENABLED=false
    env_key = f"METAFORGE_ADAPTER_{adapter_id.upper()}_ENABLED"
    env_val = os.environ.get(env_key, "").lower()
    if env_val == "false":
        return False
    if env_val == "true":
        return True

    # Global allowlist: METAFORGE_ADAPTERS=cadquery,calculix
    adapters_env = os.environ.get("METAFORGE_ADAPTERS", "")
    if adapters_env:
        allowed = {a.strip().lower() for a in adapters_env.split(",")}
        return adapter_id.lower() in allowed

    # Default: enabled
    return True


def _import_class(module_path: str, class_name: str) -> type | None:
    """Dynamically import a class from a module path."""
    try:
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        logger.debug(
            "Could not import adapter class",
            module=module_path,
            class_name=class_name,
            error=str(exc),
        )
        return None


def _get_remote_url(adapter_id: str) -> str | None:
    """Return the remote adapter URL from env, or None if not set."""
    env_key = f"METAFORGE_ADAPTER_{adapter_id.upper()}_URL"
    return os.environ.get(env_key) or None


async def _create_remote_adapter(adapter_id: str, url: str) -> McpClient:
    """Connect to a remote adapter container via HttpTransport.

    Returns a connected McpClient whose manifests have been populated by a
    ``tool/list`` JSON-RPC call through the transport.
    """
    transport = HttpTransport(url)
    client = McpClient()
    await client.connect(adapter_id, transport)

    # Issue a tool/list call so the client discovers available tools.
    # The McpClient.list_tools() method returns manifests that were
    # registered via the server's response; we trigger the RPC here.
    import json

    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tool/list",
            "params": {},
        }
    )
    response_text = await transport.send(request)
    response = json.loads(response_text)

    # Register each manifest on the client so call routing works
    from mcp_core.schemas import ToolManifest as ClientToolManifest

    for tool_data in response.get("result", {}).get("tools", []):
        manifest = ClientToolManifest(
            tool_id=tool_data["tool_id"],
            adapter_id=tool_data.get("adapter_id", adapter_id),
            name=tool_data["name"],
            description=tool_data.get("description", ""),
            capability=tool_data.get("capability", ""),
            input_schema=tool_data.get("input_schema", {}),
            output_schema=tool_data.get("output_schema", {}),
            phase=tool_data.get("phase", 1),
        )
        client.register_manifest(manifest)

    return client


def _create_adapter(adapter_id: str, spec: dict[str, str]) -> Any | None:
    """Instantiate an adapter server with its default config.

    Returns the McpToolServer instance, or None if import fails.
    """
    server_cls = _import_class(spec["module"], spec["class"])
    if server_cls is None:
        return None

    config_cls = _import_class(spec["config_module"], spec["config_class"])
    if config_cls is not None:
        config = config_cls()
        return server_cls(config=config)

    return server_cls()


async def bootstrap_tool_registry(
    registry: ToolRegistry | None = None,
    adapter_ids: list[str] | None = None,
    knowledge_service: Any = None,
    constraint_engine: Any = None,
    twin: Any = None,
    twin_allow_mutations: bool = False,
    project_backend: Any = None,
    memory_client: Any = None,
    memory_insight_store: Any = None,
    agent_session_store: Any = None,
) -> ToolRegistry:
    """Bootstrap all enabled tool adapters into a ToolRegistry.

    Args:
        registry: Existing registry to populate. Creates a new one if None.
        adapter_ids: Explicit list of adapter IDs to register. If None,
            registers all known adapters that are enabled.
        knowledge_service: Optional ``KnowledgeService`` instance. When
            supplied, the ``knowledge`` MCP adapter (knowledge.search +
            knowledge.ingest) is registered. When ``None``, the adapter
            is skipped — it has no useful default backend (MET-335).
        constraint_engine: Optional ``ConstraintEngine`` instance.
            When supplied, the ``constraint`` MCP adapter (MET-383) is
            registered. When ``None``, skipped — same pattern as
            knowledge_service since both are runtime-injected.
        twin: Optional ``TwinAPI`` instance. When supplied, the ``twin``
            MCP adapter (MET-382) registers five tools (get_node /
            thread_for / find_by_property / constraint_violations /
            query_cypher). When ``None``, skipped.
        twin_allow_mutations: When True, ``twin.query_cypher`` accepts
            mutating Cypher (CREATE / DELETE / SET / MERGE / ...). Off
            by default; every call is audit-logged regardless.

    Returns:
        The populated ToolRegistry.
    """
    with tracer.start_as_current_span("bootstrap_tool_registry") as span:
        if registry is None:
            registry = ToolRegistry()

        ids_to_register = adapter_ids or list(_ADAPTER_REGISTRY.keys())
        registered: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

        for adapter_id in ids_to_register:
            spec = _ADAPTER_REGISTRY.get(adapter_id)
            if spec is None:
                logger.warning("Unknown adapter ID", adapter_id=adapter_id)
                failed.append(adapter_id)
                continue

            if not _is_adapter_enabled(adapter_id):
                logger.info("Adapter disabled via config", adapter_id=adapter_id)
                skipped.append(adapter_id)
                continue

            # Check for remote adapter URL first (Docker / container mode).
            # MET-477 G2: when the remote fetch fails (typical when the
            # containerized adapter isn't deployed yet but the URL env
            # var is set as forward-compatible config), fall through to
            # the in-process adapter rather than marking the whole
            # adapter as ``failed``. Production deployments that DO
            # have the remote container still get the remote path
            # first; only the dev / single-container deploys benefit
            # from the fallback.
            remote_url = _get_remote_url(adapter_id)
            if remote_url is not None:
                try:
                    client = await _create_remote_adapter(adapter_id, remote_url)
                    version = spec.get("version", "0.1.0")
                    await registry.register_remote_adapter(adapter_id, version, client)
                    registered.append(adapter_id)
                    logger.info(
                        "Registered remote adapter",
                        adapter_id=adapter_id,
                        url=remote_url,
                    )
                    continue
                except Exception as exc:
                    logger.warning(
                        "Remote adapter unreachable — falling back to in-process",
                        adapter_id=adapter_id,
                        url=remote_url,
                        error=str(exc),
                    )
                    span.record_exception(exc)
                    # Drop through to the in-process path below.

            # Fall back to local adapter creation (in-process mode)
            server = _create_adapter(adapter_id, spec)
            if server is None:
                logger.warning(
                    "Adapter import failed (module not available)",
                    adapter_id=adapter_id,
                )
                skipped.append(adapter_id)
                continue

            try:
                await registry.register_adapter(server)
                registered.append(adapter_id)
            except Exception as exc:
                logger.error(
                    "Adapter registration failed",
                    adapter_id=adapter_id,
                    error=str(exc),
                )
                span.record_exception(exc)
                failed.append(adapter_id)

        # ----- Knowledge MCP adapter (MET-335) -----
        # Registered separately because it depends on a runtime-injected
        # KnowledgeService instance (no static factory in _ADAPTER_REGISTRY).
        if knowledge_service is not None and _is_adapter_enabled("knowledge"):
            try:
                from tool_registry.tools.knowledge.adapter import KnowledgeServer

                server = KnowledgeServer(service=knowledge_service)
                await registry.register_adapter(server)
                registered.append("knowledge")
                logger.info(
                    "knowledge_mcp_adapter_registered",
                    service=type(knowledge_service).__name__,
                )
            except Exception as exc:
                logger.error("knowledge_mcp_adapter_failed", error=str(exc))
                span.record_exception(exc)
                failed.append("knowledge")
        else:
            skipped.append("knowledge")
            logger.info(
                "knowledge_mcp_adapter_skipped",
                reason=(
                    "no knowledge_service supplied"
                    if knowledge_service is None
                    else "disabled via config"
                ),
            )

        # ----- Constraint MCP adapter (MET-383) -----
        # Same pattern as knowledge: depends on a runtime-injected
        # ConstraintEngine instance, so registered out-of-band.
        if constraint_engine is not None and _is_adapter_enabled("constraint"):
            try:
                from tool_registry.tools.constraint.adapter import ConstraintServer

                server = ConstraintServer(engine=constraint_engine)
                await registry.register_adapter(server)
                registered.append("constraint")
                logger.info(
                    "constraint_mcp_adapter_registered",
                    engine=type(constraint_engine).__name__,
                )
            except Exception as exc:
                logger.error("constraint_mcp_adapter_failed", error=str(exc))
                span.record_exception(exc)
                failed.append("constraint")
        else:
            skipped.append("constraint")
            logger.info(
                "constraint_mcp_adapter_skipped",
                reason=(
                    "no constraint_engine supplied"
                    if constraint_engine is None
                    else "disabled via config"
                ),
            )

        # ----- Twin MCP adapter (MET-382) -----
        # Same runtime-injection pattern as knowledge / constraint:
        # depends on a TwinAPI instance the gateway holds, not a
        # static factory.
        if twin is not None and _is_adapter_enabled("twin"):
            try:
                from tool_registry.tools.twin.adapter import TwinServer

                server = TwinServer(twin=twin, allow_mutations=twin_allow_mutations)
                await registry.register_adapter(server)
                registered.append("twin")
                logger.info(
                    "twin_mcp_adapter_registered",
                    twin=type(twin).__name__,
                    allow_mutations=twin_allow_mutations,
                )
            except Exception as exc:
                logger.error("twin_mcp_adapter_failed", error=str(exc))
                span.record_exception(exc)
                failed.append("twin")
        else:
            skipped.append("twin")
            logger.info(
                "twin_mcp_adapter_skipped",
                reason=("no twin supplied" if twin is None else "disabled via config"),
            )

        # ----- Project MCP adapter (MET-427) -----
        # Same runtime-injection pattern as knowledge / twin: depends
        # on a ProjectBackend the gateway holds, not a static factory.
        if project_backend is not None and _is_adapter_enabled("project"):
            try:
                from tool_registry.tools.project.adapter import ProjectServer

                server = ProjectServer(backend=project_backend)
                await registry.register_adapter(server)
                registered.append("project")
                logger.info(
                    "project_mcp_adapter_registered",
                    backend=type(project_backend).__name__,
                )
            except Exception as exc:
                logger.error("project_mcp_adapter_failed", error=str(exc))
                span.record_exception(exc)
                failed.append("project")
        else:
            skipped.append("project")
            logger.info(
                "project_mcp_adapter_skipped",
                reason=(
                    "no project_backend supplied"
                    if project_backend is None
                    else "disabled via config"
                ),
            )

        # ----- Session MCP adapter (MET-494) -----
        # Runtime-injected like project: depends on the agent-session store
        # the gateway/sidecar share. Lets external agents record their own
        # narrative; pairs with the MET-496 auto-capture takeover.
        if agent_session_store is not None and _is_adapter_enabled("session"):
            try:
                from tool_registry.tools.session.adapter import SessionServer

                server = SessionServer(store=agent_session_store)
                await registry.register_adapter(server)
                registered.append("session")
                logger.info(
                    "session_mcp_adapter_registered",
                    store=type(agent_session_store).__name__,
                )
            except Exception as exc:
                logger.error("session_mcp_adapter_failed", error=str(exc))
                span.record_exception(exc)
                failed.append("session")
        else:
            skipped.append("session")
            logger.info(
                "session_mcp_adapter_skipped",
                reason=(
                    "no agent_session_store supplied"
                    if agent_session_store is None
                    else "disabled via config"
                ),
            )

        # ----- Memory MCP adapter (MET-453) -----
        # Runtime-injected like knowledge / twin: the gateway holds the
        # MemoryClient on app.state.memory_client.
        if memory_client is not None and _is_adapter_enabled("memory"):
            try:
                from tool_registry.tools.memory.adapter import MemoryServer

                server = MemoryServer(
                    client=memory_client,
                    insight_store=memory_insight_store,
                )
                await registry.register_adapter(server)
                registered.append("memory")
                logger.info(
                    "memory_mcp_adapter_registered",
                    client=type(memory_client).__name__,
                    insight_store=(
                        type(memory_insight_store).__name__
                        if memory_insight_store is not None
                        else None
                    ),
                )
            except Exception as exc:
                logger.error("memory_mcp_adapter_failed", error=str(exc))
                span.record_exception(exc)
                failed.append("memory")
        else:
            skipped.append("memory")
            logger.info(
                "memory_mcp_adapter_skipped",
                reason=(
                    "no memory_client supplied" if memory_client is None else "disabled via config"
                ),
            )

        # ----- Distributor MCP adapters (MET-434) -----
        # Self-constructing clients keyed on env vars. The HTTP code
        # lives in tool_registry/tools/{digikey,mouser,nexar}/adapter.py;
        # this block wraps each one in a DistributorMcpServer if the
        # creds are present, otherwise skips. Adding Arrow / Avnet is a
        # one-line addition to ``_DISTRIBUTOR_FACTORIES``.
        from tool_registry.tools.distributors.base import DistributorAdapter
        from tool_registry.tools.distributors.mcp_adapter import DistributorMcpServer

        def _make_digikey() -> DistributorAdapter | None:
            if not (
                os.environ.get("DIGIKEY_CLIENT_ID") and os.environ.get("DIGIKEY_CLIENT_SECRET")
            ):
                return None
            from tool_registry.tools.digikey.adapter import DigiKeyAdapter

            return DigiKeyAdapter()

        def _make_mouser() -> DistributorAdapter | None:
            if not os.environ.get("MOUSER_API_KEY"):
                return None
            from tool_registry.tools.mouser.adapter import MouserAdapter

            return MouserAdapter()

        def _make_nexar() -> DistributorAdapter | None:
            if not (os.environ.get("NEXAR_CLIENT_ID") and os.environ.get("NEXAR_CLIENT_SECRET")):
                return None
            from tool_registry.tools.nexar.adapter import NexarAdapter

            return NexarAdapter()

        _DISTRIBUTOR_FACTORIES = (
            ("digikey", _make_digikey, "DIGIKEY_CLIENT_ID + DIGIKEY_CLIENT_SECRET"),
            ("mouser", _make_mouser, "MOUSER_API_KEY"),
            ("nexar", _make_nexar, "NEXAR_CLIENT_ID + NEXAR_CLIENT_SECRET"),
        )

        for distributor_id, factory, creds_hint in _DISTRIBUTOR_FACTORIES:
            if not _is_adapter_enabled(distributor_id):
                skipped.append(distributor_id)
                logger.info(
                    f"{distributor_id}_mcp_adapter_skipped",
                    reason="disabled via config",
                )
                continue
            try:
                adapter = factory()
            except Exception as exc:
                logger.error(f"{distributor_id}_mcp_adapter_construction_failed", error=str(exc))
                span.record_exception(exc)
                failed.append(distributor_id)
                continue
            if adapter is None:
                skipped.append(distributor_id)
                logger.info(
                    f"{distributor_id}_mcp_adapter_skipped",
                    reason=f"missing env vars: {creds_hint}",
                )
                continue
            try:
                server = DistributorMcpServer(adapter=adapter)
                await registry.register_adapter(server)
                registered.append(distributor_id)
                logger.info(
                    f"{distributor_id}_mcp_adapter_registered",
                    distributor=adapter.name,
                )
            except Exception as exc:
                logger.error(f"{distributor_id}_mcp_adapter_failed", error=str(exc))
                span.record_exception(exc)
                failed.append(distributor_id)

        span.set_attribute("adapters.registered", len(registered))
        span.set_attribute("adapters.skipped", len(skipped))
        span.set_attribute("adapters.failed", len(failed))

        logger.info(
            "Tool registry bootstrap complete",
            registered=registered,
            skipped=skipped,
            failed=failed,
            total_tools=len(registry.list_tools()),
        )

        return registry
