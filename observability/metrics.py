"""MetaForge Prometheus metrics registry and collector.

Defines metric descriptors as Pydantic models and provides a
``MetricsCollector`` that wraps OpenTelemetry meter instruments. When the
OTel SDK is not installed the collector degrades to a silent no-op so the
rest of the platform can import it unconditionally.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, field_validator

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
        )

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
