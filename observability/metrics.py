"""MetaForge Prometheus metrics registry and collector.

Defines metric descriptors as Pydantic models and provides a
``MetricsCollector`` that wraps OpenTelemetry meter instruments. When the
OTel SDK is not installed the collector degrades to a silent no-op so the
rest of the platform can import it unconditionally.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from pydantic import BaseModel, field_validator

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Metric definition model
# ---------------------------------------------------------------------------

_METRIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class MetricDefinition(BaseModel):
    """Declarative description of a single Prometheus-style metric."""

    name: str
    type: str  # "counter", "histogram", "gauge"
    description: str
    labels: list[str]
    unit: str = ""
    buckets: list[float] | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _METRIC_NAME_RE.match(v):
            raise ValueError(f"Metric name must be snake_case and start with a letter, got {v!r}")
        return v

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        allowed = {"counter", "histogram", "gauge"}
        if v not in allowed:
            raise ValueError(f"Metric type must be one of {allowed}, got {v!r}")
        return v


# ---------------------------------------------------------------------------
# Registry of all MetaForge metrics
# ---------------------------------------------------------------------------


class MetricsRegistry:
    """Central registry of all MetaForge Prometheus metrics."""

    # ── Gateway metrics (MET-101) ──────────────────────────────────────
    GATEWAY_REQUEST_TOTAL = MetricDefinition(
        name="metaforge_gateway_request_total",
        type="counter",
        description="Total HTTP requests to the gateway",
        labels=["method", "endpoint", "status_code"],
    )
    GATEWAY_REQUEST_DURATION = MetricDefinition(
        name="metaforge_gateway_request_duration_seconds",
        type="histogram",
        description="HTTP request duration in seconds",
        labels=["method", "endpoint"],
        unit="s",
        buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
    )
    GATEWAY_WEBSOCKET_CONNECTIONS = MetricDefinition(
        name="metaforge_gateway_websocket_connections",
        type="gauge",
        description="Active WebSocket connections",
        labels=["state"],
    )
    GATEWAY_ACTIVE_SESSIONS = MetricDefinition(
        name="metaforge_gateway_active_sessions",
        type="gauge",
        description="Active user sessions",
        labels=["status"],
    )
    # MetaForge Cloud. ``outcome`` is one of: allowed, public, rejected,
    # unavailable. Watch "unavailable" in particular — it means the identity
    # provider could not be reached, which looks like an auth outage to users
    # but is not a credential problem, and it is the signal that distinguishes
    # the two.
    GATEWAY_AUTH_TOTAL = MetricDefinition(
        name="metaforge_gateway_auth_total",
        type="counter",
        description="Gateway authentication decisions",
        labels=["mode", "outcome"],
    )

    # ── Agent metrics (MET-107) ────────────────────────────────────────
    AGENT_EXECUTION_DURATION = MetricDefinition(
        name="metaforge_agent_execution_duration_seconds",
        type="histogram",
        description="Agent execution duration in seconds",
        labels=["agent_code", "status"],
        unit="s",
        buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120],
    )
    AGENT_EXECUTION_TOTAL = MetricDefinition(
        name="metaforge_agent_execution_total",
        type="counter",
        description="Total agent executions",
        labels=["agent_code", "status"],
    )
    AGENT_LLM_TOKENS_TOTAL = MetricDefinition(
        name="metaforge_agent_llm_tokens_total",
        type="counter",
        description="Total LLM tokens consumed",
        labels=["agent_code", "llm_provider", "llm_model", "token_type"],
    )
    AGENT_LLM_COST_TOTAL = MetricDefinition(
        name="metaforge_agent_llm_cost_usd_total",
        type="counter",
        description="Total LLM cost in USD",
        labels=["agent_code", "llm_provider", "llm_model"],
        unit="usd",
    )
    AGENT_LLM_REQUEST_DURATION = MetricDefinition(
        name="metaforge_agent_llm_request_duration_seconds",
        type="histogram",
        description="LLM request duration in seconds",
        labels=["agent_code", "llm_provider", "llm_model"],
        unit="s",
    )

    # ── Skill metrics (MET-107) ────────────────────────────────────────
    SKILL_EXECUTION_DURATION = MetricDefinition(
        name="metaforge_skill_execution_duration_seconds",
        type="histogram",
        description="Skill execution duration in seconds",
        labels=["skill_name", "domain"],
        unit="s",
    )
    SKILL_EXECUTION_TOTAL = MetricDefinition(
        name="metaforge_skill_execution_total",
        type="counter",
        description="Total skill executions",
        labels=["skill_name", "domain", "status"],
    )

    # ── Kafka metrics (MET-108) ────────────────────────────────────────
    KAFKA_CONSUMER_LAG = MetricDefinition(
        name="metaforge_kafka_consumer_lag",
        type="gauge",
        description="Kafka consumer lag by partition",
        labels=["consumer_group", "topic", "partition"],
    )
    KAFKA_MESSAGES_PRODUCED = MetricDefinition(
        name="metaforge_kafka_messages_produced_total",
        type="counter",
        description="Total messages produced to Kafka",
        labels=["topic"],
    )
    KAFKA_MESSAGES_CONSUMED = MetricDefinition(
        name="metaforge_kafka_messages_consumed_total",
        type="counter",
        description="Total messages consumed from Kafka",
        labels=["topic", "consumer_group"],
    )
    KAFKA_DEAD_LETTERS = MetricDefinition(
        name="metaforge_kafka_dead_letters_total",
        type="counter",
        description="Total dead letter messages",
        labels=["topic", "consumer_group"],
    )
    KAFKA_REBALANCE_TOTAL = MetricDefinition(
        name="metaforge_kafka_rebalance_total",
        type="counter",
        description="Total Kafka consumer rebalances",
        labels=["consumer_group"],
    )

    # ── Data store metrics (MET-112) ──────────────────────────────────
    NEO4J_QUERY_DURATION = MetricDefinition(
        name="metaforge_neo4j_query_duration_seconds",
        type="histogram",
        description="Neo4j query duration in seconds",
        labels=["operation", "node_type"],
        unit="s",
        buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1],
    )
    NEO4J_ACTIVE_CONNECTIONS = MetricDefinition(
        name="metaforge_neo4j_active_connections",
        type="gauge",
        description="Active Neo4j database connections",
        labels=[],
    )
    NEO4J_QUERY_TOTAL = MetricDefinition(
        name="metaforge_neo4j_query_total",
        type="counter",
        description="Total Neo4j queries",
        labels=["operation", "status"],
    )
    PGVECTOR_SEARCH_DURATION = MetricDefinition(
        name="metaforge_pgvector_search_duration_seconds",
        type="histogram",
        description="pgvector search duration in seconds",
        labels=["knowledge_type"],
        unit="s",
        buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1],
    )
    PGVECTOR_SEARCH_TOTAL = MetricDefinition(
        name="metaforge_pgvector_search_total",
        type="counter",
        description="Total pgvector searches",
        labels=["knowledge_type", "status"],
    )
    MINIO_OPERATION_DURATION = MetricDefinition(
        name="metaforge_minio_operation_duration_seconds",
        type="histogram",
        description="MinIO operation duration in seconds",
        labels=["operation"],
        unit="s",
        buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
    )
    MINIO_OPERATION_TOTAL = MetricDefinition(
        name="metaforge_minio_operation_total",
        type="counter",
        description="Total MinIO operations",
        labels=["operation", "status"],
    )

    # ── Twin graph hygiene metrics (MET-439) ─────────────────────────
    TWIN_ORPHANS = MetricDefinition(
        name="metaforge_twin_orphans",
        type="gauge",
        description=(
            "Count of dangling dependent nodes (Constraint / BOMItem / "
            "DesignElement / Component) with zero edges. Set by "
            "TwinAPI.find_orphans() on every scan."
        ),
        labels=["kind"],
    )
    #: One sample per definition write that went through item identity
    #: (FORGE-523). ``outcome`` is ``created``, ``drafted`` (inside a run,
    #: FORGE-525) or ``failed``; ``resolved_by``
    #: says how the item was found (``new``, ``name``, ``item_key``,
    #: ``supersedes``, or one of those with ``_adopted`` when an old
    #: SUPERSEDES chain was folded in). A ``failed`` sample means a node was
    #: written but its item link was not, so it looks like a fresh sibling.
    TWIN_ITEM_REVISION_TOTAL = MetricDefinition(
        name="metaforge_twin_item_revision_total",
        type="counter",
        description="Item revisions written per definition type, by outcome and resolution",
        labels=["item_type", "outcome", "resolved_by"],
    )
    #: One sample per run change-set decision (FORGE-525). ``outcome`` is
    #: ``committed`` (a gate approval moved the heads), ``refused`` (the
    #: approval found heads that moved since the run drafted them, so nothing
    #: was committed), ``rejected`` / ``abandoned`` (drafts closed without a
    #: head move) or ``failed`` (a commit broke part-way and was restored).
    TWIN_CHANGE_SET_TOTAL = MetricDefinition(
        name="metaforge_twin_change_set_total",
        type="counter",
        description="Run change-set commits and closes, by outcome",
        labels=["outcome"],
    )
    #: Items and revisions created by the legacy-node migration (FORGE-529),
    #: per definition type. ``kind`` is ``item`` (a new Item node) or
    #: ``revision`` (a legacy node linked as a revision); ``outcome`` is
    #: ``created`` or ``failed``.
    TWIN_ITEM_MIGRATION_TOTAL = MetricDefinition(
        name="metaforge_twin_item_migration_total",
        type="counter",
        description="Items and revisions created by the item migration, per type",
        labels=["item_type", "kind", "outcome"],
    )
    #: One sample per definition write inside a design-flow phase that
    #: declares slots of its type (FORGE-524). ``outcome`` is ``slot`` (the
    #: write landed on its declared item) or ``undeclared`` (it matched none
    #: and became a new item, listed for the gate reviewer).
    FLOW_ITEM_SLOT_TOTAL = MetricDefinition(
        name="metaforge_flow_item_slot_total",
        type="counter",
        description="Design-flow definition writes by slot outcome (slot or undeclared)",
        labels=["item_type", "outcome"],
    )
    #: One sample per requirement-like value found in a prd write (FORGE-528).
    #: ``outcome`` is ``matched`` (the current constraint set has it) or
    #: ``stray`` (it does not, and the write returned a warning naming it).
    TWIN_PRD_REQUIREMENT_VALUE_TOTAL = MetricDefinition(
        name="metaforge_twin_prd_requirement_value_total",
        type="counter",
        description="Requirement values found in prd prose, matched or stray",
        labels=["outcome"],
    )
    TWIN_RECORD_STALENESS_TOTAL = MetricDefinition(
        name="metaforge_twin_record_staleness_total",
        type="counter",
        description=(
            "Records (simulation results, decisions, evidence) whose staleness changed, "
            "by record type and status (FORGE-527)"
        ),
        labels=["record_type", "status"],
    )
    #: FORGE-526: baselines recorded, by ``source`` (``gate`` approval or
    #: ``manual`` twin.create_baseline) and ``outcome`` (``created``,
    #: ``existing`` for an idempotent repeat, ``empty`` when the project had
    #: no current item to pin, ``failed``).
    TWIN_BASELINE_TOTAL = MetricDefinition(
        name="metaforge_twin_baseline_total",
        type="counter",
        description="Baselines recorded per source and outcome",
        labels=["source", "outcome"],
    )

    # ── Simulation result field metrics (FORGE-532) ──────────────────
    #: One per solved run: did the 3D field payload build (``built``), build
    #: only after decimation (``decimated``), or fail (``failed``)? A failure
    #: never fails the solve, so this counter is the only place it shows.
    SIM_FIELD_PAYLOAD_TOTAL = MetricDefinition(
        name="metaforge_sim_field_payload_total",
        type="counter",
        description="CalculiX result field payload builds, by analysis type and outcome",
        labels=["analysis_type", "outcome"],
    )
    SIM_FIELD_PAYLOAD_BYTES = MetricDefinition(
        name="metaforge_sim_field_payload_bytes",
        type="histogram",
        description="Compressed size of a CalculiX result field payload",
        labels=["analysis_type"],
        unit="By",
        buckets=[10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 1_500_000],
    )
    #: Recorder side: a simulation_result whose field blob did (``stored``)
    #: or did not (``failed``) reach MinIO, or was rejected (``invalid``).
    SIM_FIELD_STORE_TOTAL = MetricDefinition(
        name="metaforge_sim_field_store_total",
        type="counter",
        description="Simulation result field blobs stored to MinIO, by outcome",
        labels=["outcome"],
    )

    # ── Telemetry / MQTT metrics (MET-119) ───────────────────────────
    MQTT_MESSAGES_RECEIVED_TOTAL = MetricDefinition(
        name="metaforge_mqtt_messages_received_total",
        type="counter",
        description="Total MQTT messages received from devices",
        labels=["device_id", "topic"],
    )
    TELEMETRY_ROUTER_DURATION = MetricDefinition(
        name="metaforge_telemetry_router_duration_seconds",
        type="histogram",
        description="Telemetry routing duration in seconds",
        labels=["device_type"],
        unit="s",
        buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1],
    )
    TELEMETRY_INGESTION_TOTAL = MetricDefinition(
        name="metaforge_telemetry_ingestion_total",
        type="counter",
        description="Total telemetry ingestion attempts",
        labels=["status"],
    )
    TELEMETRY_INGESTION_ERRORS_TOTAL = MetricDefinition(
        name="metaforge_telemetry_ingestion_errors_total",
        type="counter",
        description="Total telemetry ingestion errors",
        labels=["error_type"],
    )
    TELEMETRY_LAG_SECONDS = MetricDefinition(
        name="metaforge_telemetry_lag_seconds",
        type="gauge",
        description="Telemetry processing lag in seconds per device",
        labels=["device_id"],
        unit="s",
    )

    # ── Constraint & Policy metrics (MET-113) ─────────────────────────
    CONSTRAINT_EVALUATION_TOTAL = MetricDefinition(
        name="metaforge_constraint_evaluation_total",
        type="counter",
        description="Total constraint evaluations",
        labels=["domain", "result"],
    )
    CONSTRAINT_EVALUATION_DURATION = MetricDefinition(
        name="metaforge_constraint_evaluation_duration_seconds",
        type="histogram",
        description="Constraint evaluation duration in seconds",
        labels=["domain"],
        unit="s",
    )
    OPA_DECISION_TOTAL = MetricDefinition(
        name="metaforge_opa_decision_total",
        type="counter",
        description="Total OPA policy decisions",
        labels=["policy", "result"],
    )
    OSCILLATION_DETECTED_TOTAL = MetricDefinition(
        name="metaforge_oscillation_detected_total",
        type="counter",
        description="Total oscillation detections in the constraint graph",
        labels=["node_type"],
    )

    # ── Retrieval & context-assembly metrics (MET-326) ─────────────────
    #
    # Wire-and-aggregate inputs come from
    # ``digital_twin.context.retrieval_metrics`` (precision / recall /
    # MRR / NDCG) and the ``context_truncated`` structlog event emitted
    # in MET-317.
    RETRIEVAL_PRECISION_AT_K = MetricDefinition(
        name="metaforge_retrieval_precision_at_k",
        type="histogram",
        description="precision@k for a knowledge retrieval (0=miss, 1=all relevant)",
        labels=["agent_id", "k"],
        # Buckets favour the high end — we expect precision in 0.4–1.0
        # for tuned queries.
        buckets=[0.0, 0.1, 0.25, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    )
    RETRIEVAL_RECALL_AT_K = MetricDefinition(
        name="metaforge_retrieval_recall_at_k",
        type="histogram",
        description="recall@k for a knowledge retrieval",
        labels=["agent_id", "k"],
        buckets=[0.0, 0.1, 0.25, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    )
    RETRIEVAL_MRR = MetricDefinition(
        name="metaforge_retrieval_mrr",
        type="histogram",
        description="Mean reciprocal rank of the first relevant hit",
        labels=["agent_id"],
        buckets=[0.0, 0.1, 0.2, 0.33, 0.5, 0.67, 1.0],
    )
    RETRIEVAL_NDCG_AT_K = MetricDefinition(
        name="metaforge_retrieval_ndcg_at_k",
        type="histogram",
        description="Normalised DCG at k for a knowledge retrieval",
        labels=["agent_id", "k"],
        buckets=[0.0, 0.1, 0.25, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    )
    CONTEXT_TRUNCATED_TOTAL = MetricDefinition(
        name="metaforge_context_truncated_total",
        type="counter",
        description=(
            "Times a context fragment was dropped by the assembler's "
            "token-budget pass (MET-317), labeled by source kind"
        ),
        labels=["agent_id", "source_kind"],
    )
    #: One sample per project brief composed (FORGE-530). ``mode`` is
    #: ``baseline`` (one line per current item) or ``legacy`` (newest work
    #: products, for a project with no items); ``capped`` is ``true`` when
    #: the brief hit ``METAFORGE_BRIEF_CHAR_LIMIT`` and was shortened.
    PROJECT_BRIEF_TOTAL = MetricDefinition(
        name="metaforge_project_brief_total",
        type="counter",
        description="Project briefs composed, by mode and whether the char cap cut them",
        labels=["mode", "capped"],
    )
    PROJECT_BRIEF_CHARS = MetricDefinition(
        name="metaforge_project_brief_chars",
        type="histogram",
        description="Characters in a composed project brief (before the cap), by mode",
        labels=["mode"],
        buckets=[500, 1000, 2000, 4000, 6000, 8000, 10000, 15000, 25000, 50000],
    )

    # ── Knowledge service latency (MET-401 / L1-A7) ───────────────────
    #
    # HP-RETR-08 SLO: p95 < 200 ms on a 1k-doc corpus. This histogram is
    # the gating signal — wall-clock probes were the previous proxy.
    # ``top_k_bucket`` is "small" (top_k <= 5) or "large" (top_k > 5);
    # the SLO is evaluated at p95 across the whole metric, but the
    # bucket label lets dashboards split per query shape.
    KNOWLEDGE_SEARCH_DURATION = MetricDefinition(
        name="metaforge_knowledge_search_duration_seconds",
        type="histogram",
        description="LightRAG knowledge search end-to-end duration in seconds",
        labels=["top_k_bucket"],
        unit="s",
        buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0],
    )

    KB_STORAGE_INIT_TOTAL = MetricDefinition(
        name="metaforge_kb_storage_init_total",
        type="counter",
        description=(
            "Knowledge-base object storage set-up at gateway start by outcome: "
            "ready, failed, skipped (FORGE-594)"
        ),
        labels=["outcome"],
    )

    # ── Consolidation / agent-memory metrics (MET-454 / MET-455) ───────
    CONSOLIDATION_PASS_DURATION = MetricDefinition(
        name="metaforge_consolidation_pass_duration_seconds",
        type="histogram",
        description="Consolidation pass (fetch→group→synth→validate→write) duration",
        labels=["mode"],
        unit="s",
        buckets=[0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60],
    )
    CONSOLIDATION_PASS_TOTAL = MetricDefinition(
        name="metaforge_consolidation_pass_total",
        type="counter",
        description="Total consolidation passes by mode",
        labels=["mode"],
    )
    CONSOLIDATION_INSIGHTS_ACCEPTED_TOTAL = MetricDefinition(
        name="metaforge_consolidation_insights_accepted_total",
        type="counter",
        description="Insights synthesized + validated + written, by mode",
        labels=["mode"],
    )
    CONSOLIDATION_INSIGHTS_REJECTED_TOTAL = MetricDefinition(
        name="metaforge_consolidation_insights_rejected_total",
        type="counter",
        description="Insights dropped during a pass (synthesis/validation), by mode",
        labels=["mode"],
    )
    CONSOLIDATION_CONTRADICTIONS_TOTAL = MetricDefinition(
        name="metaforge_consolidation_contradictions_total",
        type="counter",
        description="Synthesized insights flagged as contradicting the existing corpus",
        labels=[],
    )
    CONSOLIDATION_STALE_MARKED_TOTAL = MetricDefinition(
        name="metaforge_consolidation_stale_marked_total",
        type="counter",
        description="Insights durably marked STALE_WARN by a JANITOR pass",
        labels=[],
    )

    # ── Design-flow engine metrics (FORGE-401) ─────────────────────────
    DESIGN_FLOW_ENGINE_UNAVAILABLE = MetricDefinition(
        name="metaforge_design_flow_engine_unavailable_total",
        type="counter",
        description=("Design-flow run starts refused because the workflow engine was unreachable"),
        labels=["target"],
    )
    # FORGE-469: a human approval gate asked to decide with no Temporal
    # runtime to wait on. It used to approve on the spot, so a missing
    # dependency silently waved every gate through. It now refuses, and
    # this counts the refusals so the broken worker is visible.
    APPROVAL_GATE_NO_RUNTIME_TOTAL = MetricDefinition(
        name="metaforge_approval_gate_no_runtime_total",
        type="counter",
        description="Approval gates refused because no Temporal runtime was available",
        labels=["required_role"],
    )
    # FORGE-470: an IterationController gate decided with no approval
    # workflow behind it. outcome="blocked" is a converged loop that could
    # not be approved because nothing can ask a human (it used to approve
    # silently); outcome="auto_approved" is an explicit auto_approve config
    # merging without review, counted so automatic approvals are visible.
    ITERATION_GATE_UNATTENDED_TOTAL = MetricDefinition(
        name="metaforge_iteration_gate_unattended_total",
        type="counter",
        description="Iteration gates decided without an approval workflow, by outcome",
        labels=["outcome", "agent_code"],
    )
    DESIGN_FLOW_RUN_STARTED = MetricDefinition(
        name="metaforge_design_flow_run_started_total",
        type="counter",
        description="Design-flow runs started, by engine and flow template",
        labels=["engine", "flow"],
    )
    DESIGN_FLOW_GATE_TOTAL = MetricDefinition(
        name="metaforge_design_flow_gate_total",
        type="counter",
        description="Design-flow gate outcomes",
        labels=["gate", "outcome"],
    )
    DESIGN_FLOW_GATE_ANNOUNCE_TOTAL = MetricDefinition(
        name="metaforge_design_flow_gate_announce_total",
        type="counter",
        description="Design-flow gates announced to approvers, by outcome (FORGE-489)",
        labels=["outcome"],
    )
    CLIENT_TASK_TOTAL = MetricDefinition(
        name="metaforge_client_task_total",
        type="counter",
        description=(
            "Client-mode phase tasks by event: opened, claimed, submitted, cancelled (FORGE-581)"
        ),
        labels=["event"],
    )

    # ── Chat harness loop metrics (production-harness audit follow-up) ─
    HARNESS_TURN_DURATION = MetricDefinition(
        name="metaforge_harness_turn_duration_seconds",
        type="histogram",
        description="Full chat-harness tool-loop duration for one turn",
        labels=["loop_kind", "stop_reason"],
        unit="s",
        buckets=[0.5, 1, 2.5, 5, 10, 30, 60, 120, 300],
    )
    HARNESS_TURN_TOTAL = MetricDefinition(
        name="metaforge_harness_turn_total",
        type="counter",
        description="Total chat-harness turns by loop kind and how they stopped",
        labels=["loop_kind", "stop_reason"],
    )
    HARNESS_TOOL_CALL_DURATION = MetricDefinition(
        name="metaforge_harness_tool_call_duration_seconds",
        type="histogram",
        description="Duration of one harness tool call",
        labels=["tool_name", "status"],
        unit="s",
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30],
    )
    HARNESS_TOOL_CALL_TOTAL = MetricDefinition(
        name="metaforge_harness_tool_call_total",
        type="counter",
        description="Total harness tool calls by tool and outcome",
        labels=["tool_name", "status"],
    )
    DESIGN_FLOW_TOOL_REFUSAL_TOTAL = MetricDefinition(
        name="metaforge_design_flow_tool_refusal_total",
        type="counter",
        description=(
            "Tool calls the guardrail refused for the design-flow service caller "
            "(FORGE-492); source is 'model' (returned as an observation) or "
            "'handler' (a scripted step was refused and the phase fell back to the model)"
        ),
        labels=["tool_name", "source"],
    )
    # FORGE-98 / FORGE-520: a chat reply flagged as claiming work the turn did
    # not do. ``kind`` is no_tool_call (no successful tool call at all),
    # twin_write (claims a save/commit with no successful twin write) or
    # node_id (quotes a node id no tool returned this turn).
    CHAT_UNGROUNDED_CLAIM_TOTAL = MetricDefinition(
        name="metaforge_chat_ungrounded_claim_total",
        type="counter",
        description="Chat replies flagged as ungrounded, by kind of unsupported claim",
        labels=["kind"],
    )
    HARNESS_PROVIDER_CALL_DURATION = MetricDefinition(
        name="metaforge_harness_provider_call_duration_seconds",
        type="histogram",
        description="Duration of one harness model-provider call attempt",
        labels=["provider", "model", "role"],
        unit="s",
        buckets=[0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60],
    )
    # FORGE-468: a role served by something other than its primary provider.
    # The pipeline's fallback used to be silent, so a primary that failed on
    # every call looked like a healthy chain.
    HARNESS_PROVIDER_FALLBACK_TOTAL = MetricDefinition(
        name="metaforge_harness_provider_fallback_total",
        type="counter",
        description="Harness model calls served by a fallback instead of the primary provider",
        labels=["primary", "fallback", "role", "reason"],
    )
    # FORGE-476: token and cost accounting. ``kind`` is prompt, completion or
    # cached_input; cost is only counted when the model has a known price, so
    # a model missing from the price table adds tokens but no dollars.
    LLM_TOKENS_TOTAL = MetricDefinition(
        name="metaforge_llm_tokens_total",
        type="counter",
        description="LLM tokens consumed by role, provider, model and kind",
        labels=["role", "provider", "model", "kind"],
    )
    LLM_COST_USD_TOTAL = MetricDefinition(
        name="metaforge_llm_cost_usd_total",
        type="counter",
        description="LLM spend in USD by role, provider and model (priced models only)",
        labels=["role", "provider", "model"],
        unit="USD",
    )
    LLM_CALLS_TOTAL = MetricDefinition(
        name="metaforge_llm_calls_total",
        type="counter",
        description="LLM calls by role, provider, model and whether usage was reported",
        labels=["role", "provider", "model", "usage_reported"],
    )
    # Per-run cost cannot be a label (unbounded cardinality), so the alert is
    # on a counter bumped once when a run crosses the spend threshold.
    LLM_RUN_SPEND_EXCEEDED_TOTAL = MetricDefinition(
        name="metaforge_llm_run_spend_exceeded_total",
        type="counter",
        description="Runs whose accumulated LLM spend crossed the runaway-spend threshold",
        labels=["role"],
    )
    # FORGE-466: a held approval closed because nobody is waiting for it any
    # more. ``outcome`` is timed_out or canceled; ``trigger`` is ``waiter``
    # (the side that held the call said so) or ``deadline`` (the gateway
    # expired it because the waiter never did); ``result`` is resolved,
    # already_resolved, decided or failed. A rise in trigger="deadline" means
    # waiters are dying without saying so; result="failed" means the
    # sidecar cannot reach the ledger to close what it opened.
    TOOL_APPROVAL_RESOLUTION_TOTAL = MetricDefinition(
        name="metaforge_tool_approval_resolution_total",
        type="counter",
        description="Held tool approvals closed with no answer, by outcome, trigger and result",
        labels=["outcome", "trigger", "result"],
    )

    # FORGE-780: one durable tool approval reaching an outcome: ``held``,
    # ``approved_late_honoured`` (an identical retry ran on an approval),
    # ``auto_approved`` (a project policy approved it), ``cap_held`` (a
    # policy would have, but the spend cap was reached), ``expired``.
    TOOL_APPROVAL_TOTAL = MetricDefinition(
        name="metaforge_tool_approval_total",
        type="counter",
        description="Durable tool approvals by outcome",
        labels=["outcome"],
    )

    # FORGE-490: an approval hold created where nothing can answer it (an
    # in-process store in a non-gateway process). It can only time out and
    # deny, so any non-zero count is a wiring bug.
    UNREACHABLE_APPROVAL_HOLD_TOTAL = MetricDefinition(
        name="metaforge_unreachable_approval_hold_total",
        type="counter",
        description="Approval holds created with no reachable approver, by tool",
        labels=["tool"],
    )

    # FORGE-507: one decision through the unified /v1/approvals API. ``kind``
    # is gate, tool, change, design_loop, sketch or drawing; ``surface`` is
    # dashboard, cli, agent or unknown; ``outcome`` is ok, refused (a 4xx the
    # existing rules produced) or error. No alert: refusals are a reviewer
    # answering something already closed, not a fault.
    APPROVAL_DECISION_TOTAL = MetricDefinition(
        name="metaforge_approval_decision_total",
        type="counter",
        description="Decisions made through /v1/approvals, by kind, decision, surface and outcome",
        labels=["kind", "decision", "surface", "outcome"],
    )

    # ── MCP surface (FORGE-379) ────────────────────────────────────────
    #
    # The MCP server is where every external harness meets MetaForge, and it
    # had no metrics at all -- only logs. "Which plugin tool is failing, for
    # whom" was a question you answered by reading Loki by hand.
    MCP_TOOL_CALL_TOTAL = MetricDefinition(
        name="metaforge_mcp_tool_call_total",
        type="counter",
        description="Total MCP tool calls by tool, outcome and client",
        labels=["tool_id", "status", "client"],
    )
    MCP_TOOL_CALL_DURATION = MetricDefinition(
        name="metaforge_mcp_tool_call_duration_seconds",
        type="histogram",
        description="MCP tool call duration",
        labels=["tool_id", "status"],
        unit="s",
        buckets=[0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120],
    )
    #: Separate from the call counter, and the point of this group: a
    #: rate of "errors" says something is wrong, `error_class` says what to
    #: do about it. A spike in `approval_timed_out` needs a reviewer, in
    #: `adapter_unavailable` a container, in `tool_not_found` a client
    #: that is calling a tool this build does not have.
    MCP_ORIGIN_REJECTED_TOTAL = MetricDefinition(
        name="metaforge_mcp_origin_rejected_total",
        type="counter",
        description=(
            "MCP HTTP requests refused because their Origin is not on the allow-list (FORGE-604)"
        ),
        labels=[],
    )
    TOOL_JOB_TOTAL = MetricDefinition(
        name="metaforge_tool_job_total",
        type="counter",
        description=(
            "State changes of slow tool calls run as polled jobs, by tool and status: "
            "queued, running, succeeded, failed, or interrupted (cancelled, or left "
            "running by a process that restarted) (FORGE-783)"
        ),
        labels=["tool", "status"],
    )
    MCP_ERROR_TOTAL = MetricDefinition(
        name="metaforge_mcp_error_total",
        type="counter",
        description="MCP errors by tool, class and client",
        labels=["tool_id", "error_class", "client"],
    )
    #: One sample per health probe, labelled by outcome. FORGE-332 made the
    #: probe real; this is what lets an alert fire on it rather than someone
    #: happening to run the doctor.
    #:
    #: A counter and not a gauge on purpose: ``type="gauge"`` in this
    #: registry creates an OTel *UpDownCounter*, whose ``add()`` is a delta.
    #: Writing 1 then 0 to it would climb and never come back down, so a
    #: "reachable" gauge would read as healthy forever.
    MCP_ADAPTER_PROBE_TOTAL = MetricDefinition(
        name="metaforge_mcp_adapter_probe_total",
        type="counter",
        description="Adapter health probes by adapter and outcome",
        labels=["adapter_id", "result"],
    )
    #: Is this process running the code sitting next to it (FORGE-411)?
    #:
    #: A counter rather than a gauge for the reason given above, and
    #: three-valued rather than boolean: ``unknown`` is its own result
    #: because an image with no build SHA baked in cannot answer the
    #: question, and reporting that as ``current`` is exactly the silent
    #: pass this metric exists to end.
    MCP_CODE_VERSION_CHECK_TOTAL = MetricDefinition(
        name="metaforge_mcp_code_version_check_total",
        type="counter",
        description="Code-version checks by outcome (current, stale, unknown)",
        labels=["result", "reloads"],
    )

    # ── Host watchdog (FORGE-713) ──────────────────────────────────────

    WATCHDOG_CHECKS_TOTAL = MetricDefinition(
        name="metaforge_watchdog_checks_total",
        type="counter",
        description=(
            "Watchdog check passes completed. A heartbeat: when it stops "
            "rising, nothing is watching the host (FORGE-713)."
        ),
        labels=[],
    )
    WATCHDOG_RESTARTS_TOTAL = MetricDefinition(
        name="metaforge_watchdog_restarts_total",
        type="counter",
        description=(
            "Containers the watchdog restarted or chose not to, by compose "
            "service, reason (unhealthy, twin_in_memory) and outcome "
            "(restarted, suppressed, failed) (FORGE-713)"
        ),
        labels=["service", "reason", "outcome"],
    )
    WATCHDOG_UNHEALTHY_CONTAINERS = MetricDefinition(
        name="metaforge_watchdog_unhealthy_containers",
        type="gauge",
        description="Stack containers Docker reports unhealthy at the last check (FORGE-713)",
        labels=[],
    )
    WATCHDOG_TWIN_IN_MEMORY = MetricDefinition(
        name="metaforge_watchdog_twin_in_memory",
        type="gauge",
        description=(
            "1 when the gateway's /health says the twin runs in memory although "
            "Neo4j is configured, so persisted nodes are invisible (FORGE-713)"
        ),
        labels=[],
    )
    HOST_MEMORY_AVAILABLE_BYTES = MetricDefinition(
        name="metaforge_host_memory_available_bytes",
        type="gauge",
        description="MemAvailable of the host running the stack, from /proc/meminfo (FORGE-713)",
        labels=[],
    )

    # ── Gate reviewer (FORGE-726) ──────────────────────────────────────

    GATE_REVIEW_TOTAL = MetricDefinition(
        name="metaforge_gate_review_total",
        type="counter",
        description=(
            "Reviewer agent notes on design-flow gates, by source (server, client), "
            "recommendation (approve, retry, rework, reject, none = could not review) "
            "and what the approval policy did (advised, decided, held) (FORGE-726)"
        ),
        labels=["source", "recommendation", "applied"],
    )
    GATE_REVIEW_AGREEMENT_TOTAL = MetricDefinition(
        name="metaforge_gate_review_agreement_total",
        type="counter",
        description=(
            "A person's gate decision after a reviewer recommendation, by recommendation, "
            "decision and whether they agreed: the evidence for trusting the reviewer "
            "with more (FORGE-726)"
        ),
        labels=["recommendation", "decision", "agreed"],
    )

    # ── Operation events (FORGE-785, CDE slice 1) ──────────────────────

    OPERATION_EVENT_TOTAL = MetricDefinition(
        name="metaforge_operation_event_total",
        type="counter",
        description=(
            "Operation events stored in the durable operation log and streamed to "
            "the project, by operation kind and event type (FORGE-785)"
        ),
        labels=["kind", "type"],
    )
    OPERATION_EVENT_PUBLISH_FAILURE_TOTAL = MetricDefinition(
        name="metaforge_operation_event_publish_failure_total",
        type="counter",
        description=(
            "Operation events not stored, by kind and reason: error (the store "
            "failed), out_of_order or after_terminal (refused by the contract's "
            "ordering rules) (FORGE-785)"
        ),
        labels=["kind", "reason"],
    )
    OPERATION_STREAM_REPLAY_TOTAL = MetricDefinition(
        name="metaforge_operation_stream_replay_total",
        type="counter",
        description="Project operation stream reconnects served from the durable log (FORGE-785)",
        labels=[],
    )
    OPERATION_STREAM_RESYNC_TOTAL = MetricDefinition(
        name="metaforge_operation_stream_resync_total",
        type="counter",
        description=(
            "Project operation streams told to refetch the snapshot, by reason: "
            "unknown_cursor or cursor_expired (FORGE-785)"
        ),
        labels=["reason"],
    )
    OPERATION_STREAM_ACTIVE = MetricDefinition(
        name="metaforge_operation_stream_active",
        type="gauge",
        description="Project operation streams currently connected (FORGE-785)",
        labels=[],
    )
    OPERATION_ACTIVE = MetricDefinition(
        name="metaforge_operation_active",
        type="gauge",
        description="Operations not yet in a terminal state, by kind (FORGE-785)",
        labels=["kind"],
    )
    OPERATION_EVENT_PUBLISH_LAG = MetricDefinition(
        name="metaforge_operation_event_publish_lag_seconds",
        type="histogram",
        description=(
            "Seconds from an operation event's occurred_at to its being stored and "
            "streamed by the gateway (FORGE-785)"
        ),
        labels=["kind"],
        unit="s",
        buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
    )
    OPERATION_SCHEDULING_TOTAL = MetricDefinition(
        name="metaforge_operation_scheduling_total",
        type="counter",
        description=(
            "Operation scheduling decisions, by kind, action (superseded, "
            "stale_rejected, coalesced, cancel, retry) and outcome (ok, refused, "
            "error) (FORGE-788)"
        ),
        labels=["kind", "action", "outcome"],
    )

    # ── Sketch saves from the Create stage (FORGE-789) ─────────────────

    SKETCH_SAVE_TOTAL = MetricDefinition(
        name="metaforge_sketch_save_total",
        type="counter",
        description=(
            "Saves of a person's freehand sketch as a sketch_strokes revision, by "
            "outcome: saved, duplicate (a retried save id) and failed (FORGE-789)"
        ),
        labels=["outcome"],
    )

    # ── Sketch planes (FORGE-790) ──────────────────────────────────────

    # ── Exact CAD, validity and STEP (CDE slice 6) ─────────────────────

    EXACT_CAD_BUILD_TOTAL = MetricDefinition(
        name="metaforge_exact_cad_build_total",
        type="counter",
        description=(
            "Exact CAD builds by outcome (validated, already_validated, not_validated, "
            "failed, not_evaluated) (CDE slice 6)"
        ),
        labels=["outcome"],
    )
    GEOMETRY_VALIDITY_TOTAL = MetricDefinition(
        name="metaforge_geometry_validity_total",
        type="counter",
        description="B-Rep validity verdicts (pass, warn, fail, not_evaluated) (CDE slice 6)",
        labels=["outcome"],
    )
    STEP_EXPORT_TOTAL = MetricDefinition(
        name="metaforge_step_export_total",
        type="counter",
        description=(
            "STEP export events (cache_hit, cache_miss, ready, failed, stale) (CDE slice 6)"
        ),
        labels=["event"],
    )
    STEP_ROUND_TRIP_TOTAL = MetricDefinition(
        name="metaforge_step_round_trip_total",
        type="counter",
        description="STEP re-import round-trip verdicts (pass, fail, not_evaluated) (CDE slice 6)",
        labels=["outcome"],
    )
    CAD_ARTIFACT_DURATION = MetricDefinition(
        name="metaforge_cad_artifact_duration_seconds",
        type="histogram",
        description=(
            "Exact CAD stage durations: exact_build, validate, exact_total, step_export, "
            "round_trip (CDE slice 6)"
        ),
        labels=["stage"],
        unit="s",
        buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120],
    )

    SKETCH_PLANE_TOTAL = MetricDefinition(
        name="metaforge_sketch_plane_total",
        type="counter",
        description=(
            "Sketch plane manager actions, by action (proposed, proposed_ambiguous, "
            "confirmed, reassign_previewed, reassigned, undone, archived, failed) and "
            "proposed_by (user, existing_sketch, view, component_frame, model, none) "
            "(FORGE-790)"
        ),
        labels=["action", "proposed_by"],
    )

    # ── Face-attached sketch planes (CDE slice 9) ──────────────────────

    FACE_ATTACHMENT_TOTAL = MetricDefinition(
        name="metaforge_face_attachment_total",
        type="counter",
        description=(
            "Sketch planes put on a model face, by action (attached, resolved_pick_face, "
            "resolved_detach_principal, resolved_detach_unassigned) (CDE slice 9)"
        ),
        labels=["action"],
    )
    FACE_REMAP_TOTAL = MetricDefinition(
        name="metaforge_face_remap_total",
        type="counter",
        description=(
            "Face remaps after a CAD head move, by outcome (unique, ambiguous, missing, "
            "unavailable, error). Ambiguous and missing ask a person (CDE slice 9)"
        ),
        labels=["outcome"],
    )
    PLANE_BLOCKED_COMMIT_TOTAL = MetricDefinition(
        name="metaforge_plane_blocked_commit_total",
        type="counter",
        description=(
            "Authoritative commits refused because the sketch plane they depend on needs "
            "reattaching, by commit (exact_build, promotion) (CDE slice 9)"
        ),
        labels=["commit"],
    )

    # ── Cross-domain dependency and impact (FORGE-793) ─────────────────

    CROSS_DOMAIN_IMPACT_TOTAL = MetricDefinition(
        name="metaforge_cross_domain_impact_total",
        type="counter",
        description=(
            "Cross-domain impact events, by event (previewed, recorded, failed, "
            "result_invalidated, rerun_proposed, rerun_decided) and kind (the trigger for "
            "previewed/recorded/failed, the check kind for result_invalidated, the "
            "disposition or decision for reruns) (FORGE-793)"
        ),
        labels=["event", "kind"],
    )
    INTERFACE_EDGE_TOTAL = MetricDefinition(
        name="metaforge_interface_edge_total",
        type="counter",
        description=(
            "Interface edge actions, by type (mounts_to, clearance_to, connects_via, "
            "pin_maps_to, simulation_input_of, feature_of), provenance (human, tool, "
            "inferred) and action (declared, proposed, confirmed, removed) (FORGE-793)"
        ),
        labels=["type", "provenance", "action"],
    )

    # ── Sketch compiler (FORGE-791) ────────────────────────────────────

    SKETCH_COMPILE_TOTAL = MetricDefinition(
        name="metaforge_sketch_compile_total",
        type="counter",
        description=(
            "Sketch compiler writes, by stage (interpretation, promotion, approval) and "
            "outcome (proposed, promoted, approved, duplicate, refused, failed). A refusal "
            "is a promotion whose accepted rules do not hold, or a stale or empty one "
            "(FORGE-791)"
        ),
        labels=["stage", "outcome"],
    )

    # ── Sketch generation and references (FORGE-794) ───────────────────

    SKETCH_GENERATION_TOTAL = MetricDefinition(
        name="metaforge_sketch_generation_total",
        type="counter",
        description=(
            "AI concept sketch generations, by mode (text, reference, sketch) and outcome: "
            "generated, preservation_flagged (a region edit changed what it had to keep), "
            "stale (the accepted baseline moved while it ran; kept as history), "
            "superseded, cancelled, unavailable, timeout, failed, accepted, rejected "
            "(FORGE-794)"
        ),
        labels=["mode", "outcome"],
    )
    SKETCH_GENERATION_FIRST_RESULT_SECONDS = MetricDefinition(
        name="metaforge_sketch_generation_first_result_seconds",
        type="histogram",
        description=(
            "Seconds from a sketch generation request to its first stored proposal, by "
            "mode and provider: the measured first-useful-result latency (FORGE-794)"
        ),
        labels=["mode", "provider"],
        unit="s",
        buckets=[0.5, 1, 2, 3, 5, 8, 13, 21, 34, 60, 120],
    )
    REFERENCE_CONFLICT_TOTAL = MetricDefinition(
        name="metaforge_reference_conflict_total",
        type="counter",
        description=(
            "Conflicts raised for review between two authoritative references that state "
            "different values for the same parameter (FORGE-794)"
        ),
        labels=[],
    )

    # ── Sketch reading (FORGE-762) ─────────────────────────────────────

    SKETCH_READING_TOTAL = MetricDefinition(
        name="metaforge_sketch_reading_total",
        type="counter",
        description=(
            "Sketch readings by a vision model, by outcome: proposed, refused_output "
            "(the reply did not fit the schema), unavailable (no vision model answered), "
            "confirmed and rejected (a person's decision) (FORGE-762)"
        ),
        labels=["outcome"],
    )

    # ── Run supervisor (FORGE-734) ─────────────────────────────────────

    SUPERVISOR_ACTION_TOTAL = MetricDefinition(
        name="metaforge_run_supervisor_action_total",
        type="counter",
        description=(
            "Run supervisor actions (start, restart, stopped, verified) by reason "
            "(design, tooling, platform, person, budget, verification, ok, other) (FORGE-734)"
        ),
        labels=["action", "reason"],
    )

    # ── Concept image (FORGE-761) ──────────────────────────────────────

    CONCEPT_IMAGE_TOTAL = MetricDefinition(
        name="metaforge_concept_image_total",
        type="counter",
        description=(
            "AI concept images generated from a block layout and a visual brief, by "
            "outcome: generated, unavailable (no image provider configured), timeout, "
            "failed (the provider answered with an error) or store_failed (FORGE-761)"
        ),
        labels=["outcome"],
    )

    # ── Concept image critique (FORGE-772) ─────────────────────────────

    CONCEPT_IMAGE_CRITIQUE_TOTAL = MetricDefinition(
        name="metaforge_concept_image_critique_total",
        type="counter",
        description=(
            "Vision-model checks of a concept image against the selected assembly, by "
            "verdict: consistent, inconsistent, unclear, or unchecked (no assembly, no "
            "vision model, or a reply that did not fit the schema) (FORGE-772)"
        ),
        labels=["verdict"],
    )

    # ── Imagine stage (FORGE-821) ──────────────────────────────────────

    IMAGINE_STUDIO_TOTAL = MetricDefinition(
        name="metaforge_imagine_studio_total",
        type="counter",
        description=(
            "Imagine stage operations on a project's concept studio, by operation: "
            "put (a new config revision), event (a browser write mapped into the twin), "
            "handover, rework or engine (the engine page served), and by outcome: ok, "
            "duplicate (an event id or op id seen before), refused (validation, a frozen "
            "studio or an unready handover) or failed (a twin write raised) (FORGE-821)"
        ),
        labels=["operation", "outcome"],
    )

    # ── Concept mesh (FORGE-778) ───────────────────────────────────────

    CONCEPT_MESH_TOTAL = MetricDefinition(
        name="metaforge_concept_mesh_total",
        type="counter",
        description=(
            "AI 3D reference meshes generated from a concept image or a prompt, by "
            "outcome: generated, unavailable (no mesh provider configured), timeout, "
            "failed (the provider refused or the task failed) or store_failed, and by "
            "mode: image (one concept image), multiview (the concept's straight-on "
            "views) or text (FORGE-778, FORGE-779)"
        ),
        labels=["outcome", "mode"],
    )

    # ── Concept parts: segmentation, mapping, part regeneration ─────────

    CONCEPT_SEGMENTATION_TOTAL = MetricDefinition(
        name="metaforge_concept_segmentation_total",
        type="counter",
        description=(
            "Splits of an accepted 3D concept (L0) into parts, by outcome: segmented, "
            "not_segmented (the model came back whole, no part nodes), unavailable (no "
            "mesh provider), timeout or failed, and by the segmentation model"
        ),
        labels=["outcome", "model"],
    )

    CONCEPT_PART_MAPPING_TOTAL = MetricDefinition(
        name="metaforge_concept_part_mapping_total",
        type="counter",
        description=(
            "Parts of a segmented concept a person decided, by outcome: confirmed (as "
            "proposed), corrected (to another component) or not_a_component"
        ),
        labels=["outcome"],
    )

    CONCEPT_PART_REGENERATION_TOTAL = MetricDefinition(
        name="metaforge_concept_part_regeneration_total",
        type="counter",
        description=(
            "One part of a segmented concept generated again and swapped in, by outcome "
            "(regenerated, unavailable, timeout, failed) and source (text, image)"
        ),
        labels=["outcome", "source"],
    )

    CONCEPT_PART_COMPLETION_TOTAL = MetricDefinition(
        name="metaforge_concept_part_completion_total",
        type="counter",
        description=(
            "One part of a segmented concept sealed or completed by the provider, by "
            "outcome (completed, unavailable, timeout, failed) and mode (quick_cap, "
            "ai_completion)"
        ),
        labels=["outcome", "mode"],
    )

    # ── Manufacturing context (DFM-grounded generation) ────────────────

    MANUFACTURING_CONTEXT_WRITE_TOTAL = MetricDefinition(
        name="metaforge_manufacturing_context_write_total",
        type="counter",
        description=(
            "Manufacturing context field writes, by the authority each field was "
            "written with (user_stated, decision, assumption, unknown) and outcome: "
            "written, unchanged, refused (an assumption that may not override a "
            "stated value or a decision) or failed (a sync from Create, a decision "
            "or a flow proposal that could not be written)"
        ),
        labels=["authority", "outcome"],
    )

    MANUFACTURING_CONTEXT_INJECTION_TOTAL = MetricDefinition(
        name="metaforge_manufacturing_context_injection_total",
        type="counter",
        description=(
            "Manufacturing context blocks given to a generator, by generator "
            "(sketch_generation, concept_image, concept_mesh, concept_sketch, "
            "project_brief, flow_phase) and resolution: record (a recorded "
            "MFC revision), unresolved (no record yet, so the unresolved set was "
            "recorded instead) or speculative (recorded but not applied)"
        ),
        labels=["generator", "resolution"],
    )

    # ── Change classification (before an authoritative edit) ───────────

    CHANGE_CLASSIFICATION_TOTAL = MetricDefinition(
        name="metaforge_change_classification_total",
        type="counter",
        description=(
            "Change requests classified before any authoritative edit, by class "
            "(appearance, geometric, engineering_geometry, material_engineering, "
            "performance_objective, ambiguous_style, ambiguous_appearance_or_material, "
            "not_a_change), confidence band (high, medium, low) and stage: rules, llm, "
            "person (the person chose the look or the material) or llm_failed"
        ),
        labels=["change_class", "confidence", "stage"],
    )

    APPEARANCE_UPDATE_TOTAL = MetricDefinition(
        name="metaforge_appearance_update_total",
        type="counter",
        description=(
            "Appearance-only updates (colour, finish, render material, textures) by "
            "scope (component, product) and outcome: updated, refused or failed. "
            "None of them rebuilds CAD or writes the manufacturing context"
        ),
        labels=["scope", "outcome"],
    )

    CHANGE_GUARD_REFUSAL_TOTAL = MetricDefinition(
        name="metaforge_change_guard_refusal_total",
        type="counter",
        description=(
            "Actions held because of the turn's change classification, by class and "
            "action (cad_rebuild, manufacturing_context_write): an appearance request "
            "that tried to rebuild CAD, for example"
        ),
        labels=["change_class", "action"],
    )

    # ── Representation levels and maturity (C3D slice 2) ────────────────

    STRUCTURED_MESH_TOTAL = MetricDefinition(
        name="metaforge_structured_mesh_total",
        type="counter",
        description=(
            "Structured 3D meshes (L1) drawn from a concept block layout, by outcome: "
            "generated, reused (the same mesh already existed), no_layout, failed or "
            "store_failed"
        ),
        labels=["outcome"],
    )

    MATURITY_QUERY_TOTAL = MetricDefinition(
        name="metaforge_maturity_query_total",
        type="counter",
        description=(
            "Component maturity queries, by the product maturity answered: Illustrative, "
            "Structured, Parametric, Exact, Verified, Released, none (no components) or "
            "error"
        ),
        labels=["product"],
    )

    REPRESENTATION_MISLABEL_TOTAL = MetricDefinition(
        name="metaforge_representation_mislabel_total",
        type="counter",
        description=(
            "Stored representation claims the read-time guard corrected (an L3 without a "
            "validated exact revision, or an L0/L1/picture claiming exact or validated), "
            "by the level shown and the authority claimed"
        ),
        labels=["level", "claimed"],
    )

    # ── Heuristic DFM findings (C3D slice 4) ────────────────────────────

    DFM_EVALUATION_TOTAL = MetricDefinition(
        name="metaforge_dfm_evaluation_total",
        type="counter",
        description=(
            "Heuristic DFM evaluations of a project's components, by trigger (manual, "
            "tool, geometry_changed, context_changed) and outcome (evaluated, failed)"
        ),
        labels=["trigger", "outcome"],
    )

    DFM_FINDING_TOTAL = MetricDefinition(
        name="metaforge_dfm_finding_total",
        type="counter",
        description=(
            "DFM findings recorded in new finding sets, by severity (blocking, advisory) "
            "and rule profile (fdm, resin, cnc, injection_moulding, sheet_metal)"
        ),
        labels=["severity", "process"],
    )

    DFM_STALE_RECOMPUTE_TOTAL = MetricDefinition(
        name="metaforge_dfm_stale_recompute_total",
        type="counter",
        description=(
            "Incremental DFM work after a change, by reason: geometry (a component's "
            "geometry head moved), context (a process, material or quantity change) or "
            "revalidated (a context change no rule reads)"
        ),
        labels=["reason"],
    )

    DFM_DECISION_TOTAL = MetricDefinition(
        name="metaforge_dfm_decision_total",
        type="counter",
        description=(
            "A person's decisions on DFM findings, by decision (keep_design, try_fix, "
            "explore_process) and outcome (recorded, failed)"
        ),
        labels=["decision", "outcome"],
    )

    # ── L2 preview, comparison and direct manipulation (C3D slice 5) ────

    PREVIEW_REBUILD_SECONDS = MetricDefinition(
        name="metaforge_preview_rebuild_seconds",
        type="histogram",
        description=(
            "How long the browser took to rebuild the L2 parametric preview mesh after a "
            "parameter change, as reported with a parameter commit, by feature op "
            "(extrude, revolve). Target: 0.1 s for simple parts (C3D slice 5)"
        ),
        labels=["op"],
        unit="s",
        buckets=[0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1],
    )

    PREVIEW_COMPARISON_TOTAL = MetricDefinition(
        name="metaforge_preview_comparison_total",
        type="counter",
        description=(
            "Preview versus exact comparisons stored on a validated revision, by outcome "
            "(consistent, discrepancy, incomplete, error) (C3D slice 5)"
        ),
        labels=["outcome"],
    )

    PREVIEW_DISCREPANCY_TOTAL = MetricDefinition(
        name="metaforge_preview_discrepancy_total",
        type="counter",
        description=(
            "Comparison checks that exceeded the component's tolerance, by check "
            "(bounding_box, dimensions, volume, surface_distance, features) (C3D slice 5)"
        ),
        labels=["check"],
    )

    MANIPULATION_COMMIT_TOTAL = MetricDefinition(
        name="metaforge_manipulation_commit_total",
        type="counter",
        description=(
            "Parameter commits from direct manipulation, by source (drag, numeric) and "
            "outcome (committed, refused, stale) (C3D slice 5)"
        ),
        labels=["source", "outcome"],
    )

    EXACT_INCREMENTAL_TOTAL = MetricDefinition(
        name="metaforge_exact_incremental_total",
        type="counter",
        description=(
            "Exact builds of a parametric component, by outcome: skipped (its definition "
            "hash matched the validated head) or rebuilt (C3D slice 5)"
        ),
        labels=["outcome"],
    )

    # ── Exact DFM, report and release checklist (C3D slice 6) ──────────

    DFM_EXACT_EVALUATION_TOTAL = MetricDefinition(
        name="metaforge_dfm_exact_evaluation_total",
        type="counter",
        description=(
            "Exact DFM evaluations of a validated B-Rep revision, by outcome "
            "(analysis_passed, issues_found, incomplete, failed)"
        ),
        labels=["outcome"],
    )

    DFM_EXACT_INCOMPLETE_TOTAL = MetricDefinition(
        name="metaforge_dfm_exact_incomplete_total",
        type="counter",
        description=(
            "Exact DFM checks that could not run, by rule (fdm.min_wall, fdm.build_volume "
            "...) and reason (no_machine, not_measured, measure_failed, other)"
        ),
        labels=["rule", "reason"],
    )

    DFM_SLICER_CHECK_TOTAL = MetricDefinition(
        name="metaforge_dfm_slicer_check_total",
        type="counter",
        description=(
            "Slicer cross-checks after an exact FDM evaluation (slicer.slice on the "
            "validated revision), by outcome (evaluated, not_evaluated) and reason "
            "(sliced, unavailable, failed, no_machine, orientation, no_step, disabled)"
        ),
        labels=["outcome", "reason"],
    )

    DFM_REPORT_TOTAL = MetricDefinition(
        name="metaforge_dfm_report_total",
        type="counter",
        description=(
            "Revision-specific DFM report generations, by outcome (generated, reused, failed)"
        ),
        labels=["outcome"],
    )

    MANUFACTURING_RELEASE_REFUSED_TOTAL = MetricDefinition(
        name="metaforge_manufacturing_release_refused_total",
        type="counter",
        description=(
            "Releases refused while the manufacturing release checklist or the G8 gate "
            "was unresolved, by the first failing G8 check (manufacturing_checklist, "
            "step_round_trip, approvals, gate for any other)"
        ),
        labels=["blocker"],
    )

    # ── Parametric reconstruction from an accepted concept (spec 4B) ─────

    RECONSTRUCTION_PROPOSAL_TOTAL = MetricDefinition(
        name="metaforge_reconstruction_proposal_total",
        type="counter",
        description=(
            "Feature history proposals for a component of an accepted concept, by outcome "
            "(proposed, reused, accepted, partially_accepted, refused, failed)"
        ),
        labels=["outcome"],
    )

    RECONSTRUCTION_FEATURE_ACCEPTED_TOTAL = MetricDefinition(
        name="metaforge_reconstruction_feature_accepted_total",
        type="counter",
        description=(
            "Proposed features a person accepted, by kind (sketch_profile, extrude, revolve, "
            "shell, opening, boss, rib) and by who suggested them (deterministic, llm)"
        ),
        labels=["kind", "suggested_by"],
    )

    RECONSTRUCTION_LLM_PASS_TOTAL = MetricDefinition(
        name="metaforge_reconstruction_llm_pass_total",
        type="counter",
        description=(
            "Optional model passes over a feature history proposal (names, order and "
            "missing features, never numbers), by outcome (used, unavailable, invalid)"
        ),
        labels=["outcome"],
    )

    # ── Interface contracts (lamp golden path, step 1) ─────────────────

    INTERFACE_CONTRACT_CHECK_TOTAL = MetricDefinition(
        name="metaforge_interface_contract_check_total",
        type="counter",
        description=(
            "Interface contract evaluations, by kind (cad_eda, eda_firmware, simulation, "
            "manufacturing) and outcome (satisfied, violated, not_evaluated, stale, failed)"
        ),
        labels=["kind", "outcome"],
    )

    INTERFACE_CONTRACT_VIOLATION_TOTAL = MetricDefinition(
        name="metaforge_interface_contract_violation_total",
        type="counter",
        description=(
            "Violated checks found by interface contract evaluations, by kind and check "
            "(pcb_fits_envelope, mounting_holes_match, usb_c_matches_cutout, "
            "pins_match_netlist, ...)"
        ),
        labels=["kind", "check"],
    )

    # ── CAD design loop (self-validating CadQuery loop) ─────────────────

    CAD_LOOP_RUN_TOTAL = MetricDefinition(
        name="metaforge_cad_loop_run_total",
        type="counter",
        description=(
            "CAD design loop runs that ended, by stop reason: satisfied (critic satisfied, "
            "validity pass, no blocking DFM finding, stated dimensions in tolerance), "
            "iteration_cap or spend_cap (stopped with open issues), needs_input (blocked "
            "on a person's answer) or failed (a model or adapter could not be reached)"
        ),
        labels=["stop_reason"],
    )

    CAD_LOOP_ITERATION_TOTAL = MetricDefinition(
        name="metaforge_cad_loop_iteration_total",
        type="counter",
        description=(
            "CAD design loop iterations, by outcome: built (the script built and was "
            "checked), build_failed, invalid (validity did not pass), question (the model "
            "asked instead of writing) or unrendered"
        ),
        labels=["outcome"],
    )

    CAD_LOOP_RENDER_TOTAL = MetricDefinition(
        name="metaforge_cad_loop_render_total",
        type="counter",
        description=(
            "Model view renders the CAD design loop asked for (render.model_views), by "
            "outcome: rendered, convert_failed (STEP to GLB) or render_failed"
        ),
        labels=["outcome"],
    )

    CAD_LOOP_CRITIC_TOTAL = MetricDefinition(
        name="metaforge_cad_loop_critic_total",
        type="counter",
        description=(
            "Vision critic verdicts in the CAD design loop: satisfied, not_satisfied, "
            "unparsed (the reply could not be read) or unavailable. A verdict is never "
            "evidence and never validates geometry"
        ),
        labels=["verdict"],
    )

    CAD_LOOP_MODEL_CALL_TOTAL = MetricDefinition(
        name="metaforge_cad_loop_model_call_total",
        type="counter",
        description="Model calls the CAD design loop made, by role (author, critic)",
        labels=["role"],
    )

    CAD_LOOP_SPEND_USD_TOTAL = MetricDefinition(
        name="metaforge_cad_loop_spend_usd_total",
        type="counter",
        description=(
            "Priced model spend of the CAD design loop in USD, by role (author, critic). "
            "Calls with no known price are counted in metaforge_cad_loop_model_call_total "
            "and add nothing here"
        ),
        labels=["role"],
    )

    # ── Class methods for grouped access ───────────────────────────────

    @classmethod
    def all_metrics(cls) -> list[MetricDefinition]:
        """Return every metric definition in the registry."""
        return (
            cls.gateway_metrics()
            + cls.agent_metrics()
            + cls.skill_metrics()
            + cls.kafka_metrics()
            + cls.datastore_metrics()
            + cls.telemetry_metrics()
            + cls.constraint_metrics()
            + cls.retrieval_metrics()
            + cls.knowledge_metrics()
            + cls.twin_metrics()
            + cls.consolidation_metrics()
            + cls.harness_metrics()
            + cls.mcp_metrics()
            + cls.design_flow_metrics()
            + cls.sim_field_metrics()
            + cls.watchdog_metrics()
            + cls.gate_review_metrics()
            + cls.supervisor_metrics()
            + cls.operation_metrics()
        )

    @classmethod
    def operation_metrics(cls) -> list[MetricDefinition]:
        """Operation event log and project stream metrics (FORGE-785)."""
        return [
            cls.OPERATION_EVENT_TOTAL,
            cls.OPERATION_EVENT_PUBLISH_FAILURE_TOTAL,
            cls.OPERATION_STREAM_REPLAY_TOTAL,
            cls.OPERATION_STREAM_RESYNC_TOTAL,
            cls.OPERATION_STREAM_ACTIVE,
            cls.OPERATION_ACTIVE,
            cls.OPERATION_EVENT_PUBLISH_LAG,
            cls.OPERATION_SCHEDULING_TOTAL,
        ]

    @classmethod
    def supervisor_metrics(cls) -> list[MetricDefinition]:
        """Run supervisor metrics (FORGE-734)."""
        return [cls.SUPERVISOR_ACTION_TOTAL]

    @classmethod
    def gate_review_metrics(cls) -> list[MetricDefinition]:
        """Gate reviewer metrics (FORGE-726)."""
        return [cls.GATE_REVIEW_TOTAL, cls.GATE_REVIEW_AGREEMENT_TOTAL]

    @classmethod
    def watchdog_metrics(cls) -> list[MetricDefinition]:
        """Host watchdog metrics (FORGE-713)."""
        return [
            cls.WATCHDOG_CHECKS_TOTAL,
            cls.WATCHDOG_RESTARTS_TOTAL,
            cls.WATCHDOG_UNHEALTHY_CONTAINERS,
            cls.WATCHDOG_TWIN_IN_MEMORY,
            cls.HOST_MEMORY_AVAILABLE_BYTES,
        ]

    @classmethod
    def sim_field_metrics(cls) -> list[MetricDefinition]:
        """Simulation result field metrics (FORGE-532)."""
        return [
            cls.SIM_FIELD_PAYLOAD_TOTAL,
            cls.SIM_FIELD_PAYLOAD_BYTES,
            cls.SIM_FIELD_STORE_TOTAL,
        ]

    @classmethod
    def design_flow_metrics(cls) -> list[MetricDefinition]:
        """Design-flow engine and approval-gate metrics (FORGE-401, FORGE-469, FORGE-470)."""
        return [
            cls.DESIGN_FLOW_ENGINE_UNAVAILABLE,
            cls.DESIGN_FLOW_RUN_STARTED,
            cls.DESIGN_FLOW_GATE_TOTAL,
            cls.DESIGN_FLOW_GATE_ANNOUNCE_TOTAL,
            cls.CLIENT_TASK_TOTAL,
            cls.APPROVAL_GATE_NO_RUNTIME_TOTAL,
            cls.ITERATION_GATE_UNATTENDED_TOTAL,
        ]

    @classmethod
    def mcp_metrics(cls) -> list[MetricDefinition]:
        """MCP-surface metrics (FORGE-379)."""
        return [
            cls.MCP_TOOL_CALL_TOTAL,
            cls.MCP_TOOL_CALL_DURATION,
            cls.MCP_ERROR_TOTAL,
            cls.MCP_ADAPTER_PROBE_TOTAL,
            cls.MCP_CODE_VERSION_CHECK_TOTAL,
            cls.MCP_ORIGIN_REJECTED_TOTAL,
            cls.TOOL_JOB_TOTAL,
        ]

    @classmethod
    def harness_metrics(cls) -> list[MetricDefinition]:
        """Chat harness tool-loop metrics (production-harness audit follow-up)."""
        return [
            cls.HARNESS_TURN_DURATION,
            cls.HARNESS_TURN_TOTAL,
            cls.HARNESS_TOOL_CALL_DURATION,
            cls.HARNESS_TOOL_CALL_TOTAL,
            cls.DESIGN_FLOW_TOOL_REFUSAL_TOTAL,
            cls.CHAT_UNGROUNDED_CLAIM_TOTAL,
            cls.HARNESS_PROVIDER_CALL_DURATION,
            cls.HARNESS_PROVIDER_FALLBACK_TOTAL,
            cls.LLM_TOKENS_TOTAL,
            cls.LLM_COST_USD_TOTAL,
            cls.LLM_CALLS_TOTAL,
            cls.LLM_RUN_SPEND_EXCEEDED_TOTAL,
            cls.TOOL_APPROVAL_RESOLUTION_TOTAL,
            cls.UNREACHABLE_APPROVAL_HOLD_TOTAL,
            cls.APPROVAL_DECISION_TOTAL,
            cls.TOOL_APPROVAL_TOTAL,
        ]

    @classmethod
    def consolidation_metrics(cls) -> list[MetricDefinition]:
        """MET-454 / MET-455 consolidation-pipeline metrics."""
        return [
            cls.CONSOLIDATION_PASS_DURATION,
            cls.CONSOLIDATION_PASS_TOTAL,
            cls.CONSOLIDATION_INSIGHTS_ACCEPTED_TOTAL,
            cls.CONSOLIDATION_INSIGHTS_REJECTED_TOTAL,
            cls.CONSOLIDATION_CONTRADICTIONS_TOTAL,
            cls.CONSOLIDATION_STALE_MARKED_TOTAL,
        ]

    @classmethod
    def twin_metrics(cls) -> list[MetricDefinition]:
        """MET-439 twin graph hygiene metrics."""
        return [
            cls.TWIN_ORPHANS,
            cls.TWIN_ITEM_REVISION_TOTAL,
            cls.TWIN_CHANGE_SET_TOTAL,
            cls.TWIN_ITEM_MIGRATION_TOTAL,
            cls.FLOW_ITEM_SLOT_TOTAL,
            cls.TWIN_PRD_REQUIREMENT_VALUE_TOTAL,
            cls.TWIN_RECORD_STALENESS_TOTAL,
            cls.CONCEPT_IMAGE_TOTAL,
            cls.SKETCH_READING_TOTAL,
            cls.SKETCH_SAVE_TOTAL,
            cls.SKETCH_PLANE_TOTAL,
            cls.FACE_ATTACHMENT_TOTAL,
            cls.FACE_REMAP_TOTAL,
            cls.PLANE_BLOCKED_COMMIT_TOTAL,
            cls.SKETCH_COMPILE_TOTAL,
            cls.SKETCH_GENERATION_TOTAL,
            cls.SKETCH_GENERATION_FIRST_RESULT_SECONDS,
            cls.REFERENCE_CONFLICT_TOTAL,
            cls.EXACT_CAD_BUILD_TOTAL,
            cls.GEOMETRY_VALIDITY_TOTAL,
            cls.STEP_EXPORT_TOTAL,
            cls.STEP_ROUND_TRIP_TOTAL,
            cls.CAD_ARTIFACT_DURATION,
            cls.CROSS_DOMAIN_IMPACT_TOTAL,
            cls.INTERFACE_EDGE_TOTAL,
            cls.CONCEPT_IMAGE_CRITIQUE_TOTAL,
            cls.CONCEPT_MESH_TOTAL,
            cls.IMAGINE_STUDIO_TOTAL,
            cls.MANUFACTURING_CONTEXT_WRITE_TOTAL,
            cls.MANUFACTURING_CONTEXT_INJECTION_TOTAL,
            cls.CHANGE_CLASSIFICATION_TOTAL,
            cls.APPEARANCE_UPDATE_TOTAL,
            cls.CHANGE_GUARD_REFUSAL_TOTAL,
            cls.STRUCTURED_MESH_TOTAL,
            cls.MATURITY_QUERY_TOTAL,
            cls.REPRESENTATION_MISLABEL_TOTAL,
            cls.DFM_EVALUATION_TOTAL,
            cls.DFM_FINDING_TOTAL,
            cls.DFM_STALE_RECOMPUTE_TOTAL,
            cls.DFM_DECISION_TOTAL,
            cls.PREVIEW_REBUILD_SECONDS,
            cls.PREVIEW_COMPARISON_TOTAL,
            cls.PREVIEW_DISCREPANCY_TOTAL,
            cls.MANIPULATION_COMMIT_TOTAL,
            cls.EXACT_INCREMENTAL_TOTAL,
            cls.DFM_EXACT_EVALUATION_TOTAL,
            cls.DFM_EXACT_INCOMPLETE_TOTAL,
            cls.DFM_SLICER_CHECK_TOTAL,
            cls.DFM_REPORT_TOTAL,
            cls.MANUFACTURING_RELEASE_REFUSED_TOTAL,
            cls.RECONSTRUCTION_PROPOSAL_TOTAL,
            cls.RECONSTRUCTION_FEATURE_ACCEPTED_TOTAL,
            cls.RECONSTRUCTION_LLM_PASS_TOTAL,
            cls.CONCEPT_SEGMENTATION_TOTAL,
            cls.CONCEPT_PART_MAPPING_TOTAL,
            cls.CONCEPT_PART_REGENERATION_TOTAL,
            cls.CONCEPT_PART_COMPLETION_TOTAL,
            cls.INTERFACE_CONTRACT_CHECK_TOTAL,
            cls.INTERFACE_CONTRACT_VIOLATION_TOTAL,
            cls.CAD_LOOP_RUN_TOTAL,
            cls.CAD_LOOP_ITERATION_TOTAL,
            cls.CAD_LOOP_RENDER_TOTAL,
            cls.CAD_LOOP_CRITIC_TOTAL,
            cls.CAD_LOOP_MODEL_CALL_TOTAL,
            cls.CAD_LOOP_SPEND_USD_TOTAL,
        ]

    @classmethod
    def retrieval_metrics(cls) -> list[MetricDefinition]:
        """MET-326 retrieval-quality + context-truncation metrics."""
        return [
            cls.RETRIEVAL_PRECISION_AT_K,
            cls.RETRIEVAL_RECALL_AT_K,
            cls.RETRIEVAL_MRR,
            cls.RETRIEVAL_NDCG_AT_K,
            cls.CONTEXT_TRUNCATED_TOTAL,
            cls.PROJECT_BRIEF_TOTAL,
            cls.PROJECT_BRIEF_CHARS,
        ]

    @classmethod
    def knowledge_metrics(cls) -> list[MetricDefinition]:
        """MET-401 / L1-A7 knowledge-service latency metrics."""
        return [
            cls.KNOWLEDGE_SEARCH_DURATION,
            cls.KB_STORAGE_INIT_TOTAL,
        ]

    @classmethod
    def gateway_metrics(cls) -> list[MetricDefinition]:
        """Return the 4 gateway metrics."""
        return [
            cls.GATEWAY_REQUEST_TOTAL,
            cls.GATEWAY_REQUEST_DURATION,
            cls.GATEWAY_WEBSOCKET_CONNECTIONS,
            cls.GATEWAY_ACTIVE_SESSIONS,
        ]

    # Keep backward-compatible alias
    all_gateway_metrics = gateway_metrics

    @classmethod
    def agent_metrics(cls) -> list[MetricDefinition]:
        """Return the 5 agent metrics."""
        return [
            cls.AGENT_EXECUTION_DURATION,
            cls.AGENT_EXECUTION_TOTAL,
            cls.AGENT_LLM_TOKENS_TOTAL,
            cls.AGENT_LLM_COST_TOTAL,
            cls.AGENT_LLM_REQUEST_DURATION,
        ]

    @classmethod
    def skill_metrics(cls) -> list[MetricDefinition]:
        """Return the 2 skill metrics."""
        return [
            cls.SKILL_EXECUTION_DURATION,
            cls.SKILL_EXECUTION_TOTAL,
        ]

    @classmethod
    def kafka_metrics(cls) -> list[MetricDefinition]:
        """Return the 5 Kafka metrics."""
        return [
            cls.KAFKA_CONSUMER_LAG,
            cls.KAFKA_MESSAGES_PRODUCED,
            cls.KAFKA_MESSAGES_CONSUMED,
            cls.KAFKA_DEAD_LETTERS,
            cls.KAFKA_REBALANCE_TOTAL,
        ]

    @classmethod
    def datastore_metrics(cls) -> list[MetricDefinition]:
        """Return the 7 data store metrics (Neo4j, pgvector, MinIO)."""
        return [
            cls.NEO4J_QUERY_DURATION,
            cls.NEO4J_ACTIVE_CONNECTIONS,
            cls.NEO4J_QUERY_TOTAL,
            cls.PGVECTOR_SEARCH_DURATION,
            cls.PGVECTOR_SEARCH_TOTAL,
            cls.MINIO_OPERATION_DURATION,
            cls.MINIO_OPERATION_TOTAL,
        ]

    @classmethod
    def telemetry_metrics(cls) -> list[MetricDefinition]:
        """Return the 5 MQTT/telemetry metrics."""
        return [
            cls.MQTT_MESSAGES_RECEIVED_TOTAL,
            cls.TELEMETRY_ROUTER_DURATION,
            cls.TELEMETRY_INGESTION_TOTAL,
            cls.TELEMETRY_INGESTION_ERRORS_TOTAL,
            cls.TELEMETRY_LAG_SECONDS,
        ]

    @classmethod
    def constraint_metrics(cls) -> list[MetricDefinition]:
        """Return the 4 constraint and policy metrics."""
        return [
            cls.CONSTRAINT_EVALUATION_TOTAL,
            cls.CONSTRAINT_EVALUATION_DURATION,
            cls.OPA_DECISION_TOTAL,
            cls.OSCILLATION_DETECTED_TOTAL,
        ]


# ---------------------------------------------------------------------------
# Metrics collector (wraps OTel meter or degrades to no-op)
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Collects metrics using an OTel MeterProvider (or no-op if unavailable).

    All public recording methods are safe to call even when no OTel meter is
    configured -- they simply become no-ops.
    """

    def __init__(self, meter: Any | None = None) -> None:
        self._meter = meter
        self._instruments: dict[str, Any] = {}
        self._gauge_values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    @property
    def is_recording(self) -> bool:
        """True when this collector will actually publish samples.

        FORGE-413: ``metrics is not None`` was never the right question. A
        no-op collector is not None and records nothing, so a caller that
        checked only for None would report healthy telemetry while every
        sample went nowhere -- which is how four metrics stayed at zero
        series through a release that added alert rules on them.
        """
        return self._meter is not None and bool(self._instruments)

    def create_instruments(self, definitions: list[MetricDefinition]) -> None:
        """Create OTel instruments from *definitions*.  No-op if no meter."""
        if self._meter is None:
            return

        for defn in definitions:
            if defn.type == "counter":
                self._instruments[defn.name] = self._meter.create_counter(
                    name=defn.name,
                    description=defn.description,
                    unit=defn.unit,
                )
            elif defn.type == "histogram":
                self._instruments[defn.name] = self._meter.create_histogram(
                    name=defn.name,
                    description=defn.description,
                    unit=defn.unit,
                )
            elif defn.type == "gauge":
                self._instruments[defn.name] = self._meter.create_up_down_counter(
                    name=defn.name,
                    description=defn.description,
                    unit=defn.unit,
                )

    # ── Gateway ────────────────────────────────────────────────────────

    def record_request(self, method: str, endpoint: str, status_code: int, duration: float) -> None:
        """Record an HTTP request (counter + histogram)."""
        counter = self._instruments.get(MetricsRegistry.GATEWAY_REQUEST_TOTAL.name)
        if counter is not None:
            counter.add(
                1,
                attributes={
                    "method": method,
                    "endpoint": endpoint,
                    "status_code": str(status_code),
                },
            )
        histogram = self._instruments.get(MetricsRegistry.GATEWAY_REQUEST_DURATION.name)
        if histogram is not None:
            histogram.record(
                duration,
                attributes={"method": method, "endpoint": endpoint},
            )

    def set_websocket_connections(self, state: str, count: int) -> None:
        """Record the current number of WebSocket connections."""
        gauge = self._instruments.get(MetricsRegistry.GATEWAY_WEBSOCKET_CONNECTIONS.name)
        if gauge is not None:
            gauge.add(count, attributes={"state": state})

    def set_active_sessions(self, status: str, count: int) -> None:
        """Record the current number of active sessions."""
        gauge = self._instruments.get(MetricsRegistry.GATEWAY_ACTIVE_SESSIONS.name)
        if gauge is not None:
            gauge.add(count, attributes={"status": status})

    # ── Twin graph hygiene (MET-439) ──────────────────────────────────

    def set_twin_orphans(self, kind: str, count: int) -> None:
        """Record the current orphan count for a dependent node kind.

        ``kind`` is one of ``constraint``, ``bom_item``, ``design_element``,
        ``component``. Called by ``TwinAPI.find_orphans()`` after every
        scan so the gauge reflects the most recent state.
        """
        gauge = self._instruments.get(MetricsRegistry.TWIN_ORPHANS.name)
        if gauge is not None:
            gauge.add(count, attributes={"kind": kind})

    def record_twin_change_set(self, outcome: str) -> None:
        """Record one run change-set commit or close (FORGE-525)."""
        counter = self._instruments.get(MetricsRegistry.TWIN_CHANGE_SET_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_twin_item_migration(
        self, item_type: str, kind: str, outcome: str = "created", count: int = 1
    ) -> None:
        """Record items / revisions the item migration created (FORGE-529)."""
        counter = self._instruments.get(MetricsRegistry.TWIN_ITEM_MIGRATION_TOTAL.name)
        if counter is not None and count > 0:
            counter.add(
                count, attributes={"item_type": item_type, "kind": kind, "outcome": outcome}
            )

    def record_twin_prd_requirement_value(self, outcome: str, count: int = 1) -> None:
        """Record requirement values seen in a prd write (FORGE-528)."""
        counter = self._instruments.get(MetricsRegistry.TWIN_PRD_REQUIREMENT_VALUE_TOTAL.name)
        if counter is not None and count > 0:
            counter.add(count, attributes={"outcome": outcome})

    def record_twin_record_staleness(self, record_type: str, status: str) -> None:
        """Record one record staleness change (FORGE-527)."""
        counter = self._instruments.get(MetricsRegistry.TWIN_RECORD_STALENESS_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"record_type": record_type, "status": status})

    def record_twin_item_revision(self, item_type: str, outcome: str, resolved_by: str) -> None:
        """Record one item revision write (FORGE-523)."""
        counter = self._instruments.get(MetricsRegistry.TWIN_ITEM_REVISION_TOTAL.name)
        if counter is not None:
            counter.add(
                1,
                attributes={
                    "item_type": item_type,
                    "outcome": outcome,
                    "resolved_by": resolved_by,
                },
            )

    def record_flow_item_slot(self, item_type: str, outcome: str) -> None:
        """Record one design-flow write's slot outcome (FORGE-524)."""
        counter = self._instruments.get(MetricsRegistry.FLOW_ITEM_SLOT_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"item_type": item_type, "outcome": outcome})

    def record_twin_baseline(self, source: str, outcome: str) -> None:
        """Record one baseline attempt (FORGE-526)."""
        counter = self._instruments.get(MetricsRegistry.TWIN_BASELINE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"source": source, "outcome": outcome})

    # ── Agent ──────────────────────────────────────────────────────────

    def record_agent_execution(self, agent_code: str, status: str, duration: float) -> None:
        """Record an agent execution (counter + histogram)."""
        counter = self._instruments.get(MetricsRegistry.AGENT_EXECUTION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"agent_code": agent_code, "status": status})
        hist = self._instruments.get(MetricsRegistry.AGENT_EXECUTION_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes={"agent_code": agent_code, "status": status})

    def record_llm_tokens(
        self,
        agent_code: str,
        provider: str,
        model: str,
        token_type: str,
        count: int,
    ) -> None:
        """Record LLM token consumption."""
        counter = self._instruments.get(MetricsRegistry.AGENT_LLM_TOKENS_TOTAL.name)
        if counter is not None:
            counter.add(
                count,
                attributes={
                    "agent_code": agent_code,
                    "llm_provider": provider,
                    "llm_model": model,
                    "token_type": token_type,
                },
            )

    def record_llm_cost(self, agent_code: str, provider: str, model: str, cost_usd: float) -> None:
        """Record LLM cost in USD."""
        counter = self._instruments.get(MetricsRegistry.AGENT_LLM_COST_TOTAL.name)
        if counter is not None:
            counter.add(
                cost_usd,
                attributes={
                    "agent_code": agent_code,
                    "llm_provider": provider,
                    "llm_model": model,
                },
            )

    def record_llm_request_duration(
        self, agent_code: str, provider: str, model: str, duration: float
    ) -> None:
        """Record LLM request duration."""
        hist = self._instruments.get(MetricsRegistry.AGENT_LLM_REQUEST_DURATION.name)
        if hist is not None:
            hist.record(
                duration,
                attributes={
                    "agent_code": agent_code,
                    "llm_provider": provider,
                    "llm_model": model,
                },
            )

    # ── Skill ──────────────────────────────────────────────────────────

    def record_skill_execution(
        self, skill_name: str, domain: str, status: str, duration: float
    ) -> None:
        """Record a skill execution (counter + histogram)."""
        counter = self._instruments.get(MetricsRegistry.SKILL_EXECUTION_TOTAL.name)
        if counter is not None:
            counter.add(
                1, attributes={"skill_name": skill_name, "domain": domain, "status": status}
            )
        hist = self._instruments.get(MetricsRegistry.SKILL_EXECUTION_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes={"skill_name": skill_name, "domain": domain})

    # ── Kafka ──────────────────────────────────────────────────────────

    def set_consumer_lag(self, group: str, topic: str, partition: str, lag: int) -> None:
        """Set the current Kafka consumer lag for a partition."""
        gauge = self._instruments.get(MetricsRegistry.KAFKA_CONSUMER_LAG.name)
        if gauge is not None:
            gauge.add(
                lag,
                attributes={"consumer_group": group, "topic": topic, "partition": partition},
            )

    def record_message_produced(self, topic: str) -> None:
        """Record a Kafka message produced."""
        counter = self._instruments.get(MetricsRegistry.KAFKA_MESSAGES_PRODUCED.name)
        if counter is not None:
            counter.add(1, attributes={"topic": topic})

    def record_message_consumed(self, topic: str, group: str) -> None:
        """Record a Kafka message consumed."""
        counter = self._instruments.get(MetricsRegistry.KAFKA_MESSAGES_CONSUMED.name)
        if counter is not None:
            counter.add(1, attributes={"topic": topic, "consumer_group": group})

    def record_dead_letter(self, topic: str, group: str) -> None:
        """Record a Kafka dead letter message."""
        counter = self._instruments.get(MetricsRegistry.KAFKA_DEAD_LETTERS.name)
        if counter is not None:
            counter.add(1, attributes={"topic": topic, "consumer_group": group})

    def record_rebalance(self, group: str) -> None:
        """Record a Kafka consumer rebalance."""
        counter = self._instruments.get(MetricsRegistry.KAFKA_REBALANCE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"consumer_group": group})

    # ── Data store (Neo4j, pgvector, MinIO) ───────────────────────────

    def record_neo4j_query(
        self, operation: str, node_type: str, status: str, duration: float
    ) -> None:
        """Record a Neo4j query (counter + histogram)."""
        counter = self._instruments.get(MetricsRegistry.NEO4J_QUERY_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"operation": operation, "status": status})
        hist = self._instruments.get(MetricsRegistry.NEO4J_QUERY_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes={"operation": operation, "node_type": node_type})

    def set_neo4j_connections(self, count: int) -> None:
        """Set the current number of active Neo4j connections."""
        gauge = self._instruments.get(MetricsRegistry.NEO4J_ACTIVE_CONNECTIONS.name)
        if gauge is not None:
            gauge.add(count, attributes={})

    def record_pgvector_search(self, knowledge_type: str, status: str, duration: float) -> None:
        """Record a pgvector search (counter + histogram)."""
        counter = self._instruments.get(MetricsRegistry.PGVECTOR_SEARCH_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"knowledge_type": knowledge_type, "status": status})
        hist = self._instruments.get(MetricsRegistry.PGVECTOR_SEARCH_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes={"knowledge_type": knowledge_type})

    def record_minio_operation(self, operation: str, status: str, duration: float) -> None:
        """Record a MinIO operation (counter + histogram)."""
        counter = self._instruments.get(MetricsRegistry.MINIO_OPERATION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"operation": operation, "status": status})
        hist = self._instruments.get(MetricsRegistry.MINIO_OPERATION_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes={"operation": operation})

    # ── Telemetry / MQTT ─────────────────────────────────────────────

    def record_mqtt_message(self, device_id: str, topic: str) -> None:
        """Record an MQTT message received from a device."""
        counter = self._instruments.get(MetricsRegistry.MQTT_MESSAGES_RECEIVED_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"device_id": device_id, "topic": topic})

    def record_telemetry_routing(self, device_type: str, duration: float) -> None:
        """Record telemetry routing duration."""
        hist = self._instruments.get(MetricsRegistry.TELEMETRY_ROUTER_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes={"device_type": device_type})

    def record_telemetry_ingestion(self, status: str) -> None:
        """Record a telemetry ingestion attempt (status: success/error)."""
        counter = self._instruments.get(MetricsRegistry.TELEMETRY_INGESTION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"status": status})

    def record_telemetry_error(self, error_type: str) -> None:
        """Record a telemetry ingestion error (error_type: malformed/write_failure)."""
        counter = self._instruments.get(MetricsRegistry.TELEMETRY_INGESTION_ERRORS_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"error_type": error_type})

    def set_telemetry_lag(self, device_id: str, lag_seconds: float) -> None:
        """Set the telemetry processing lag for a device."""
        gauge = self._instruments.get(MetricsRegistry.TELEMETRY_LAG_SECONDS.name)
        if gauge is not None:
            gauge.add(lag_seconds, attributes={"device_id": device_id})

    # ── Constraint & Policy ───────────────────────────────────────────

    def record_constraint_evaluation(self, domain: str, result: str, duration: float) -> None:
        """Record a constraint evaluation (counter + histogram)."""
        counter = self._instruments.get(MetricsRegistry.CONSTRAINT_EVALUATION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"domain": domain, "result": result})
        hist = self._instruments.get(MetricsRegistry.CONSTRAINT_EVALUATION_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes={"domain": domain})

    def record_opa_decision(self, policy: str, result: str) -> None:
        """Record an OPA policy decision."""
        counter = self._instruments.get(MetricsRegistry.OPA_DECISION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"policy": policy, "result": result})

    def record_oscillation_detected(self, node_type: str) -> None:
        """Record an oscillation detection in the constraint graph."""
        counter = self._instruments.get(MetricsRegistry.OSCILLATION_DETECTED_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"node_type": node_type})

    # ── Retrieval quality (MET-326) ───────────────────────────────────

    def record_retrieval_precision(self, agent_id: str, k: int, value: float) -> None:
        """Record a precision@k measurement for the given agent."""
        hist = self._instruments.get(MetricsRegistry.RETRIEVAL_PRECISION_AT_K.name)
        if hist is not None:
            hist.record(value, attributes={"agent_id": agent_id, "k": str(k)})

    def record_retrieval_recall(self, agent_id: str, k: int, value: float) -> None:
        """Record a recall@k measurement for the given agent."""
        hist = self._instruments.get(MetricsRegistry.RETRIEVAL_RECALL_AT_K.name)
        if hist is not None:
            hist.record(value, attributes={"agent_id": agent_id, "k": str(k)})

    def record_retrieval_mrr(self, agent_id: str, value: float) -> None:
        """Record a mean-reciprocal-rank measurement for the given agent."""
        hist = self._instruments.get(MetricsRegistry.RETRIEVAL_MRR.name)
        if hist is not None:
            hist.record(value, attributes={"agent_id": agent_id})

    def record_retrieval_ndcg(self, agent_id: str, k: int, value: float) -> None:
        """Record an NDCG@k measurement for the given agent."""
        hist = self._instruments.get(MetricsRegistry.RETRIEVAL_NDCG_AT_K.name)
        if hist is not None:
            hist.record(value, attributes={"agent_id": agent_id, "k": str(k)})

    def record_context_truncated(self, agent_id: str, source_kind: str, count: int = 1) -> None:
        """Increment ``metaforge_context_truncated_total`` by ``count``.

        Wired from the MET-317 ``context_truncated`` event in
        ``digital_twin.context.assembler``. One call per
        (agent_id, source_kind) bucket per truncation.
        """
        counter = self._instruments.get(MetricsRegistry.CONTEXT_TRUNCATED_TOTAL.name)
        if counter is not None:
            counter.add(count, attributes={"agent_id": agent_id, "source_kind": source_kind})

    def record_project_brief(self, mode: str, chars: int, capped: bool) -> None:
        """Record one composed project brief (FORGE-530)."""
        counter = self._instruments.get(MetricsRegistry.PROJECT_BRIEF_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"mode": mode, "capped": "true" if capped else "false"})
        hist = self._instruments.get(MetricsRegistry.PROJECT_BRIEF_CHARS.name)
        if hist is not None:
            hist.record(chars, attributes={"mode": mode})

    # ── Knowledge service latency (MET-401 / L1-A7) ───────────────────

    def record_knowledge_search_duration(self, top_k: int, duration: float) -> None:
        """Observe a ``knowledge_search_duration_seconds`` sample.

        ``top_k`` is bucketed into ``small`` (<=5) or ``large`` (>5) so
        the SLO can split per query shape. The Prometheus alert in
        ``observability/alerting/rules.yaml`` evaluates p95 over 5
        minutes against a 200 ms threshold (HP-RETR-08).
        """
        hist = self._instruments.get(MetricsRegistry.KNOWLEDGE_SEARCH_DURATION.name)
        if hist is not None:
            bucket = "small" if top_k <= 5 else "large"
            hist.record(duration, attributes={"top_k_bucket": bucket})

    # ── Consolidation (MET-454 / MET-455) ──────────────────────────────

    def record_consolidation_pass(
        self,
        mode: str,
        duration: float,
        *,
        accepted: int = 0,
        rejected: int = 0,
        contradictions: int = 0,
        stale_marked: int = 0,
    ) -> None:
        """Record one consolidation pass — duration + per-pass counters.

        ``mode`` is the ConsolidationMode value (background / on_demand /
        proactive / janitor). Counters are incremented by the per-pass
        totals from the ConsolidationReport so dashboards can track
        throughput, rejection rate, contradiction rate, and active-
        forgetting volume without scraping logs.
        """
        attrs = {"mode": mode}
        hist = self._instruments.get(MetricsRegistry.CONSOLIDATION_PASS_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes=attrs)
        pass_total = self._instruments.get(MetricsRegistry.CONSOLIDATION_PASS_TOTAL.name)
        if pass_total is not None:
            pass_total.add(1, attributes=attrs)
        accepted_c = self._instruments.get(
            MetricsRegistry.CONSOLIDATION_INSIGHTS_ACCEPTED_TOTAL.name
        )
        if accepted_c is not None and accepted:
            accepted_c.add(accepted, attributes=attrs)
        rejected_c = self._instruments.get(
            MetricsRegistry.CONSOLIDATION_INSIGHTS_REJECTED_TOTAL.name
        )
        if rejected_c is not None and rejected:
            rejected_c.add(rejected, attributes=attrs)
        contra_c = self._instruments.get(MetricsRegistry.CONSOLIDATION_CONTRADICTIONS_TOTAL.name)
        if contra_c is not None and contradictions:
            contra_c.add(contradictions, attributes={})
        stale_c = self._instruments.get(MetricsRegistry.CONSOLIDATION_STALE_MARKED_TOTAL.name)
        if stale_c is not None and stale_marked:
            stale_c.add(stale_marked, attributes={})

    # ── Chat harness loop (production-harness audit follow-up) ─────────

    def record_harness_turn(self, loop_kind: str, stop_reason: str, duration: float) -> None:
        """Record one full chat-harness tool-loop turn (counter + histogram).

        ``loop_kind`` is ``"native"`` or ``"react"``; ``stop_reason`` is
        ``ReActResult.stop_reason`` (``done``/``max_steps``/``timeout``/``error``).
        """
        attrs = {"loop_kind": loop_kind, "stop_reason": stop_reason}
        counter = self._instruments.get(MetricsRegistry.HARNESS_TURN_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes=attrs)
        hist = self._instruments.get(MetricsRegistry.HARNESS_TURN_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes=attrs)

    def record_harness_tool_call(self, tool_name: str, status: str, duration: float) -> None:
        """Record one harness tool call (counter + histogram).

        ``status`` is ``"ok"`` or ``"error"``.
        """
        attrs = {"tool_name": tool_name, "status": status}
        counter = self._instruments.get(MetricsRegistry.HARNESS_TOOL_CALL_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes=attrs)
        hist = self._instruments.get(MetricsRegistry.HARNESS_TOOL_CALL_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes=attrs)

    def record_design_flow_tool_refusal(self, tool_name: str, source: str) -> None:
        """Count one guardrail refusal of a design-flow service call (FORGE-492)."""
        counter = self._instruments.get(MetricsRegistry.DESIGN_FLOW_TOOL_REFUSAL_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"tool_name": tool_name, "source": source})

    def record_sim_field_payload(
        self, analysis_type: str, outcome: str, size_bytes: int | None = None
    ) -> None:
        """Count one result-field payload build, and its size when built (FORGE-532)."""
        counter = self._instruments.get(MetricsRegistry.SIM_FIELD_PAYLOAD_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"analysis_type": analysis_type, "outcome": outcome})
        if size_bytes is not None:
            hist = self._instruments.get(MetricsRegistry.SIM_FIELD_PAYLOAD_BYTES.name)
            if hist is not None:
                hist.record(size_bytes, attributes={"analysis_type": analysis_type})

    def record_sim_field_store(self, outcome: str) -> None:
        """Count one simulation_result field blob store attempt (FORGE-532)."""
        counter = self._instruments.get(MetricsRegistry.SIM_FIELD_STORE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_chat_ungrounded_claim(self, kind: str) -> None:
        """Count one chat reply flagged as ungrounded (FORGE-520)."""
        counter = self._instruments.get(MetricsRegistry.CHAT_UNGROUNDED_CLAIM_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"kind": kind})

    def record_mcp_tool_call(
        self, tool_id: str, status: str, duration: float, client: str = "unknown"
    ) -> None:
        """Record one MCP tool call (counter + histogram).

        ``client`` comes from the ``initialize`` handshake and is
        low-cardinality by construction -- a handful of harness names.
        Nothing here is labelled with a node id, an actor or a project:
        those are unbounded, and a label that grows without limit takes
        Prometheus down rather than telling you anything.
        """
        counter = self._instruments.get(MetricsRegistry.MCP_TOOL_CALL_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"tool_id": tool_id, "status": status, "client": client})
        hist = self._instruments.get(MetricsRegistry.MCP_TOOL_CALL_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes={"tool_id": tool_id, "status": status})

    def record_design_flow_started(self, engine: str, flow: str) -> None:
        """One design-flow run handed to an engine (FORGE-401)."""
        counter = self._instruments.get(MetricsRegistry.DESIGN_FLOW_RUN_STARTED.name)
        if counter is not None:
            counter.add(1, attributes={"engine": engine, "flow": flow})

    def record_design_flow_engine_unavailable(self, target: str) -> None:
        """A run start refused because the workflow engine was unreachable.

        The signal that matters most in FORGE-401. Without it, "Temporal is
        down" shows up as users reporting that runs will not start, which is
        both slower and less specific than a counter that says so.
        """
        counter = self._instruments.get(MetricsRegistry.DESIGN_FLOW_ENGINE_UNAVAILABLE.name)
        if counter is not None:
            counter.add(1, attributes={"target": target})

    def record_design_flow_gate(self, gate: str, outcome: str) -> None:
        """One gate outcome: approved, rejected, timed_out or not_ready."""
        counter = self._instruments.get(MetricsRegistry.DESIGN_FLOW_GATE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"gate": gate, "outcome": outcome})

    def record_design_flow_gate_announce(self, outcome: str) -> None:
        """One gate announcement: announced, unannounced or failed (FORGE-489)."""
        counter = self._instruments.get(MetricsRegistry.DESIGN_FLOW_GATE_ANNOUNCE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_kb_storage_init(self, outcome: str) -> None:
        """One knowledge-base storage set-up at gateway start (FORGE-594)."""
        counter = self._instruments.get(MetricsRegistry.KB_STORAGE_INIT_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_client_task(self, event: str) -> None:
        """One client-mode phase task event (FORGE-581)."""
        counter = self._instruments.get(MetricsRegistry.CLIENT_TASK_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"event": event})

    def record_mcp_origin_rejected(self) -> None:
        """One MCP HTTP request refused for its Origin (FORGE-604)."""
        counter = self._instruments.get(MetricsRegistry.MCP_ORIGIN_REJECTED_TOTAL.name)
        if counter is not None:
            counter.add(1)

    def record_mcp_error(self, tool_id: str, error_class: str, client: str = "unknown") -> None:
        """Record one MCP error, classified."""
        counter = self._instruments.get(MetricsRegistry.MCP_ERROR_TOTAL.name)
        if counter is not None:
            counter.add(
                1,
                attributes={"tool_id": tool_id, "error_class": error_class, "client": client},
            )

    def record_mcp_adapter_probe(self, adapter_id: str, reachable: bool) -> None:
        """Record the outcome of one adapter health probe."""
        counter = self._instruments.get(MetricsRegistry.MCP_ADAPTER_PROBE_TOTAL.name)
        if counter is not None:
            counter.add(
                1,
                attributes={
                    "adapter_id": adapter_id,
                    "result": "reachable" if reachable else "unreachable",
                },
            )

    def record_mcp_code_version(self, result: str, reloads: bool = False) -> None:
        """Record one code-version check (FORGE-411).

        ``result`` is ``stale``, ``current`` or ``unknown``. ``reloads`` says
        whether the process picks the mounted source up by itself, which is
        what makes a difference between the two SHAs expected rather than a
        fault -- it is a label so an alert can exclude the gateway without
        needing a second metric.
        """
        counter = self._instruments.get(MetricsRegistry.MCP_CODE_VERSION_CHECK_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"result": result, "reloads": str(reloads).lower()})

    def record_harness_provider_call(
        self, provider: str, model: str, role: str, duration: float
    ) -> None:
        """Record one harness model-provider call attempt's duration."""
        hist = self._instruments.get(MetricsRegistry.HARNESS_PROVIDER_CALL_DURATION.name)
        if hist is not None:
            hist.record(duration, attributes={"provider": provider, "model": model, "role": role})

    def record_harness_provider_fallback(
        self, primary: str, fallback: str, role: str, reason: str
    ) -> None:
        """Record one harness call served by a fallback provider (FORGE-468)."""
        counter = self._instruments.get(MetricsRegistry.HARNESS_PROVIDER_FALLBACK_TOTAL.name)
        if counter is not None:
            counter.add(
                1,
                attributes={
                    "primary": primary,
                    "fallback": fallback,
                    "role": role,
                    "reason": reason,
                },
            )

    def record_llm_usage(
        self,
        *,
        role: str,
        provider: str,
        model: str,
        prompt: int | None,
        completion: int | None,
        cached_input: int | None,
        cost_usd: float | None,
    ) -> None:
        """Record one LLM call's tokens and cost (FORGE-476)."""
        reported = prompt is not None or completion is not None
        calls = self._instruments.get(MetricsRegistry.LLM_CALLS_TOTAL.name)
        if calls is not None:
            calls.add(
                1,
                attributes={
                    "role": role,
                    "provider": provider,
                    "model": model,
                    "usage_reported": str(reported).lower(),
                },
            )
        tokens = self._instruments.get(MetricsRegistry.LLM_TOKENS_TOTAL.name)
        if tokens is not None:
            base = {"role": role, "provider": provider, "model": model}
            for kind, count in (
                ("prompt", prompt),
                ("completion", completion),
                ("cached_input", cached_input),
            ):
                if count:
                    tokens.add(count, attributes={**base, "kind": kind})
        cost = self._instruments.get(MetricsRegistry.LLM_COST_USD_TOTAL.name)
        if cost is not None and cost_usd:
            cost.add(cost_usd, attributes={"role": role, "provider": provider, "model": model})

    def record_llm_run_spend_exceeded(self, role: str) -> None:
        """A run's LLM spend crossed the runaway threshold (FORGE-476)."""
        counter = self._instruments.get(MetricsRegistry.LLM_RUN_SPEND_EXCEEDED_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"role": role})

    def record_tool_approval_resolution(self, outcome: str, trigger: str, result: str) -> None:
        """Record one held approval closed with no answer (FORGE-466)."""
        counter = self._instruments.get(MetricsRegistry.TOOL_APPROVAL_RESOLUTION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome, "trigger": trigger, "result": result})

    def record_approval_decision(
        self, kind: str, decision: str, surface: str, outcome: str
    ) -> None:
        """Record one decision through the unified approvals API (FORGE-507)."""
        counter = self._instruments.get(MetricsRegistry.APPROVAL_DECISION_TOTAL.name)
        if counter is not None:
            counter.add(
                1,
                attributes={
                    "kind": kind,
                    "decision": decision,
                    "surface": surface,
                    "outcome": outcome,
                },
            )

    def record_tool_approval(self, outcome: str) -> None:
        """Record one durable tool approval outcome (FORGE-780)."""
        counter = self._instruments.get(MetricsRegistry.TOOL_APPROVAL_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_unreachable_approval_hold(self, tool: str) -> None:
        """Record one approval hold with no reachable approver (FORGE-490)."""
        counter = self._instruments.get(MetricsRegistry.UNREACHABLE_APPROVAL_HOLD_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"tool": tool})

    def record_approval_gate_no_runtime(self, required_role: str) -> None:
        """Record one approval gate refused for lack of a Temporal runtime (FORGE-469)."""
        counter = self._instruments.get(MetricsRegistry.APPROVAL_GATE_NO_RUNTIME_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"required_role": required_role})

    def record_iteration_gate_unattended(self, outcome: str, agent_code: str) -> None:
        """Record one iteration gate decided with no approval workflow (FORGE-470)."""
        counter = self._instruments.get(MetricsRegistry.ITERATION_GATE_UNATTENDED_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome, "agent_code": agent_code})

    # ── Host watchdog (FORGE-713) ──────────────────────────────────────

    def _set_gauge(self, name: str, value: float, attributes: dict[str, str] | None = None) -> None:
        """Set an up-down-counter gauge to an absolute value by adding the delta."""
        gauge = self._instruments.get(name)
        if gauge is None:
            return
        key = (name, tuple(sorted((attributes or {}).items())))
        last = self._gauge_values.get(key, 0.0)
        if value != last:
            gauge.add(value - last, attributes=attributes or {})
            self._gauge_values[key] = value

    def record_watchdog_check(
        self, *, unhealthy: int, twin_in_memory: bool, memory_available: int | None
    ) -> None:
        """Record one watchdog pass: the heartbeat plus the state it saw."""
        counter = self._instruments.get(MetricsRegistry.WATCHDOG_CHECKS_TOTAL.name)
        if counter is not None:
            counter.add(1)
        self._set_gauge(MetricsRegistry.WATCHDOG_UNHEALTHY_CONTAINERS.name, unhealthy)
        self._set_gauge(MetricsRegistry.WATCHDOG_TWIN_IN_MEMORY.name, 1 if twin_in_memory else 0)
        if memory_available is not None:
            self._set_gauge(MetricsRegistry.HOST_MEMORY_AVAILABLE_BYTES.name, memory_available)

    def record_gate_review(self, source: str, recommendation: str, applied: str) -> None:
        """Record one reviewer note on a gate and what the policy did with it."""
        counter = self._instruments.get(MetricsRegistry.GATE_REVIEW_TOTAL.name)
        if counter is not None:
            counter.add(
                1,
                attributes={"source": source, "recommendation": recommendation, "applied": applied},
            )

    def record_gate_review_agreement(
        self, recommendation: str, decision: str, agreed: bool
    ) -> None:
        """Record whether a person's gate decision matched the reviewer's."""
        counter = self._instruments.get(MetricsRegistry.GATE_REVIEW_AGREEMENT_TOTAL.name)
        if counter is not None:
            counter.add(
                1,
                attributes={
                    "recommendation": recommendation,
                    "decision": decision,
                    "agreed": "true" if agreed else "false",
                },
            )

    def record_concept_image(self, outcome: str) -> None:
        """Record one concept image generation outcome (FORGE-761)."""
        counter = self._instruments.get(MetricsRegistry.CONCEPT_IMAGE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_concept_image_critique(self, verdict: str) -> None:
        """Record one concept image check by its verdict (FORGE-772)."""
        counter = self._instruments.get(MetricsRegistry.CONCEPT_IMAGE_CRITIQUE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"verdict": verdict})

    def record_imagine_studio(self, operation: str, outcome: str) -> None:
        """Record one Imagine stage operation by operation and outcome (FORGE-821)."""
        counter = self._instruments.get(MetricsRegistry.IMAGINE_STUDIO_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"operation": operation, "outcome": outcome})

    def record_concept_mesh(self, outcome: str, mode: str = "image") -> None:
        """Record one concept mesh generation outcome by mode (FORGE-778, FORGE-779)."""
        counter = self._instruments.get(MetricsRegistry.CONCEPT_MESH_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome, "mode": mode})

    def record_concept_segmentation(self, outcome: str, model: str) -> None:
        """Record one split of a 3D concept into parts, by outcome and model."""
        counter = self._instruments.get(MetricsRegistry.CONCEPT_SEGMENTATION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome, "model": model})

    def record_concept_part_mapping(self, outcome: str) -> None:
        """Record one part a person mapped (confirmed, corrected, not_a_component)."""
        counter = self._instruments.get(MetricsRegistry.CONCEPT_PART_MAPPING_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_concept_part_regeneration(self, outcome: str, source: str) -> None:
        """Record one part of a concept generated again, by outcome and source."""
        counter = self._instruments.get(MetricsRegistry.CONCEPT_PART_REGENERATION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome, "source": source})

    def record_concept_part_completion(self, outcome: str, mode: str) -> None:
        """Record one part of a concept sealed or completed, by outcome and mode."""
        counter = self._instruments.get(MetricsRegistry.CONCEPT_PART_COMPLETION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome, "mode": mode})

    def record_manufacturing_context_write(self, authority: str, outcome: str) -> None:
        """Record one manufacturing context field write by authority and outcome."""
        counter = self._instruments.get(MetricsRegistry.MANUFACTURING_CONTEXT_WRITE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"authority": authority, "outcome": outcome})

    def record_manufacturing_context_injection(self, generator: str, resolution: str) -> None:
        """Record one manufacturing context block given to a generator."""
        counter = self._instruments.get(MetricsRegistry.MANUFACTURING_CONTEXT_INJECTION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"generator": generator, "resolution": resolution})

    def record_change_classification(self, change_class: str, confidence: str, stage: str) -> None:
        """Record one change request classified before an authoritative edit."""
        counter = self._instruments.get(MetricsRegistry.CHANGE_CLASSIFICATION_TOTAL.name)
        if counter is not None:
            counter.add(
                1,
                attributes={"change_class": change_class, "confidence": confidence, "stage": stage},
            )

    def record_appearance_update(self, scope: str, outcome: str) -> None:
        """Record one appearance-only update by scope and outcome."""
        counter = self._instruments.get(MetricsRegistry.APPEARANCE_UPDATE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"scope": scope, "outcome": outcome})

    def record_change_guard_refusal(self, change_class: str, action: str) -> None:
        """Record one action held because of the turn's change classification."""
        counter = self._instruments.get(MetricsRegistry.CHANGE_GUARD_REFUSAL_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"change_class": change_class, "action": action})

    def record_structured_mesh(self, outcome: str) -> None:
        """Record one structured mesh (L1) outcome (C3D slice 2)."""
        counter = self._instruments.get(MetricsRegistry.STRUCTURED_MESH_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_maturity_query(self, product: str) -> None:
        """Record one maturity query by the product maturity answered (C3D slice 2)."""
        counter = self._instruments.get(MetricsRegistry.MATURITY_QUERY_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"product": product})

    def record_representation_mislabel(self, level: str, claimed: str) -> None:
        """Record one representation claim the guard corrected (C3D slice 2)."""
        counter = self._instruments.get(MetricsRegistry.REPRESENTATION_MISLABEL_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"level": level, "claimed": claimed})

    def record_dfm_evaluation(self, trigger: str, outcome: str) -> None:
        """Record one heuristic DFM evaluation of a project (C3D slice 4)."""
        counter = self._instruments.get(MetricsRegistry.DFM_EVALUATION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"trigger": trigger, "outcome": outcome})

    def record_dfm_finding(self, severity: str, process: str) -> None:
        """Record one DFM finding in a new finding set (C3D slice 4)."""
        counter = self._instruments.get(MetricsRegistry.DFM_FINDING_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"severity": severity, "process": process})

    def record_dfm_stale_recompute(self, reason: str) -> None:
        """Record incremental DFM work after a change (C3D slice 4)."""
        counter = self._instruments.get(MetricsRegistry.DFM_STALE_RECOMPUTE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"reason": reason})

    def record_dfm_decision(self, decision: str, outcome: str) -> None:
        """Record a person's decision on a DFM finding (C3D slice 4)."""
        counter = self._instruments.get(MetricsRegistry.DFM_DECISION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"decision": decision, "outcome": outcome})

    def record_preview_rebuild(self, op: str, seconds: float) -> None:
        """Record one browser-reported L2 preview rebuild time (C3D slice 5)."""
        hist = self._instruments.get(MetricsRegistry.PREVIEW_REBUILD_SECONDS.name)
        if hist is not None:
            hist.record(max(0.0, float(seconds)), attributes={"op": op})

    def record_preview_comparison(self, outcome: str) -> None:
        """Record one stored preview versus exact comparison (C3D slice 5)."""
        counter = self._instruments.get(MetricsRegistry.PREVIEW_COMPARISON_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_preview_discrepancy(self, check: str) -> None:
        """Record one comparison check over its tolerance (C3D slice 5)."""
        counter = self._instruments.get(MetricsRegistry.PREVIEW_DISCREPANCY_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"check": check})

    def record_manipulation_commit(self, source: str, outcome: str) -> None:
        """Record one direct-manipulation parameter commit (C3D slice 5)."""
        counter = self._instruments.get(MetricsRegistry.MANIPULATION_COMMIT_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"source": source, "outcome": outcome})

    def record_exact_incremental(self, outcome: str) -> None:
        """Record whether a parametric component's exact build was skipped (C3D slice 5)."""
        counter = self._instruments.get(MetricsRegistry.EXACT_INCREMENTAL_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_dfm_exact_evaluation(self, outcome: str) -> None:
        """Record one exact DFM evaluation of a validated revision (C3D slice 6)."""
        counter = self._instruments.get(MetricsRegistry.DFM_EXACT_EVALUATION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_dfm_exact_incomplete(self, rule: str, reason: str) -> None:
        """Record one exact DFM check that could not run (C3D slice 6)."""
        counter = self._instruments.get(MetricsRegistry.DFM_EXACT_INCOMPLETE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"rule": rule, "reason": reason})

    def record_dfm_slicer_check(self, outcome: str, reason: str) -> None:
        """Record one slicer cross-check after an exact FDM evaluation."""
        counter = self._instruments.get(MetricsRegistry.DFM_SLICER_CHECK_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome, "reason": reason})

    def record_dfm_report(self, outcome: str) -> None:
        """Record one DFM report generation (C3D slice 6)."""
        counter = self._instruments.get(MetricsRegistry.DFM_REPORT_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_manufacturing_release_refused(self, blocker: str) -> None:
        """Record one release refused while the checklist was unresolved (C3D slice 6)."""
        counter = self._instruments.get(MetricsRegistry.MANUFACTURING_RELEASE_REFUSED_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"blocker": blocker})

    def record_reconstruction_proposal(self, outcome: str) -> None:
        """Record one feature history proposal outcome (spec 4B)."""
        counter = self._instruments.get(MetricsRegistry.RECONSTRUCTION_PROPOSAL_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_reconstruction_feature_accepted(self, kind: str, suggested_by: str) -> None:
        """Record one proposed feature a person accepted (spec 4B)."""
        counter = self._instruments.get(MetricsRegistry.RECONSTRUCTION_FEATURE_ACCEPTED_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"kind": kind, "suggested_by": suggested_by})

    def record_reconstruction_llm_pass(self, outcome: str) -> None:
        """Record one optional model pass over a proposal (spec 4B)."""
        counter = self._instruments.get(MetricsRegistry.RECONSTRUCTION_LLM_PASS_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_interface_contract_check(self, kind: str, outcome: str) -> None:
        """Record one interface contract evaluation (lamp golden path)."""
        counter = self._instruments.get(MetricsRegistry.INTERFACE_CONTRACT_CHECK_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"kind": kind, "outcome": outcome})

    def record_interface_contract_violation(self, kind: str, check: str) -> None:
        """Record one violated contract check (lamp golden path)."""
        counter = self._instruments.get(MetricsRegistry.INTERFACE_CONTRACT_VIOLATION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"kind": kind, "check": check})

    def record_cad_loop_run(self, stop_reason: str) -> None:
        """Record one CAD design loop run that ended, by stop reason."""
        counter = self._instruments.get(MetricsRegistry.CAD_LOOP_RUN_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"stop_reason": stop_reason})

    def record_cad_loop_iteration(self, outcome: str) -> None:
        """Record one CAD design loop iteration, by outcome."""
        counter = self._instruments.get(MetricsRegistry.CAD_LOOP_ITERATION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_cad_loop_render(self, outcome: str) -> None:
        """Record one model view render the CAD design loop asked for."""
        counter = self._instruments.get(MetricsRegistry.CAD_LOOP_RENDER_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_cad_loop_critic(self, verdict: str) -> None:
        """Record one vision critic verdict in the CAD design loop."""
        counter = self._instruments.get(MetricsRegistry.CAD_LOOP_CRITIC_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"verdict": verdict})

    def record_cad_loop_model_call(self, role: str, cost_usd: float | None) -> None:
        """Record one CAD design loop model call, and its priced spend when known."""
        calls = self._instruments.get(MetricsRegistry.CAD_LOOP_MODEL_CALL_TOTAL.name)
        if calls is not None:
            calls.add(1, attributes={"role": role})
        spend = self._instruments.get(MetricsRegistry.CAD_LOOP_SPEND_USD_TOTAL.name)
        if spend is not None and cost_usd:
            spend.add(float(cost_usd), attributes={"role": role})

    def record_tool_job(self, tool: str, status: str) -> None:
        """Record one state change of a tool job (FORGE-783)."""
        counter = self._instruments.get(MetricsRegistry.TOOL_JOB_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"tool": tool, "status": status})

    def record_sketch_reading(self, outcome: str) -> None:
        """Record one sketch reading outcome (FORGE-762)."""
        counter = self._instruments.get(MetricsRegistry.SKETCH_READING_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_sketch_save(self, outcome: str) -> None:
        """Record one sketch save outcome (FORGE-789)."""
        counter = self._instruments.get(MetricsRegistry.SKETCH_SAVE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_exact_build(self, outcome: str) -> None:
        """Record one exact CAD build's outcome (CDE slice 6)."""
        counter = self._instruments.get(MetricsRegistry.EXACT_CAD_BUILD_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_geometry_validity(self, outcome: str) -> None:
        """Record one B-Rep validity verdict (CDE slice 6)."""
        counter = self._instruments.get(MetricsRegistry.GEOMETRY_VALIDITY_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_step_export(self, event: str) -> None:
        """Record a STEP export cache hit or miss, or its ending (CDE slice 6)."""
        counter = self._instruments.get(MetricsRegistry.STEP_EXPORT_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"event": event})

    def record_step_round_trip(self, outcome: str) -> None:
        """Record one STEP re-import round-trip verdict (CDE slice 6)."""
        counter = self._instruments.get(MetricsRegistry.STEP_ROUND_TRIP_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_cad_artifact_duration(self, stage: str, seconds: float) -> None:
        """Record how long one exact CAD stage took (CDE slice 6)."""
        hist = self._instruments.get(MetricsRegistry.CAD_ARTIFACT_DURATION.name)
        if hist is not None:
            hist.record(max(0.0, float(seconds)), attributes={"stage": stage})

    def record_cross_domain_impact(self, event: str, kind: str) -> None:
        """Record one cross-domain impact event (FORGE-793)."""
        counter = self._instruments.get(MetricsRegistry.CROSS_DOMAIN_IMPACT_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"event": event, "kind": kind})

    def record_interface_edge(self, edge_type: str, provenance: str, action: str) -> None:
        """Record one interface edge action (FORGE-793)."""
        counter = self._instruments.get(MetricsRegistry.INTERFACE_EDGE_TOTAL.name)
        if counter is not None:
            counter.add(
                1, attributes={"type": edge_type, "provenance": provenance, "action": action}
            )

    def record_sketch_plane(self, action: str, proposed_by: str) -> None:
        """Record one sketch plane manager action (FORGE-790)."""
        counter = self._instruments.get(MetricsRegistry.SKETCH_PLANE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"action": action, "proposed_by": proposed_by})

    def record_face_attachment(self, action: str) -> None:
        """Record one face attachment or reattachment answer (CDE slice 9)."""
        counter = self._instruments.get(MetricsRegistry.FACE_ATTACHMENT_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"action": action})

    def record_face_remap(self, outcome: str) -> None:
        """Record one face remap after a CAD head move (CDE slice 9)."""
        counter = self._instruments.get(MetricsRegistry.FACE_REMAP_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"outcome": outcome})

    def record_plane_blocked_commit(self, commit: str) -> None:
        """Record one commit refused while its sketch plane needs reattaching (CDE slice 9)."""
        counter = self._instruments.get(MetricsRegistry.PLANE_BLOCKED_COMMIT_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"commit": commit})

    def record_sketch_compile(self, stage: str, outcome: str) -> None:
        """Record one sketch compiler write (FORGE-791)."""
        counter = self._instruments.get(MetricsRegistry.SKETCH_COMPILE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"stage": stage, "outcome": outcome})

    def record_sketch_generation(self, mode: str, outcome: str) -> None:
        """Record one sketch generation outcome (FORGE-794)."""
        counter = self._instruments.get(MetricsRegistry.SKETCH_GENERATION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"mode": mode, "outcome": outcome})

    def record_sketch_generation_latency(self, mode: str, provider: str, seconds: float) -> None:
        """Record the measured time to a generation's first stored proposal (FORGE-794)."""
        histogram = self._instruments.get(
            MetricsRegistry.SKETCH_GENERATION_FIRST_RESULT_SECONDS.name
        )
        if histogram is not None:
            histogram.record(max(seconds, 0.0), attributes={"mode": mode, "provider": provider})

    def record_reference_conflict(self) -> None:
        """Record one conflict between authoritative references (FORGE-794)."""
        counter = self._instruments.get(MetricsRegistry.REFERENCE_CONFLICT_TOTAL.name)
        if counter is not None:
            counter.add(1)

    def record_supervisor_action(self, action: str, reason: str) -> None:
        """Record one run supervisor action (FORGE-734)."""
        counter = self._instruments.get(MetricsRegistry.SUPERVISOR_ACTION_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"action": action, "reason": reason})

    def record_operation_event(self, kind: str, event_type: str, lag_seconds: float) -> None:
        """Record one stored operation event and its publish lag (FORGE-785)."""
        counter = self._instruments.get(MetricsRegistry.OPERATION_EVENT_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"kind": kind, "type": event_type})
        histogram = self._instruments.get(MetricsRegistry.OPERATION_EVENT_PUBLISH_LAG.name)
        if histogram is not None:
            histogram.record(max(lag_seconds, 0.0), attributes={"kind": kind})

    def record_operation_publish_failure(self, kind: str, reason: str) -> None:
        """Record an operation event that was not stored (FORGE-785)."""
        counter = self._instruments.get(MetricsRegistry.OPERATION_EVENT_PUBLISH_FAILURE_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"kind": kind, "reason": reason})

    def record_operation_stream_replay(self) -> None:
        """Record a project stream reconnect served from the log (FORGE-785)."""
        counter = self._instruments.get(MetricsRegistry.OPERATION_STREAM_REPLAY_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={})

    def record_operation_stream_resync(self, reason: str) -> None:
        """Record a project stream told to refetch its snapshot (FORGE-785)."""
        counter = self._instruments.get(MetricsRegistry.OPERATION_STREAM_RESYNC_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"reason": reason})

    def record_operation_stream_active(self, delta: int) -> None:
        """Add ``delta`` (+1 or -1) to the connected project streams (FORGE-785)."""
        gauge = self._instruments.get(MetricsRegistry.OPERATION_STREAM_ACTIVE.name)
        if gauge is not None:
            gauge.add(delta, attributes={})

    def record_operation_active(self, kind: str, delta: int) -> None:
        """Add ``delta`` to the operations not yet terminal (FORGE-785)."""
        gauge = self._instruments.get(MetricsRegistry.OPERATION_ACTIVE.name)
        if gauge is not None:
            gauge.add(delta, attributes={"kind": kind})

    def record_operation_scheduling(self, kind: str, action: str, outcome: str = "ok") -> None:
        """Record a supersede, stale rejection, coalesce, cancel or retry (FORGE-788)."""
        counter = self._instruments.get(MetricsRegistry.OPERATION_SCHEDULING_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"kind": kind, "action": action, "outcome": outcome})

    def record_watchdog_restart(self, service: str, reason: str, outcome: str) -> None:
        """Record a restart the watchdog made, suppressed, or failed to make."""
        counter = self._instruments.get(MetricsRegistry.WATCHDOG_RESTARTS_TOTAL.name)
        if counter is not None:
            counter.add(1, attributes={"service": service, "reason": reason, "outcome": outcome})


def collector_for(component: str) -> MetricsCollector:
    """A :class:`MetricsCollector` on this process's active OTel meter.

    FORGE-413. Every MCP metric had zero series in Prometheus because the
    sidecar built no collector and passed none to the MCP server, so each
    recorder hit its ``if counter is not None`` guard and returned. Four
    FORGE-379 metrics and two FORGE-411 ones had never emitted a sample, and
    six alert rules could not fire.

    The gateway had this logic inline. It is here instead so a second
    entrypoint gets it by calling one function rather than by remembering to
    reproduce six lines -- the forgetting is the bug.

    ``init_observability`` publishes the SDK meter provider globally, so this
    needs no state passed through: whoever initialised telemetry first wins,
    and a process that initialised none gets a working no-op collector.

    It says which it got, unconditionally. A no-op collector is invisible by
    construction -- that is the whole failure mode -- so the one place it can
    be noticed is the line it writes at startup.
    """
    try:
        from opentelemetry import metrics as otel_metrics
    except ImportError:
        logger.info("metrics_collector_noop", component=component, reason="opentelemetry absent")
        return MetricsCollector()

    provider = otel_metrics.get_meter_provider()
    # A proxy provider is what you get before (or without) an SDK: it hands
    # out meters whose instruments go nowhere. Named rather than duck-typed
    # because "has get_meter" is true of both.
    if (
        type(provider).__name__.startswith("_Proxy")
        or type(provider).__name__ == "NoOpMeterProvider"
    ):
        logger.warning(
            "metrics_collector_noop",
            component=component,
            reason="no OTel SDK meter provider is configured, so nothing this "
            "component records will reach Prometheus",
            provider=type(provider).__name__,
        )
        return MetricsCollector()

    collector = MetricsCollector(meter=provider.get_meter(component))
    definitions = MetricsRegistry.all_metrics()
    collector.create_instruments(definitions)
    logger.info("metrics_collector_initialized", component=component, instruments=len(definitions))
    return collector
