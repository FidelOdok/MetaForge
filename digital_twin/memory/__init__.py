"""Agent memory layer — semantic indexing of agent task events.

This package implements Tier 2→Tier 3b of the agent memory architecture
(see ``docs/architecture/agent-memory-architecture.md``). It consumes
``AGENT_TASK_*`` events from the event bus, scores their importance,
transforms them into experience records, embeds them, and stores them
in pgvector for semantic retrieval via ``retrieve_similar_experience``.
"""

from digital_twin.memory.importance import (
    DEFAULT_RECENCY_HALF_LIFE_HOURS,
    ImportanceScore,
    ImportanceWeights,
    score_importance,
)
from digital_twin.memory.models import (
    ConfidenceTier,
    ExperienceMemory,
    MemorySearchHit,
)

__all__ = [
    "ConfidenceTier",
    "DEFAULT_RECENCY_HALF_LIFE_HOURS",
    "ExperienceMemory",
    "ImportanceScore",
    "ImportanceWeights",
    "MemorySearchHit",
    "score_importance",
]
