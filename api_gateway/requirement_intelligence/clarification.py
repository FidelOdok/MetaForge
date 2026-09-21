"""ClarificationAgent (FORGE-54, spec sections 26.2 Clarification Agent, 60
Clarification Strategy).

Pure ranking/selection -- no LLM call. The doc's "ask high-information
questions... avoid exhaustive questionnaires... minimize user burden" is a
selection policy over Unknowns some other agent already raised (e.g.
IntentInterpreterAgent's ``ambiguous_phrases``), not a generation task.
"""

from __future__ import annotations

from api_gateway.requirement_intelligence.models import Unknown


def _gate_index(gate_id: str) -> int:
    """ "G3" -> 3. Unparseable gate ids sort first (index 0) rather than raise --
    a malformed required_by_gate shouldn't crash selection, just lose its
    gate-scoping and fall back to always-eligible-like behavior at worst."""
    try:
        return int(gate_id.strip().lstrip("Gg"))
    except ValueError:
        return 0


class ClarificationAgent:
    """Selects the highest-value unresolved questions due at the current gate."""

    def select_questions(
        self,
        unknowns: list[Unknown],
        *,
        current_gate: str,
        max_questions: int = 3,
    ) -> list[Unknown]:
        """Return at most `max_questions`, ranked by priority (spec 26.2),
        restricted to unknowns due by `current_gate` (spec 60) -- an unknown
        whose `required_by_gate` is later than `current_gate` is not yet
        needed and is deliberately left out (it survives, unasked, into a
        later call once its gate arrives)."""
        current_idx = _gate_index(current_gate)
        due = [
            u
            for u in unknowns
            if u.required_by_gate is None or _gate_index(u.required_by_gate) <= current_idx
        ]
        ranked = sorted(due, key=lambda u: u.priority, reverse=True)
        return ranked[:max_questions]
