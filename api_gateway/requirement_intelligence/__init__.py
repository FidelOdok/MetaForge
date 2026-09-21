"""Requirement-intelligence agents (FORGE-54, Phase 3 of epic FORGE-35).

Lives under ``api_gateway`` rather than ``orchestrator`` (where the epic's
Phase 1/2 primitives sit) because these agents need an LLM completion —
``api_gateway.chat.harness_backend.run_chat_turn`` — and ``orchestrator``'s
own layer rule (orchestrator/CLAUDE.md) forbids importing ``api_gateway``.
``api_gateway`` (layer 4) may import ``orchestrator``/``twin_core`` freely,
so this is the correct side of that boundary for an LLM-calling agent.
"""

from api_gateway.requirement_intelligence.clarification import ClarificationAgent
from api_gateway.requirement_intelligence.intent_interpreter import IntentInterpreterAgent
from api_gateway.requirement_intelligence.models import AgentResult, Unknown, UnknownSeverity

__all__ = [
    "AgentResult",
    "ClarificationAgent",
    "IntentInterpreterAgent",
    "Unknown",
    "UnknownSeverity",
]
