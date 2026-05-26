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
from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.llm import (
    LLMClient,
    StubLLMClient,
    parse_strict_json,
)
from digital_twin.memory.consolidation.modes import (
    ConsolidationMode,
    ConsolidationModeError,
    ConsolidationRunRequest,
)
from digital_twin.memory.consolidation.openrouter import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_PRIMARY_MODEL,
    OpenRouterConfig,
    OpenRouterError,
    OpenRouterLLMClient,
)
from digital_twin.memory.consolidation.orchestrator import (
    ConsolidationOrchestrator,
    ConsolidationReport,
)
from digital_twin.memory.consolidation.pgvector_insight_store import (
    PgVectorInsightStore,
)
from digital_twin.memory.consolidation.workflow import (
    DEFAULT_INTERVAL_SECONDS,
    ConsolidationActivities,
    ConsolidationActivityInput,
    ConsolidationActivityOutput,
    ConsolidationWorkflow,
    ConsolidationWorkflowInput,
    ConsolidationWorkflowOutput,
    register_consolidation_activities,
)
from digital_twin.memory.consolidation.synthesizer import (
    MAX_EXAMPLES_PER_GROUP,
    InsightSynthesizer,
)
from digital_twin.memory.consolidation.themes import (
    ConsolidationTheme,
    classify_theme,
)
from digital_twin.memory.consolidation.validator import (
    DEFAULT_MIN_CONFIDENCE,
    InsightValidator,
    ValidationResult,
)
from digital_twin.memory.consolidation.writer import (
    InMemoryInsightStore,
    InsightStore,
    SemanticMemoryWriter,
)

__all__ = [
    "ConsolidationMode",
    "ConsolidationModeError",
    "ConsolidationOrchestrator",
    "ConsolidationReport",
    "ConsolidationRunRequest",
    "ConsolidationTheme",
    "DEFAULT_FALLBACK_MODEL",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_PRIMARY_MODEL",
    "EventFetcher",
    "EventGrouper",
    "ExperienceGroup",
    "InMemoryEventFetcher",
    "InMemoryInsightStore",
    "Insight",
    "InsightKind",
    "InsightStore",
    "InsightSynthesizer",
    "InsightValidator",
    "LLMClient",
    "MAX_EXAMPLES_PER_GROUP",
    "OpenRouterConfig",
    "OpenRouterError",
    "OpenRouterLLMClient",
    "ConsolidationActivities",
    "ConsolidationActivityInput",
    "ConsolidationActivityOutput",
    "ConsolidationWorkflow",
    "ConsolidationWorkflowInput",
    "ConsolidationWorkflowOutput",
    "DEFAULT_INTERVAL_SECONDS",
    "PgVectorInsightStore",
    "register_consolidation_activities",
    "SemanticMemoryWriter",
    "StubLLMClient",
    "ValidationResult",
    "classify_theme",
    "parse_strict_json",
]
