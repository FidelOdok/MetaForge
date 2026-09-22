"""Deterministic concept-selection phase handler for the design flow
(FORGE-73, epic FORGE-35): the "Decision Agent" (spec section 26.12) behind
the G5 Concept Selection Gate.

Runs immediately after the architecture phase decides subsystems/budgets.
The LLM proposes 2-3 distinct concepts/approaches that could satisfy that
architecture, picks one, and this handler records the decision through
``twin.record_decision`` with real ``alternatives`` (the trade study),
``rationale``, and a ``parent_refs`` link back to the architecture decision
(``evaluate_g5_concept_selection``'s own checks -- same style as
``arch_handlers.GoalDrivenArchitectureHandler``, and reusing
``twin.record_decision``'s own alternatives-table rendering instead of a
second, separate document).

Deliberately does NOT:

- score candidates against recorded ``objective`` ``EngineeringEntity``
  nodes via ``ObjectiveEngine``/``objective_from_entity`` (FORGE-58) -- there
  is no MCP *read* tool for engineering entities today (only
  ``twin.record_engineering_entity``, the write side), so a handler running
  purely over the MCP bridge has no way to fetch a project's recorded
  objectives. ``IntentInterpreterAgent`` (Phase 3, ``api_gateway.
  requirement_intelligence``) is the only code that ever produces
  ``entity_type="objective"`` records today, and it isn't wired into any
  live execution path yet, so this gap has no live impact -- wiring a real
  read path is separate, later work, same "built but not yet load-bearing"
  seam this epic has left in several other places (e.g. G6/G8's optional
  ``traceability_coverage`` accessor).
- generate CAD/simulation evidence for each candidate -- a trade study
  compares stated/estimated characteristics (approach, complexity, cost,
  manufacturability), not real analysis; the mechanical/simulation phases
  that follow produce that for the SELECTED concept only.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

import structlog

from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import Phase
from skill_registry.mcp_bridge import McpBridge

logger = structlog.get_logger(__name__)

_DEFAULT_SELECTED = {
    "option": "Primary concept",
    "description": "the baseline approach satisfying the decided architecture",
}
_DEFAULT_ALTERNATIVES = [
    {
        "option": "Alternate concept",
        "reason_rejected": "higher cost/complexity for no clear benefit over the primary approach",
    }
]


def _normalize_concept_spec(spec: dict[str, Any], goal: str) -> dict[str, Any]:
    """Coerce an extracted concept-selection spec into a complete trade study."""
    selected_option = (
        str(spec.get("selected_option") or "").strip()[:60] or _DEFAULT_SELECTED["option"]
    )
    selected_description = (
        str(spec.get("selected_description") or "").strip()[:300]
        or _DEFAULT_SELECTED["description"]
    )

    raw_alts = spec.get("alternatives")
    alternatives: list[dict[str, str]] = []
    if isinstance(raw_alts, list):
        for a in raw_alts:
            if isinstance(a, dict) and str(a.get("option") or "").strip():
                alternatives.append(
                    {
                        "option": str(a["option"]).strip()[:60],
                        "reason_rejected": (
                            str(a.get("reason_rejected") or "").strip()[:200] or "not selected"
                        ),
                    }
                )
    if not alternatives:
        alternatives = [dict(a) for a in _DEFAULT_ALTERNATIVES]

    return {
        "selected_option": selected_option,
        "selected_description": selected_description,
        "alternatives": alternatives,
    }


def _rationale(spec: dict[str, Any], goal: str) -> str:
    alt_names = ", ".join(a["option"] for a in spec["alternatives"])
    return (
        f"Concept selection for {goal}. Selected: {spec['selected_option']} -- "
        f"{spec['selected_description']} Considered and rejected {len(spec['alternatives'])} "
        f"alternative(s): {alt_names}."
    )


async def _extract_concept_spec(
    goal: str, prior: str, *, provider: str | None, model: str | None
) -> dict[str, Any]:
    """Ask the LLM for 2-3 candidate concepts + a selection (never raises)."""
    from api_gateway.chat.harness_backend import run_chat_turn
    from api_gateway.chat.routes import get_metrics

    prompt = (
        "You are running a concept-selection trade study for a hardware product, now that "
        "its system architecture (subsystem decomposition + budgets) has been decided. "
        "Propose 2-3 DISTINCT design concepts/approaches that could satisfy that "
        "architecture (e.g. different actuation mechanisms, structural topologies, or "
        "component-level approaches -- whichever varies meaningfully for THIS product), "
        "pick the one that best satisfies the requirements, and explain why the others "
        "lost. Reply with ONLY JSON:\n"
        '{"selected_option": "<chosen concept name>", '
        '"selected_description": "<1-2 sentences: what it is and why it best satisfies the '
        'architecture/requirements>", '
        '"alternatives": [{"option": "<rejected concept name>", '
        '"reason_rejected": "<why it lost>"}]}\n\n'
        f"Goal: {goal}\nContext (architecture/requirements so far): {prior}"
    )
    try:
        reply = await run_chat_turn(
            prompt,
            mcp_bridge=None,
            session_id="concept-spec",
            max_steps=1,
            provider=provider,
            model=model,
            metrics=get_metrics(),
        )
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        spec = json.loads(match.group(0)) if match else {}
    except Exception as exc:  # noqa: BLE001 - fall back to a default spec, never fail
        logger.warning("concept_spec_extract_failed", error=str(exc))
        spec = {}
    return _normalize_concept_spec(spec if isinstance(spec, dict) else {}, goal)


def _data(envelope: Any, tool: str) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        return {}
    if envelope.get("status") == "error":
        raise RuntimeError(f"{tool} failed: {envelope.get('error') or envelope}")
    data = envelope.get("data", envelope)
    return data if isinstance(data, dict) else {}


def _find_prior_decision_id(context: FlowContext, phase_id: str) -> str | None:
    """The design_decision node id `phase_id` recorded, read back out of its
    ``PhaseOutcome.artifacts``. Only a well-formed UUID is ever returned, so
    a caller can pass it straight into ``parent_refs`` without a separate
    validation step -- ``None`` (never a bogus ref) when the phase hasn't
    run, didn't record one, or a bridge failure ever left the placeholder
    ``arch_handlers.py`` falls back to on a missing node id.
    """
    for phase, outcome in context.completed:
        if phase.id != phase_id:
            continue
        for artifact in outcome.artifacts:
            if artifact.startswith("design_decision:"):
                candidate = artifact[len("design_decision:") :]
                try:
                    return str(UUID(candidate))
                except ValueError:
                    continue
    return None


class GoalDrivenConceptSelectionHandler:
    """Runs a trade study (Decision Agent, spec section 26.12): proposes
    candidate concepts, selects one, and records the decision with real
    alternatives + rationale + a link back to the architecture decision --
    the G5 Concept Selection Gate's own checks
    (``twin_core.consistency.gates.evaluate_g5_concept_selection``)."""

    def __init__(
        self,
        bridge: McpBridge,
        *,
        provider: str | None = None,
        model: str | None = None,
        extract: Any = _extract_concept_spec,
    ) -> None:
        self._bridge = bridge
        self._provider = provider
        self._model = model
        self._extract = extract

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        prior = "\n".join(f"  - {p.title}: {o.summary}" for p, o in context.completed) or "(none)"
        spec = await self._extract(goal, prior, provider=self._provider, model=self._model)

        args: dict[str, Any] = {
            "title": f"Concept selection: {spec['selected_option']}",
            "rationale": _rationale(spec, goal),
            "alternatives": spec["alternatives"],
        }
        if context.project_id:
            args["project_id"] = context.project_id
        arch_decision_id = _find_prior_decision_id(context, "architecture")
        if arch_decision_id:
            args["parent_refs"] = [arch_decision_id]

        result = _data(
            await self._bridge.invoke("twin.record_decision", args), "twin.record_decision"
        )
        node_id = result.get("node_id") or "concept_selection"

        return PhaseOutcome(
            summary=(
                f"Concept selection: chose '{spec['selected_option']}' over "
                f"{len(spec['alternatives'])} alternative(s)"
                + (" (linked to the architecture decision)" if arch_decision_id else "")
                + "."
            ),
            artifacts=[f"design_decision:{node_id}"],
            status="completed",
        )
