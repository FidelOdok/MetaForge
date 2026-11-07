"""Prompt-cache layout for harness requests (FORGE-478).

Providers cache by exact prefix, so every request is laid out as a stable
prefix (tool schemas, system prompt) followed by the volatile suffix (history,
latest tool results), and the prefix must be byte-identical from one step to
the next while its inputs are unchanged.

- **Anthropic** caches only where told: ``cache_control`` breakpoints on the
  last tool, the stable system block and the last message block (3 of the
  4 allowed). The prefix order Anthropic hashes is tools, system, messages.
- **OpenAI / OpenRouter** cache automatically on the longest identical prefix,
  so this module only guarantees ordering and supplies a stable
  ``prompt_cache_key`` that routes steps of a conversation to the same cache.
- A per-round, volatile system note travels as ``system_suffix`` so it never
  mutates the cached system block.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Anthropic allows at most four cache breakpoints per request.
MAX_ANTHROPIC_BREAKPOINTS = 4

_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tools in a deterministic order with deterministic key order.

    Sorted by tool name and round-tripped through sorted-key JSON so a schema
    built in a different insertion order serialises to the same bytes.
    """

    def _name(t: dict[str, Any]) -> str:
        fn = t.get("function", t)
        return str(fn.get("name", ""))

    return [json.loads(_canonical(t)) for t in sorted(tools, key=_name)]


def prompt_cache_key(system: str | None, tools: list[dict[str, Any]] | None) -> str:
    """Stable key for the prefix: same system prompt and tools, same key."""
    digest = hashlib.sha256(_canonical([system or "", tools or []]).encode("utf-8")).hexdigest()
    return f"mf-{digest[:32]}"


def supports_prompt_cache_key(base_url: str | None) -> bool:
    """Only endpoints known to accept ``prompt_cache_key`` get it.

    Self-hosted OpenAI-compatible servers (vLLM, Ollama) may reject unknown
    fields, so the default is to omit it.
    """
    if not base_url:
        return True
    return "api.openai.com" in base_url or "openrouter.ai" in base_url


def anthropic_system(system: str | None, suffix: str | None = None) -> list[dict[str, Any]] | None:
    """System as blocks: the stable text carries the breakpoint, the note does not."""
    blocks: list[dict[str, Any]] = []
    if system:
        blocks.append({"type": "text", "text": system, "cache_control": dict(_EPHEMERAL)})
    if suffix:
        blocks.append({"type": "text", "text": suffix})
    return blocks or None


def mark_last_tool(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Breakpoint on the last Anthropic tool, caching the whole tools array."""
    if not tools:
        return tools
    return [*tools[:-1], {**tools[-1], "cache_control": dict(_EPHEMERAL)}]


def mark_last_message(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Breakpoint on the final message block so the next step reads the history."""
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str):
        if not content:
            return messages
        blocks: list[Any] = [{"type": "text", "text": content}]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        blocks = list(content)
    else:
        return messages
    blocks[-1] = {**blocks[-1], "cache_control": dict(_EPHEMERAL)}
    return [*messages[:-1], {**last, "content": blocks}]


def system_text(request: Any) -> tuple[str | None, str | None]:
    """(stable system, volatile suffix) from a request dict."""
    if not isinstance(request, dict):
        return None, None
    return request.get("system"), request.get("system_suffix")


# ---------------------------------------------------------------------------
# Claude over an OpenAI-compatible router (FORGE-825)
# ---------------------------------------------------------------------------
#
# OpenRouter caches Claude only where told, exactly like Anthropic's own API:
# its automatic prefix caching covers OpenAI-family models, not Claude. A
# live run sent 75k to 84k prompt tokens per call with ``cached_tokens: 0``.
# So a Claude request through OpenRouter carries:
#
# - an explicit ``cache_control`` breakpoint on the stable system text. Claude
#   hashes the prefix as tools, then system, then messages, so this one
#   breakpoint caches the whole tools array and the system prompt together;
# - the volatile per-round note as a second, unmarked system part, so it never
#   changes the cached bytes;
# - a top-level ``cache_control`` (OpenRouter's automatic mode), which puts a
#   moving breakpoint on the last cacheable block. Calls 2 to N in a turn and
#   the next turn then read the conversation so far from the cache.
#
# https://openrouter.ai/docs/features/prompt-caching documents both shapes.

#: Routers known to forward Claude ``cache_control`` parts. A self-hosted
#: OpenAI-compatible server could reject a content-part system message.
_CLAUDE_CACHE_ROUTERS = ("openrouter.ai",)


def is_claude_model(model: str | None) -> bool:
    """A Claude model id, bare or behind a router prefix (``anthropic/...``)."""
    m = (model or "").strip().lower().lstrip("~")
    return m.startswith(("anthropic/", "claude")) or "/claude" in m


def routes_claude_cache_control(
    provider: str | None, base_url: str | None, model: str | None
) -> bool:
    """Does this OpenAI-compatible request need explicit Claude breakpoints?"""
    if not is_claude_model(model):
        return False
    if (provider or "").strip().lower() == "openrouter":
        return True
    return bool(base_url) and any(host in str(base_url) for host in _CLAUDE_CACHE_ROUTERS)


def openai_claude_system_message(stable: str | None, suffix: str | None) -> dict[str, Any] | None:
    """The system message as content parts; the stable part carries the breakpoint."""
    parts: list[dict[str, Any]] = []
    if stable:
        parts.append({"type": "text", "text": stable, "cache_control": dict(_EPHEMERAL)})
    if suffix:
        parts.append({"type": "text", "text": suffix})
    if not parts:
        return None
    return {"role": "system", "content": parts}


def automatic_cache_control() -> dict[str, str]:
    """OpenRouter's top-level field: a breakpoint that follows the conversation."""
    return dict(_EPHEMERAL)
