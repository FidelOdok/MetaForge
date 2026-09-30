"""Asking the model which template to tailor, and how (FORGE-398).

The model's whole job here is two answers: which template fits the intent, and
which operations to apply. It never writes a flow — see
``orchestrator.design_flow.generator`` for why the operation set is closed.

The prompt shows it the real catalogue, because a model asked to pick from
flows it has to remember will invent one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog

from orchestrator.design_flow.generator import (
    FlowProposal,
    build_proposal,
    parse_operations,
)
from orchestrator.design_flow.spec import DEFAULT_FLOW_ID, FLOWS, get_flow
from orchestrator.design_flow.templates import load_templates

logger = structlog.get_logger(__name__)

__all__ = ["GeneratorUnavailableError", "TailoringRequest", "generate_proposal"]


class GeneratorUnavailableError(RuntimeError):
    """No model was reachable, so no proposal was made.

    Deliberately not "fall back to the default template". A flow the human
    believes was tailored to their project, and was not, is worse than being
    told the generator is down -- they would approve it on the strength of a
    tailoring that never happened.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"the flow generator could not reach a model: {detail}. No proposal was "
            "made. Pick a template directly, or retry once the provider is back -- "
            "there is no untailored fallback on purpose, because a flow that was "
            "not tailored must not be presented as though it was."
        )


@dataclass
class TailoringRequest:
    intent: str
    project_id: str | None = None
    requirements: list[str] | None = None
    provider: str | None = None
    model: str | None = None


def _catalogue_for_prompt() -> str:
    lines: list[str] = []
    for flow_id in sorted(FLOWS):
        template = load_templates()[flow_id]
        flow = template.definition
        lines.append(f"- {flow_id} ({template.display_label()}): {template.description}")
        for phase in flow.phases:
            gate = phase.gate.name if phase.gate else "no gate"
            lines.append(
                f"    {phase.id}: {phase.title} "
                f"[produces: {', '.join(phase.expected_artifacts) or 'nothing declared'}] "
                f"[gate: {gate}]"
            )
    return "\n".join(lines)


_PROMPT = """You are tailoring an engineering design flow to one project.

You do NOT write a flow. You pick a template and propose changes to it. The
only changes that exist are:

  drop_phase        — the phase does not apply to this product at all
  add_deliverable   — require an artifact from a phase, so its gate demands it
  set_disciplines   — the engineering disciplines a phase fans out into

There is no operation to remove a gate, remove a deliverable, or relax a
check. Tailoring may make a flow stricter, never laxer. If a phase is
genuinely unnecessary, drop the whole phase — do not keep it and weaken it.

Available templates and their phases:
{catalogue}

Reply with ONLY JSON:
{{"template": "<template id>",
  "rationale": "<one sentence: why this template fits this product>",
  "operations": [
    {{"op": "drop_phase", "phase": "<phase id>", "rationale": "<why it does not apply>"}},
    {{"op": "add_deliverable", "phase": "<phase id>", "value": "<artifact type>",
      "rationale": "<why this product needs that evidence>"}},
    {{"op": "set_disciplines", "phase": "<phase id>", "value": ["mechanical", "electronics"],
      "rationale": "<why these disciplines>"}}
  ]}}

Every operation needs a rationale. An operation without one is discarded.

Project intent: {intent}
Known requirements: {requirements}
"""


async def generate_proposal(request: TailoringRequest) -> FlowProposal:
    """Ask the model to tailor a template. Raises if no model answers."""
    from api_gateway.chat.harness_backend import run_chat_turn
    from api_gateway.chat.routes import get_metrics

    prompt = _PROMPT.format(
        catalogue=_catalogue_for_prompt(),
        intent=request.intent,
        requirements=", ".join(request.requirements or []) or "(none recorded yet)",
    )
    try:
        reply = await run_chat_turn(
            prompt,
            mcp_bridge=None,
            session_id="flow-generator",
            max_steps=1,
            provider=request.provider,
            model=request.model,
            metrics=get_metrics(),
        )
    except Exception as exc:  # noqa: BLE001 — reported, never swallowed
        logger.error("flow_generator_model_unreachable", error=str(exc))
        raise GeneratorUnavailableError(str(exc)) from exc

    parsed = _parse_reply(reply)
    template_id = parsed.get("template")
    if template_id not in FLOWS:
        # A model naming a template that does not exist is a model that did
        # not read the catalogue. Fall back to the default *template choice*
        # -- which is a different thing from falling back to an untailored
        # flow: the operations it proposed still apply, and the diff the human
        # approves shows which template they landed on.
        logger.info("flow_generator_unknown_template", requested=template_id, using=DEFAULT_FLOW_ID)
        template_id = DEFAULT_FLOW_ID

    operations = parse_operations(parsed.get("operations"))
    proposal = build_proposal(
        get_flow(template_id),
        base_version=load_templates()[template_id].version,
        operations=operations,
        intent=request.intent,
    )
    logger.info(
        "flow_generated",
        template=template_id,
        operations=len(proposal.operations),
        valid=proposal.valid,
    )
    return proposal


def _parse_reply(reply: Any) -> dict[str, Any]:
    """Pull the JSON object out of a model reply, tolerating prose around it."""
    text = reply if isinstance(reply, str) else str(getattr(reply, "content", "") or reply)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        logger.info("flow_generator_unparseable_reply", length=len(text))
        return {}
    return parsed if isinstance(parsed, dict) else {}
