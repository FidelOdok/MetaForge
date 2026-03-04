"""Pydantic v2 models for the enterprise audit log.

MET-126
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AuditEventType(enum.StrEnum):
    """Categories of auditable events."""

    policy_decision = "policy_decision"
    graph_mutation = "graph_mutation"
    approval_action = "approval_action"
    session_lifecycle = "session_lifecycle"
    authentication = "authentication"
    authorization = "authorization"


class ExportDestination(enum.StrEnum):
    """Supported export targets for audit logs."""

    s3 = "s3"
    azure_blob = "azure_blob"
    gcs = "gcs"
    siem = "siem"
    local_file = "local_file"


# ---------------------------------------------------------------------------
# Core audit event
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    """A single auditable event in the MetaForge platform."""

    event_id: UUID = Field(default_factory=uuid4)
    event_type: AuditEventType
    actor: str
    action: str
    resource_type: str
    resource_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tenant_id: str = ""
    trace_id: str | None = None

    @field_validator("actor", "action", "resource_type", "resource_id")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must be a non-empty string")
        return v


# ---------------------------------------------------------------------------
# Export configuration
# ---------------------------------------------------------------------------


class ExportConfig(BaseModel):
    """Configuration for audit log export destinations."""

    destination: ExportDestination
    format: str = "jsonl"
    batch_size: int = 100
    flush_interval_seconds: int = 60

    @field_validator("batch_size")
    @classmethod
    def _validate_batch_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"batch_size must be >= 1, got {v}")
        return v

    @field_validator("flush_interval_seconds")
    @classmethod
    def _validate_flush_interval(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"flush_interval_seconds must be >= 1, got {v}")
        return v
