"""Assistant API layer — common IDE integration interface.

Provides schemas, routes, and the approval workflow that IDE assistants
(VS Code, KiCad plugin, FreeCAD plugin) use to interact with the
MetaForge Orchestrator and Digital Twin.
"""

from api_gateway.assistant.approval import ApprovalWorkflow
from api_gateway.assistant.routes import router, workflow
from api_gateway.assistant.schemas import (
    ApprovalDecision,
    ApprovalDecisionType,
    AssistantRequest,
    AssistantResponse,
    ChangeStatus,
    DesignChangeProposal,
    EventType,
    ProposalListResponse,
    WebSocketEvent,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalDecisionType",
    "ApprovalWorkflow",
    "AssistantRequest",
    "AssistantResponse",
    "ChangeStatus",
    "DesignChangeProposal",
    "EventType",
    "ProposalListResponse",
    "WebSocketEvent",
    "router",
    "workflow",
]
