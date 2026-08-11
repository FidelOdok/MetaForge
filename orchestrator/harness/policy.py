"""Model-backed ReAct policy (MET-548).

The ReAct loop's `Policy` decides the next action. `ModelPolicy` asks a model
(through `HarnessRuntime.complete` + a provider `invoke`) and parses its reply
into a `ReActAction`. The model is instructed to answer in a small JSON
protocol:

    {"thought": "...", "tool": "mcp_calculix_run_fea", "arguments": {...}}   # act
    {"thought": "...", "final": "the answer"}                                # done

Parsing tolerates fenced JSON or surrounding prose AROUND the JSON object, but
a reply with no parseable JSON object at all, or a JSON object with neither a
`tool` nor a `final` key, raises ``ReActParseError`` rather than silently
treating the stray text as a final answer — a model that ignores the protocol
must not be able to "succeed" with a narrative substituted for real tool
calls. ``run_react`` catches this and feeds it back as an observation, giving
the model a concrete chance to self-correct instead of crashing the loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from orchestrator.harness.compression import compress_trace, truncate_observation_value
from orchestrator.harness.providers import default_invoke
from orchestrator.harness.providers.pipeline import Invoke
from orchestrator.harness.react import ReActAction, ReActParseError, ReActStep, ToolCall
from orchestrator.harness.runtime import HarnessRuntime

logger = structlog.get_logger(__name__)

# MET-568: per-step observation cap in the rendered trace (explicit marker).
# MET-598: was a tiny, fixed 2000 chars regardless of the model's actual
# context window (compare trace_token_budget() in harness_backend.py, which
# already scales the *overall* trace to ~60% of a model's real window). This
# per-step cap was the odd one out and forced tools like project.list down to
# a handful of items per page no matter what `limit` a caller requested.
# Raised to match native_tools.py's _MAX_OBSERVATION_CHARS.
_MAX_OBS_CHARS = 75_000

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_SYSTEM = (
    "You are a MetaForge harness agent helping an engineer. Reply ONLY with a JSON "
    "object, either:\n"
    '  {"thought": "...", "tool": "<name>", "arguments": {...}}  to call a tool, or\n'
    '  {"thought": "...", "final": "<answer>"}  to answer.\n'
    "\n"
    "Tool discipline (important):\n"
    "- Answer DIRECTLY with a `final` when you already know the answer or the message "
    "is conversational (a greeting, a general question, small talk). Do NOT call a tool "
    "just to answer such messages.\n"
    "- Call a tool ONLY when you genuinely need external data or must take an action to "
    "answer the goal. Prefer the fewest calls.\n"
    "- If a tool call FAILED in the progress above, do NOT repeat the same call — try a "
    "different approach or give your best `final` answer with what you have.\n"
    "- If a tool call already SUCCEEDED in the progress above with the same arguments, "
    "do NOT call it again — its result is already visible above; reuse it.\n"
    "- NEVER claim in your `final` answer that an action was performed unless a "
    "successful tool call in the progress above actually performed it. If a call "
    "failed or you could not do something, say so plainly — a truthful partial "
    "result beats a fabricated complete one.\n"
    "- Always finish with a `final` answer; never leave the user without a reply.\n"
    "\n"
    "Available tools:\n"
)


def parse_action(text: str) -> ReActAction:
    """Parse a model reply into a ReActAction.

    Raises ``ReActParseError`` when the reply has no parseable ``{...}`` JSON
    object, or a JSON object with neither a ``tool`` nor a ``final`` key — the
    only two shapes the protocol defines. Tolerates a fenced code block or
    prose surrounding the JSON object itself (models often wrap it in
    ```json ... ``` or a short preamble); it does NOT tolerate a reply that
    never emits the object at all.
    """
    raw = text.strip()
    fenced = _FENCE_RE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    obj = None
    if start != -1 and end > start:
        # MET-614: strict=False — long payloads (multi-line rationale, CAD
        # scripts) routinely carry literal newlines inside string values,
        # which strict JSON rejects even though the reply is structurally
        # exactly what the protocol asked for.
        try:
            obj = json.loads(raw[start : end + 1], strict=False)
        except json.JSONDecodeError:
            obj = None
    if not isinstance(obj, dict) and start != -1:
        # MET-614: the first-{ .. last-} slice breaks when prose after the
        # object contains its own "}". Fall back to decoding the FIRST
        # complete object and ignoring whatever trails it.
        try:
            candidate, _ = json.JSONDecoder(strict=False).raw_decode(raw[start:])
        except json.JSONDecodeError:
            candidate = None
        if isinstance(candidate, dict):
            obj = candidate
    if not isinstance(obj, dict):
        raise ReActParseError(
            "reply must be ONLY a JSON object shaped "
            '{"thought": "...", "tool": "<name>", "arguments": {...}} or '
            '{"thought": "...", "final": "<answer>"} — no such object was found in: '
            + text.strip()[:200]
        )
    thought = str(obj.get("thought", ""))
    if "final" in obj:
        return ReActAction(thought=thought, final_output=obj["final"])
    tool = obj.get("tool")
    if tool:
        arguments = obj.get("arguments") or {}
        return ReActAction(thought=thought, tool_call=ToolCall(str(tool), dict(arguments)))
    raise ReActParseError(
        'the JSON object must have a "tool" or "final" key — got: ' + json.dumps(obj)[:200]
    )


@dataclass
class ModelPolicy:
    """A ReAct policy that asks a model for the next action."""

    runtime: HarnessRuntime
    role: str = "generator"
    invoke: Invoke = field(default=default_invoke)
    system_prefix: str = _SYSTEM
    # MET-568: budget for the rendered trace; older steps fold into a
    # synopsis (compress_trace) once the estimate crosses it.
    trace_token_budget: int = 16000
    # Prior conversation ([{role, content}], oldest first). Rendered as text
    # inside the goal message rather than as real chat turns: this policy's
    # protocol demands JSON-only replies, and prose assistant turns in the
    # message list would teach the model to answer in prose (MET-565).
    history: list[dict[str, Any]] | None = None

    def _render_history(self) -> str:
        if not self.history:
            return ""
        lines = [
            f"{m.get('role', 'user')}: {m.get('content', '')}"
            for m in self.history
            if str(m.get("content", "")).strip()
        ]
        if not lines:
            return ""
        return "Conversation so far:\n" + "\n".join(lines) + "\n\n"

    def _tool_catalog(self) -> str:
        """Render the tool catalog as name + description + argument schema.

        Live-caught: with only name+description (no schema), a model on this
        text-only ReAct path had no way to know a tool's actual argument names
        — it guessed ("shape_type", "kind" omitted, "body_id" omitted) and
        every real tool call failed. The native tool-calling path has always
        sent the full JSON schema as a structured API field (see
        native_tools._tool_schemas); this inlines the same ``input_schema`` as
        text so a ReAct-path model has the same information to work from.
        """
        tools = self.runtime.tools.all_tools()
        if not tools:
            return "(no tools registered)"
        lines = []
        for t in tools:
            schema = t.input_schema if isinstance(t.input_schema, dict) else {}
            lines.append(
                f"- {t.name}: {t.description}\n"
                f"  arguments schema: {json.dumps(schema, separators=(',', ':'))}"
            )
        return "\n".join(lines)

    def _render_trace(self, steps: list[ReActStep], synopsis: str | None = None) -> str:
        if not steps and synopsis is None:
            return "(no steps yet)"
        lines = [] if synopsis is None else [synopsis]
        for s in steps:
            if s.tool_call is not None:
                # MET-568: cap each observation with an explicit marker — a
                # huge tool result used to be inlined verbatim into EVERY
                # subsequent prompt of the turn, compounding until overflow.
                # MET-58X: a dict/list-shaped observation (e.g. project.list's
                # {"projects": [...], "total": N}) shrinks its list first, so
                # the trailing summary field survives instead of being
                # chopped off by a blind character slice mid-array.
                obs = truncate_observation_value(s.error or s.observation, _MAX_OBS_CHARS)
                lines.append(f"- called {s.tool_call.name} -> {obs}")
        return "\n".join(lines) or "(no tool calls yet)"

    async def next_action(self, goal: str, steps: list[ReActStep]) -> ReActAction:
        # MET-568: fold steps beyond the token budget into a synopsis instead
        # of letting the rendered trace grow without bound.
        ctx = compress_trace(goal, steps, max_tokens=self.trace_token_budget)
        content = (
            f"{self._render_history()}Goal: {goal}\n\n"
            f"Progress so far:\n{self._render_trace(ctx.recent, ctx.synopsis)}\n\nNext action?"
        )
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
