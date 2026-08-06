"""Native tool-calling loop (matches Claude Code's harness).

Instead of asking the model to emit JSON that we parse (see ``policy.py`` /
``react.py``), this drives tools through the provider's own function-calling API:
tool schemas go out as ``tools=``; the model replies with either text (the final
answer) or native ``tool_calls`` that we execute and feed back as ``tool`` role
messages. The model decides when a tool is needed — so a greeting is answered
directly, not flailed at with tools.

The loop speaks one canonical (OpenAI-compatible) message shape — ``openai_invoke``
returns ``{text, tool_calls:[{id,name,arguments}]}`` directly (OpenAI / OpenRouter /
vLLM / Ollama), and ``anthropic_invoke`` translates that shape to/from Anthropic's
``tool_use``/``tool_result`` blocks — so the same loop drives Claude models too.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from orchestrator.harness.compression import compact_native_messages, truncate_observation_value
from orchestrator.harness.providers import default_invoke
from orchestrator.harness.providers.pipeline import Invoke, StreamEvents
from orchestrator.harness.react import OnStep, ReActResult, ReActStep, ToolCall
from orchestrator.harness.runtime import HarnessRuntime

logger = structlog.get_logger(__name__)

NATIVE_SYSTEM = (
    "You are MetaForge's assistant helping an engineer with hardware design. "
    "Answer directly when you already know the answer or the message is "
    "conversational — do not call a tool just to reply. Call a tool only when you "
    "genuinely need external data or must take an action; prefer the fewest calls. "
    "If a tool fails, adapt or answer with what you have — do not repeat a failed "
    "call. Never claim an action was performed unless one of your tool calls "
    "actually performed it; if something failed or was skipped, say so plainly. "
    "Always give the user a clear final answer."
)


def _tool_schemas(runtime: HarnessRuntime) -> list[dict[str, Any]]:
    """Build OpenAI-style function schemas from the runtime's registered tools."""
    schemas: list[dict[str, Any]] = []
    for t in runtime.tools.all_tools():
        params = (
            t.input_schema
            if isinstance(t.input_schema, dict) and t.input_schema.get("type") == "object"
            else {"type": "object"}
        )
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": (t.description or t.name)[:1024],
                    "parameters": params,
                },
            }
        )
    return schemas


_MAX_OBSERVATION_CHARS = 8000


def _render_json(value: Any) -> str:
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def _json_safe(value: Any) -> str:
    """Serialize a tool observation, truncating LOUDLY past the cap (MET-568,
    MET-58X).

    The cap was previously a silent ``[:8000]`` slice — the model had no way
    to know a result was cut, so truncated data read as complete data. A
    dict/list-shaped result (the common ``{"items": [...], "total": N}``
    tool-envelope shape) now has its list shrunk structurally first — a
    plain character slice can (and did) land mid-array and chop off the
    trailing ``total`` field entirely, the exact metadata a model needs to
    self-report "N of M" instead of discovering an arbitrary cut with no
    idea what's missing.
    """
    return truncate_observation_value(value, _MAX_OBSERVATION_CHARS, render=_render_json)


async def run_native_tools(
    runtime: HarnessRuntime,
    goal: str,
    *,
    role: str = "generator",
    invoke: Invoke = default_invoke,
    max_steps: int = 8,
    system: str = NATIVE_SYSTEM,
    history: list[dict[str, Any]] | None = None,
    max_context_tokens: int | None = None,
    on_step: OnStep | None = None,
    on_stream_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    stream_events: StreamEvents | None = None,
) -> ReActResult:
    """Drive a native tool-calling loop until the model returns a final answer.

    ``history`` is the prior conversation ([{role, content}], oldest first) so
    the model can answer with context from earlier turns; it is seeded ahead of
    the current ``goal``. Falls back to a forced text answer if the step cap is
    hit, so a run never ends without a reply.

    ``max_context_tokens`` (MET-568) bounds within-turn growth: when the
    estimated message-list size crosses it, older tool exchanges are folded
    into a synopsis (``compact_native_messages``) before the next model call.
    ``None`` keeps the historical unbounded behavior.
    """
    tools = _tool_schemas(runtime)
    messages: list[dict[str, Any]] = [*(history or []), {"role": "user", "content": goal}]
    steps: list[ReActStep] = []

    async def _emit(step: ReActStep) -> None:
        # MET-590: live progress — a broken observer must never break the turn.
        if on_step is None:
            return
        try:
            await on_step(step, len(steps) - 1)
        except Exception as exc:  # noqa: BLE001 - observer is best-effort
            logger.warning("native_on_step_failed", error=str(exc))

    async def _forward(event: dict[str, Any]) -> None:
        # MET-591/592: token-level liveness, typed — never breaks the turn.
        if on_stream_event is None:
            return
        try:
            await on_stream_event(event)
        except Exception as exc:  # noqa: BLE001 - observer is best-effort
            logger.warning("native_on_stream_event_failed", error=str(exc))

    async def _model_call(request: dict[str, Any]) -> Any:
        """One model call: event-streaming when wired (text deltas flow to
        ``on_thinking`` as they generate), non-streaming invoke otherwise.
        A streaming failure (unsupported family, mid-negotiation error) falls
        back to the invoke path — behavior then matches the pre-MET-591 loop.
        """
        if stream_events is None or on_stream_event is None:
            return await runtime.complete(role, request, invoke)
        try:
            result: Any = None
            async for event in runtime.stream_events(role, request, stream_events):
                etype = event.get("type")
                if etype == "response":
                    result = event.get("result")
                elif etype in ("text_delta", "thinking_delta", "action_started"):
                    await _forward(event)
            if result is not None:
                return result
            logger.warning("native_stream_no_response_event")
        except Exception as exc:  # noqa: BLE001 - streaming is an optimization
            logger.info("native_stream_fallback_to_invoke", reason=str(exc)[:200])
        return await runtime.complete(role, request, invoke)

    for step_no in range(1, max_steps + 1):
        if max_context_tokens is not None:
            messages = compact_native_messages(messages, max_tokens=max_context_tokens)
        resp = await _model_call({"system": system, "messages": messages, "tools": tools})
        text = resp.get("text", "") if isinstance(resp, dict) else str(resp)
        calls = resp.get("tool_calls") if isinstance(resp, dict) else None

        if not calls:
            logger.info("native_tools_completed", steps=step_no)
            return ReActResult(status="completed", output=text, steps=steps)

        # Record the assistant turn (with its tool calls) so the next request has
        # the full history the provider expects.
        messages.append(
            {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c.get("arguments") or {}),
                        },
                    }
                    for c in calls
                ],
            }
        )
        for c in calls:
            name = c["name"]
            args = c.get("arguments") or {}
            try:
                observation = await runtime.call_tool(name, args)
                content = _json_safe(observation)
                steps.append(
                    ReActStep(thought=text, tool_call=ToolCall(name, args), observation=observation)
                )
            except Exception as exc:  # noqa: BLE001 - surface to the model, don't abort
                content = f"ERROR: {exc}"
                steps.append(
                    ReActStep(thought=text, tool_call=ToolCall(name, args), error=str(exc))
                )
                logger.warning("native_tool_error", tool=name, error=str(exc))
            await _emit(steps[-1])
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})

    # Step cap hit — force a final text answer (no tools) so we never return empty.
    messages.append(
        {
            "role": "user",
            "content": (
                "Give your final answer to the user now using what you have. Do not call tools."
            ),
        }
    )
    resp = await _model_call({"system": system, "messages": messages})
    final = resp.get("text", "") if isinstance(resp, dict) else str(resp)
    logger.info("native_tools_finalized", steps=max_steps)
    return ReActResult(status="completed", output=final, steps=steps)
