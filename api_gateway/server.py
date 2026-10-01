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
from api_gateway.auth import (
    AuthConfigurationError,
    AuthSettings,
    load_auth_settings,
)
from api_gateway.bom.risk_routes import router as bom_risk_router
from api_gateway.bom.routes import router as bom_router
from api_gateway.bringup.routes import router as bringup_router
from api_gateway.cad.routes import router as cad_router
from api_gateway.cad_export.routes import router as cad_export_router
from api_gateway.chat.routes import router as chat_router
from api_gateway.chat.tool_approvals import router as tool_approvals_router
from api_gateway.compliance.routes import router as compliance_router
from api_gateway.component_selection.routes import router as component_selection_router
from api_gateway.constraint.routes import router as constraint_router
from api_gateway.convert.routes import router as convert_router
from api_gateway.design_flows.mcp_bindings import (
    make_catalogue_reader,
    make_proposer,
    make_run_starter,
    make_run_status_reader,
)
from api_gateway.design_flows.routes import router as design_flows_router
from api_gateway.design_loop.routes import router as design_loop_router
from api_gateway.dfm.routes import router as dfm_router
from api_gateway.evals.routes import router as evals_router
from api_gateway.features.routes import router as features_router
from api_gateway.firmware.routes import router as firmware_router
from api_gateway.harness import router as harness_router
from api_gateway.health import health_router, reset_health_checker, set_reported_auth_mode
from api_gateway.knowledge.routes import router as knowledge_router
from api_gateway.manufacture.routes import router as manufacture_router
from api_gateway.memory import router as memory_router
from api_gateway.projects.routes import router as projects_router
from api_gateway.promotion.routes import router as promotion_router
from api_gateway.releases.routes import router as releases_router
from api_gateway.requirement_intelligence.routes import router as requirements_router
from api_gateway.robot_loads.routes import router as robot_loads_router
from api_gateway.runs.routes import router as runs_router
from api_gateway.sessions.routes import router as sessions_router
from api_gateway.simulation.routes import router as simulation_router
from api_gateway.testplans.routes import router as testplans_router
from api_gateway.trade_study.routes import router as trade_study_router
from api_gateway.twin.decision_routes import router as decisions_router
from api_gateway.twin.harness_estimate_routes import router as harness_estimate_router
from api_gateway.twin.hierarchy_routes import router as hierarchy_router
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


class _LazyBridgeMeasure:
    """FORGE-233: an async ``measure(session_id, obj_id) -> dict`` callable
    whose backing MCP bridge is bound AFTER construction, not at it.

    ``geometry_recorder_fn``/``measure_tool`` are built and handed to
    ``bootstrap_tool_registry`` before the ``ToolRegistry``/MCP bridge they'd
    need to call ``freecad.measure`` even exist -- the bridge is built FROM
    the registry ``bootstrap_tool_registry`` itself constructs, so passing a
    real bridge in at construction time is a genuine circular dependency,
    not just an ordering inconvenience. This holder breaks the cycle: it's
    handed to ``bootstrap_tool_registry`` (transitively, to
    ``twin.commit_geometry``) while still empty, and ``self.bridge`` is set
    once, right after ``bootstrap_tool_registry`` returns and the real
    bridge exists -- well before the gateway serves its first request.
    ``__call__`` reads ``self.bridge`` lazily, at call time, so every real
    request sees it populated.
    """

    def __init__(self) -> None:
        self.bridge: Any = None

    async def __call__(self, session_id: str, obj_id: str) -> dict[str, Any]:
        if self.bridge is None:
            return {}
        try:
            return await self.bridge.invoke(
                "freecad.measure", {"session_id": session_id, "obj_id": obj_id}
            )
        except Exception as exc:  # noqa: BLE001 — best-effort derivation, never raise
            logger.warning(
                "geometry_measure_tool_invoke_failed",
                session_id=session_id,
                obj_id=obj_id,
                error=str(exc),
            )
            return {}


class _LazyBridgeAssemblyInfo:
    """FORGE-245: an async ``assembly_info(session_id) -> dict`` callable,
    same lazy-bridge-binding seam as ``_LazyBridgeMeasure`` (see its
    docstring for why the bridge can't be supplied at construction time).

    Reads a still-live FreeCAD session's assembly structure -- every
    part currently registered (``freecad.describe_session``) and every
    joint defined between them (``freecad.list_joints``) -- so
    ``twin.commit_geometry`` can attach ``metadata.assembly = {parts,
    joints}`` to a commit-by-reference call. Without this, the joints a
    session built up (``add_assembly_joint``) are lost the moment the
    session's TTL expires, since the committed STEP carries only merged
    geometry with no part/joint structure of its own.
    """

    def __init__(self) -> None:
        self.bridge: Any = None

    async def __call__(self, session_id: str) -> dict[str, Any]:
        if self.bridge is None:
            return {}
        try:
            session = await self.bridge.invoke(
                "freecad.describe_session", {"session_id": session_id}
            )
            joints = await self.bridge.invoke("freecad.list_joints", {"session_id": session_id})
        except Exception as exc:  # noqa: BLE001 — best-effort derivation, never raise
            logger.warning(
                "geometry_assembly_info_tool_invoke_failed",
                session_id=session_id,
                error=str(exc),
            )
            return {}
        parts = [
            {"name": obj.get("name"), "obj_id": obj.get("obj_id"), "kind": obj.get("kind")}
            for obj in (session.get("objects") or [])
            if isinstance(obj, dict)
        ]
        return {"parts": parts, "joints": joints.get("joints") or []}


class _LazyBridge:
    """FORGE-315: a duck-typed McpBridge (``async invoke(tool_id, params)``)
    whose backing bridge is bound AFTER construction -- same seam as
    ``_LazyBridgeMeasure``/``_LazyBridgeAssemblyInfo`` (see their docstrings
    for the circular-dependency reason: the real bridge is built FROM the
    registry ``bootstrap_tool_registry`` itself populates, so a caller
    handed to that same call can't receive a real bridge yet), generalized
    to a plain passthrough for a caller that needs to invoke a DIFFERENT
    tool by id (``twin.evaluate_metric``'s tier-2 escalation calling
    ``calculix.run_fea``) rather than one fixed tool of its own.
    """

    def __init__(self) -> None:
        self.bridge: Any = None

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        if self.bridge is None:
            raise RuntimeError(
                f"_LazyBridge: no backing MCP bridge bound yet (tool_id={tool_id!r})"
            )
        return await self.bridge.invoke(tool_id, params, timeout=timeout)


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
    """Create PostgreSQL tables and register the Postgres health check.

    Boot policy (MET-305):

    * If ``DATABASE_URL`` is set, the gateway connects on startup and
      calls ``Base.metadata.create_all`` so chat / projects tables
      exist.
    * If ``METAFORGE_REQUIRE_POSTGRES=true`` is set, a connection
      failure is fatal — production deployments opt into this so a
      silent fall-back to in-memory cannot mask data loss for chat
      threads, project metadata, etc.
    * Otherwise (dev), failures log at ERROR level and the gateway
      falls back to in-memory backends so contributors can still boot
      without a running Postgres.

    Pool sizing is configurable via ``METAFORGE_PG_POOL_SIZE`` /
    ``METAFORGE_PG_MAX_OVERFLOW`` / ``METAFORGE_PG_POOL_RECYCLE``
    (see ``api_gateway.db.engine``).
    """
    require_pg = os.environ.get("METAFORGE_REQUIRE_POSTGRES", "").lower() in (
        "1",
        "true",
        "yes",
    )

    try:
        from api_gateway.db import HAS_SQLALCHEMY
        from api_gateway.db.engine import get_engine

        if not HAS_SQLALCHEMY:
            if require_pg:
                raise RuntimeError(
                    "METAFORGE_REQUIRE_POSTGRES=true but sqlalchemy is not installed"
                )
            logger.debug("pg_init_skipped", reason="sqlalchemy not installed")
            return

        engine = get_engine()
        if engine is None:
            if require_pg:
                raise RuntimeError("METAFORGE_REQUIRE_POSTGRES=true but DATABASE_URL is not set")
            logger.debug("pg_init_skipped", reason="DATABASE_URL not set")
            return

        from api_gateway.db.models import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("pg_tables_created")
        _register_postgres_health_check()
    except Exception as exc:
        if require_pg:
            logger.error("pg_required_but_failed", error=str(exc))
            raise
        logger.error(
            "pg_init_failed",
            error=str(exc),
            hint="Set METAFORGE_REQUIRE_POSTGRES=true to fail fast in production.",
        )


def _register_postgres_health_check() -> None:
    """Register a ``postgres`` health check on the gateway.

    Distinct from the ``pgvector`` check, which probes the L1 knowledge
    store. This one verifies the SQLAlchemy engine that backs chat,
    projects, sessions, etc.
    """
    from api_gateway.health import ComponentHealth, DependencyStatus, get_health_checker

    async def _postgres_health() -> ComponentHealth:
        import time as _time

        from sqlalchemy import text

        from api_gateway.db.engine import get_engine, get_session

        eng = get_engine()
        if eng is None:
            return ComponentHealth(
                name="postgres",
                status=DependencyStatus.UNHEALTHY,
                latency_ms=0,
                message="Engine not initialized",
            )
        t0 = _time.monotonic()
        try:
            async with get_session() as session:
                await session.execute(text("SELECT 1"))
            latency = round((_time.monotonic() - t0) * 1000, 2)
            return ComponentHealth(
                name="postgres",
                status=DependencyStatus.HEALTHY,
                latency_ms=latency,
                message="Connected",
            )
        except Exception as exc:
            latency = round((_time.monotonic() - t0) * 1000, 2)
            return ComponentHealth(
                name="postgres",
                status=DependencyStatus.UNHEALTHY,
                latency_ms=latency,
                message=str(exc),
            )

    get_health_checker().register_check("postgres", _postgres_health)
    logger.info("postgres_health_check_registered")


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
    # MET-335: hybrid-search reranker is opt-in. Default false because
    # the BGE cross-encoder model is ~440 MB and few deployments need it
    # by default — flipping it on is a deliberate ops choice.
    reranker_enabled = os.environ.get("KNOWLEDGE_RERANKER_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    app.state.knowledge_reranker_enabled = reranker_enabled

    knowledge_service = None
    component_catalog_store: Any = None
    component_intent_llm: Any = None
    if db_url:
        try:
            dsn = db_url.replace("postgresql+asyncpg://", "postgresql://")

            # MET-466 Task 4: wire the OpenRouter llm_model_func into
            # LightRAG so entity extraction actually runs. Without this,
            # ``LightRAGConfig.llm_model_func`` falls back to the no-op
            # stub and LightRAG runs in naive vector mode — ingest still
            # chunks and embeds, but produces ``0 Ent + 0 Rel`` per
            # chunk and the entity tables stay empty.
            from digital_twin.knowledge.lightrag_service import LightRAGConfig

            llm_model_func: Any = None
            llm_provider = "noop"
            if os.environ.get("OPEN_ROUTER_API_KEY"):
                try:
                    from digital_twin.knowledge.openrouter_lightrag import (
                        OpenRouterLightRAGConfig,
                        build_openrouter_llm_model_func,
                    )

                    or_cfg = OpenRouterLightRAGConfig.from_env()
                    llm_model_func = build_openrouter_llm_model_func(or_cfg)
                    llm_provider = or_cfg.primary_model
                except Exception as or_exc:  # noqa: BLE001
                    logger.warning(
                        "knowledge_service_openrouter_wiring_failed",
                        error=str(or_exc),
                    )

            lr_config = LightRAGConfig(
                working_dir=os.environ.get("METAFORGE_LIGHTRAG_WORKDIR", "./.lightrag-storage"),
                postgres_dsn=dsn,
                llm_model_func=llm_model_func,
            )
            knowledge_service = create_knowledge_service(
                "lightrag",
                config=lr_config,
                reranker_enabled=reranker_enabled,
            )
            await knowledge_service.initialize()  # type: ignore[attr-defined]
            pgvector_active = True

            # MET-422: wire the OpenRouter PropertyLLM (MET-462) into the
            # service so ``knowledge.extract`` returns Tier-2 / Tier-3
            # values (``llm_inferred`` / ``derived``) for properties the
            # Tier-1 verbatim path misses. Without this hook, the tool
            # surfaces ``not_found`` for every property the datasheet
            # tables don't carry literally — even when the datasheet
            # prose plainly states the answer.
            property_llm_provider = "tier1_only"
            if os.environ.get("OPEN_ROUTER_API_KEY"):
                try:
                    from digital_twin.knowledge.openrouter_property_llm import (
                        OpenRouterPropertyConfig,
                        OpenRouterPropertyLLM,
                    )

                    prop_cfg = OpenRouterPropertyConfig.from_env()
                    prop_llm = OpenRouterPropertyLLM(prop_cfg)
                    knowledge_service.set_property_llm(prop_llm)  # type: ignore[attr-defined]
                    property_llm_provider = prop_cfg.primary_model
                    # MET-436: the same client satisfies component.search_intent's
                    # IntentLLM Protocol structurally (both are a single
                    # async def complete(prompt) -> str) — no second client.
                    component_intent_llm = prop_llm
                except Exception as prop_exc:  # noqa: BLE001
                    logger.warning(
                        "knowledge_service_property_llm_wiring_failed",
                        error=str(prop_exc),
                    )

            # MET-436: the parametric component catalog. Same DATABASE_URL
            # gating as knowledge_service above; failure here must not take
            # down knowledge_service (already initialized) — component.*
            # just stays unregistered (component_mcp_adapter_skipped).
            try:
                from digital_twin.catalog import ComponentCatalogStore

                component_catalog_store = ComponentCatalogStore(dsn=dsn)
                await component_catalog_store.initialize()
            except Exception as cc_exc:  # noqa: BLE001
                logger.warning(
                    "component_catalog_store_init_failed",
                    error=str(cc_exc),
                )
                component_catalog_store = None

            logger.info(
                "knowledge_service_lightrag_initialized",
                reranker_enabled=reranker_enabled,
                llm_provider=llm_provider,
                property_llm_provider=property_llm_provider,
                component_catalog_store_active=component_catalog_store is not None,
            )
        except Exception as exc:
            logger.warning("knowledge_service_lightrag_failed", error=str(exc))
            knowledge_service = None
    app.state.knowledge_service = knowledge_service
    app.state.component_catalog_store = component_catalog_store
    app.state.component_intent_llm = component_intent_llm

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

    # MET-453: agent memory layer — experience store + retrieval client.
    # When DATABASE_URL is set, prefer the pgvector backend so experiences
    # survive gateway restarts. Fall back to InMemoryExperienceStore for
    # local dev / test where DATABASE_URL is absent or the pgvector init
    # fails (extension missing, etc.).
    app.state.memory_store = None
    app.state.memory_client = None
    if app.state.embedding_service is None:
        logger.warning("memory_client_init_skipped", reason="no_embedding_service")
    else:
        try:
            from digital_twin.memory.client import MemoryClient
            from digital_twin.memory.pgvector_store import PgVectorExperienceStore
            from digital_twin.memory.store import InMemoryExperienceStore

            memory_store: InMemoryExperienceStore | PgVectorExperienceStore | None = None
            if db_url:
                try:
                    dsn = db_url.replace("postgresql+asyncpg://", "postgresql://")
                    pg_memory = PgVectorExperienceStore(dsn=dsn)
                    await pg_memory.initialize()
                    memory_store = pg_memory
                    logger.info("memory_store_pgvector_initialized")
                except Exception as exc:
                    logger.warning("memory_store_pgvector_failed", error=str(exc))
            if memory_store is None:
                memory_store = InMemoryExperienceStore()
                logger.info("memory_store_in_memory_initialized")

            app.state.memory_store = memory_store
            # MET-567: pass the knowledge service through. Without it
            # ``search_design_rationale`` / ``get_component_context`` raise,
            # so ``POST /v1/memory/search`` and
            # ``GET /v1/memory/components/{name}`` answered 503 forever even
            # on a fully-configured deployment.
            app.state.memory_client = MemoryClient(
                memory_store,
                app.state.embedding_service,
                knowledge_service=getattr(app.state, "knowledge_service", None),
            )
        except Exception as exc:
            logger.warning("memory_client_init_failed", error=str(exc))
            app.state.memory_store = None
            app.state.memory_client = None

    # MET-454: consolidation pipeline orchestrator.
    # Build the full fetcher → grouper → synthesizer → validator → writer
    # chain so the Temporal worker (or an on-demand REST/CLI trigger) can
    # invoke it. Synthesis uses Open Router when OPEN_ROUTER_API_KEY is
    # set; otherwise falls back to StubLLMClient so dev boot stays usable.
    # Insight store prefers pgvector when DATABASE_URL is available.
    app.state.consolidation_orchestrator = None
    app.state.consolidation_insight_store = None
    app.state.consolidation_stack = None
    if app.state.memory_store is None:
        logger.warning("consolidation_orchestrator_init_skipped", reason="no_memory_store")
    else:
        try:
            # MET-723: this construction used to live inline here -- about
            # ninety lines of pgvector / Neo4j / LLM wiring reachable from
            # nowhere else. That made it impossible for the Temporal worker to
            # bind the same orchestrator, so a worker serving
            # ConsolidationWorkflow failed its activity with "orchestrator was
            # not bound before activity ran". Both processes now share one
            # factory. The already-open experience store is passed in so a
            # second pgvector pool is never created for the same data.
            from digital_twin.memory.consolidation import (
                ConsolidationScheduler,
                build_consolidation_stack,
                interval_seconds_from_env,
            )

            stack = await build_consolidation_stack(
                experience_store=app.state.memory_store,
                collector=getattr(app.state, "collector", None),
            )
            app.state.consolidation_stack = stack
            app.state.consolidation_orchestrator = stack.orchestrator
            app.state.consolidation_insight_store = stack.insight_store
            logger.info("consolidation_orchestrator_initialized")

            # MET-567: nothing ever triggered a pass. The pipeline, the
            # Temporal workflow, and the REST trigger all existed while
            # ``memory.list_insights`` stayed empty in every deployment,
            # because no scheduler and no worker ever called any of them.
            scheduler = ConsolidationScheduler(
                stack.orchestrator, interval_seconds=interval_seconds_from_env()
            )
            app.state.consolidation_scheduler = scheduler
            scheduler.start()
        except Exception as exc:
            logger.warning("consolidation_orchestrator_init_failed", error=str(exc))
            app.state.consolidation_orchestrator = None
            app.state.consolidation_insight_store = None

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

    # Select Twin backend from environment.
    #
    # Boot policy (MET-304):
    #   * If NEO4J_URI is set, the Twin connects to Neo4j on startup.
    #   * If METAFORGE_REQUIRE_NEO4J=true is set, a connection failure is
    #     fatal — production deployments opt into this so a silent
    #     fall-back to in-memory cannot mask data loss.
    #   * Otherwise (dev), a connection failure logs an error and falls
    #     back to InMemoryGraphEngine so contributors can still boot
    #     without a running Neo4j.
    require_neo4j = os.environ.get("METAFORGE_REQUIRE_NEO4J", "").lower() in (
        "1",
        "true",
        "yes",
    )
    neo4j_uri = os.environ.get("NEO4J_URI") or os.environ.get("METAFORGE_NEO4J_URI")
    try:
        twin = await InMemoryTwinAPI.create_from_env(collector=_collector)
    except Exception as exc:
        if require_neo4j:
            logger.error(
                "neo4j_required_but_failed",
                uri=neo4j_uri,
                error=str(exc),
            )
            raise
        logger.error(
            "neo4j_fallback_to_in_memory",
            uri=neo4j_uri,
            error=str(exc),
            hint="Set METAFORGE_REQUIRE_NEO4J=true to fail fast in production.",
        )
        twin = InMemoryTwinAPI.create_with_collector(_collector)
    twin_backend = (
        "neo4j" if type(twin._graph).__name__ == "Neo4jGraphEngine" else "in_memory"  # noqa: SLF001
    )
    logger.info("twin_backend_selected", backend=twin_backend, neo4j_uri=neo4j_uri)
    mcp = InMemoryMcpBridge()

    # Initialize PostgreSQL schema first so chat/project backends can wire,
    # AND the L1 KnowledgeService — both are needed before the tool registry
    # boots so the knowledge MCP adapter (MET-335) and the consumer
    # subscription (MET-307) can be registered with the live service.
    await _init_database()
    await _init_knowledge_store(app)

    # MET-427: instantiate the project backend *before* the tool registry
    # boots so the `project.*` MCP adapter can be wired with the same
    # backend the gateway HTTP routes use. The backend is set on the
    # routes initialiser further down.
    from api_gateway.projects.backend import create_project_backend as _create_pb

    project_backend = await _create_pb()

    # MET-493: externally-recorded agent-session store (MCP/CLI agents).
    # Shares the same DATABASE_URL-selected backend as the rest of the
    # gateway; read by the /v1/sessions routes via app.state.
    from api_gateway.sessions.backend import create_agent_session_store as _create_ass
    from api_gateway.sessions.experience_bridge import wrap_with_experience_bridge

    # MET-567: wrap the store so completing a session also deposits one
    # experience row. All three completion paths (REST route, session.complete
    # MCP tool, sidecar idle rollover) go through the store, so this is the
    # single seam that catches them.
    app.state.agent_session_store = wrap_with_experience_bridge(
        await _create_ass(),
        getattr(app.state, "memory_store", None),
        getattr(app.state, "embedding_service", None),
    )

    # Bootstrap tool adapters into the registry and create real MCP bridge.
    # The knowledge MCP adapter is included only when a KnowledgeService is
    # available on app.state.
    from api_gateway.assistant.apply import make_apply_executor
    from api_gateway.assistant.proposal_recorder import make_proposal_recorder
    from api_gateway.assistant.routes import workflow as approval_workflow
    from api_gateway.requirement_intelligence.promotion import attempt_promotion
    from api_gateway.runs.launcher import make_run_launcher
    from api_gateway.twin.baseline import make_baseline_creator
    from api_gateway.twin.blob_stager import make_blob_stager
    from api_gateway.twin.bom_risk import make_bom_risk_scorer
    from api_gateway.twin.bringup_checklist import (
        make_bringup_checklist_creator,
        make_bringup_checklist_lister,
    )
    from api_gateway.twin.calibration import (
        make_calibrated_band_lookup,
        make_calibration_recorder,
    )
    from api_gateway.twin.claim_recorder import make_claim_recorder
    from api_gateway.twin.component_recorder import make_component_recorder
    from api_gateway.twin.component_selection import make_component_selector
    from api_gateway.twin.constraint_recorder import make_constraint_recorder
    from api_gateway.twin.decision_recorder import make_decision_recorder
    from api_gateway.twin.design_loop import (
        make_design_loop_approver,
        make_design_loop_reader,
        make_design_loop_starter,
        tube_height_candidate_mapper,
    )
    from api_gateway.twin.design_sketch_recorder import (
        make_design_sketch_approver,
        make_design_sketch_recorder,
    )
    from api_gateway.twin.device_instance_recorder import make_device_instance_registrar
    from api_gateway.twin.dfm_evidence import make_overhang_evidence_recorder
    from api_gateway.twin.document_recorder import make_document_recorder
    from api_gateway.twin.ect_tools import make_ect_bridge
    from api_gateway.twin.engineering_entity_approval import make_engineering_entity_approver
    from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder
    from api_gateway.twin.evidence_recorder import make_evidence_recorder
    from api_gateway.twin.firmware_scaffold import make_firmware_scaffold_creator
    from api_gateway.twin.geometry_diff import make_geometry_diff
    from api_gateway.twin.geometry_recorder import make_geometry_recorder
    from api_gateway.twin.git_repo_registry import GitRepoRegistry, init_git_registry
    from api_gateway.twin.harness_estimate import make_harness_estimate_getter
    from api_gateway.twin.hierarchy_recorder import (
        make_hierarchy_geometry_linker,
        make_hierarchy_node_recorder,
        make_hierarchy_rollup_fn,
    )
    from api_gateway.twin.manufacture_release import make_manufacture_release
    from api_gateway.twin.measurement_recorder import make_measurement_recorder
    from api_gateway.twin.metric_evaluator import make_metric_evaluator
    from api_gateway.twin.optimizer import make_tube_height_optimizer, make_wall_thickness_optimizer
    from api_gateway.twin.release_package import (
        make_release_package_creator,
        make_release_package_lister,
    )
    from api_gateway.twin.revalidation import make_revalidation_executor
    from api_gateway.twin.robot_description_recorder import (
        make_robot_description_recorder,
        make_robot_description_updater,
    )
    from api_gateway.twin.sensitivity import make_sensitivity_ranker
    from api_gateway.twin.structured_document_recorder import (
        make_compliance_checklist_recorder,
        make_hazard_analysis_recorder,
        make_procurement_record_recorder,
        make_system_architecture_recorder,
        make_technical_drawing_recorder,
    )
    from api_gateway.twin.test_plan import make_test_plan_generator, make_test_plan_lister
    from api_gateway.twin.thermal_evidence import make_thermal_evidence_recorder
    from api_gateway.twin.trade_study import make_trade_study_selector

    decision_recorder = make_decision_recorder(twin, project_backend)
    git_registry = GitRepoRegistry.from_env(twin.graph)
    init_git_registry(git_registry)
    geometry_recorder_fn = make_geometry_recorder(twin, project_backend, git_registry)
    # FORGE-233: empty until the real MCP bridge exists (below, after
    # bootstrap_tool_registry returns) -- see _LazyBridgeMeasure's own
    # docstring for why this can't just be constructed with the bridge
    # directly.
    measure_tool = _LazyBridgeMeasure()
    # FORGE-245: same lazy-bridge seam, for a live FreeCAD session's
    # assembly structure (parts + joints) at commit time.
    assembly_info_tool = _LazyBridgeAssemblyInfo()
    # FORGE-315: same lazy-bridge seam, for twin.evaluate_metric's tier-2
    # escalation (a real calculix.run_fea call).
    metric_evaluator_bridge = _LazyBridge()
    # FORGE-297: same lazy-bridge seam, for twin.evaluate_thermal_metric's
    # real calculix.run_thermal / calculix.cross_check_thermal_steady_state
    # calls.
    thermal_evaluator_bridge = _LazyBridge()
    # FORGE-273: same lazy-bridge seam, for twin.evaluate_overhang_metric's
    # real freecad.list_named_faces call.
    overhang_evaluator_bridge = _LazyBridge()
    # FORGE-294: same lazy-bridge seam, for manufacture_release's real
    # cadquery.export_geometry call.
    manufacture_release_bridge = _LazyBridge()
    evidence_recorder_fn = make_evidence_recorder(twin, project_backend)
    # FORGE-321: a real measurement history overrides evaluate_metric's
    # fixed-prior band once enough of it exists for a given metric/tier --
    # built before metric_evaluator_fn so it can be wired straight in.
    calibrated_band_lookup_fn = make_calibrated_band_lookup(twin)
    metric_evaluator_fn = make_metric_evaluator(
        twin,
        evidence_recorder=evidence_recorder_fn,
        mcp_bridge=metric_evaluator_bridge,
        calibrated_band_lookup=calibrated_band_lookup_fn,
    )
    # FORGE-297: single-tier thermal analysis evaluator -- runs a real
    # calculix.run_thermal call and records its output as Evidence, closing
    # the gap where FORGE-282's thermal analysis had no path into the
    # requirements matrix/coverage numbers.
    thermal_evaluator_fn = make_thermal_evidence_recorder(
        twin,
        evidence_recorder=evidence_recorder_fn,
        mcp_bridge=thermal_evaluator_bridge,
    )
    # FORGE-273: 3D-print overhang DFM check -- runs a real
    # freecad.list_named_faces mesh lookup and records the flagged-face
    # result as Evidence (gap G-D5).
    overhang_evaluator_fn = make_overhang_evidence_recorder(
        twin,
        evidence_recorder=evidence_recorder_fn,
        mcp_bridge=overhang_evaluator_bridge,
    )
    # MET-618: hoisted so the SAME blob-stager instance (and its staged-file
    # cache) is reused by both twin.stage_work_product_file (below) and
    # manufacture_release_fn, same pattern FORGE-265 established for
    # component_recorder_fn.
    blob_stager_fn = make_blob_stager(twin)
    # FORGE-294: "Release for manufacture" -- resolves a work product's real
    # committed STEP file (via blob_stager_fn), exports it to a real
    # process-specific manufacturing file (via cadquery.export_geometry),
    # and returns the result. manufacture_release_bridge is bound to the
    # real active_bridge below, once it exists.
    manufacture_release_fn = make_manufacture_release(
        twin,
        blob_stager=blob_stager_fn,
        mcp_bridge=manufacture_release_bridge,
    )
    # FORGE-301: same lazy-bridge seam, for geometry_diff's real
    # freecad.describe_step_file calls (reuses blob_stager_fn -- both nodes
    # in a SUPERSEDES pair are resolved by node id, no new staging logic).
    geometry_diff_bridge = _LazyBridge()
    geometry_diff_fn = make_geometry_diff(
        twin,
        blob_stager=blob_stager_fn,
        mcp_bridge=geometry_diff_bridge,
    )
    # FORGE-268: same lazy-bridge seam, for bom_risk's real
    # distributors.resolve_offers / {distributor}.get_product calls.
    bom_risk_bridge = _LazyBridge()
    bom_risk_fn = make_bom_risk_scorer(twin, mcp_bridge=bom_risk_bridge)
    # FORGE-316: dispatch table for twin.execute_revalidation_plan --
    # every tool a stale Evidence's metadata["replay"]["tool_id"] can name.
    # Only twin.evaluate_metric exists today; adding a future evaluator/
    # recorder here is how it becomes automatically re-runnable too.
    revalidation_executor_fn = make_revalidation_executor(
        twin, tool_dispatch={"twin.evaluate_metric": metric_evaluator_fn}
    )
    sensitivity_ranker_fn = make_sensitivity_ranker(twin, evidence_recorder=evidence_recorder_fn)
    # FORGE-320: reuses the same evidence_recorder_fn as sensitivity_ranker
    # above, plus decision_recorder (built just below) for the "Decision
    # with alternatives" output -- twin.record_decision already is exactly
    # that, no new node type needed.
    parameter_optimizer_fn = make_wall_thickness_optimizer(
        twin, evidence_recorder=evidence_recorder_fn, decision_recorder=decision_recorder
    )
    # FORGE-287: composes the same optimizer above (so its Evidence/Decision
    # recording isn't duplicated) and layers a persisted, queryable
    # DesignLoopIteration timeline + approval gate on top.
    design_loop_starter_fn = make_design_loop_starter(twin, optimize=parameter_optimizer_fn)
    design_loop_reader_fn = make_design_loop_reader(twin)
    design_loop_approver_fn = make_design_loop_approver(twin)
    # FORGE-288: a second real parameter (height_mm) plugged into the SAME
    # design-loop machinery above via the generalized parameter_name/metric/
    # candidate_mapper seam -- proof the loop generalizes, not a copy.
    tube_height_optimizer_fn = make_tube_height_optimizer(
        twin, evidence_recorder=evidence_recorder_fn, decision_recorder=decision_recorder
    )
    tube_height_design_loop_starter_fn = make_design_loop_starter(
        twin,
        optimize=tube_height_optimizer_fn,
        parameter_name="height_mm",
        candidate_mapper=tube_height_candidate_mapper,
    )
    # FORGE-262: trade-study selection over recorded concept_option
    # entities -- reuses decision_recorder unchanged (no new "Decision-like"
    # node type), same precedent as parameter_optimizer_fn above. Also
    # reuses the SAME engineering_entity_recorder_fn instance
    # bootstrap_tool_registry wires up below, so a concept_option created
    # via the dashboard's REST route and via an agent's own MCP call are
    # indistinguishable afterward.
    engineering_entity_recorder_fn = make_engineering_entity_recorder(twin, project_backend)
    concept_selector_fn = make_trade_study_selector(twin, decision_recorder=decision_recorder)

    # FORGE-299: a versioned snapshot (release_package) of a project's
    # hierarchy/BOM/evidence/decisions, gated at creation time on a real
    # evaluate_g8_release(...) == PASSED check -- the first real consumer
    # of that previously purely-advisory gate. traceability_coverage is
    # wired to the SAME TraceabilityAgent FORGE-297's coverage route uses,
    # so G8's "required verification complete" check can actually evaluate
    # instead of reporting NOT_EVALUATED.
    from api_gateway.requirement_intelligence.traceability import TraceabilityAgent

    async def _traceability_coverage_accessor(project_id: Any) -> Any:
        return await TraceabilityAgent(twin).coverage(str(project_id))

    release_package_creator_fn = make_release_package_creator(
        twin,
        engineering_entity_recorder=engineering_entity_recorder_fn,
        traceability_coverage=_traceability_coverage_accessor,
    )
    release_package_lister_fn = make_release_package_lister(twin)

    # FORGE-405: a real gateway/MCP-reachable way to create a Baseline --
    # follow-up to FORGE-299, which found the G8 release gate's
    # "configuration baseline fixed" check could never pass on a real
    # project because nothing ever called the pre-existing
    # twin_core.transactions.baseline.create_baseline.
    baseline_creator_fn = make_baseline_creator(twin)

    # FORGE-298: for each Constraint on a project with verification_method
    # == "test", mechanically derives one verification_case entity from the
    # requirement's own structured metric/operator/limit/unit/
    # target_node_type fields. Reuses the SAME engineering_entity_recorder_fn
    # instance every other entity type goes through.
    test_plan_generator_fn = make_test_plan_generator(
        twin, engineering_entity_recorder=engineering_entity_recorder_fn
    )
    test_plan_lister_fn = make_test_plan_lister(twin)

    # FORGE-295: derives a step-by-step assembly sequence from a work
    # product's real metadata.assembly.joints (gap G-H3). Reuses the SAME
    # engineering_entity_recorder_fn instance every other entity type goes
    # through.
    bringup_checklist_creator_fn = make_bringup_checklist_creator(
        twin, engineering_entity_recorder=engineering_entity_recorder_fn
    )
    bringup_checklist_lister_fn = make_bringup_checklist_lister(twin)

    # MET-588: hoisted to a named variable (previously only constructed
    # inline at the TwinServer(...) call site below) so this SAME instance
    # can also back firmware_scaffold_creator_fn below (FORGE-276).
    document_recorder_fn = make_document_recorder(twin, project_backend)

    # FORGE-276: derives a per-joint CAN node table + a minimal firmware
    # header scaffold from a work product's real metadata.assembly.joints
    # (gap G-E3). Reuses the SAME document_recorder_fn instance
    # api_gateway/runs/fw_handlers.py's design-flow phase already goes
    # through for its own pinmap/firmware_source pair.
    firmware_scaffold_creator_fn = make_firmware_scaffold_creator(
        twin, document_recorder=document_recorder_fn
    )

    # FORGE-275: per-joint cumulative cable-length estimate from a work
    # product's real metadata.assembly.joints (gap G-E2). The power-tree
    # half of this ticket (draw/dissipation per hierarchy node) needed no
    # new wiring -- it extends the existing hierarchy tree response with
    # fields twin_core.consistency.hierarchy_rollup (FORGE-390) already
    # computes; see api_gateway/twin/hierarchy_routes.py.
    harness_estimate_getter_fn = make_harness_estimate_getter(twin)

    # FORGE-265: requirement-driven component selection -- hoisted to a
    # named variable (unlike every other component_recorder use, which is
    # constructed inline at the TwinServer(...) call site below) so this
    # SAME instance can be reused both for twin.record_component_selection
    # and inside component_selector_fn, and again by the REST route's own
    # init_component_selector below -- one recording path, not three.
    component_recorder_fn = make_component_recorder(
        twin,
        project_backend,
        catalog_store=getattr(app.state, "component_catalog_store", None),
    )
    component_selector_fn = make_component_selector(
        twin, decision_recorder=decision_recorder, component_recorder=component_recorder_fn
    )

    # FORGE-266: attach/replace a hierarchy node's REALIZED_BY/INSTANCE_OF
    # geometry after the node already exists ("replace placeholder with
    # part") -- hoisted so the REST route's own init_hierarchy_geometry_linker
    # below reuses the SAME instance twin.realize_hierarchy_node uses.
    hierarchy_geometry_linker_fn = make_hierarchy_geometry_linker(twin)

    # FORGE-319: attempt_promotion is a plain function (twin, ...) -- bind
    # twin once here, same injected-callable shape as every make_X(twin,
    # ...) factory above.
    async def promotion_attempter_fn(**kwargs: Any) -> dict[str, Any]:
        return await attempt_promotion(twin, **kwargs)

    # FORGE-355: renders metaforge://twin/brief/{project_id} using the same
    # builder the chat harness uses, so an MCP client and a chat agent are
    # briefed identically. FORGE-337 moved the body to
    # api_gateway.projects.brief_provider so the standalone MCP sidecar can
    # build the same one -- it had none, and served no resources at all.
    from api_gateway.projects.brief_provider import make_brief_provider
    from api_gateway.projects.routes import get_project_backend

    brief_provider_fn = make_brief_provider(twin, get_project_backend())

    # FORGE-321: device registration; measurement recording (reuses
    # evidence_recorder_fn via a calibration_recorder for residuals).
    device_instance_registrar_fn = make_device_instance_registrar(twin)
    calibration_recorder_fn = make_calibration_recorder(
        twin, evidence_recorder=evidence_recorder_fn
    )
    measurement_recorder_fn = make_measurement_recorder(
        twin, calibration_recorder=calibration_recorder_fn
    )

    # MET-740: robot-description (URDF/SDF/USD) persistence for the
    # dashboard's cad-export routes. REST-route-triggered, not agent/MCP-
    # triggered like geometry_recorder above, so it's wired directly into
    # that module rather than through bootstrap_tool_registry.
    from api_gateway.cad_export.routes import init_robot_description_recorder

    init_robot_description_recorder(
        make_robot_description_recorder(twin, project_backend),
        make_robot_description_updater(twin),
    )
    tool_registry = await bootstrap_tool_registry(
        knowledge_service=getattr(app.state, "knowledge_service", None),
        twin=twin,
        constraint_engine=twin.constraints,
        project_backend=project_backend,
        memory_client=getattr(app.state, "memory_client", None),
        memory_insight_store=getattr(app.state, "consolidation_insight_store", None),
        agent_session_store=getattr(app.state, "agent_session_store", None),
        decision_recorder=decision_recorder,
        geometry_recorder=geometry_recorder_fn,
        measure_tool=measure_tool,
        assembly_info_tool=assembly_info_tool,
        proposal_recorder=make_proposal_recorder(approval_workflow),
        # MET-582: structured requirements -> evaluable Constraint nodes +
        # a constraint_set work product (feeds MET-583's gate criteria).
        constraint_recorder=make_constraint_recorder(twin, project_backend),
        # FORGE-45 (epic FORGE-35): Engineering Intent & Requirements Harness
        # entities (intent/need/objective/assumption/question/risk/
        # verification_case/evidence) -- the "why" a requirement exists,
        # recorded before/alongside the quantified requirements themselves.
        engineering_entity_recorder=engineering_entity_recorder_fn,
        # FORGE-73 (waiver/release model): a real approval step distinct from
        # creation, so a waiver/release_approval only counts once actually
        # approved, never just because it was recorded.
        engineering_entity_approver=make_engineering_entity_approver(twin),
        # MET-587: chat-triggered design flows (run.start_design_flow) — the
        # flow's own phase gates are the HITL approval mechanism.
        run_launcher=make_run_launcher(),
        # MET-588: chat had no direct way to save a document (requirements,
        # notes) — it fell back to twin.propose_change, whose apply-on-approve
        # executor only implements a `record_decision` action, so anything
        # else silently no-ops even after a human approves it.
        document_recorder=document_recorder_fn,
        # MET-618: lets an agent recover a committed work product's actual
        # file (STEP, mesh, ...) by node id once its authoring session is
        # gone, unknown, or was never its own. Hoisted above (FORGE-294) so
        # the same instance backs manufacture_release_fn too.
        blob_stager=blob_stager_fn,
        # MET-436: the parametric component catalog + the intent-translation
        # LLM (reused from the property-extraction Tier-2 wiring above).
        # component.* registers only when both are supplied, together with
        # knowledge_service (used for the intent-search fuzzy fallback).
        component_catalog_store=getattr(app.state, "component_catalog_store", None),
        component_intent_llm=getattr(app.state, "component_intent_llm", None),
        # Follow-up to MET-740/747: registers twin.commit_design_sketch, the
        # human-approval gate before real CAD/build work on anything
        # non-trivial or any revision of an already-built design.
        design_sketch_recorder=make_design_sketch_recorder(twin, project_backend),
        # MET-747 lifecycle-mapping follow-up: five structured-document work
        # products (hazard analysis, system architecture, technical drawing,
        # compliance checklist, procurement record), each backed by the
        # shared structured_document_recorder persistence helper.
        hazard_analysis_recorder=make_hazard_analysis_recorder(twin, project_backend),
        system_architecture_recorder=make_system_architecture_recorder(twin, project_backend),
        technical_drawing_recorder=make_technical_drawing_recorder(twin, project_backend),
        compliance_checklist_recorder=make_compliance_checklist_recorder(twin, project_backend),
        procurement_record_recorder=make_procurement_record_recorder(twin, project_backend),
        # MET-436 follow-up: without this, a component.search_* result was
        # pure chat output -- no reviewable, versioned trace in the twin.
        # Persists a chosen result as a BOMItem work product + project link.
        # catalog_store lets it auto-fill image/footprint/CAD/cost from an
        # already-indexed catalog row when the caller doesn't pass them.
        # Hoisted to component_recorder_fn above (FORGE-265) so the SAME
        # instance is also reused inside component_selector_fn.
        component_recorder=component_recorder_fn,
        # FORGE-64 (epic FORGE-35, Phase 6: Evidence Integration): persists
        # tool-generated Evidence entities -- the constructive fix for
        # "requirement satisfaction claims" being LLM assertion instead of a
        # real, checkable graph fact.
        evidence_recorder=evidence_recorder_fn,
        # FORGE-65: the requirement-satisfaction claim this whole phase is
        # named for -- an artefact's real edge to the requirement it
        # satisfies, citing evidence, with status always computed live.
        claim_recorder=make_claim_recorder(twin),
        # FORGE-70 (epic FORGE-35, Phase 7): the ECT lifecycle
        # (propose/analyze/approve/reject/commit/mark_rolled_back) was real
        # and tested since FORGE-66/67 but unreachable from any agent --
        # nothing wired it to an MCP tool until this.
        ect_bridge=make_ect_bridge(twin),
        # FORGE-260 (gap G-B1): the product hierarchy tree -- a project's
        # structural product/system/subsystem/assembly nesting, distinct
        # from the twin's other "by type" views.
        hierarchy_node_recorder=make_hierarchy_node_recorder(twin, project_backend),
        hierarchy_rollup_fn=make_hierarchy_rollup_fn(twin),
        # FORGE-266 (gap G-C2): "replace placeholder with part".
        hierarchy_geometry_linker=hierarchy_geometry_linker_fn,
        # FORGE-315: tier-0 closed-form hand-calc + tier-2 FEA escalation
        # (spec §30, minimum sufficient fidelity). metric_evaluator_bridge
        # is bound to the real active_bridge below, once it exists.
        metric_evaluator=metric_evaluator_fn,
        # FORGE-297: single-tier thermal analysis evaluator + evidence
        # recorder. thermal_evaluator_bridge is bound to the real
        # active_bridge below, once it exists.
        thermal_evaluator=thermal_evaluator_fn,
        # FORGE-273: 3D-print overhang DFM check + evidence recorder.
        # overhang_evaluator_bridge is bound to the real active_bridge
        # below, once it exists.
        overhang_evaluator=overhang_evaluator_fn,
        # FORGE-316: automatic selective re-run of exactly what a
        # committed ECT's real revalidation_plan marked stale.
        revalidation_executor=revalidation_executor_fn,
        # FORGE-317: one-at-a-time finite-difference sensitivity ranking.
        sensitivity_ranker=sensitivity_ranker_fn,
        # FORGE-319: the first real gate that refuses, not just reports.
        promotion_attempter=promotion_attempter_fn,
        # FORGE-400: design flows reach the harness plugins. Note the absence
        # of an approver binding -- there is no tool that approves, so there
        # is nothing to inject for one.
        design_flow_catalogue_reader=make_catalogue_reader(),
        design_flow_proposer=make_proposer(),
        design_flow_status_reader=make_run_status_reader(),
        design_flow_run_starter=make_run_starter(),
        # FORGE-355: the project brief as an MCP resource.
        brief_provider=brief_provider_fn,
        # FORGE-320: bisection search for the minimum-mass wall thickness
        # satisfying deflection/safety-factor constraints.
        parameter_optimizer=parameter_optimizer_fn,
        # FORGE-321: DeviceInstance registration + measurement recording
        # (residuals feed the calibrated band evaluate_metric now reads).
        device_instance_registrar=device_instance_registrar_fn,
        measurement_recorder=measurement_recorder_fn,
        # FORGE-287: closed design loop -- persisted, queryable iteration
        # timeline + approval gate over the same optimizer above.
        design_loop_starter=design_loop_starter_fn,
        design_loop_reader=design_loop_reader_fn,
        design_loop_approver=design_loop_approver_fn,
        # FORGE-288: a second real parameter (height_mm) over the same loop.
        tube_height_design_loop_starter=tube_height_design_loop_starter_fn,
        # FORGE-262: concept generation + trade study (gap G-B2).
        concept_selector=concept_selector_fn,
        # FORGE-265: requirement-driven component selection (gap G-C1).
        component_selector=component_selector_fn,
        # FORGE-299: versioned release-package snapshot, gated on G8 (gap G-I3).
        release_package_creator=release_package_creator_fn,
        # FORGE-405: baseline creation, so G8's baseline check is satisfiable.
        baseline_creator=baseline_creator_fn,
        # FORGE-298: mechanical test-plan derivation from test-method
        # requirements (gap G-I2).
        test_plan_generator=test_plan_generator_fn,
        # FORGE-295: bring-up checklist derived from assembly joints (gap G-H3).
        bringup_checklist_creator=bringup_checklist_creator_fn,
        # FORGE-276: firmware scaffold derived from assembly joints (gap G-E3).
        firmware_scaffold_creator=firmware_scaffold_creator_fn,
        # FORGE-275: per-joint cable-length estimate from assembly joints (gap G-E2).
        harness_estimate_getter=harness_estimate_getter_fn,
    )
    app.state.tool_registry = tool_registry
    registry_bridge = RegistryMcpBridge(tool_registry)
    logger.info(
        "tool_registry_bootstrapped",
        adapters=len(tool_registry.list_adapters()),
        tools=len(tool_registry.list_tools()),
    )

    # MET-306: pick the active MCP bridge from env config. Default
    # ``METAFORGE_MCP_BRIDGE=registry`` keeps the in-process registry
    # bridge; ``http`` / ``stdio`` connect to an external server (e.g.
    # the standalone ``python -m metaforge.mcp`` from MET-337) and fall
    # back to the registry bridge on connection failure unless
    # ``METAFORGE_REQUIRE_MCP=true`` is set.
    from skill_registry.bridge_factory import create_mcp_bridge

    active_bridge = await create_mcp_bridge(fallback=registry_bridge)
    app.state.mcp_bridge = active_bridge
    # FORGE-233: the bridge measure_tool (handed to bootstrap_tool_registry,
    # and from there to twin.commit_geometry, above) was waiting for -- now
    # populated, well before the gateway serves its first request.
    measure_tool.bridge = active_bridge
    # FORGE-245: same for assembly_info_tool.
    assembly_info_tool.bridge = active_bridge
    # FORGE-315: same for metric_evaluator_bridge (twin.evaluate_metric's
    # tier-2 escalation to calculix.run_fea).
    metric_evaluator_bridge.bridge = active_bridge
    # FORGE-297: same for thermal_evaluator_bridge (twin.
    # evaluate_thermal_metric's calculix.run_thermal call).
    thermal_evaluator_bridge.bridge = active_bridge
    # FORGE-273: same for overhang_evaluator_bridge (twin.
    # evaluate_overhang_metric's freecad.list_named_faces call).
    overhang_evaluator_bridge.bridge = active_bridge
    # FORGE-294: same for manufacture_release_bridge (manufacture_release's
    # cadquery.export_geometry call).
    manufacture_release_bridge.bridge = active_bridge
    # FORGE-301: same for geometry_diff_bridge (geometry_diff's
    # freecad.describe_step_file calls).
    geometry_diff_bridge.bridge = active_bridge
    # FORGE-268: same for bom_risk_bridge (bom_risk's distributors.
    # resolve_offers / {distributor}.get_product calls).
    bom_risk_bridge.bridge = active_bridge
    # FORGE-273: bind the same evaluator closure to the dashboard's REST
    # route (api_gateway/dfm/routes.py) -- only now, after the bridge above
    # is bound, since the closure calls mcp_bridge.invoke internally.
    from api_gateway.dfm.routes import init_overhang_evaluator

    init_overhang_evaluator(overhang_evaluator_fn)
    # FORGE-294: same, for the manufacture-release REST route
    # (api_gateway/manufacture/routes.py).
    from api_gateway.manufacture.routes import init_manufacture_release

    init_manufacture_release(manufacture_release_fn)
    # FORGE-301: bind the geometry-diff evaluator to its route in
    # api_gateway/twin/routes.py (not a separate module -- it's the direct
    # sibling of /nodes/{id}/diff there).
    from api_gateway.twin.routes import init_geometry_diff

    init_geometry_diff(geometry_diff_fn)
    # FORGE-268: same, for the BOM risk REST route
    # (api_gateway/bom/risk_routes.py).
    from api_gateway.bom.risk_routes import init_bom_risk_scorer

    init_bom_risk_scorer(bom_risk_fn)
    # FORGE-299: bind the release-package creator/lister to the dashboard's
    # REST route (api_gateway/releases/routes.py) -- unlike the evaluators
    # above, this doesn't call mcp_bridge at all (pure graph reads/writes +
    # a gate check), so it doesn't need to wait for active_bridge, but is
    # wired here for locality with the other route inits.
    from api_gateway.releases.routes import init_release_package

    init_release_package(release_package_creator_fn, release_package_lister_fn)
    # FORGE-298: bind the test-plan generator/lister to the dashboard's REST
    # route (api_gateway/testplans/routes.py) -- same locality rationale as
    # release_package above.
    from api_gateway.testplans.routes import init_test_plan

    init_test_plan(test_plan_generator_fn, test_plan_lister_fn)
    # FORGE-295: bind the bring-up checklist creator/lister to the
    # dashboard's REST route (api_gateway/bringup/routes.py) -- same
    # locality rationale as release_package/test_plan above.
    from api_gateway.bringup.routes import init_bringup_checklist

    init_bringup_checklist(bringup_checklist_creator_fn, bringup_checklist_lister_fn)
    # FORGE-276: bind the firmware scaffold creator to the dashboard's REST
    # route (api_gateway/firmware/routes.py) -- same locality rationale as
    # bringup_checklist above.
    from api_gateway.firmware.routes import init_firmware_scaffold

    init_firmware_scaffold(firmware_scaffold_creator_fn)
    # FORGE-275: bind the harness-estimate getter to the dashboard's REST
    # route (api_gateway/twin/harness_estimate_routes.py) -- same locality
    # rationale as firmware_scaffold/bringup_checklist above.
    from api_gateway.twin.harness_estimate_routes import init_harness_estimate

    init_harness_estimate(harness_estimate_getter_fn)
    logger.info(
        "mcp_bridge_active",
        bridge_type=type(active_bridge).__name__,
    )
    # Apply-on-approve executor (MET-548/MET-630): runs an approved
    # proposal's diff. Needs the resolved MCP bridge (not yet available
    # above) to apply `regenerate_geometry` actions.
    app.state.proposal_apply = make_apply_executor(
        decision_recorder, mcp_bridge=active_bridge, geometry_recorder=geometry_recorder_fn
    )

    # Initialize chat backend (PG or in-memory). Project backend is
    # already initialised above (before the tool registry) so the
    # project MCP adapter can pick it up.
    from api_gateway.bom.routes import init_twin as init_bom_twin
    from api_gateway.chat.backend import create_backend
    from api_gateway.chat.context_adapter import init_context_assembler
    from api_gateway.chat.routes import init_chat_backend, init_mcp_bridge, init_metrics, init_twin
    from api_gateway.component_selection.routes import init_component_selector
    from api_gateway.design_loop.routes import init_design_loop_starter
    from api_gateway.design_loop.routes import init_twin as init_design_loop_twin
    from api_gateway.features.routes import init_twin as init_features_twin
    from api_gateway.projects.routes import init_project_backend
    from api_gateway.projects.routes import init_twin as init_projects_twin
    from api_gateway.promotion.routes import init_twin as init_promotion_twin
    from api_gateway.requirement_intelligence.routes import init_twin as init_requirements_twin
    from api_gateway.simulation.routes import init_twin as init_simulation_twin
    from api_gateway.trade_study.routes import init_concept_selector
    from api_gateway.trade_study.routes import init_entity_recorder as init_ts_entity_recorder
    from api_gateway.trade_study.routes import init_twin as init_trade_study_twin
    from api_gateway.twin.decision_routes import init_twin as init_decisions_twin
    from api_gateway.twin.hierarchy_routes import init_hierarchy_geometry_linker
    from api_gateway.twin.hierarchy_routes import init_twin as init_hierarchy_twin
    from api_gateway.twin.routes import init_design_sketch_approver
    from api_gateway.twin.routes import init_twin as init_twin_viewer

    chat_backend = await create_backend()
    init_chat_backend(chat_backend)
    logger.info(
        "chat_backend_selected",
        backend="postgres" if type(chat_backend).__name__ == "PgChatBackend" else "in_memory",
    )

    from api_gateway.chat.experience_adapter import init_chat_experience_recorder
    from api_gateway.chat.turn_capture import init_turn_capture

    # MET-594: tee live chat steps into the agent-session event log.
    init_turn_capture(getattr(app.state, "agent_session_store", None))

    # MET-567: give chat turns an experience recorder. Until now only
    # MechanicalAgent had one, so no amount of chat traffic filled the store
    # the memory tier reads from.
    init_chat_experience_recorder(
        getattr(app.state, "memory_store", None),
        getattr(app.state, "embedding_service", None),
    )

    # MET-672: HeartbeatMonitor has existed since MET-547 Phase 4 with no
    # production caller -- its docstring describes "a cron/heartbeat job" that
    # was never built, so an abandoned run sat non-terminal forever. Observed
    # live: three `awaiting_approval` runs left behind by a client killed
    # mid-turn, still listed as pending long afterwards.
    from api_gateway.chat.tool_approvals import get_approval_store
    from orchestrator.harness.heartbeat import RunReaper

    app.state.run_reaper = RunReaper(get_approval_store())
    app.state.run_reaper.start()

    init_project_backend(project_backend)
    logger.info(
        "project_backend_selected",
        backend=(
            "postgres" if type(project_backend).__name__ == "PgProjectBackend" else "in_memory"
        ),
    )

    # Wire the active bridge and twin into chat routes and projects routes
    init_mcp_bridge(active_bridge)
    init_twin(twin)
    # Production-harness audit follow-up: give the chat harness loop the
    # gateway's real MetricsCollector (was previously never wired at all).
    init_metrics(_collector)
    # Production-harness audit follow-up: the /v1/runs store was always
    # process-local despite a real SQLite ledger existing for exactly this
    # ("persistence lands in Phase 4" — never actually connected). Disabled
    # via METAFORGE_RUNS_LEDGER_DISABLE for tests/environments that don't
    # want a file touched.
    if (os.environ.get("METAFORGE_RUNS_LEDGER_DISABLE", "").strip().lower()) not in (
        "1",
        "true",
        "on",
        "yes",
    ):
        from api_gateway.runs.routes import init_run_ledger
        from orchestrator.harness.ledger import SqliteRunLedger, default_ledger_path

        init_run_ledger(SqliteRunLedger(str(default_ledger_path())))
    # FORGE-89: same gap as above but for the chat tool-approval queue
    # (api_gateway/chat/tool_approvals.py), which had zero durability at
    # all — an in-flight approval record vanished on every gateway restart
    # with no trace it ever existed. Shares the runs-ledger disable flag
    # since both are the same "skip touching a file" concern.
    if (os.environ.get("METAFORGE_RUNS_LEDGER_DISABLE", "").strip().lower()) not in (
        "1",
        "true",
        "on",
        "yes",
    ):
        from api_gateway.chat.tool_approvals import init_approval_ledger
        from orchestrator.harness.ledger import SqliteRunLedger, default_tool_approvals_ledger_path

        init_approval_ledger(SqliteRunLedger(str(default_tool_approvals_ledger_path())))
    # MET-566: chat-turn context assembly (knowledge fragments with
    # attribution/staleness/conflicts). No-op when LightRAG isn't configured.
    init_context_assembler(
        twin,
        getattr(app.state, "knowledge_service", None),
        collector=_collector,
    )
    init_projects_twin(twin)
    init_twin_viewer(twin)
    init_bom_twin(twin)
    init_simulation_twin(twin)
    init_hierarchy_twin(twin)
    # FORGE-266: the dashboard's "Replace placeholder with part" action
    # reuses the SAME bound callable wired into bootstrap_tool_registry
    # above, so recording isn't duplicated between the MCP tool and the
    # REST route.
    init_hierarchy_geometry_linker(hierarchy_geometry_linker_fn)
    init_requirements_twin(twin)
    init_design_loop_twin(twin)
    init_promotion_twin(twin)
    init_features_twin(twin)
    init_decisions_twin(twin)
    init_trade_study_twin(twin)
    # FORGE-262: the dashboard's "Select concept"/"+ add option" actions
    # reuse the SAME bound callables wired into bootstrap_tool_registry
    # above, so recording isn't duplicated between the MCP tools and the
    # REST routes.
    init_concept_selector(concept_selector_fn)
    init_ts_entity_recorder(engineering_entity_recorder_fn)
    # FORGE-265: the dashboard's BOM-page "Select component" action reuses
    # the SAME bound callable wired into bootstrap_tool_registry above, so
    # recording isn't duplicated between the MCP tool and the REST route.
    init_component_selector(component_selector_fn)
    # FORGE-287: the dashboard's "start closed design loop" action reuses
    # the SAME bound optimizer callable wired into bootstrap_tool_registry
    # above (design_loop_starter_fn), so Evidence/Decision recording isn't
    # duplicated between the MCP tool and the REST route.
    init_design_loop_starter(design_loop_starter_fn)
    # Follow-up to MET-740/747: the dashboard's human-approval action for a
    # design_sketch work product (twin.commit_design_sketch is the agent
    # side of this same gate, wired above via bootstrap_tool_registry).
    init_design_sketch_approver(make_design_sketch_approver(twin))

    # MET-197 has been in the tree since 2026-03-08 and nothing ever
    # constructed the publisher: `KAFKA_BOOTSTRAP_SERVERS` was passed to the
    # container and read by zero Python code, so the broker ran with **zero
    # topics ever created** while the in-process bus carried everything. That
    # made both deposit paths (WORK_PRODUCT_CREATED -> knowledge,
    # AGENT_TASK_* -> experiences) unreplayable, and it is why they have to be
    # best-effort: with no durable transport the ingest runs synchronously
    # inside the request, so a slow embedder must not be allowed to fail the
    # write it was triggered by.
    #
    # The Kafka bus is additive, not a swap: it still dispatches in-process to
    # every subscriber AND persists to the topic, so replay becomes possible
    # without needing a separate consumer process first.
    _kafka_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    _knowledge_service = getattr(app.state, "knowledge_service", None)
    kafka_publisher: Any = None
    event_bus = None
    if _kafka_servers:
        try:
            from orchestrator.event_bus.subscribers import create_kafka_bus

            event_bus, kafka_publisher = create_kafka_bus(
                bootstrap_servers=_kafka_servers,
                workflow_engine=workflow_engine,
                knowledge_service=_knowledge_service,
                collector=_collector,
            )
            await kafka_publisher.start()
            # `start()` never raises -- a missing SDK or an unreachable broker
            # degrades internally -- so "did it actually attach a producer?"
            # has to be asked explicitly. Caught live: without this check the
            # gateway logged `event_bus_kafka_initialized` while `aiokafka`
            # was absent from the image and every event was being dropped,
            # which is the same false-positive that let this tier stay dark
            # for six months.
            if not kafka_publisher.started:
                raise RuntimeError(
                    "Kafka producer did not start (missing aiokafka, or broker "
                    "unreachable) -- see the kafka_producer_start_failed log line"
                )
            logger.info("event_bus_kafka_initialized", bootstrap_servers=_kafka_servers)
        except Exception as exc:
            # A broker that is down must not take the gateway with it -- the
            # in-process bus is a complete implementation, just not durable.
            logger.warning(
                "event_bus_kafka_failed",
                bootstrap_servers=_kafka_servers,
                error=str(exc),
                hint="falling back to the in-process bus; events will not be replayable",
            )
            if kafka_publisher is not None:
                await kafka_publisher.stop()
            kafka_publisher = None
            event_bus = None

    if event_bus is None:
        event_bus = create_default_bus(
            workflow_engine,
            collector=_collector,
            knowledge_service=_knowledge_service,
        )
        logger.info("event_bus_in_process_initialized", kafka_configured=bool(_kafka_servers))
    app.state.kafka_publisher = kafka_publisher

    # MET-567: let the twin recorders publish WORK_PRODUCT_CREATED onto this
    # bus. ``KnowledgeConsumer`` has subscribed to that event since MET-307
    # but nothing ever published one, so a recorded design decision never
    # became searchable knowledge.
    from api_gateway.twin.work_product_events import init_work_product_events

    init_work_product_events(event_bus)

    # MET-453: subscribe the ExperienceConsumer so AGENT_TASK_* events
    # actually flow into the experience store (the rest of the memory
    # pipeline — retrieval, consolidation — is inert without this). Guarded
    # on the memory store + embedding service the boot wired earlier.
    _memory_store = getattr(app.state, "memory_store", None)
    _embedding_service = getattr(app.state, "embedding_service", None)
    if _memory_store is not None and _embedding_service is not None:
        try:
            from digital_twin.memory.consumer import ExperienceConsumer

            event_bus.subscribe(ExperienceConsumer(_memory_store, _embedding_service))
            logger.info("experience_consumer_subscribed")
        except Exception as exc:
            logger.warning("experience_consumer_subscribe_failed", error=str(exc))

    # Register all workflow definitions
    for defn in ACTION_WORKFLOWS.values():
        await workflow_engine.register_workflow(defn)

    # Create agents — use active bridge for tool access (MET-306).
    # Defaults to RegistryMcpBridge in-process; switches to external MCP
    # server when METAFORGE_MCP_BRIDGE=http|stdio is set.
    #
    # MET-454-followup: inject an ExperienceRecorder when both the
    # memory store and embedding service are available. Each agent task
    # writes one ``agent_experiences`` row so
    # ``memory.retrieve_similar_experience`` has traffic to learn from.
    # When either dependency is absent (in-memory dev, unit tests), the
    # agent runs silent — the Protocol injection is optional.
    experience_recorder: Any = None
    memory_store = getattr(app.state, "memory_store", None)
    embedding_service = getattr(app.state, "embedding_service", None)
    if memory_store is not None and embedding_service is not None:
        try:
            from digital_twin.memory.experience_recorder import MemoryExperienceRecorder

            experience_recorder = MemoryExperienceRecorder(
                store=memory_store,
                embeddings=embedding_service,
            )
            logger.info("agent_experience_recorder_wired", agent="mechanical")
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_experience_recorder_wiring_failed", error=str(exc))

    mech_agent = MechanicalAgent(
        twin=twin,
        mcp=active_bridge,
        experience_recorder=experience_recorder,
    )
    ee_agent = ElectronicsAgent(twin=twin, mcp=active_bridge)

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

    # MET-433: late-bind the twin into the knowledge service so
    # ``knowledge.extract`` can resolve MPN → current Datasheet. The
    # knowledge service is initialised in ``_init_knowledge_store``
    # which runs *before* the twin exists in the lifespan order, so
    # the binding has to happen here.
    knowledge_service = getattr(app.state, "knowledge_service", None)
    if knowledge_service is not None:
        set_twin = getattr(knowledge_service, "set_twin", None)
        if set_twin is not None:
            set_twin(twin)
            logger.info("knowledge_service_twin_late_bound")

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

    # MET-710: the check above is registered ONLY when the twin is already
    # Neo4j-backed -- so the one signal that would reveal a degraded twin
    # disappears in exactly the case it is needed. A boot DNS race put this
    # gateway on the in-memory backend for 43h, ignoring 623 persisted nodes,
    # while /health reported "healthy" with no neo4j component at all.
    # This component is always registered and reports the *intent* mismatch:
    # configured for Neo4j, running on memory.
    from api_gateway.health import ComponentHealth, DependencyStatus, get_health_checker

    _twin_backend = twin_backend
    _neo4j_configured = (
        bool(neo4j_uri) or os.environ.get("METAFORGE_GRAPH_BACKEND", "").lower() == "neo4j"
    )

    async def _twin_backend_health() -> ComponentHealth:
        if _twin_backend != "in_memory":
            return ComponentHealth(
                name="twin_backend",
                status=DependencyStatus.HEALTHY,
                message=f"{_twin_backend} (persistent)",
            )
        if _neo4j_configured:
            return ComponentHealth(
                name="twin_backend",
                status=DependencyStatus.DEGRADED,
                message=(
                    "configured for Neo4j but running in_memory -- twin writes "
                    "are process-local and will be lost on restart; any "
                    "persisted graph is invisible. Restart once Neo4j is "
                    "reachable, or set METAFORGE_REQUIRE_NEO4J=true to fail fast."
                ),
            )
        # No Neo4j configured at all: in-memory is the intended local-dev mode.
        return ComponentHealth(
            name="twin_backend",
            status=DependencyStatus.HEALTHY,
            message="in_memory (no Neo4j configured)",
        )

    get_health_checker().register_check("twin_backend", _twin_backend_health)
    logger.info(
        "twin_backend_health_check_registered",
        backend=_twin_backend,
        neo4j_configured=_neo4j_configured,
    )

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

    This runs after ``configure_logging()`` (observability/logging.py), which
    has already called ``ensure_console_handler()`` -- so a stdout sink exists
    regardless of what happens here. MET-646: previously this OTel handler was
    the *only* sink, so a down/unreachable collector meant every application
    log vanished (not in ``docker logs``, not in Loki, nowhere), which is
    exactly what happened on fidel-dev during the MET-642 eval.
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
    if hasattr(app.state, "tool_registry"):
        await app.state.tool_registry.close_all()
    if hasattr(app.state, "scheduler"):
        await app.state.scheduler.stop()
    # MET-567: stop the periodic consolidation pass.
    if getattr(app.state, "consolidation_scheduler", None) is not None:
        await app.state.consolidation_scheduler.stop()
    # MET-723: release only what the factory itself opened (the insight store
    # pools) -- the experience store was handed in and stays ours to close.
    if getattr(app.state, "consolidation_stack", None) is not None:
        await app.state.consolidation_stack.aclose()
    # MET-672: stop the abandoned-run reaper.
    if getattr(app.state, "run_reaper", None) is not None:
        await app.state.run_reaper.stop()
    # Flush and close the Kafka producer so buffered events are not dropped.
    if getattr(app.state, "kafka_publisher", None) is not None:
        try:
            await app.state.kafka_publisher.stop()
            logger.info("event_bus_kafka_stopped")
        except Exception as exc:
            logger.warning("event_bus_kafka_stop_failed", error=str(exc))
    # Close LightRAG service if active
    if hasattr(app.state, "knowledge_service") and app.state.knowledge_service is not None:
        try:
            await app.state.knowledge_service.close()
            logger.info("knowledge_service_closed")
        except Exception:
            pass
    # Close the component catalog store if active (MET-436)
    if (
        hasattr(app.state, "component_catalog_store")
        and app.state.component_catalog_store is not None
    ):
        try:
            await app.state.component_catalog_store.close()
            logger.info("component_catalog_store_closed")
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


def _resolve_cors_origins(explicit: list[str] | None, auth_settings: AuthSettings) -> list[str]:
    """Decide the CORS allow-list, refusing the one unsafe combination.

    The gateway historically defaulted to ``["*"]`` with ``allow_credentials``.
    That was inert while nothing was authenticated — there were no credentials
    to leak. Once a bearer token exists it is a real hole, so a wildcard is
    rejected outright when auth is on.

    Precedence: an explicit argument (tests), then ``METAFORGE_CORS_ORIGINS`` as
    a comma-separated list, then the historical wildcard for local use.
    """
    if explicit is not None:
        origins = explicit
    else:
        raw = os.environ.get("METAFORGE_CORS_ORIGINS", "").strip()
        origins = [o.strip() for o in raw.split(",") if o.strip()] if raw else ["*"]

    if auth_settings.enabled and "*" in origins:
        raise AuthConfigurationError(
            "CORS is set to '*' while METAFORGE_AUTH_MODE is enabled. A wildcard "
            "origin combined with credentialed requests would let any site call this "
            "gateway with a user's token. Set METAFORGE_CORS_ORIGINS to the exact "
            "dashboard origin(s), e.g. 'https://app.metaforge.uk'."
        )
    return origins


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
    # FORGE-391: per-app state that used to live for the whole process.
    # Health checks are registered as this app wires up its backends and
    # close over its connections; the approval workflow holds pending
    # proposals. Inheriting either from a previous app meant reporting on
    # dependencies this one never configured, and listing proposals it
    # never received.
    reset_health_checker()
    from api_gateway.assistant.routes import workflow as _approval_workflow

    _approval_workflow.reset()

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

    # -- Authentication (MetaForge Cloud) ----------------------------------
    #
    # ``load_auth_settings`` raises rather than returning a downgraded result,
    # so a gateway configured for cloud auth that cannot verify tokens fails to
    # start here instead of coming up silently open.
    auth_settings = load_auth_settings()
    app.state.auth_settings = auth_settings
    set_reported_auth_mode(auth_settings.mode.value)

    origins = _resolve_cors_origins(cors_origins, auth_settings)

    # Middleware nesting is decided by call order: the LAST added is outermost.
    # Auth must sit inside CORS so that a 401 travels back out through the CORS
    # layer and arrives at the browser with its headers, as a readable error
    # rather than an opaque network failure.
    if auth_settings.enabled:
        # Imported here, not at module scope: these pull in PyJWT, which is a
        # cloud-only dependency. A local gateway must import nothing extra.
        from api_gateway.auth import AuthMiddleware, TokenVerifier

        app.state.token_verifier = TokenVerifier(auth_settings)
        app.add_middleware(AuthMiddleware, verifier=app.state.token_verifier)

    # -- CORS --------------------------------------------------------------
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
    app.include_router(runs_router)
    app.include_router(tool_approvals_router)
    app.include_router(cad_router)
    app.include_router(cad_export_router)
    app.include_router(robot_loads_router)
    app.include_router(compliance_router)
    app.include_router(twin_router)
    app.include_router(hierarchy_router)
    app.include_router(bom_router)
    app.include_router(bom_risk_router)
    app.include_router(simulation_router)
    app.include_router(constraint_router)
    app.include_router(requirements_router)
    app.include_router(design_loop_router)
    app.include_router(evals_router)
    app.include_router(promotion_router)
    app.include_router(design_flows_router)
    app.include_router(features_router)
    app.include_router(decisions_router)
    app.include_router(trade_study_router)
    app.include_router(component_selection_router)
    app.include_router(dfm_router)
    app.include_router(manufacture_router)
    app.include_router(releases_router)
    app.include_router(testplans_router)
    app.include_router(bringup_router)
    app.include_router(firmware_router)
    app.include_router(harness_estimate_router)

    # -- FastAPI auto-instrumentation (traces all routes automatically) ----
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.info("fastapi_auto_instrumented")
    except ImportError:
        pass

    app.include_router(knowledge_router)
    app.include_router(memory_router)
    app.include_router(harness_router)

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
