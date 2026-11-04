"""The operation event contract (FORGE-785, CDE slice 1).

One operation is one unit of work a person started and may watch: a
design-flow run (``run:<run_id>``), a slow tool job (``job:<job_id>``) or a
Create chat turn (``chat:<turn_id>``). Its phases are stages. Approvals are
not operations: they surface as ``review_required`` events, with
``action_refs``, on the operation they block.

Field names here are a contract shared with the dashboard (FORGE-786). Do not
rename them; see ``docs/operations.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OperationKind(StrEnum):
    RUN = "run"
    JOB = "job"
    CHAT = "chat"


class OperationDomain(StrEnum):
    CONCEPT = "concept"
    SKETCH = "sketch"
    MECHANICAL = "mechanical"
    ELECTRONICS = "electronics"
    FIRMWARE = "firmware"
    SIMULATION = "simulation"
    MANUFACTURING = "manufacturing"
    CROSS_DOMAIN = "cross_domain"
    GENERAL = "general"


class OperationEventType(StrEnum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    STARTED = "started"
    MILESTONE = "milestone"
    PARTIAL_RESULT = "partial_result"
    PREVIEW_READY = "preview_ready"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"
    RESUMED = "resumed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    STALE = "stale"


class OperationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    STALE = "stale"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


#: Once an operation reaches one of these, no later non-terminal event applies.
TERMINAL_STATES: frozenset[OperationState] = frozenset(
    {
        OperationState.COMPLETED,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.SUPERSEDED,
        OperationState.STALE,
    }
)

#: The prefix of an ``operation_id`` names its kind.
_PREFIX_KIND = {"run": OperationKind.RUN, "job": OperationKind.JOB, "chat": OperationKind.CHAT}

#: Longest server-authored message kept on an event.
MESSAGE_MAX_CHARS = 500


def kind_of(operation_id: str) -> OperationKind | None:
    """The kind an ``operation_id`` names by its prefix, or None."""
    prefix, sep, rest = operation_id.partition(":")
    if not sep or not rest:
        return None
    return _PREFIX_KIND.get(prefix)


def is_terminal(state: OperationState | str) -> bool:
    return OperationState(state) in TERMINAL_STATES


def utc_now() -> datetime:
    return datetime.now(UTC)


class ArtifactRef(BaseModel):
    """Something an operation produced or points at."""

    kind: Literal["work_product", "file", "glb", "image", "step"]
    id: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=200)
    url: str | None = Field(default=None, max_length=2000)
    #: How far the artifact can be trusted. A preview is never ``validated``.
    authority: Literal["illustrative", "preview", "exact", "validated"] | None = None


class ActionRef(BaseModel):
    """Something a person can do about an operation."""

    kind: Literal["approval", "retry", "cancel", "open"]
    id: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=200)


class Progress(BaseModel):
    """Measured progress. Only sent when the total is known."""

    done: int = Field(ge=0)
    total: int = Field(ge=1)
    unit: str = Field(default="steps", max_length=40)

    @model_validator(mode="after")
    def _done_within_total(self) -> Progress:
        if self.done > self.total:
            raise ValueError("progress.done cannot exceed progress.total")
        return self


class _EventFields(BaseModel):
    """The fields a producer sets (everything bar the store's own)."""

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=3, max_length=200)
    run_id: str | None = Field(default=None, max_length=200)
    kind: OperationKind | None = None
    title: str = Field(default="", max_length=200)
    domain: OperationDomain = OperationDomain.GENERAL
    stage_id: str | None = Field(default=None, max_length=200)
    stage_title: str | None = Field(default=None, max_length=200)
    type: OperationEventType
    state: OperationState
    occurred_at: datetime | None = None
    base_revision: str | None = Field(default=None, max_length=200)
    target_revision: str | None = Field(default=None, max_length=200)
    #: Server-authored plain text. Never a prompt, tool arguments or a result.
    message: str = Field(default="", max_length=MESSAGE_MAX_CHARS)
    severity: Severity = Severity.INFO
    artifact_refs: list[ArtifactRef] = Field(default_factory=list, max_length=50)
    requires_action: bool = False
    action_refs: list[ActionRef] = Field(default_factory=list, max_length=10)
    progress: Progress | None = None

    @field_validator("operation_id")
    @classmethod
    def _known_prefix(cls, v: str) -> str:
        if kind_of(v) is None:
            raise ValueError("operation_id must be 'run:<id>', 'job:<id>' or 'chat:<id>'")
        return v

    @model_validator(mode="after")
    def _kind_matches_prefix(self) -> _EventFields:
        expected = kind_of(self.operation_id)
        if self.kind is None:
            self.kind = expected
        elif self.kind is not expected:
            raise ValueError(f"kind '{self.kind}' does not match operation_id prefix")
        if self.occurred_at is None:
            self.occurred_at = utc_now()
        elif self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=UTC)
        return self


class OperationEventIn(_EventFields):
    """An event as a producer hands it over: no ``project_seq`` yet.

    ``event_id`` makes a retried post idempotent. ``sequence`` may be left
    out, and the store then gives the event the next one for its operation.
    """

    event_id: str | None = Field(default=None, min_length=1, max_length=100)
    sequence: int | None = Field(default=None, ge=1)
    #: Optional on ingest; when given it must match the path's project.
    project_id: str | None = Field(default=None, max_length=200)


class OperationEvent(_EventFields):
    """One stored event, as it is streamed (the contract's ``OperationEvent``)."""

    model_config = ConfigDict(extra="ignore")

    event_id: str
    project_id: str
    project_seq: int
    sequence: int
    kind: OperationKind | None = None
    occurred_at: datetime | None = None

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class StageSnapshot(BaseModel):
    """The latest state of one stage of an operation."""

    stage_id: str
    stage_title: str | None = None
    domain: OperationDomain = OperationDomain.GENERAL
    state: OperationState
    updated_at: datetime


class OperationSnapshot(BaseModel):
    """The latest state of one operation, folded from its events."""

    operation_id: str
    kind: OperationKind
    domain: OperationDomain
    title: str = ""
    state: OperationState
    run_id: str | None = None
    started_at: datetime | None = None
    updated_at: datetime
    last_sequence: int
    base_revision: str | None = None
    target_revision: str | None = None
    message: str = ""
    severity: Severity = Severity.INFO
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    requires_action: bool = False
    action_refs: list[ActionRef] = Field(default_factory=list)
    progress: Progress | None = None
    stages: list[StageSnapshot] = Field(default_factory=list)


class OperationListResponse(BaseModel):
    """``GET /v1/projects/{project_id}/operations``."""

    project_id: str
    #: The project's latest ``project_seq``: pass it as ``?after=`` to the stream.
    cursor: int
    operations: list[OperationSnapshot]


class OperationCancelResponse(BaseModel):
    operation_id: str
    state: OperationState


class OperationIngestResponse(BaseModel):
    event_id: str
    project_seq: int
    sequence: int
    duplicate: bool = False
