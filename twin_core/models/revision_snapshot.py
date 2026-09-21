"""RevisionSnapshot — captures a controlled object's field values just
before an update overwrites them (FORGE-51, spec section 12 Revision Model:
"Historical revisions remain queryable").

``TwinAPI.update_constraint``/``update_engineering_entity`` (FORGE-50) always
increment ``revision`` on write, so the *current* object never silently
changes value at the same revision number -- but until this node existed,
the *previous* revision's field values were simply overwritten and lost,
which would have made a Baseline's ``includes: [REQ-001@3, ...]`` references
unanswerable the moment REQ-001 moved on to @4. One snapshot is recorded per
applied update, holding the state as it was *before* that update.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from twin_core.models.base import NodeBase
from twin_core.models.enums import NodeType
from twin_core.models.patch import ControlledEntityKind


class RevisionSnapshot(NodeBase):
    """The field values of one controlled entity at one past revision."""

    id: UUID = Field(default_factory=uuid4)
    node_type: NodeType = NodeType.REVISION_SNAPSHOT
    entity_id: UUID
    entity_kind: ControlledEntityKind
    revision: int
    data: dict[str, Any]
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
