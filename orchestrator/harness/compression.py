"""Context compression for long ReAct traces (MET-547, Phase 4).

A ReAct loop can accumulate more steps than fit a model's context budget. This
compresses a trace *deterministically* (no model call): the goal is always
kept, the most recent ``keep_recent`` steps are kept verbatim, and everything
older is folded into a compact lineage-preserving synopsis (how many steps,
which tools were called, how many errored). That keeps the immediate working
context intact while summarizing history.

The token counter is injected (default: a ~4-chars/token heuristic) so callers
can plug in a real tokenizer without this module taking the dependency.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import structlog

from orchestrator.harness.react import ReActStep

logger = structlog.get_logger(__name__)

TokenCounter = Callable[[str], int]


def default_token_count(text: str) -> int:
    """Cheap deterministic estimate (~4 characters per token)."""
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class CompressedContext:
    """A trace reduced to fit a budget."""

    goal: str
    synopsis: str | None  # summary of dropped older steps; None if none dropped
    recent: list[ReActStep]
    est_tokens: int

    @property
    def compressed(self) -> bool:
        return self.synopsis is not None


def _render_step(step: ReActStep) -> str:
    parts = [f"thought: {step.thought}"]
    if step.tool_call is not None:
        parts.append(f"action: {step.tool_call.name}({step.tool_call.arguments})")
    if step.error is not None:
        parts.append(f"error: {step.error}")
    elif step.observation is not None:
        parts.append(f"observation: {step.observation}")
    return " | ".join(parts)


def _summarize(older: Sequence[ReActStep]) -> str:
    tools: dict[str, int] = {}
    errors = 0
    for step in older:
        if step.tool_call is not None:
            tools[step.tool_call.name] = tools.get(step.tool_call.name, 0) + 1
        if step.error is not None:
            errors += 1
    tool_desc = ", ".join(f"{name}×{n}" for name, n in sorted(tools.items())) or "none"
    return f"[{len(older)} earlier steps compressed — tools: {tool_desc}; errors: {errors}]"


def _render_all(goal: str, synopsis: str | None, steps: Sequence[ReActStep]) -> str:
    lines = [f"goal: {goal}"]
    if synopsis is not None:
        lines.append(synopsis)
    lines.extend(_render_step(s) for s in steps)
    return "\n".join(lines)


def truncate_observation(text: str, max_chars: int = 2000) -> str:
    """Cap an observation with an EXPLICIT marker (MET-568).

    The old behavior was a silent slice — the model (and anyone reading the
    trace) had no way to know content was missing, which turns a truncated
    tool result into a source of confident wrong answers. The marker states
    exactly how much was dropped.
    """
    if len(text) <= max_chars:
        return text
    dropped = len(text) - max_chars
    return f"{text[:max_chars]}…[truncated {dropped} chars]"


def budget_history(
    history: Sequence[dict[str, Any]],
    *,
    max_tokens: int,
    count_tokens: TokenCounter = default_token_count,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split ``history`` into (kept, dropped) under a token budget (MET-568).

    Keeps the NEWEST turns whole, accumulating backwards until the budget is
    spent; everything older is returned as ``dropped`` (oldest first) for the
    caller to summarize. Replaces the old fixed 20-turn slice, which threw
    away short early turns that would have fit and kept huge recent ones that
    blew the window.
    """
    kept_rev: list[dict[str, Any]] = []
    spent = 0
    cut = len(history)
    for i in range(len(history) - 1, -1, -1):
        turn = history[i]
        cost = count_tokens(str(turn.get("content", "")))
        if spent + cost > max_tokens and kept_rev:
            break
        spent += cost
        kept_rev.append(turn)
        cut = i
    return list(reversed(kept_rev)), list(history[:cut])


def summarize_turns(
    turns: Sequence[dict[str, Any]],
    *,
    max_chars_per_turn: int = 200,
    max_total_chars: int = 6000,
) -> str:
    """Deterministic content-preserving summary of dropped chat turns (MET-568).

    One line per dropped turn, content head-truncated — facts stated early in
    a conversation (serials, specs, names) survive into the summary so the
    model can still recall them after the verbatim turns no longer fit the
    budget. Deterministic (no model call) so it is free, instant, and
    reproducible; an LLM-written rolling summary can replace this later
    without changing the call sites.
    """
    lines = [f"[Summary of {len(turns)} earlier conversation turns:]"]
    total = len(lines[0])
    for turn in turns:
        content = " ".join(str(turn.get("content", "")).split())
        if len(content) > max_chars_per_turn:
            content = content[:max_chars_per_turn] + "…"
        line = f"- {turn.get('role', '?')}: {content}"
        if total + len(line) > max_total_chars:
            lines.append(f"- …and {len(turns) - (len(lines) - 1)} more turns omitted")
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _is_tool_exchange_start(msg: dict[str, Any]) -> bool:
    return msg.get("role") == "assistant" and bool(msg.get("tool_calls"))


def compact_native_messages(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    keep_recent_exchanges: int = 3,
    count_tokens: TokenCounter = default_token_count,
) -> list[dict[str, Any]]:
    """Fold older tool exchanges in a native-loop message list (MET-568).

    A native tool-calling turn grows without bound: each iteration appends an
    assistant tool_calls message plus one ``tool`` result message per call
    (historically 24 steps × 8KB). When the estimated total exceeds
    ``max_tokens``, the exchanges older than ``keep_recent_exchanges`` are
    replaced by ONE synthetic user message carrying a ``compress_trace``-style
    synopsis (which tools ran, how often, how many errored). The leading
    segment (conversation history + the goal) and the most recent exchanges
    stay verbatim.
    """
    est = sum(count_tokens(json.dumps(m, default=str)) for m in messages)
    if est <= max_tokens:
        return messages

    # Locate exchange boundaries: each starts at an assistant tool_calls
    # message and runs through its tool results.
    starts = [i for i, m in enumerate(messages) if _is_tool_exchange_start(m)]
    if len(starts) <= keep_recent_exchanges:
        return messages
    lead_end = starts[0]
    fold_end = starts[len(starts) - keep_recent_exchanges]
    folded = messages[lead_end:fold_end]

    tools: dict[str, int] = {}
    errors = 0
    for m in folded:
        if _is_tool_exchange_start(m):
            for call in m.get("tool_calls") or []:
                name = str((call.get("function") or {}).get("name", "?"))
                tools[name] = tools.get(name, 0) + 1
        elif m.get("role") == "tool" and str(m.get("content", "")).startswith("ERROR"):
            errors += 1
    tool_desc = ", ".join(f"{name}×{n}" for name, n in sorted(tools.items())) or "none"
    synopsis = {
        "role": "user",
        "content": (
            f"[{len(folded)} earlier tool-exchange messages compressed — "
            f"tools: {tool_desc}; errors: {errors}. Results already reflected "
            f"in the conversation; do not repeat these calls.]"
        ),
    }
    compacted = [*messages[:lead_end], synopsis, *messages[fold_end:]]
    logger.info(
        "native_messages_compacted",
        before_msgs=len(messages),
        after_msgs=len(compacted),
        before_tokens=est,
        folded=len(folded),
    )
    return compacted


def compress_trace(
    goal: str,
    steps: Sequence[ReActStep],
    *,
    max_tokens: int,
    keep_recent: int = 3,
    count_tokens: TokenCounter = default_token_count,
) -> CompressedContext:
    """Compress ``steps`` to fit ``max_tokens``, keeping goal + recent verbatim."""
    full_text = _render_all(goal, None, steps)
    full_tokens = count_tokens(full_text)
    if full_tokens <= max_tokens or len(steps) <= keep_recent:
        # Fits, or nothing old enough to fold away.
        return CompressedContext(
            goal=goal, synopsis=None, recent=list(steps), est_tokens=full_tokens
        )

    recent = list(steps[-keep_recent:])
    older = steps[: len(steps) - keep_recent]
    synopsis = _summarize(older)
    est = count_tokens(_render_all(goal, synopsis, recent))
    logger.info(
        "context_compressed",
        goal=goal,
        dropped=len(older),
        kept=len(recent),
        before=full_tokens,
        after=est,
    )
    return CompressedContext(goal=goal, synopsis=synopsis, recent=recent, est_tokens=est)
