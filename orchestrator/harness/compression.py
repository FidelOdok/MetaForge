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

from collections.abc import Callable, Sequence
from dataclasses import dataclass

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
