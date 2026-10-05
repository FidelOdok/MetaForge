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
        )

    @classmethod
    def design_flow_metrics(cls) -> list[MetricDefinition]:
        """Design-flow engine and approval-gate metrics (FORGE-401, FORGE-469, FORGE-470)."""
        return [
            cls.DESIGN_FLOW_ENGINE_UNAVAILABLE,
            cls.DESIGN_FLOW_RUN_STARTED,
            cls.DESIGN_FLOW_GATE_TOTAL,
            cls.DESIGN_FLOW_GATE_ANNOUNCE_TOTAL,
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
            cls.FLOW_ITEM_SLOT_TOTAL,
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
        ]

    @classmethod
    def knowledge_metrics(cls) -> list[MetricDefinition]:
        """MET-401 / L1-A7 knowledge-service latency metrics."""
        return [
            cls.KNOWLEDGE_SEARCH_DURATION,
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
