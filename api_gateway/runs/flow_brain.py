"""Production phase brain for the design-flow executor (MET-10).

Wraps the chat ReAct harness (``run_chat_turn``) so each design-flow phase is
executed by the LLM brain with the full MCP tool surface (project / twin / CAD /
FEA / EDA / knowledge). Per ADR-008 the reasoning lives in the external harness;
this adapter is the thin bridge that hands a phase a scoped prompt and lets it
drive tools to produce + record artifacts into the digital twin.

Lives in ``api_gateway`` (layer 4) because it depends on the chat harness and
the MCP bridge; the executor it plugs into stays pure in ``orchestrator``.
"""

from __future__ import annotations

import structlog

from api_gateway.chat.harness_backend import run_chat_turn
from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import Phase
from skill_registry.mcp_bridge import McpBridge

logger = structlog.get_logger(__name__)


class ReActPhaseBrain:
    """A :class:`~orchestrator.design_flow.executor.PhaseBrain` backed by ReAct.

    ``max_steps`` bounds the tool-use budget per phase; ``provider``/``model``
    override the env defaults (else the gateway's configured LLM is used).
    """

    def __init__(
        self,
        *,
        mcp_bridge: McpBridge | None,
        session_id: str = "design-flow",
        max_steps: int = 24,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self._bridge = mcp_bridge
        self._session_id = session_id
        self._max_steps = max_steps
        self._provider = provider
        self._model = model

    def _prompt(self, goal: str, phase: Phase, context: FlowContext) -> str:
        prior = (
            "\n".join(f"  - {p.title}: {o.summary}" for p, o in context.completed)
            or "  (none yet — this is the first phase)"
        )
        project_line = (
            f"Project id: {context.project_id} — scope every tool call to this project "
            f"(pass project_id where accepted) and record artifacts to its digital twin."
            if context.project_id
            else "No project id supplied; still record decisions to the twin."
        )
        expected = ", ".join(phase.expected_artifacts) or "the appropriate work products"
        deliverables = self._deliverable_guidance(phase, context)
        return (
            f"You are MetaForge's autonomous design engineer executing the "
            f"**{phase.title}** phase of a gated design flow.\n\n"
            f"Product goal: {goal}\n"
            f"{project_line}\n\n"
            f"Prior phases completed:\n{prior}\n\n"
            f"Your objective for THIS phase:\n{phase.objective}\n\n"
            f"{deliverables}\n"
            f"Use the available MCP tools (project, twin, CAD/FEA/EDA, knowledge) to "
            f"actually perform the work and record {expected} into the digital twin — "
            f"do not just describe it. Work efficiently: prefer one decisive tool call "
            f"per step and avoid repeating a failed call with the same arguments. When "
            f"done, reply with a concise summary (3-5 sentences) of what you produced "
            f"and the specific artifacts/decisions you recorded, so a human reviewer "
            f"can decide whether to pass this gate."
        )

    @staticmethod
    def _deliverable_guidance(phase: Phase, context: FlowContext) -> str:
        """Tell the brain exactly which twin work products the gate requires."""
        if not phase.required_deliverables:
            return ""
        pid = context.project_id or "<the project>"
        hints = {
            "design_decision": (
                "record it with the record-decision tool (title, rationale, alternatives), "
                f"project_id={pid}"
            ),
            "cad_model": (
                "author the geometry with the FreeCAD authoring tools, then PERSIST it "
                f"with the commit-geometry tool (project_id={pid}) so it becomes a "
                "viewable cad_model in the twin — a described-but-uncommitted model does "
                "NOT count"
            ),
        }
        lines = "\n".join(
            f"  - {d}: {hints.get(d, 'record it into the twin, scoped to the project')}"
            for d in phase.required_deliverables
        )
        return (
            "This gate will FAIL unless the following work products are actually "
            f"recorded into the twin for this project during this phase:\n{lines}\n"
        )

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        prompt = self._prompt(goal, phase, context)
        logger.info("design_flow_brain_phase", phase=phase.id, project_id=context.project_id)
        summary = await run_chat_turn(
            prompt,
            mcp_bridge=self._bridge,
            session_id=f"{self._session_id}:{phase.id}",
            max_steps=self._max_steps,
            provider=self._provider,
            model=self._model,
        )
        # run_chat_turn returns a fallback sentence when the loop doesn't converge.
        status = "exhausted" if summary.startswith("I couldn't converge") else "completed"
        return PhaseOutcome(summary=summary, artifacts=[], status=status)
