"""SLO/SLI definitions as Pydantic v2 models.

Each SLO is a class constant on :class:`SLORegistry` so that every part of
the platform can reference a canonical target without magic numbers.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class SLIDefinition(BaseModel):
    """Service Level Indicator -- the metric being measured."""

    name: str
    description: str
    metric_name: str
    good_events_query: str
    total_events_query: str
    unit: str = ""

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("SLI name must not be empty")
        return v


class SLODefinition(BaseModel):
    """Service Level Objective -- a target applied to an SLI."""

    name: str
    description: str
    sli: SLIDefinition
    target: float  # e.g. 99.9 means 99.9%
    window_days: int = 30
    error_budget_minutes: float  # total budget in minutes for the window

    @field_validator("target")
    @classmethod
    def _target_range(cls, v: float) -> float:
        if not (0.0 < v <= 100.0):
            raise ValueError("SLO target must be between 0 (exclusive) and 100 (inclusive)")
        return v

    @field_validator("window_days")
    @classmethod
    def _window_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("SLO window must be a positive number of days")
        return v

    @field_validator("error_budget_minutes")
    @classmethod
    def _budget_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Error budget must be non-negative")
        return v


# ---------------------------------------------------------------------------
# Canonical SLO registry -- 9 SLOs
# ---------------------------------------------------------------------------


class SLORegistry:
    """Central collection of all MetaForge SLO definitions."""

    # 1. Gateway availability 99.9% (30d)
    GATEWAY_AVAILABILITY = SLODefinition(
        name="gateway_availability",
        description="Gateway HTTP availability (non-5xx responses)",
        sli=SLIDefinition(
            name="gateway_availability_sli",
            description="Ratio of non-5xx responses to total responses",
            metric_name="metaforge_gateway_request_total",
            good_events_query=(
                'sum(rate(metaforge_gateway_request_total{status_code!~"5.."}[30d]))'
            ),
            total_events_query=("sum(rate(metaforge_gateway_request_total[30d]))"),
            unit="ratio",
        ),
        target=99.9,
        window_days=30,
        error_budget_minutes=43.2,  # 30d * 24h * 60m * 0.001
    )

    # 2. Gateway latency p99 < 100ms
    GATEWAY_LATENCY_P99 = SLODefinition(
        name="gateway_latency_p99",
        description="Gateway HTTP p99 latency below 100ms",
        sli=SLIDefinition(
            name="gateway_latency_p99_sli",
            description="Ratio of requests completing within 100ms at p99",
            metric_name="metaforge_gateway_request_duration_seconds",
            good_events_query=(
                'sum(rate(metaforge_gateway_request_duration_seconds_bucket{le="0.1"}[30d]))'
            ),
            total_events_query=("sum(rate(metaforge_gateway_request_duration_seconds_count[30d]))"),
            unit="s",
        ),
        target=99.0,
        window_days=30,
        error_budget_minutes=432.0,  # 30d * 24h * 60m * 0.01
    )

    # 3. Agent success rate 95%
    AGENT_SUCCESS_RATE = SLODefinition(
        name="agent_success_rate",
        description="Agent execution success rate",
        sli=SLIDefinition(
            name="agent_success_rate_sli",
            description="Ratio of successful agent executions to total",
            metric_name="metaforge_agent_execution_total",
            good_events_query=('sum(rate(metaforge_agent_execution_total{status="success"}[30d]))'),
            total_events_query=("sum(rate(metaforge_agent_execution_total[30d]))"),
        ),
        target=95.0,
        window_days=30,
        error_budget_minutes=2160.0,  # 30d * 24h * 60m * 0.05
    )

    # 4. Agent latency p95 < 30s
    AGENT_LATENCY_P95 = SLODefinition(
        name="agent_latency_p95",
        description="Agent execution p95 latency below 30 seconds",
        sli=SLIDefinition(
            name="agent_latency_p95_sli",
            description="Ratio of agent executions completing within 30s at p95",
            metric_name="metaforge_agent_execution_duration_seconds",
            good_events_query=(
                'sum(rate(metaforge_agent_execution_duration_seconds_bucket{le="30"}[30d]))'
            ),
            total_events_query=("sum(rate(metaforge_agent_execution_duration_seconds_count[30d]))"),
            unit="s",
        ),
        target=95.0,
        window_days=30,
        error_budget_minutes=2160.0,  # 30d * 24h * 60m * 0.05
    )

    # 5. Neo4j read latency p99 < 50ms
    NEO4J_READ_LATENCY_P99 = SLODefinition(
        name="neo4j_read_latency_p99",
        description="Neo4j read query p99 latency below 50ms",
        sli=SLIDefinition(
            name="neo4j_read_latency_p99_sli",
            description="Ratio of Neo4j reads completing within 50ms at p99",
            metric_name="metaforge_neo4j_query_duration_seconds",
            good_events_query=(
                'sum(rate(metaforge_neo4j_query_duration_seconds_bucket{operation="read",le="0.05"}[30d]))'
            ),
            total_events_query=(
                'sum(rate(metaforge_neo4j_query_duration_seconds_count{operation="read"}[30d]))'
            ),
            unit="s",
        ),
        target=99.0,
        window_days=30,
        error_budget_minutes=432.0,
    )

    # 6. Neo4j write latency p99 < 200ms
    NEO4J_WRITE_LATENCY_P99 = SLODefinition(
        name="neo4j_write_latency_p99",
        description="Neo4j write query p99 latency below 200ms",
        sli=SLIDefinition(
            name="neo4j_write_latency_p99_sli",
            description="Ratio of Neo4j writes completing within 200ms at p99",
            metric_name="metaforge_neo4j_query_duration_seconds",
            good_events_query=(
                'sum(rate(metaforge_neo4j_query_duration_seconds_bucket{operation="write",le="0.2"}[30d]))'
            ),
            total_events_query=(
                'sum(rate(metaforge_neo4j_query_duration_seconds_count{operation="write"}[30d]))'
            ),
            unit="s",
        ),
        target=99.0,
        window_days=30,
        error_budget_minutes=432.0,
    )

    # 7. pgvector search latency p99 < 100ms
    PGVECTOR_SEARCH_LATENCY_P99 = SLODefinition(
        name="pgvector_search_latency_p99",
        description="pgvector search p99 latency below 100ms",
        sli=SLIDefinition(
            name="pgvector_search_latency_p99_sli",
            description="Ratio of pgvector searches completing within 100ms at p99",
            metric_name="metaforge_pgvector_search_duration_seconds",
            good_events_query=(
                'sum(rate(metaforge_pgvector_search_duration_seconds_bucket{le="0.1"}[30d]))'
            ),
            total_events_query=("sum(rate(metaforge_pgvector_search_duration_seconds_count[30d]))"),
            unit="s",
        ),
        target=99.0,
        window_days=30,
        error_budget_minutes=432.0,
    )

    # 8. Kafka consumer lag < 1000 messages
    KAFKA_CONSUMER_LAG = SLODefinition(
        name="kafka_consumer_lag",
        description="Kafka consumer lag stays below 1000 messages per partition",
        sli=SLIDefinition(
            name="kafka_consumer_lag_sli",
            description="Ratio of time consumer lag is below 1000 messages",
            metric_name="metaforge_kafka_consumer_lag",
            good_events_query=("count(metaforge_kafka_consumer_lag < 1000)"),
            total_events_query=("count(metaforge_kafka_consumer_lag)"),
        ),
        target=99.0,
        window_days=30,
        error_budget_minutes=432.0,
    )

    # 9. Kafka DLQ rate < 0.1%
    KAFKA_DLQ_RATE = SLODefinition(
        name="kafka_dlq_rate",
        description="Kafka dead letter queue rate below 0.1% of consumed messages",
        sli=SLIDefinition(
            name="kafka_dlq_rate_sli",
            description="Ratio of consumed messages that do NOT go to DLQ",
            metric_name="metaforge_kafka_dead_letters_total",
            good_events_query=(
                "sum(rate(metaforge_kafka_messages_consumed_total[30d]))"
                " - sum(rate(metaforge_kafka_dead_letters_total[30d]))"
            ),
            total_events_query=("sum(rate(metaforge_kafka_messages_consumed_total[30d]))"),
        ),
        target=99.9,
        window_days=30,
        error_budget_minutes=43.2,
    )

    @classmethod
    def all_slos(cls) -> list[SLODefinition]:
        """Return all 9 SLO definitions."""
        return [
            cls.GATEWAY_AVAILABILITY,
            cls.GATEWAY_LATENCY_P99,
            cls.AGENT_SUCCESS_RATE,
            cls.AGENT_LATENCY_P95,
            cls.NEO4J_READ_LATENCY_P99,
            cls.NEO4J_WRITE_LATENCY_P99,
            cls.PGVECTOR_SEARCH_LATENCY_P99,
            cls.KAFKA_CONSUMER_LAG,
            cls.KAFKA_DLQ_RATE,
        ]
