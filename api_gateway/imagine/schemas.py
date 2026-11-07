"""Request and response shapes of the Imagine stage (FORGE-821).

The field names are a contract shared with the dashboard (FORGE-822); see
``docs/imagine-stage.md``. Do not rename them without changing both sides.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: The kinds ``POST /events`` takes, one per browser collection.
EventKind = Literal[
    "choice",
    "scenario",
    "context_edit",
    "assumption_confirmed",
    "requirement",
    "ledger",
    "screening",
]

#: Browser collection (``fg:write``) to event kind.
COLLECTION_KINDS: dict[str, str] = {
    "choices": "choice",
    "scenarios": "scenario",
    "contextEdits": "context_edit",
    "assumptions": "assumption_confirmed",
    "requirements": "requirement",
    "ledger": "ledger",
    "screening": "screening",
}
#: Event kind to browser collection, for the snapshot the host sends back.
KIND_COLLECTIONS: dict[str, str] = {v: k for k, v in COLLECTION_KINDS.items()}

StudioStatus = Literal["none", "draft", "handed_over"]
ItemStatus = Literal["done", "missing", "assumed"]


class HandoverRecord(BaseModel):
    """The handover that froze a studio revision."""

    op_id: str
    revision: int
    at: str
    by: str
    created: list[str] = Field(default_factory=list)
    #: FORGE-824: set when an agent handed over for a person under the
    #: owner's delegation (``by`` is then ``agent:<name>``, never the person).
    agent: str | None = None
    on_behalf_of: str | None = None


class StudioResponse(BaseModel):
    """``GET``/``PUT /studio``."""

    project_id: str
    status: StudioStatus
    revision: int
    config_ref: str | None = None
    engine_url: str
    config: dict[str, Any] | None = None
    updated_at: str | None = None
    handover: HandoverRecord | None = None
    #: The twin-backed state of each browser collection, keyed by id. The
    #: host sends it to the engine as ``fg:snapshot {collections}``.
    collections: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    #: What the config validation flagged (warnings only; errors refuse the PUT).
    warnings: list[str] = Field(default_factory=list)
    #: After ``POST /rework``: the records the impact path marked stale because
    #: they were made for the handed-over revision. Empty otherwise.
    stale: list[str] = Field(default_factory=list)


class StudioPut(BaseModel):
    """``PUT /studio``: a formgraph product config."""

    model_config = ConfigDict(extra="forbid")

    config: dict[str, Any]


class ImagineEvent(BaseModel):
    """``POST /events``: one browser write, idempotent per ``event_id``."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    kind: EventKind
    data: dict[str, Any]


class EventAccepted(BaseModel):
    accepted: bool = True
    mapped_to: list[str] = Field(default_factory=list)
    #: True when the event id was seen before; nothing was written again.
    duplicate: bool = False


class HandoverItem(BaseModel):
    id: str
    label: str
    status: ItemStatus
    detail: str = ""


class HandoverChecklist(BaseModel):
    ready: bool
    items: list[HandoverItem]


class HandoverPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op_id: str = Field(min_length=1, max_length=128)


class HandoverResult(BaseModel):
    handed_over: bool = True
    revision: int
    created: list[str] = Field(default_factory=list)


class ReworkPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)


class ReworkResult(BaseModel):
    reopened: bool = True
    revision: int
    #: Records the impact path marked stale because they were for the
    #: handed-over revision.
    stale: list[str] = Field(default_factory=list)


class AcceptResult(BaseModel):
    """``twin.imagine_accept``: what an agent accepted for a person (FORGE-827)."""

    assumptions: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    mapped_to: list[str] = Field(default_factory=list)
    agent: str
    on_behalf_of: str
    #: The handover checklist after the acceptance.
    checklist: HandoverChecklist
