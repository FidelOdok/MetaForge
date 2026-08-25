"""LLM-driven translation of free-text intent into catalog search candidates (MET-436).

An engineer asking "I need to step 12V down to 5V for a flight controller,
around 2A" isn't stating a spec query — they're stating a goal. This module
is the first stage of mode 3 ("intent" search): it turns that free text into
one or more category candidates, each carrying inferred spec bounds tagged
with *how* confident that inference is, so the caller (``intent_search.py``)
can run them as parametric queries without ever presenting a guess as a
stated fact.

Mirrors ``digital_twin/knowledge/llm_property_extractor.py``'s conventions
exactly (the ``PropertyLLM``-shaped ``Protocol``, the fenced-JSON-object
parser, the fail-open contract) rather than inventing a second LLM-calling
convention in this codebase.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import structlog

from digital_twin.catalog.query import CatalogFilterOp
from digital_twin.catalog.taxonomy import PurchaseUnit
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.intent_translator")

# Mirrors digital_twin.catalog.query._ALLOWED_OPS — re-declared locally
# rather than importing a private cross-module symbol (same convention
# digital_twin/catalog/store.py uses for taxonomy._VALID_NAME).
_ALLOWED_OPS: tuple[CatalogFilterOp, ...] = ("==", "!=", ">=", "<=", ">", "<", "in", "not_in")

_DEFAULT_CANDIDATE_CONFIDENCE = 0.5
_DEFAULT_CONSTRAINT_CONFIDENCE = 0.6


class IntentLLM(Protocol):
    """Provider-agnostic single-shot completion — identical shape to
    ``llm_property_extractor.PropertyLLM`` so one concrete provider client
    satisfies both protocols without adapter glue."""

    async def complete(self, prompt: str) -> str: ...


class StubIntentLLM:
    """Deterministic in-process fake for tests. Same shape as ``StubPropertyLLM``.

    ``responses`` may be a dict mapping a substring of the prompt to the raw
    response string, a callable ``(prompt) -> str``, or ``None`` (always
    answers with an empty candidate list).
    """

    def __init__(self, responses: dict[str, str] | Callable[[str], str] | None = None) -> None:
        self._responses = responses
        self._calls: list[str] = []

    @property
    def calls(self) -> list[str]:
        return list(self._calls)

    async def complete(self, prompt: str) -> str:
        self._calls.append(prompt)
        if callable(self._responses):
            return self._responses(prompt)
        if isinstance(self._responses, dict):
            for needle, response in self._responses.items():
                if needle in prompt:
                    return response
        return '{"subsystem": null, "reasoning": null, "categories": []}'


class IntentConstraintSource(StrEnum):
    """Provenance of one inferred constraint — never trust an inference as
    if it were literally stated."""

    STATED = "stated"
    LLM_INFERRED = "llm_inferred"
    DEFAULT = "default"


@dataclass(frozen=True)
class IntentConstraint:
    """One spec bound inferred (or read literally) from the intent text."""

    property: str
    op: CatalogFilterOp
    value: Any
    weight: float = 1.0
    source: IntentConstraintSource = IntentConstraintSource.STATED
    inference_confidence: float = 1.0


@dataclass(frozen=True)
class CategoryCandidate:
    """One category the intent could resolve to, with its inferred spec envelope."""

    category: str
    purchase_unit: PurchaseUnit
    role: str | None
    confidence: float
    constraints: tuple[IntentConstraint, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class IntentTranslation:
    """Outcome of one ``translate_intent()`` call.

    ``parse_error`` is set (and ``candidates`` is empty, or missing the
    dropped entries) on any provider/parse failure or when the model names
    a category outside ``known_categories`` — never raises.
    """

    raw_intent: str
    subsystem_hint: str | None
    candidates: tuple[CategoryCandidate, ...]
    reasoning: str | None
    parse_error: str | None = None


def build_intent_prompt(
    intent_text: str,
    *,
    known_categories: Sequence[str] | None,
    known_subsystems: Sequence[str] | None,
) -> str:
    """Build the single-shot intent-translation prompt.

    The "never mark unstated as stated" rule is enforced here as an
    instruction to the model, not mechanically — ``translate_intent`` can't
    verify a model isn't lying about provenance, only prompt it clearly and
    document the limitation (see that function's docstring).
    """
    categories_hint = (
        f"Valid categories — choose ONLY from this list, do not invent others: "
        f"{', '.join(sorted(known_categories))}\n"
        if known_categories
        else ""
    )
    subsystems_hint = (
        f'Known subsystem names — set "subsystem" to one of these if the intent '
        f"matches, else null: {', '.join(sorted(known_subsystems))}\n"
        if known_subsystems
        else ""
    )
    return (
        "You translate an engineer's free-text component need into structured "
        "search candidates. Respond with ONLY a JSON object, no prose.\n\n"
        f'Engineer intent: "{intent_text}"\n\n'
        f"{categories_hint}{subsystems_hint}"
        "Rules:\n"
        "- Identify one or more component categories that could satisfy this intent.\n"
        '- For each category, state purchase_unit: "discrete_part" (an individual '
        'component to design in) or "cots_assembly" (a complete, ready-to-buy '
        "board/module that satisfies the whole need on its own).\n"
        "- If the intent describes a subsystem built from several parts (not just "
        'one component), set "subsystem" and give each category a short "role" '
        '(e.g. "mcu", "imu") describing what it is for in that subsystem; otherwise '
        'leave "role" null.\n'
        '- For each category, list constraints as {"property", "op", "value", '
        '"source", "confidence"}. "op" must be one of '
        f"{list(_ALLOWED_OPS)}.\n"
        '- CRITICAL: set "source":"stated" ONLY when the value is explicitly present '
        "in the engineer's own words (a number, a named part type, an explicit "
        "range). For anything you infer from context but the engineer didn't "
        'literally say, use "source":"llm_inferred" with a confidence below 0.8. '
        "For a category-standard default you are assuming with no textual basis "
        'at all, use "source":"default" with confidence below 0.4. Never mark an '
        'inferred or assumed value as "stated" — a caller trusts "stated" '
        "constraints as ground truth and will not double-check them.\n\n"
        'Schema: {"subsystem": string|null, "reasoning": string, "categories": '
        '[{"category": string, "purchase_unit": "discrete_part"|"cots_assembly", '
        '"role": string|null, "confidence": number, "constraints": '
        '[{"property": string, "op": string, "value": any, "source": string, '
        '"confidence": number}]}]}\n'
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object from a possibly fenced LLM response.

    Same shape as ``llm_property_extractor._parse_json_object`` — kept as a
    local, small, private helper rather than importing that module's
    private function across a module boundary.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[: -len("```")]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
    return parsed


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_constraint(raw: Any) -> IntentConstraint | None:
    """Parse one constraint dict from the model's response.

    Returns ``None`` (drop, don't raise) on any malformed entry — one bad
    constraint in a list of otherwise-good ones shouldn't sink the whole
    category candidate.
    """
    if not isinstance(raw, dict):
        return None
    prop = raw.get("property")
    op = raw.get("op")
    if not isinstance(prop, str) or not prop.strip() or op not in _ALLOWED_OPS:
        return None
    if "value" not in raw:
        return None

    source_raw = str(raw.get("source", "llm_inferred")).lower()
    try:
        source = IntentConstraintSource(source_raw)
    except ValueError:
        source = IntentConstraintSource.LLM_INFERRED

    try:
        confidence = _clamp01(float(raw.get("confidence", _DEFAULT_CONSTRAINT_CONFIDENCE)))
    except (TypeError, ValueError):
        confidence = _DEFAULT_CONSTRAINT_CONFIDENCE

    try:
        weight = float(raw.get("weight", 1.0))
    except (TypeError, ValueError):
        weight = 1.0

    return IntentConstraint(
        property=prop.strip(),
        op=op,
        value=raw["value"],
        weight=weight,
        source=source,
        inference_confidence=confidence,
    )


async def translate_intent(
    llm: IntentLLM,
    *,
    intent_text: str,
    known_categories: Sequence[str] | None = None,
    known_subsystems: Sequence[str] | None = None,
) -> IntentTranslation:
    """Translate free-text intent into category candidates + spec bounds.

    Fail-open: any provider error or malformed JSON yields
    ``candidates=()`` with ``parse_error`` set, never raises — a broken
    intent-translation step must not take down the whole search call, and
    the caller (``intent_search.search_intent``) is expected to fall back to
    a plain fuzzy search on ``intent_text`` when this returns nothing.

    A category name the model returns that isn't in ``known_categories``
    (when given) is dropped, not trusted — the taxonomy is the source of
    truth for what categories exist, not the model's guess. Dropped names
    are reported via ``parse_error`` (non-fatal — surviving candidates are
    still returned) rather than failing the whole translation.
    """
    with tracer.start_as_current_span("intent_translator.translate_intent") as span:
        span.set_attribute("intent.text_length", len(intent_text))
        prompt = build_intent_prompt(
            intent_text, known_categories=known_categories, known_subsystems=known_subsystems
        )
        try:
            raw = await llm.complete(prompt)
            parsed = _parse_json_object(raw)
        except Exception as exc:
            logger.warning("intent_translation_failed", error=str(exc))
            return IntentTranslation(
                raw_intent=intent_text,
                subsystem_hint=None,
                candidates=(),
                reasoning=None,
                parse_error=str(exc),
            )

        subsystem = parsed.get("subsystem")
        subsystem_hint = (
            str(subsystem) if isinstance(subsystem, str) and subsystem.strip() else None
        )
        reasoning = parsed.get("reasoning")
        reasoning_str = str(reasoning) if reasoning else None

        raw_categories = parsed.get("categories")
        if not isinstance(raw_categories, list):
            span.set_attribute("intent.candidate_count", 0)
            return IntentTranslation(
                raw_intent=intent_text,
                subsystem_hint=subsystem_hint,
                candidates=(),
                reasoning=reasoning_str,
                parse_error="response missing a 'categories' array",
            )

        known_set = set(known_categories) if known_categories else None
        candidates: list[CategoryCandidate] = []
        dropped: list[str] = []
        for raw_cat in raw_categories:
            if not isinstance(raw_cat, dict):
                continue
            category = raw_cat.get("category")
            if not isinstance(category, str) or not category.strip():
                continue
            category = category.strip()
            if known_set is not None and category not in known_set:
                dropped.append(category)
                continue

            purchase_unit: PurchaseUnit = (
                "cots_assembly"
                if raw_cat.get("purchase_unit") == "cots_assembly"
                else "discrete_part"
            )
            role = raw_cat.get("role")
            role_str = str(role) if isinstance(role, str) and role.strip() else None
            try:
                confidence = _clamp01(
                    float(raw_cat.get("confidence", _DEFAULT_CANDIDATE_CONFIDENCE))
                )
            except (TypeError, ValueError):
                confidence = _DEFAULT_CANDIDATE_CONFIDENCE

            constraints = tuple(
                c
                for raw_c in (raw_cat.get("constraints") or [])
                if (c := _parse_constraint(raw_c)) is not None
            )

            candidates.append(
                CategoryCandidate(
                    category=category,
                    purchase_unit=purchase_unit,
                    role=role_str,
                    confidence=confidence,
                    constraints=constraints,
                )
            )

        parse_error = (
            f"dropped categories not in the known taxonomy: {dropped}" if dropped else None
        )
        span.set_attribute("intent.candidate_count", len(candidates))
        span.set_attribute("intent.dropped_count", len(dropped))
        logger.info(
            "intent_translated",
            subsystem=subsystem_hint,
            candidate_count=len(candidates),
            dropped_count=len(dropped),
        )
        return IntentTranslation(
            raw_intent=intent_text,
            subsystem_hint=subsystem_hint,
            candidates=tuple(candidates),
            reasoning=reasoning_str,
            parse_error=parse_error,
        )


__all__ = [
    "CategoryCandidate",
    "IntentConstraint",
    "IntentConstraintSource",
    "IntentLLM",
    "IntentTranslation",
    "StubIntentLLM",
    "build_intent_prompt",
    "translate_intent",
]
