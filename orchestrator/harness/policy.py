"""Model-backed ReAct policy (MET-548).

The ReAct loop's `Policy` decides the next action. `ModelPolicy` asks a model
(through `HarnessRuntime.complete` + a provider `invoke`) and parses its reply
into a `ReActAction`. The model is instructed to answer in a small JSON
protocol:

    {"thought": "...", "tool": "mcp_calculix_run_fea", "arguments": {...}}   # act
    {"thought": "...", "final": "the answer"}                                # done

Parsing is lenient — fenced JSON, surrounding prose, or a plain non-JSON reply
(treated as a final answer) all resolve — so a model that doesn't perfectly
follow the protocol still makes progress instead of crashing the loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import structlog

from orchestrator.harness.providers import default_invoke
from orchestrator.harness.providers.pipeline import Invoke
from orchestrator.harness.react import ReActAction, ReActStep, ToolCall
from orchestrator.harness.runtime import HarnessRuntime

logger = structlog.get_logger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_SYSTEM = (
    "You are a MetaForge harness agent. Work toward the goal by calling tools, "
    "one step at a time. Reply ONLY with a JSON object, either:\n"
    '  {"thought": "...", "tool": "<name>", "arguments": {...}}  to call a tool, or\n'
    '  {"thought": "...", "final": "<answer>"}  when done.\n'
    "Available tools:\n"
)


def parse_action(text: str) -> ReActAction:
    """Parse a model reply into a ReActAction (lenient)."""
    raw = text.strip()
    fenced = _FENCE_RE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    obj = None
    if start != -1 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            obj = None
    if not isinstance(obj, dict):
        # Model didn't emit JSON — treat the whole reply as a final answer.
        return ReActAction(thought="(unstructured reply)", final_output=text.strip())
    thought = str(obj.get("thought", ""))
    if "final" in obj:
        return ReActAction(thought=thought, final_output=obj["final"])
    tool = obj.get("tool")
    if tool:
        arguments = obj.get("arguments") or {}
        return ReActAction(thought=thought, tool_call=ToolCall(str(tool), dict(arguments)))
    return ReActAction(thought=thought, final_output=obj.get("output", text.strip()))


@dataclass
class ModelPolicy:
    """A ReAct policy that asks a model for the next action."""

    runtime: HarnessRuntime
    role: str = "generator"
    invoke: Invoke = field(default=default_invoke)
    system_prefix: str = _SYSTEM

    def _tool_catalog(self) -> str:
        tools = self.runtime.tools.all_tools()
        if not tools:
            return "(no tools registered)"
        return "\n".join(f"- {t.name}: {t.description}" for t in tools)

    def _render_trace(self, steps: list[ReActStep]) -> str:
        if not steps:
            return "(no steps yet)"
        lines = []
        for s in steps:
            if s.tool_call is not None:
                obs = s.error or s.observation
                lines.append(f"- called {s.tool_call.name} -> {obs}")
        return "\n".join(lines) or "(no tool calls yet)"

    async def next_action(self, goal: str, steps: list[ReActStep]) -> ReActAction:
        content = f"Goal: {goal}\n\nProgress so far:\n{self._render_trace(steps)}\n\nNext action?"
        request = {
            "system": self.system_prefix + self._tool_catalog(),
            "messages": [{"role": "user", "content": content}],
        }
        resp = await self.runtime.complete(self.role, request, self.invoke)
        text = resp.get("text", "") if isinstance(resp, dict) else str(resp)
        action = parse_action(text)
        logger.info(
            "model_policy_action",
            role=self.role,
            is_final=action.is_final,
            tool=action.tool_call.name if action.tool_call else None,
        )
        return action
