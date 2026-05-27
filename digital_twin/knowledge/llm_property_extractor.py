"""Tier-2/3 LLM property extraction (MET-462).

The pure Tier-1 extractor in ``property_extractor.py`` answers verbatim
table-cell lookups at confidence 1.0. This module is the additive
follow-up it describes: when Tier 1 can't locate a property, ask an LLM
to read the datasheet prose and either *infer* the value from text
(Tier 2, ``llm_inferred``, 0.6-0.8) or *derive* it from related fields
(Tier 3, ``derived``, 0.4-0.6). The model self-classifies which it did;
this module maps that onto the MET-422 confidence ladder.

The LLM provider is injected via the narrow ``PropertyLLM`` protocol so
this module never imports ``anthropic`` / ``openai`` directly and stays
unit-testable with ``StubPropertyLLM``. The call is fail-open: any
provider error, malformed JSON, or "not found" verdict yields a
``NOT_FOUND`` ``ExtractedProperty`` rather than raising — a missing
Tier-2 answer must never break the Tier-1 result for sibling properties.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

import structlog

from digital_twin.knowledge.property_extractor import (
    ExtractedProperty,
    ExtractionMethod,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.llm_property_extractor")

# MET-422 confidence ladder for the LLM tiers.
LLM_INFERRED_BAND = (0.6, 0.8)
DERIVED_BAND = (0.4, 0.6)
DEFAULT_LLM_CONFIDENCE = 0.7

# Cap the datasheet text fed to the model so a 100-page PDF can't blow
# the prompt budget / time out. ~24k chars ≈ a generous slice of the
# most relevant pages; callers pre-select chunks for longer docs.
DEFAULT_MAX_TEXT_CHARS = 24_000


class PropertyLLM(Protocol):
    """Provider-agnostic single-shot completion used for property extraction."""

    async def complete(self, prompt: str) -> str: ...


class StubPropertyLLM:
    """Deterministic in-process fake for tests.

    ``responses`` may be:
      * a dict mapping a substring (e.g. a property name) → the raw
        response string the model would return for a prompt containing it;
      * a callable ``(prompt) -> str``;
      * ``None`` → always answers ``{"found": false}``.
    """

    def __init__(
        self,
        responses: dict[str, str] | Callable[[str], str] | None = None,
    ) -> None:
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
        return '{"found": false}'


def build_extraction_prompt(mpn: str, property_name: str, datasheet_text: str) -> str:
    """Build the single-property extraction prompt sent to the model."""
    return (
        "You extract a single typed property from an electronic component "
        "datasheet. Respond with ONLY a JSON object, no prose.\n\n"
        f'Component MPN: "{mpn}"\n'
        f'Property to extract: "{property_name}"\n\n'
        "Rules:\n"
        '- If the value is stated or clearly inferable from the text, set "found": true,\n'
        '  put the numeric/text value in "value", any unit in "unit", and set\n'
        '  "method": "llm_inferred".\n'
        '- If you had to compute/derive it from other fields, set "method": "derived".\n'
        '- If it is absent, set "found": false.\n'
        '- "confidence" is your 0..1 certainty.\n'
        'Schema: {"found": bool, "value": string|null, "unit": string|null,'
        ' "confidence": number, "method": "llm_inferred"|"derived", "reasoning": string}\n\n'
        "Datasheet text:\n"
        f"{datasheet_text}\n"
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object from a possibly fenced LLM response."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[: -len("```")]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
    return parsed


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


async def infer_property(
    llm: PropertyLLM,
    *,
    mpn: str,
    property_name: str,
    datasheet_text: str,
    max_chars: int = DEFAULT_MAX_TEXT_CHARS,
) -> ExtractedProperty:
    """Tier-2/3 lookup: ask the LLM to extract ``property_name`` from prose.

    Returns an ``llm_inferred`` (0.6-0.8) or ``derived`` (0.4-0.6) result
    when the model finds the value, else ``NOT_FOUND``. Never raises —
    provider/parse failures degrade to ``NOT_FOUND``.
    """
    text = datasheet_text[:max_chars] if datasheet_text else ""
    if not text:
        return ExtractedProperty(
            property_name=property_name,
            value=None,
            extraction_method=ExtractionMethod.NOT_FOUND,
        )

    with tracer.start_as_current_span("llm_property_extractor.infer_property") as span:
        span.set_attribute("knowledge.mpn", mpn)
        span.set_attribute("knowledge.property", property_name)
        prompt = build_extraction_prompt(mpn, property_name, text)
        try:
            raw = await llm.complete(prompt)
            parsed = _parse_json_object(raw)
        except Exception as exc:
            logger.warning(
                "llm_property_extraction_failed",
                mpn=mpn,
                property=property_name,
                error=str(exc),
            )
            return ExtractedProperty(
                property_name=property_name,
                value=None,
                extraction_method=ExtractionMethod.NOT_FOUND,
            )

        value = parsed.get("value")
        if not parsed.get("found") or value is None:
            span.set_attribute("knowledge.found", False)
            return ExtractedProperty(
                property_name=property_name,
                value=None,
                extraction_method=ExtractionMethod.NOT_FOUND,
            )

        method_raw = str(parsed.get("method", "llm_inferred")).lower()
        if method_raw == "derived":
            method, band = ExtractionMethod.DERIVED, DERIVED_BAND
        else:
            method, band = ExtractionMethod.LLM_INFERRED, LLM_INFERRED_BAND

        raw_confidence = parsed.get("confidence", DEFAULT_LLM_CONFIDENCE)
        try:
            confidence = _clamp(float(raw_confidence), band[0], band[1])
        except (TypeError, ValueError):
            confidence = band[0]

        conditions: dict[str, Any] = {}
        reasoning = parsed.get("reasoning")
        if reasoning:
            conditions["reasoning"] = str(reasoning)

        span.set_attribute("knowledge.found", True)
        span.set_attribute("knowledge.extraction_method", method.value)
        span.set_attribute("knowledge.confidence", confidence)
        logger.info(
            "llm_property_extracted",
            mpn=mpn,
            property=property_name,
            method=method.value,
            confidence=confidence,
        )
        return ExtractedProperty(
            property_name=property_name,
            value=str(value),
            unit=(str(parsed["unit"]) if parsed.get("unit") is not None else None),
            confidence=confidence,
            extraction_method=method,
            conditions=conditions,
        )
