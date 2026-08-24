"""Illustrative token pricing for the chat-harness dollar spend cap.

Production-harness audit follow-up: no token-price table existed anywhere in
the codebase before this (``observability/cost_attribution.py`` only
aggregates a ``cost_usd`` that's computed elsewhere). Prices genuinely change
over time and vary per provider/model; this table is intentionally small
(only the providers ``native_tools_enabled()`` actually defaults to) and
**must be treated as illustrative, not authoritative** — verify against the
provider's current pricing page before relying on it for real budget
enforcement, and update it periodically.

A caller can always inject its own table (:class:`TokenPricing` is a plain
mapping) — this module's ``DEFAULT_PRICING`` is a reasonable starting point,
not a hardcoded assumption baked into the loop itself.
"""

from __future__ import annotations

TokenPricing = dict[tuple[str, str], tuple[float, float]]
"""(provider, model) -> (usd per 1K input tokens, usd per 1K output tokens)."""

# As of the time this was written (2026). Anthropic/OpenAI published pricing
# for the models this harness's env defaults actually resolve to
# (`METAFORGE_LLM_MODEL` default is "claude-opus-4-8"). Deliberately small —
# an unpriced (provider, model) pair means the spend cap is not enforced for
# that call, not that it's free (see `estimate_cost_usd`).
DEFAULT_PRICING: TokenPricing = {
    ("anthropic", "claude-opus-4-8"): (15.0, 75.0),
    ("anthropic", "claude-sonnet-5"): (3.0, 15.0),
    ("anthropic", "claude-haiku-4-5-20251001"): (0.8, 4.0),
    ("openai", "gpt-4o"): (2.5, 10.0),
    ("openai", "gpt-5.5"): (5.0, 15.0),
}


def estimate_cost_usd(
    pricing: TokenPricing, provider: str, model: str, usage: dict[str, int] | None
) -> float | None:
    """Estimate USD cost for token ``usage`` against ``provider``/``model``.

    Returns ``None`` — not ``0.0`` — when the pair isn't in ``pricing`` or
    ``usage`` is absent, so a caller enforcing a spend cap can tell "unknown,
    don't enforce" apart from "genuinely free," and never silently blocks a
    turn just because pricing data is missing for an uncommon provider.
    """
    if not usage:
        return None
    rates = pricing.get((provider, model))
    if rates is None:
        return None
    in_rate, out_rate = rates
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    return (input_tokens / 1000) * in_rate + (output_tokens / 1000) * out_rate
