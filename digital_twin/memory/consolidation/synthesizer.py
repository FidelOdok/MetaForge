"""Stage 3 of the consolidation pipeline — synthesize insights.

Takes an ``ExperienceGroup`` and asks an LLM (via ``LLMClient``) what
pattern emerges. The synthesizer is responsible for:

* building the prompt deterministically from the group
* converting the LLM's structured response into an ``Insight``
* defending against bad LLM output (missing fields, out-of-range
  confidence) — never raise from a single bad insight, just return
  ``None`` so the orchestrator can skip and keep going.

Validation gates (confidence >= 0.70, hallucination heuristics) live in
``validator.py`` so the synthesizer stays focused on the LLM round-trip.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from digital_twin.memory.consolidation.grouper import ExperienceGroup
from digital_twin.memory.consolidation.insight import Insight, InsightKind
from digital_twin.memory.consolidation.llm import LLMClient
from digital_twin.memory.models import ExperienceMemory
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.synthesizer")

MAX_EXAMPLES_PER_GROUP = 12
"""Cap on experiences cited per group prompt — keeps LLM input under
roughly 8 KB even when groups span hundreds of events."""


class InsightSynthesizer:
    """Build prompts, call the LLM, materialize ``Insight``s."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    async def synthesize(self, group: ExperienceGroup) -> Insight | None:
        """Synthesize a single insight for an experience group.

        Returns ``None`` when the LLM produced an unparseable or
        malformed response. The caller (the orchestrator) treats this
        as "skip this group" rather than "fail the consolidation pass".
        """
        if group.size == 0:
            return None
        with tracer.start_as_current_span("consolidation.synthesizer.synthesize") as span:
            span.set_attribute("memory.theme", group.theme.value)
            span.set_attribute("memory.group_size", group.size)

            prompt = self.build_prompt(group)
            try:
                raw = await self._client.synthesize_insight(prompt)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning(
                    "consolidation_synthesizer_llm_error",
                    theme=group.theme.value,
                    error=str(exc),
                )
                return None

            insight = self._materialize(group, raw)
            if insight is None:
                logger.info(
                    "consolidation_synthesizer_skipped_invalid",
                    theme=group.theme.value,
                )
            return insight

    def build_prompt(self, group: ExperienceGroup) -> str:
        """Compose the prompt sent to the LLM.

        Deterministic — useful for snapshot tests and for the
        ``StubLLMClient`` substring-routing pattern.
        """
        head = [
            "You are a senior systems engineer reviewing a batch of agent",
            "task outcomes. Summarize one pattern, principle, failure mode,",
            "or observation that holds across the group. Return strict JSON",
            'with keys: {"narrative": str, "kind": '
            '"pattern"|"principle"|"failure_mode"|"observation", '
            '"confidence": float}.',
            "",
            f"Theme: {group.theme.value}",
            f"Total experiences: {group.size} "
            f"(successes: {group.success_count}, failures: {group.failure_count})",
            "",
            "Examples:",
        ]
        examples: list[str] = []
        for idx, exp in enumerate(group.experiences[:MAX_EXAMPLES_PER_GROUP]):
            examples.append(
                f"{idx + 1}. agent={exp.agent_code} task={exp.task_type} "
                f"success={exp.success} summary={exp.result_summary[:140]}"
            )
        return "\n".join(head + examples)

    def _materialize(
        self,
        group: ExperienceGroup,
        raw: dict[str, Any] | None,
    ) -> Insight | None:
        if not isinstance(raw, dict):
            return None
        narrative = raw.get("narrative")
        if not isinstance(narrative, str) or not narrative.strip():
            return None
        confidence = _coerce_confidence(raw.get("confidence"))
        if confidence is None:
            return None
        kind = _coerce_kind(raw.get("kind"))

        # Carry the IDs of the source experiences so downstream readers
        # can audit which events motivated the insight. Cap at the same
        # MAX_EXAMPLES_PER_GROUP so the citation list mirrors the prompt.
        supporting = [_experience_id(exp) for exp in group.experiences[:MAX_EXAMPLES_PER_GROUP]]
        try:
            return Insight(
                theme=group.theme,
                kind=kind,
                narrative=narrative.strip(),
                confidence=confidence,
                supporting_experience_ids=supporting,
            )
        except Exception as exc:
            logger.warning(
                "consolidation_synthesizer_insight_construct_failed",
                error=str(exc),
            )
            return None


def _coerce_confidence(value: Any) -> float | None:
    """Cast to ``float`` and clamp to ``[0, 1]``; ``None`` on bad input."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return max(0.0, min(1.0, f))


def _coerce_kind(value: Any) -> InsightKind:
    """Map an LLM-emitted ``kind`` string to the enum; default observation."""
    if isinstance(value, InsightKind):
        return value
    if not isinstance(value, str):
        return InsightKind.OBSERVATION
    try:
        return InsightKind(value.strip().lower())
    except ValueError:
        return InsightKind.OBSERVATION


def _experience_id(experience: ExperienceMemory) -> UUID:
    return experience.id
