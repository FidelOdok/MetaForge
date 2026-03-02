"""AgentNode — records which agent produced or modified artifacts."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType


class AgentNode(NodeBase):
    """An agent execution record linking provenance from intent to output."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.AGENT
    agent_type: str
    domain: str
    session_id: UUID
    skills_used: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    status: str = "running"
