"""Event bus configuration and Kafka topic definitions."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TopicConfig(BaseModel):
    """Configuration for a Kafka topic."""

    name: str
    partitions: int = 6
    replication_factor: int = 1
    retention_ms: int = Field(
        default=604800000, description="Retention in ms (default 7 days)"
    )
    cleanup_policy: str = "delete"


# --- Topic definitions ---

TOPIC_TWIN_EVENTS = TopicConfig(
    name="twin.events",
    partitions=12,
    retention_ms=604800000,  # 7 days
)

TOPIC_SESSION_EVENTS = TopicConfig(
    name="session.events",
    partitions=6,
    retention_ms=2592000000,  # 30 days
)

TOPIC_AGENT_EVENTS = TopicConfig(
    name="agent.events",
    partitions=6,
    retention_ms=2592000000,  # 30 days
)

TOPIC_APPROVAL_EVENTS = TopicConfig(
    name="approval.events",
    partitions=3,
    retention_ms=7776000000,  # 90 days
)

# MET-79: Chat topic with 90-day retention
TOPIC_AGENT_CHAT = TopicConfig(
    name="agent.chat",
    partitions=6,
    retention_ms=7776000000,  # 90 days
)

ALL_TOPICS = [
    TOPIC_TWIN_EVENTS,
    TOPIC_SESSION_EVENTS,
    TOPIC_AGENT_EVENTS,
    TOPIC_APPROVAL_EVENTS,
    TOPIC_AGENT_CHAT,
]
