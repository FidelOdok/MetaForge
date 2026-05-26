"""LLM client abstraction for the synthesizer stage.

Wraps a single-shot ``synthesize(prompt) -> json_response`` call so the
synthesizer doesn't import ``anthropic`` or ``openai`` directly.
Tests use ``StubLLMClient`` to return canned responses deterministically.
Production wires an Anthropic-backed adapter in a follow-up commit.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    """Single-call LLM interface used by ``InsightSynthesizer``.

    The contract is intentionally narrow: callers pass a prompt, get
    back a parsed dict matching ``Insight``'s wire shape. Production
    impls handle retries, rate-limiting, and JSON-mode parsing; this
    interface stays small so swapping providers (Claude / OpenAI /
    local LLM) doesn't ripple through the consolidation pipeline.
    """

    @abstractmethod
    async def synthesize_insight(self, prompt: str) -> dict[str, Any]:
        """Run the prompt; return the parsed insight payload."""


class StubLLMClient(LLMClient):
    """Deterministic in-process fake.

    Returns a canned response for each prompt-hash bucket. The callable
    form (``responses=callable``) lets tests vary the response by
    inspecting the prompt; the dict form picks the first response that
    contains the matching substring.
    """

    def __init__(
        self,
        responses: dict[str, dict[str, Any]] | list[dict[str, Any]] | None = None,
    ) -> None:
        self._dict_responses = responses if isinstance(responses, dict) else {}
        self._list_responses: list[dict[str, Any]] = (
            list(responses) if isinstance(responses, list) else []
        )
        self._calls: list[str] = []
        self._cursor = 0

    @property
    def calls(self) -> list[str]:
        return list(self._calls)

    async def synthesize_insight(self, prompt: str) -> dict[str, Any]:
        self._calls.append(prompt)
        for needle, response in self._dict_responses.items():
            if needle in prompt:
                return _copy(response)
        if self._list_responses:
            response = self._list_responses[self._cursor % len(self._list_responses)]
            self._cursor += 1
            return _copy(response)
        # Default: a low-confidence empty observation so validator gates fire.
        return {
            "narrative": "no_response",
            "confidence": 0.0,
            "kind": "observation",
        }


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a stub response so tests can't mutate the canned payload."""
    return {k: _copy_any(v) for k, v in value.items()}


def _copy_any(value: Any) -> Any:
    """Recursive copy helper for arbitrarily nested stub values."""
    if isinstance(value, dict):
        return {k: _copy_any(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_any(item) for item in value]
    return value


def parse_strict_json(raw: str) -> dict[str, Any]:
    """Parse a JSON payload from an LLM response, fenced or not.

    Centralized because every adapter handles markdown fences slightly
    differently — strip them once here so the rest of the pipeline
    doesn't have to guess what the model returned.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[: -len("```")]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object, got {type(parsed).__name__}")
    return parsed
