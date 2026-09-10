"""Unit tests for ReAct-trace context compression (MET-547, Phase 4)."""

from __future__ import annotations

from orchestrator.harness.compression import (
    compress_trace,
    default_token_count,
    summarize_trajectory,
)
from orchestrator.harness.react import ReActStep, ToolCall


def _step(i: int, *, tool: str = "double", error: str | None = None) -> ReActStep:
    return ReActStep(
        thought=f"thinking about step {i} " * 5,
        tool_call=ToolCall(tool, {"x": i}),
        observation=None if error else {"result": i},
        error=error,
    )


def test_default_token_count_heuristic() -> None:
    assert default_token_count("") == 1  # floored at 1
    assert default_token_count("a" * 40) == 10


def test_no_compression_when_under_budget() -> None:
    steps = [_step(1), _step(2)]
    ctx = compress_trace("goal", steps, max_tokens=10_000)
    assert not ctx.compressed
    assert ctx.synopsis is None
    assert ctx.recent == steps


def test_no_compression_when_fewer_than_keep_recent() -> None:
    steps = [_step(1), _step(2)]
    ctx = compress_trace("g", steps, max_tokens=1, keep_recent=3)
    assert not ctx.compressed  # nothing old enough to fold
    assert ctx.recent == steps


def test_compresses_older_steps() -> None:
    steps = [_step(i) for i in range(10)]
    ctx = compress_trace("build a widget", steps, max_tokens=50, keep_recent=3)
    assert ctx.compressed
    assert len(ctx.recent) == 3
    assert ctx.recent == steps[-3:]
    assert "7 earlier steps compressed" in ctx.synopsis
    assert "double×7" in ctx.synopsis
    assert "errors: 0" in ctx.synopsis


def test_synopsis_counts_errors_and_tools() -> None:
    steps = [
        _step(0, tool="alpha"),
        _step(1, tool="beta", error="boom"),
        _step(2, tool="alpha"),
        _step(3),
        _step(4),
        _step(5),
    ]
    ctx = compress_trace("g", steps, max_tokens=30, keep_recent=2)
    assert ctx.compressed
    assert "alpha×2" in ctx.synopsis
    assert "beta×1" in ctx.synopsis
    assert "errors: 1" in ctx.synopsis


def test_compression_reduces_estimated_tokens() -> None:
    steps = [_step(i) for i in range(20)]
    full = default_token_count("goal: g\n" + "\n".join(f"thought: {s.thought}" for s in steps))
    ctx = compress_trace("g", steps, max_tokens=40, keep_recent=3)
    assert ctx.est_tokens < full


def test_summarize_trajectory_empty_steps() -> None:
    assert "any actions" in summarize_trajectory([])


def test_summarize_trajectory_lists_successes_and_failures() -> None:
    steps = [
        _step(0, tool="create_body"),
        _step(1, tool="pad_sketch"),
        _step(2, tool="fillet_edges", error="edge not found"),
    ]
    summary = summarize_trajectory(steps)
    assert "ran out of turns" in summary
    assert "1. create_body — succeeded" in summary
    assert "2. pad_sketch — succeeded" in summary
    assert "3. fillet_edges — failed: edge not found" in summary
    assert "continue" in summary.lower()


def test_summarize_trajectory_skips_final_answer_steps() -> None:
    """A step with no tool_call (a final-answer step) contributes nothing to
    the numbered list — only real tool attempts are hand-off-worthy."""
    final_step = ReActStep(thought="done", tool_call=None, observation="answer")
    summary = summarize_trajectory([final_step])
    assert "1." not in summary
