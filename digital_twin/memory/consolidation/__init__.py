"""Consolidation pipeline (MET-454).

Reads agent-task experiences from the Tier-2 event stream, groups them
by theme, synthesizes insights via an LLM, validates the output, writes
durable knowledge to Neo4j + pgvector, and archives the raw events.

This package holds the stage-by-stage implementation:

* ``themes`` — ``ConsolidationTheme`` enum + rule-based classifier
* ``grouper`` — ``EventGrouper`` clusters experiences by theme
* ``fetcher`` — ``EventFetcher`` Protocol + in-memory adapter

Synthesizer / validator / writer / archiver land in subsequent commits
once the deterministic backbone is testable end-to-end.
"""

from digital_twin.memory.consolidation.fetcher import (
    EventFetcher,
    InMemoryEventFetcher,
)
from digital_twin.memory.consolidation.grouper import EventGrouper, ExperienceGroup
from digital_twin.memory.consolidation.themes import (
    ConsolidationTheme,
    classify_theme,
)

__all__ = [
    "ConsolidationTheme",
    "EventFetcher",
    "EventGrouper",
    "ExperienceGroup",
    "InMemoryEventFetcher",
    "classify_theme",
]
