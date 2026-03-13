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
    twin = InMemoryTwinAPI.create_with_collector(_collector)
    mcp = InMemoryMcpBridge()
    event_bus = create_default_bus(workflow_engine, collector=_collector)

    # Register all workflow definitions
    for defn in ACTION_WORKFLOWS.values():
        await workflow_engine.register_workflow(defn)

    # Create agents
    mech_agent = MechanicalAgent(twin=twin, mcp=mcp)
    ee_agent = ElectronicsAgent(twin=twin, mcp=mcp)

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
    yield
    logger.info("gateway_stopping")
    if hasattr(app.state, "scheduler"):
        await app.state.scheduler.stop()
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
