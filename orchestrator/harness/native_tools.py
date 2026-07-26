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
from typing import Any

import structlog

from orchestrator.harness.providers import default_invoke
from orchestrator.harness.providers.pipeline import Invoke
from orchestrator.harness.react import ReActResult, ReActStep, ToolCall
from orchestrator.harness.runtime import HarnessRuntime

logger = structlog.get_logger(__name__)

NATIVE_SYSTEM = (
    "You are MetaForge's assistant helping an engineer with hardware design. "
    "Answer directly when you already know the answer or the message is "
    "conversational — do not call a tool just to reply. Call a tool only when you "
    "genuinely need external data or must take an action; prefer the fewest calls. "
    "If a tool fails, adapt or answer with what you have — do not repeat a failed "
    "call. Always give the user a clear final answer."
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


def _json_safe(value: Any) -> str:
    try:
        return json.dumps(value)[:8000]
    except (TypeError, ValueError):
        return str(value)[:8000]


async def run_native_tools(
    runtime: HarnessRuntime,
    goal: str,
    *,
    role: str = "generator",
    invoke: Invoke = default_invoke,
    max_steps: int = 8,
    system: str = NATIVE_SYSTEM,
) -> ReActResult:
    """Drive a native tool-calling loop until the model returns a final answer.

    Falls back to a forced text answer if the step cap is hit, so a run never
    ends without a reply.
    """
    tools = _tool_schemas(runtime)
    messages: list[dict[str, Any]] = [{"role": "user", "content": goal}]
    steps: list[ReActStep] = []

    for step_no in range(1, max_steps + 1):
        resp = await runtime.complete(
            role, {"system": system, "messages": messages, "tools": tools}, invoke
        )
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
    resp = await runtime.complete(role, {"system": system, "messages": messages}, invoke)
    final = resp.get("text", "") if isinstance(resp, dict) else str(resp)
    logger.info("native_tools_finalized", steps=max_steps)
    return ReActResult(status="completed", output=final, steps=steps)
