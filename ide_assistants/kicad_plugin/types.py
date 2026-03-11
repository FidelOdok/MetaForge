"""Data types for the MetaForge KiCad plugin.

Uses plain dataclasses (not Pydantic) to avoid dependency conflicts with
the KiCad Python environment.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

# ---------------------------------------------------------------------------
# Actor
# ---------------------------------------------------------------------------

ActorKind = Literal["user", "agent", "system"]


@dataclass
class ChatActor:
    """Represents who authored a chat message."""

    kind: ActorKind
    display_name: str
    agent_code: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"kind": self.kind, "displayName": self.display_name}
        if self.agent_code is not None:
            d["agentCode"] = self.agent_code
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ChatActor:
        return cls(
            kind=data.get("kind", "system"),
            display_name=data.get("displayName", "Unknown"),
            agent_code=data.get("agentCode"),
        )


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

ScopeKind = Literal["session", "project", "bom-entry", "digital-twin-node"]


@dataclass
class ChatScope:
    """Defines the context scope for a chat thread."""

    kind: ScopeKind
    entity_id: str | None = None
    label: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"kind": self.kind}
        if self.entity_id is not None:
            d["entityId"] = self.entity_id
        if self.label is not None:
            d["label"] = self.label
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ChatScope:
        return cls(
            kind=data.get("kind", "project"),
            entity_id=data.get("entityId"),
            label=data.get("label"),
        )


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


@dataclass
class ChatMessage:
    """A single message in a chat thread."""

    id: str
    thread_id: str
    actor: ChatActor
    content: str
    created_at: str
    status: str | None = None

    @classmethod
    def create(
        cls,
        thread_id: str,
        actor: ChatActor,
        content: str,
    ) -> ChatMessage:
        """Factory that generates an id and timestamp automatically."""
        return cls(
            id=str(uuid.uuid4()),
            thread_id=thread_id,
            actor=actor,
            content=content,
            created_at=datetime.now(UTC).isoformat(),
        )

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "threadId": self.thread_id,
            "actor": self.actor.to_dict(),
            "content": self.content,
            "createdAt": self.created_at,
        }
        if self.status is not None:
            d["status"] = self.status
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ChatMessage:
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            thread_id=data.get("threadId", ""),
            actor=ChatActor.from_dict(data.get("actor", {})),
            content=data.get("content", ""),
            created_at=data.get("createdAt", ""),
            status=data.get("status"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, raw: str) -> ChatMessage:
        return cls.from_dict(json.loads(raw))


# ---------------------------------------------------------------------------
# Thread
# ---------------------------------------------------------------------------


@dataclass
class ChatThread:
    """A conversation thread consisting of messages within a scope."""

    id: str
    title: str
    scope: ChatScope
    messages: list[ChatMessage] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def create(cls, title: str, scope: ChatScope) -> ChatThread:
        now = datetime.now(UTC).isoformat()
        return cls(
            id=str(uuid.uuid4()),
            title=title,
            scope=scope,
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "scope": self.scope.to_dict(),
            "messages": [m.to_dict() for m in self.messages],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ChatThread:
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            title=data.get("title", ""),
            scope=ChatScope.from_dict(data.get("scope", {})),
            messages=[ChatMessage.from_dict(m) for m in data.get("messages", [])],
            created_at=data.get("createdAt", ""),
            updated_at=data.get("updatedAt", ""),
        )
