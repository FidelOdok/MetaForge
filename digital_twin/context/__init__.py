"""Context assembly layer (MET-315).

Orchestrates structural (Twin graph) and semantic (KnowledgeService)
context for agent reasoning. Every fragment carries a source attribution
so the agent can trace any claim back to its origin.

Follow-up issues build on this foundation:

* MET-316 — role-based scoping (agent → knowledge_type map)
* MET-317 — token-budget management with tiktoken + priority scoring
* MET-322 — conflict detection across sources
* MET-323 — staleness aging
"""

from digital_twin.context.assembler import ContextAssembler
from digital_twin.context.models import (
    ContextAssemblyRequest,
    ContextAssemblyResponse,
    ContextFragment,
    ContextScope,
    ContextSourceKind,
)

__all__ = [
    "ContextAssembler",
    "ContextAssemblyRequest",
    "ContextAssemblyResponse",
    "ContextFragment",
    "ContextScope",
    "ContextSourceKind",
]
