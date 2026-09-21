"""IntentInterpreterAgent (FORGE-54, spec section 26.1 Intent Interpreter
Agent).

Reuses the same "one-shot structured-JSON LLM call" pattern
``api_gateway/runs/req_handlers.py``'s ``_extract_req_spec`` already
established for the requirements phase -- ``run_chat_turn`` with
``mcp_bridge=None`` and ``max_steps=1``, tolerant ``{...}`` JSON extraction
from the reply. This agent produces a proposed :class:`~twin_core.models.
patch.Patch`, never a direct write (spec section 38: agents shall not own
state) -- nothing in this module calls ``TwinAPI``/``TransactionEngine``.

Deliberately not wired into any live call site yet (``req_handlers.py``
still commits directly via ``twin.record_constraint_set``/
``twin.record_decision``, unchanged by this PR) -- replacing that flow to
route through this agent + ``TransactionEngine.commit`` + the Phase 2
Policy/HITL engines is separate integration work, once a real approval UI
exists to act on a returned ``proposed_patch``. Same deferral discipline as
every Phase 1/2 PR.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from api_gateway.requirement_intelligence.models import AgentResult, Unknown
from twin_core.models.patch import Patch, PatchOp, PatchOperation

logger = structlog.get_logger(__name__)

_PROMPT_TEMPLATE = (
    "You are the Intent Interpreter for a hardware engineering harness. Extract structured "
    "intent from the goal below. Reply with ONLY JSON:\n"
    '{{"intent": "<one sentence: what is this system for>", '
    '"goals": ["<goal 1>", "..."], '
    '"candidate_constraints": [{{"statement": "<constraint>", "confidence": 0.0}}], '
    '"preferences": ["<preference>", "..."], '
    '"assumptions": [{{"statement": "<assumption>", "confidence": 0.0}}], '
    '"ambiguous_phrases": [{{"phrase": "<exact substring of the goal>", '
    '"question": "<specific question that would resolve it>", '
    '"downstream_impact": 0.0, "uncertainty": 0.0, "cost_of_error": 0.0, '
    '"dependency_count": 0, "affected": ["<what this affects>"]}}]}}\n'
    "Every ambiguous phrase must be a literal substring of the goal. Score confidence and the "
    "four clarification fields honestly in [0, 1] (dependency_count as a real integer count) -- "
    "do not pad every field to the same value.\n\n"
    "Goal: {goal}\nContext: {context}"
)


def _clamp01(value: Any, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _str(value: Any, cap: int = 300) -> str:
    return str(value).strip()[:cap] if isinstance(value, (str, int, float)) else ""


def _confidence_dict(item: dict[str, Any]) -> dict[str, Any]:
    return {"value": _clamp01(item.get("confidence")), "basis": "model_inference"}


class IntentInterpreterAgent:
    """Extracts intent/goals/candidate constraints/assumptions/ambiguities
    from a goal, returning a proposed patch -- never a committed write."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        invoke: Any = None,
        extract: Any = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._invoke = invoke
        # DI seam for tests, mirroring req_handlers.py's GoalDrivenRequirements
        # Handler(extract=...) -- avoids mocking deep into run_chat_turn/the
        # provider pipeline just to test the extraction/patch-building logic.
        self._extract_fn = extract or self._extract

    async def interpret(
        self, goal: str, context: str = "", *, project_id: str | None = None
    ) -> AgentResult:
        spec = await self._extract_fn(goal, context)

        operations: list[PatchOperation] = []
        conclusions: list[str] = []
        assumptions_out: list[str] = []
        confidences: list[float] = []

        intent_statement = _str(spec.get("intent"), cap=500)
        if intent_statement:
            conclusions.append(intent_statement)
            operations.append(
                PatchOperation(
                    op=PatchOp.ADD,
                    entity_kind="engineering_entity",
                    entity={
                        "entity_type": "intent",
                        "statement": intent_statement,
                        "metadata": {"source": "intent_interpreter_agent"},
                    },
                )
            )

        for goal_text in spec.get("goals") or []:
            text = _str(goal_text)
            if not text:
                continue
            conclusions.append(text)
            operations.append(
                PatchOperation(
                    op=PatchOp.ADD,
                    entity_kind="engineering_entity",
                    entity={
                        "entity_type": "objective",
                        "statement": text,
                        "metadata": {"source": "intent_interpreter_agent"},
                    },
                )
            )

        for item in spec.get("assumptions") or []:
            if not isinstance(item, dict):
                continue
            text = _str(item.get("statement"))
            if not text:
                continue
            assumptions_out.append(text)
            confidences.append(_clamp01(item.get("confidence")))
            operations.append(
                PatchOperation(
                    op=PatchOp.ADD,
                    entity_kind="engineering_entity",
                    entity={
                        "entity_type": "assumption",
                        "statement": text,
                        "confidence": _confidence_dict(item),
                        "metadata": {"source": "intent_interpreter_agent"},
                    },
                )
            )

        for item in spec.get("candidate_constraints") or []:
            if not isinstance(item, dict):
                continue
            text = _str(item.get("statement"))
            if not text:
                continue
            confidences.append(_clamp01(item.get("confidence")))
            operations.append(
                PatchOperation(
                    op=PatchOp.ADD,
                    entity_kind="constraint",
                    entity={
                        "name": _constraint_name(text),
                        # A candidate constraint has no evaluable expression
                        # yet (that's the Requirement Author agent's job,
                        # FORGE-55) -- INFO severity, always-true, so it's
                        # visible and traceable without gating anything.
                        "expression": "True",
                        "severity": "info",
                        "domain": "candidate",
                        "source": "intent_interpreter_agent",
                        "message": text,
                        "metadata": {
                            "candidate": True,
                            "confidence": _clamp01(item.get("confidence")),
                        },
                    },
                )
            )

        unresolved: list[Unknown] = []
        for i, item in enumerate(spec.get("ambiguous_phrases") or []):
            if not isinstance(item, dict):
                continue
            phrase = _str(item.get("phrase"), cap=120)
            question = _str(item.get("question")) or f"Please clarify: {phrase or 'the goal'}"
            affected = [_str(a, cap=60) for a in (item.get("affected") or []) if _str(a, cap=60)]
            dependency_count = _int(item.get("dependency_count"), default=1)
            unresolved.append(
                Unknown(
                    id=f"TBD-{i + 1:03d}",
                    question=question,
                    affected=affected,
                    downstream_impact=_clamp01(item.get("downstream_impact")),
                    uncertainty=_clamp01(item.get("uncertainty")),
                    cost_of_error=_clamp01(item.get("cost_of_error")),
                    dependency_count=dependency_count,
                )
            )

        patch = None
        if operations:
            patch = Patch(
                operations=operations,
                reason=f"Intent Interpreter extraction for goal: {goal[:120]}",
                created_by="agent:intent_interpreter",
                project_id=None if project_id is None else _uuid_or_none(project_id),
            )

        confidence = sum(confidences) / len(confidences) if confidences else 0.5
        evidence: list[str] = []  # this agent calls no external tools -- nothing to cite yet

        logger.info(
            "intent_interpreter_result",
            goal=goal[:80],
            operation_count=len(operations),
            unresolved_count=len(unresolved),
            confidence=confidence,
        )
        return AgentResult(
            conclusions=conclusions,
            assumptions=assumptions_out,
            evidence=evidence,
            proposed_patch=patch,
            unresolved=unresolved,
            confidence=confidence,
        )

    async def _extract(self, goal: str, context: str) -> dict[str, Any]:
        """Ask the LLM for structured intent (never raises -- returns {} on failure,
        mirroring req_handlers.py's _extract_req_spec)."""
        from api_gateway.chat.harness_backend import run_chat_turn
        from api_gateway.chat.routes import get_metrics

        prompt = _PROMPT_TEMPLATE.format(goal=goal, context=context or "(none)")
        try:
            kwargs: dict[str, Any] = {
                "mcp_bridge": None,
                "session_id": "intent-interpreter",
                "max_steps": 1,
                "provider": self._provider,
                "model": self._model,
                "metrics": get_metrics(),
            }
            if self._invoke is not None:
                kwargs["invoke"] = self._invoke
            reply = await run_chat_turn(prompt, **kwargs)
            match = re.search(r"\{.*\}", reply, re.DOTALL)
            spec = json.loads(match.group(0)) if match else {}
        except Exception as exc:  # noqa: BLE001 - degrade to an empty spec, never fail the turn
            logger.warning("intent_interpreter_extract_failed", error=str(exc))
            spec = {}
        return spec if isinstance(spec, dict) else {}


def _constraint_name(statement: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in statement.strip().lower())
    slug = "_".join(p for p in slug.split("_") if p)
    return (slug or "candidate_constraint")[:60]


def _uuid_or_none(value: str) -> Any:
    from uuid import UUID

    try:
        return UUID(value)
    except ValueError:
        return None
