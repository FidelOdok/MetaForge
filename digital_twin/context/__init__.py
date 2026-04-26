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
from digital_twin.context.conflicts import (
    Conflict,
    ConflictDetector,
    ConflictSeverity,
)
from digital_twin.context.models import (
    ContextAssemblyRequest,
    ContextAssemblyResponse,
    ContextFragment,
    ContextScope,
    ContextSourceKind,
)
from digital_twin.context.role_scope import (
    ROLE_COMPLIANCE_AGENT,
    ROLE_ELECTRONICS_AGENT,
    ROLE_MECHANICAL_AGENT,
    ROLE_SIMULATION_AGENT,
    all_roles,
    get_role_knowledge_types,
    is_known_role,
)
from digital_twin.context.staleness import compute_staleness

__all__ = [
    "ContextAssembler",
    "ContextAssemblyRequest",
    "ContextAssemblyResponse",
    "ContextFragment",
    "ContextScope",
    "ContextSourceKind",
    "ROLE_COMPLIANCE_AGENT",
    "ROLE_ELECTRONICS_AGENT",
    "ROLE_MECHANICAL_AGENT",
    "ROLE_SIMULATION_AGENT",
    "all_roles",
    "compute_staleness",
    "get_role_knowledge_types",
    "is_known_role",
    "Conflict",
    "ConflictDetector",
    "ConflictSeverity",
]
