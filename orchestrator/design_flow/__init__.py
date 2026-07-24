"""Gated design-flow harness (MET-10, Phase 1).

A product-agnostic spine that walks an engineering design lifecycle
(goal -> requirements -> design -> simulation -> ...) as a sequence of
*phases*, pausing at a *gate* between phases for human approval. The
reasoning inside each phase is delegated to a :class:`PhaseBrain` (in
production, the ReAct harness per ADR-008); the spine owns only the
sequencing, the gates, and the run lifecycle.

The executor is transport-free and drives an
:class:`orchestrator.harness.runs.InMemoryRunStore`, so the existing
``/v1/runs`` REST + SSE surface and ``forge runs`` CLI light up for free.
"""

from __future__ import annotations

from orchestrator.design_flow.executor import (
    DesignFlowExecutor,
    FlowCanceled,
    FlowContext,
    GateCoordinator,
    PhaseBrain,
    PhaseOutcome,
)
from orchestrator.design_flow.spec import (
    DEFAULT_FLOW_ID,
    FLOWS,
    FlowDefinition,
    Gate,
    Phase,
    get_flow,
)

__all__ = [
    "DEFAULT_FLOW_ID",
    "FLOWS",
    "DesignFlowExecutor",
    "FlowCanceled",
    "FlowContext",
    "FlowDefinition",
    "Gate",
    "GateCoordinator",
    "Phase",
    "PhaseBrain",
    "PhaseOutcome",
    "get_flow",
]
