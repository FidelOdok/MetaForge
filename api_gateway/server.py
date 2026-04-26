"""MetaForge API Gateway server.

FastAPI application factory that wires together all routers, middleware,
and lifecycle hooks.  Run with ``uvicorn api_gateway.server:app``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_gateway.assistant.routes import router as assistant_router
from api_gateway.chat.routes import router as chat_router
from api_gateway.compliance.routes import router as compliance_router
from api_gateway.convert.routes import router as convert_router
from api_gateway.health import health_router
from api_gateway.knowledge.routes import router as knowledge_router
from api_gateway.projects.routes import router as projects_router
from api_gateway.sessions.routes import router as sessions_router
from api_gateway.twin.routes import router as twin_router
from domain_agents.electronics.agent import ElectronicsAgent
from domain_agents.mechanical.agent import MechanicalAgent
from observability.bootstrap import init_observability, shutdown_observability
from observability.config import ObservabilityConfig, OtlpExporterConfig
from observability.logging import configure_logging
from observability.metrics import MetricsCollector, MetricsRegistry
from observability.middleware import ObservabilityMiddleware
from observability.tracing import get_tracer
from orchestrator.dependency_engine import DependencyGraph
from orchestrator.event_bus.subscribers import create_default_bus
from orchestrator.scheduler import InMemoryScheduler
from orchestrator.workflow_dag import (
    InMemoryWorkflowEngine,
    WorkflowDefinition,
    WorkflowStep,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge
from skill_registry.registry_bridge import RegistryMcpBridge
from tool_registry.bootstrap import bootstrap_tool_registry
from twin_core.api import InMemoryTwinAPI

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.server")

# ---------------------------------------------------------------------------
# OTel bootstrap (module-level so providers are active before first request)
# ---------------------------------------------------------------------------

_otel_config = ObservabilityConfig(
    service_name="metaforge-gateway",
    environment=os.getenv("METAFORGE_ENV", "development"),
    otlp=OtlpExporterConfig(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
    ),
)
_otel_state = init_observability(_otel_config)


def _create_collector() -> MetricsCollector:
    """Create a MetricsCollector backed by a real OTel meter (or no-op)."""
    if _otel_state.is_active and _otel_state.meter_provider is not None:
        meter = _otel_state.meter_provider.get_meter("metaforge-gateway")
        collector = MetricsCollector(meter=meter)
        collector.create_instruments(MetricsRegistry.all_metrics())
        logger.info("metrics_collector_initialized", instruments=len(MetricsRegistry.all_metrics()))
        return collector
    logger.info("metrics_collector_noop", reason="OTel SDK not available or disabled")
    return MetricsCollector()


# ---------------------------------------------------------------------------
# Workflow definitions registry
# ---------------------------------------------------------------------------

ACTION_WORKFLOWS: dict[str, WorkflowDefinition] = {
    "validate_stress": WorkflowDefinition(
        name="validate_stress",
        steps=[
            WorkflowStep(step_id="stress", agent_code="MECH", task_type="validate_stress"),
        ],
    ),
    "generate_mesh": WorkflowDefinition(
        name="generate_mesh",
        steps=[
            WorkflowStep(step_id="mesh", agent_code="MECH", task_type="generate_mesh"),
        ],
    ),
    "check_tolerances": WorkflowDefinition(
        name="check_tolerances",
        steps=[
            WorkflowStep(step_id="tolerances", agent_code="MECH", task_type="check_tolerances"),
        ],
    ),
    "generate_cad": WorkflowDefinition(
        name="generate_cad",
        steps=[
            WorkflowStep(step_id="cad", agent_code="MECH", task_type="generate_cad"),
        ],
    ),
    "generate_cad_script": WorkflowDefinition(
        name="generate_cad_script",
        steps=[
            WorkflowStep(
                step_id="cad_script",
                agent_code="MECH",
                task_type="generate_cad_script",
            ),
        ],
    ),
    "run_erc": WorkflowDefinition(
        name="run_erc",
        steps=[
            WorkflowStep(step_id="erc", agent_code="EE", task_type="run_erc"),
        ],
    ),
    "run_drc": WorkflowDefinition(
        name="run_drc",
        steps=[
            WorkflowStep(step_id="drc", agent_code="EE", task_type="run_drc"),
        ],
    ),
    "full_validation": WorkflowDefinition(
        name="full_validation",
        steps=[
            WorkflowStep(step_id="stress", agent_code="MECH", task_type="validate_stress"),
            WorkflowStep(
                step_id="erc",
                agent_code="EE",
                task_type="run_erc",
            ),
        ],
    ),
}


async def _init_database() -> None:
    """Create PostgreSQL tables if DATABASE_URL is set and SQLAlchemy is available."""
    try:
        from api_gateway.db import HAS_SQLALCHEMY
        from api_gateway.db.engine import get_engine

        if not HAS_SQLALCHEMY:
            logger.debug("pg_init_skipped", reason="sqlalchemy not installed")
            return

        engine = get_engine()
        if engine is None:
            logger.debug("pg_init_skipped", reason="DATABASE_URL not set")
            return

        from api_gateway.db.models import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("pg_tables_created")
    except Exception as exc:
        logger.warning("pg_init_failed", error=str(exc))


async def _init_knowledge_store(app: FastAPI) -> None:
    """Initialize the L1 ``KnowledgeService`` and the legacy store on app.state.

    Per MET-346 / ADR-008, the L1 entry point is now the
    ``KnowledgeService`` Protocol via ``create_knowledge_service``.
    The legacy ``KnowledgeStore`` (Pgvector / in-memory) stays wired on
    ``app.state.knowledge_store`` until its consumers (skill handlers,
    knowledge routes, ``KnowledgeConsumer``) migrate — see MET-307.

    Boot order:
      1. Try the LightRAG service if ``DATABASE_URL`` is set; expose
         it on ``app.state.knowledge_service``.
      2. Initialize the legacy PgVector store (or in-memory fallback)
         on ``app.state.knowledge_store`` for back-compat.
    """
    from digital_twin.knowledge import create_knowledge_service
    from digital_twin.knowledge.store import InMemoryKnowledgeStore

    db_url = os.environ.get("DATABASE_URL")
    pgvector_active = False

    # ----- New L1 service (LightRAG) ---------------------------------
    knowledge_service = None
    if db_url:
        try:
            dsn = db_url.replace("postgresql+asyncpg://", "postgresql://")
            knowledge_service = create_knowledge_service(
                "lightrag",
                working_dir=os.environ.get("METAFORGE_LIGHTRAG_WORKDIR", "./.lightrag-storage"),
                postgres_dsn=dsn,
            )
            await knowledge_service.initialize()  # type: ignore[attr-defined]
            pgvector_active = True
            logger.info("knowledge_service_lightrag_initialized")
        except Exception as exc:
            logger.warning("knowledge_service_lightrag_failed", error=str(exc))
            knowledge_service = None
    app.state.knowledge_service = knowledge_service

    # ----- Legacy store (still consumed by skills + routes) ----------
    knowledge_store = None
    if db_url:
        try:
            from digital_twin.knowledge.store import PgVectorKnowledgeStore

            dsn = db_url.replace("postgresql+asyncpg://", "postgresql://")
            pg_store = PgVectorKnowledgeStore(dsn=dsn)
            await pg_store.initialize()
            knowledge_store = pg_store
            pgvector_active = True
            logger.info("knowledge_store_pgvector_initialized")
        except Exception as exc:
            logger.warning("knowledge_store_pgvector_failed", error=str(exc))

    if knowledge_store is None:
        knowledge_store = InMemoryKnowledgeStore()
        logger.info("knowledge_store_in_memory_initialized")

    app.state.knowledge_store = knowledge_store

    # Initialize embedding service (local fallback)
    try:
        from digital_twin.knowledge.embedding_service import create_embedding_service

        openai_key = os.environ.get("OPENAI_API_KEY")
        if openai_key:
            embedding_svc = create_embedding_service("openai", api_key=openai_key)
            logger.info("embedding_service_openai_initialized")
        else:
            embedding_svc = create_embedding_service("local")
            logger.info("embedding_service_local_initialized")
        app.state.embedding_service = embedding_svc
    except Exception as exc:
        logger.warning("embedding_service_init_failed", error=str(exc))
        app.state.embedding_service = None

    # Register pgvector health check
    if pgvector_active:
        from api_gateway.health import ComponentHealth, DependencyStatus, get_health_checker

        _pg_store = knowledge_store

        async def _pgvector_health() -> ComponentHealth:
            import time as _time

            t0 = _time.monotonic()
            try:
                # Simple connectivity check — list with limit 0
                await _pg_store.list(limit=1)
                latency = round((_time.monotonic() - t0) * 1000, 2)
                return ComponentHealth(
                    name="pgvector",
                    status=DependencyStatus.HEALTHY,
                    latency_ms=latency,
                    message="Connected",
                )
            except Exception as exc:
                latency = round((_time.monotonic() - t0) * 1000, 2)
                return ComponentHealth(
                    name="pgvector",
                    status=DependencyStatus.UNHEALTHY,
                    latency_ms=latency,
                    message=str(exc),
                )

        get_health_checker().register_check("pgvector", _pgvector_health)
        logger.info("pgvector_health_check_registered")


async def _init_orchestrator(app: FastAPI) -> None:
    """Wire up orchestrator subsystems and store on app.state."""
    # Skip if test-injected components are already present
    if hasattr(app.state, "workflow_engine") and hasattr(app.state, "scheduler"):
        logger.info("orchestrator_skip_init", reason="test-injected")
        if not hasattr(app.state, "action_workflows"):
            app.state.action_workflows = ACTION_WORKFLOWS
        return

    _collector = getattr(app.state, "collector", None)
    workflow_engine = InMemoryWorkflowEngine.create()

    # Select Twin backend from environment (Neo4j if NEO4J_URI is set)
    try:
        twin = await InMemoryTwinAPI.create_from_env(collector=_collector)
    except Exception as exc:
        logger.warning(
            "neo4j_fallback_to_in_memory",
            error=str(exc),
        )
        twin = InMemoryTwinAPI.create_with_collector(_collector)
    mcp = InMemoryMcpBridge()

    # Bootstrap tool adapters into the registry and create real MCP bridge
    tool_registry = await bootstrap_tool_registry()
    app.state.tool_registry = tool_registry
    registry_bridge = RegistryMcpBridge(tool_registry)
    app.state.mcp_bridge = registry_bridge
    logger.info(
        "tool_registry_bootstrapped",
        adapters=len(tool_registry.list_adapters()),
        tools=len(tool_registry.list_tools()),
    )

    # Initialize PostgreSQL schema if DATABASE_URL is set
    await _init_database()

    # Initialize chat and project backends (PG or in-memory)
    from api_gateway.chat.backend import create_backend
    from api_gateway.chat.routes import init_chat_backend, init_mcp_bridge, init_twin
    from api_gateway.projects.backend import create_project_backend
    from api_gateway.projects.routes import init_project_backend
    from api_gateway.projects.routes import init_twin as init_projects_twin
    from api_gateway.twin.routes import init_twin as init_twin_viewer

    chat_backend = await create_backend()
    init_chat_backend(chat_backend)

    project_backend = await create_project_backend()
    init_project_backend(project_backend)

    # Wire the real bridge and twin into chat routes and projects routes
    init_mcp_bridge(registry_bridge)
    init_twin(twin)
    init_projects_twin(twin)
    init_twin_viewer(twin)

    event_bus = create_default_bus(workflow_engine, collector=_collector)

    # Register all workflow definitions
    for defn in ACTION_WORKFLOWS.values():
        await workflow_engine.register_workflow(defn)

    # Create agents — use real bridge for tool access, InMemoryMcpBridge as fallback
    mech_agent = MechanicalAgent(twin=twin, mcp=registry_bridge)
    ee_agent = ElectronicsAgent(twin=twin, mcp=registry_bridge)

    # Build a dependency graph from the full_validation workflow (most complex)
    # For single-step workflows the dep_graph is optional
    dep_graph = DependencyGraph(ACTION_WORKFLOWS["full_validation"])
    dep_graph.validate()

    scheduler = InMemoryScheduler(
        workflow_engine=workflow_engine,
        event_bus=event_bus,
        dependency_graph=dep_graph,
        max_concurrency=4,
        collector=_collector,
    )
    scheduler.register_agent("MECH", mech_agent)
    scheduler.register_agent("EE", ee_agent)
    await scheduler.start()

    # Store on app.state for route access
    app.state.workflow_engine = workflow_engine
    app.state.scheduler = scheduler
    app.state.twin = twin
    app.state.mcp = mcp
    app.state.event_bus = event_bus
    app.state.action_workflows = ACTION_WORKFLOWS

    # Initialize knowledge store and embedding service
    await _init_knowledge_store(app)

    # Register Neo4j health check if using Neo4j backend
    _graph_engine = twin._graph  # noqa: SLF001
    if hasattr(_graph_engine, "health_check"):
        from api_gateway.health import ComponentHealth, DependencyStatus, get_health_checker

        async def _neo4j_health() -> ComponentHealth:
            import time as _time

            t0 = _time.monotonic()
            try:
                healthy = await _graph_engine.health_check()
                latency = round((_time.monotonic() - t0) * 1000, 2)
                return ComponentHealth(
                    name="neo4j",
                    status=DependencyStatus.HEALTHY if healthy else DependencyStatus.UNHEALTHY,
                    latency_ms=latency,
                    message="Connected" if healthy else "Connection lost",
                )
            except Exception as exc:
                latency = round((_time.monotonic() - t0) * 1000, 2)
                return ComponentHealth(
                    name="neo4j",
                    status=DependencyStatus.UNHEALTHY,
                    latency_ms=latency,
                    message=str(exc),
                )

        get_health_checker().register_check("neo4j", _neo4j_health)
        logger.info("neo4j_health_check_registered")

    logger.info(
        "orchestrator_initialized",
        workflows=list(ACTION_WORKFLOWS.keys()),
        agents=["MECH", "EE"],
    )


def _reattach_otel_log_handler() -> None:
    """Re-attach the OTel LoggingHandler after uvicorn resets logging.

    Uvicorn's ``configure_logging()`` calls ``dictConfig`` which clears the
    root logger handlers.  We re-attach the handler so structlog events
    (which flow through stdlib ``LoggerFactory``) reach the OTLP exporter.
    """
    import logging as _logging

    if _otel_state.logger_provider is None:
        return
    try:
        from opentelemetry.sdk._logs import LoggingHandler

        root = _logging.getLogger()
        # Avoid duplicates
        if any(isinstance(h, LoggingHandler) for h in root.handlers):
            return
        handler = LoggingHandler(level=_logging.DEBUG, logger_provider=_otel_state.logger_provider)
        root.addHandler(handler)
        if root.level > _logging.INFO:
            root.setLevel(_logging.INFO)
    except ImportError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown lifecycle handler."""
    configure_logging(_otel_config)
    _reattach_otel_log_handler()
    logger.info("gateway_starting", version="0.1.0", otel_active=_otel_state.is_active)
    await _init_orchestrator(app)
    from api_gateway.twin.file_watcher import file_watcher

    if hasattr(app.state, "twin"):
        file_watcher.set_twin(app.state.twin)
        await file_watcher.start()
    yield
    logger.info("gateway_stopping")
    await file_watcher.stop()
    if hasattr(app.state, "scheduler"):
        await app.state.scheduler.stop()
    # Close LightRAG service if active
    if hasattr(app.state, "knowledge_service") and app.state.knowledge_service is not None:
        try:
            await app.state.knowledge_service.close()
            logger.info("knowledge_service_closed")
        except Exception:
            pass
    # Close PgVector knowledge store if active
    if hasattr(app.state, "knowledge_store") and hasattr(app.state.knowledge_store, "close"):
        try:
            await app.state.knowledge_store.close()
            logger.info("pgvector_store_closed")
        except Exception:
            pass
    # Close Neo4j connection if active
    if hasattr(app.state, "twin"):
        graph = app.state.twin._graph  # noqa: SLF001
        if hasattr(graph, "close"):
            await graph.close()
            logger.info("neo4j_connection_closed")
    # Dispose PostgreSQL engine
    try:
        from api_gateway.db.engine import dispose_engine

        await dispose_engine()
    except Exception:
        pass
    shutdown_observability(_otel_state)


def create_app(
    *,
    cors_origins: list[str] | None = None,
    collector: Any | None = None,
    workflow_engine: Any | None = None,
    scheduler: Any | None = None,
) -> FastAPI:
    """Create and configure the MetaForge Gateway FastAPI application.

    Parameters
    ----------
    cors_origins:
        Allowed CORS origins.  Defaults to ``["*"]`` for development.
    collector:
        Optional ``MetricsCollector`` for the observability middleware.
    workflow_engine:
        Optional pre-built workflow engine (for testing).
    scheduler:
        Optional pre-built scheduler (for testing).
    """
    app = FastAPI(
        title="MetaForge Gateway",
        version="0.1.0",
        description="HTTP/WebSocket front door for the MetaForge platform",
        lifespan=lifespan,
    )

    # Store collector on app.state for use by orchestrator subsystems
    app.state.collector = collector

    # Store test-injected components (lifespan will skip init if present)
    if workflow_engine is not None:
        app.state.workflow_engine = workflow_engine
    if scheduler is not None:
        app.state.scheduler = scheduler

    # -- CORS --------------------------------------------------------------
    origins = cors_origins if cors_origins is not None else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Observability middleware -------------------------------------------
    app.add_middleware(ObservabilityMiddleware, collector=collector)

    # -- Routers -----------------------------------------------------------
    app.include_router(health_router)
    app.include_router(assistant_router)
    app.include_router(chat_router)
    app.include_router(convert_router)
    app.include_router(sessions_router)
    app.include_router(projects_router)
    app.include_router(compliance_router)
    app.include_router(twin_router)

    # -- FastAPI auto-instrumentation (traces all routes automatically) ----
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("fastapi_auto_instrumented")
    except ImportError:
        pass

    app.include_router(knowledge_router)

    logger.info(
        "gateway_configured",
        cors_origins=origins,
        routers=[
            "health",
            "assistant",
            "chat",
            "convert",
            "sessions",
            "projects",
            "knowledge",
            "compliance",
            "twin",
        ],
    )

    return app


# Module-level app for ``uvicorn api_gateway.server:app``
app = create_app(collector=_create_collector())


def main() -> None:
    """Run the gateway with uvicorn (development entry point)."""
    import uvicorn

    logger.info("gateway_main_starting", host="0.0.0.0", port=8000)
    uvicorn.run(
        "api_gateway.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
