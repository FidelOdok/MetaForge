"""Contradiction detection between consolidated insights (MET-455).

When the synthesizer produces a new insight, it may conflict with a
prior lesson — "ESP32 is the low-power WiFi pick" vs "ESP32 burns too
much in deep sleep". Persisting both unflagged means the memory layer
hands agents contradictory advice. This module asks the LLM whether a
candidate insight contradicts any of the existing insights in the same
theme and returns a structured verdict.

Reuses the ``LLMClient`` Protocol (``synthesize_insight`` is really a
single-shot JSON completion). The detector owns the prompt shape and
the defensive parse so a malformed LLM response degrades to
"no contradiction found" rather than raising into the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from digital_twin.memory.consolidation.insight import Insight
from digital_twin.memory.consolidation.llm import LLMClient
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.memory.consolidation.contradiction_detector")

MAX_COMPARISON_INSIGHTS = 20
"""Cap on existing insights cited in one prompt — keeps the LLM input
bounded even when a theme has accumulated hundreds of insights."""


@dataclass(frozen=True)
class ContradictionResult:
    """Verdict for a candidate insight vs the existing corpus."""

    contradicts: bool
    conflicting_insight_ids: tuple[UUID, ...] = field(default_factory=tuple)
    explanation: str = ""


class ContradictionDetector:
    """Ask the LLM whether a candidate insight conflicts with existing ones."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    async def detect(
        self,
        candidate: Insight,
        existing: list[Insight],
    ) -> ContradictionResult:
        """Return a contradiction verdict for ``candidate`` vs ``existing``.

        Compares only against insights in the same theme (the caller is
        expected to pre-filter, but we defend here too). With no
        comparable existing insights the answer is trivially "no
        contradiction" and no LLM call is made.
        """
        comparable = [
            i for i in existing if i.theme == candidate.theme and i.id != candidate.id
        ]
        if not comparable:
            return ContradictionResult(contradicts=False)

        with tracer.start_as_current_span("consolidation.contradiction.detect") as span:
            span.set_attribute("memory.theme", candidate.theme.value)
            span.set_attribute("memory.comparison_count", len(comparable))

            prompt = self.build_prompt(candidate, comparable)
            try:
                raw = await self._client.synthesize_insight(prompt)
            except Exception as exc:
                span.record_exception(exc)
                logger.warning(
                    "contradiction_detector_llm_error",
                    theme=candidate.theme.value,
                    error=str(exc),
                )
                # Fail open — a detector outage must not block the pipeline.
                return ContradictionResult(contradicts=False)

            result = self._parse(raw, comparable)
            span.set_attribute("memory.contradicts", result.contradicts)
            logger.info(
                "contradiction_detector_completed",
                theme=candidate.theme.value,
                contradicts=result.contradicts,
                conflicts=len(result.conflicting_insight_ids),
            )
            return result

    def build_prompt(self, candidate: Insight, existing: list[Insight]) -> str:
        """Compose a deterministic contradiction-detection prompt."""
        head = [
            "You are reviewing engineering insights for logical contradictions.",
            "Decide whether the CANDIDATE insight contradicts any EXISTING insight.",
            "Two insights contradict when acting on both is impossible or when one",
            "asserts the opposite of the other. Differing scope is NOT contradiction.",
            'Return strict JSON: {"contradicts": bool, "conflicting_ids": [str],'
            ' "explanation": str}.',
            "",
            f"CANDIDATE (theme={candidate.theme.value}):",
            f"  {candidate.narrative}",
            "",
            "EXISTING:",
        ]
        body = [
            f"  id={insight.id} :: {insight.narrative}"
            for insight in existing[:MAX_COMPARISON_INSIGHTS]
        ]
        return "\n".join(head + body)

    def _parse(
        self,
        raw: dict[str, Any] | None,
        existing: list[Insight],
    ) -> ContradictionResult:
        if not isinstance(raw, dict):
            return ContradictionResult(contradicts=False)
        contradicts = bool(raw.get("contradicts", False))
        if not contradicts:
            return ContradictionResult(contradicts=False)

        # Only accept ids that actually appear in the comparison set —
        # the LLM occasionally invents or mangles ids, and a fabricated
        # id pointing at nothing is worse than dropping it.
        valid_ids = {i.id for i in existing}
        conflicting: list[UUID] = []
        for raw_id in raw.get("conflicting_ids", []) or []:
            try:
                parsed = UUID(str(raw_id))
            except (TypeError, ValueError):
                continue
            if parsed in valid_ids:
                conflicting.append(parsed)

        explanation = raw.get("explanation")
        return ContradictionResult(
            contradicts=True,
            conflicting_insight_ids=tuple(conflicting),
            explanation=str(explanation) if isinstance(explanation, str) else "",
        )
