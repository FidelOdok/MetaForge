"""Three-agent orchestration patterns (MET-474, MET-475).

Both hardware-design and coding workflows run the same three-agent
pattern:

  Planner → Generator → Evaluator → (gate fails) → Planner → ...

This package owns the **shared abstraction** — the Protocols every
concrete triple implements, the artifact store every cycle reads
and writes through, the quality-gate result shape every evaluator
returns, and the orchestrator loop that wires them together with
MET-474/MET-475's cap of 5 iterations before escalation.

The concrete agents (hardware Planner/Generator/Evaluator, coding
Planner/Generator/Evaluator) live in follow-up PRs that import from
here. Keeping the abstraction in ``orchestrator`` (Layer 3) means it
can read the Twin / Knowledge / Memory / Skill registry but stays
above ``domain_agents`` / ``tool_registry`` / ``mcp_core``, matching
the layer rules in ``CLAUDE.md``.
"""

from orchestrator.harness.artifacts import ArtifactStore, InMemoryArtifactStore
from orchestrator.harness.runtime import HarnessRuntime
from orchestrator.harness.three_agent import (
    Evaluator,
    GateResult,
    Generator,
    HarnessConfig,
    HarnessOutcome,
    Planner,
    ThreeAgentHarness,
)
from orchestrator.harness.toolkit import AgentContext, NativeToolDef, build_agent_runtime

__all__ = [
    "AgentContext",
    "ArtifactStore",
    "Evaluator",
    "GateResult",
    "Generator",
    "HarnessConfig",
    "HarnessOutcome",
    "HarnessRuntime",
    "InMemoryArtifactStore",
    "NativeToolDef",
    "Planner",
    "ThreeAgentHarness",
    "build_agent_runtime",
]
