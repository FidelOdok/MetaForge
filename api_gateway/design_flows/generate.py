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

from orchestrator.design_flow.context import (
    MAX_EXTRA_QUESTIONS,
    ClarifyingQuestion,
    FlowContext,
)
from orchestrator.design_flow.generator import (
    CallerProvenance,
    FlowProposal,
    ModelProvenance,
    TailoringError,
    build_proposal,
    parse_caller_operations,
    parse_operations,
)
from orchestrator.design_flow.spec import DEFAULT_FLOW_ID, FLOWS, get_flow
from orchestrator.design_flow.templates import load_templates
from orchestrator.harness.providers.provenance import capture_served
from orchestrator.harness.providers.usage import usage_scope

logger = structlog.get_logger(__name__)

__all__ = [
    "CallerTailoringRequest",
    "GeneratorUnavailableError",
    "TailoringRequest",
    "build_caller_proposal",
    "generate_proposal",
    "parse_extra_questions",
    "suggest_extra_questions",
]


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
    #: What the project was said to be made with, how far it should go and
    #: what it must carry (FORGE-463). The route checks that the required
    #: parts are present before a model is ever asked.
    context: FlowContext | None = None


@dataclass
class CallerTailoringRequest:
    """A tailoring the caller's own model wrote (FORGE-481).

    ``operations`` is the raw list as received; it is validated strictly
    against the chosen template, not leniently like a server model's reply.
    """

    intent: str
    operations: Any
    template: str | None = None
    requirements: list[str] | None = None
    context: FlowContext | None = None
    client: str | None = None
    model: str | None = None


def build_caller_proposal(request: CallerTailoringRequest) -> FlowProposal:
    """Apply a caller's operations deterministically. Makes no model call.

    Raises :class:`orchestrator.design_flow.generator.TailoringError` for an
    unknown template, operation or phase. The invariants run in
    ``build_proposal`` exactly as for a generated flow, and the route refuses
    an invalid result before anything is stored.
    """
    template_id = request.template or DEFAULT_FLOW_ID
    if template_id not in FLOWS:
        raise TailoringError(
            f"unknown template '{template_id}'; templates: {', '.join(sorted(FLOWS))}"
        )
    base = get_flow(template_id)
    operations = parse_caller_operations(request.operations, base)
    proposal = build_proposal(
        base,
        base_version=load_templates()[template_id].version,
        operations=operations,
        intent=request.intent,
        context=request.context,
        proposed_by=CallerProvenance(client=request.client, model=request.model),
    )
    logger.info(
        "flow_caller_proposal_built",
        template=template_id,
        operations=len(proposal.operations),
        valid=proposal.valid,
        client=request.client,
        model=request.model,
    )
    return proposal


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
  set_model         = run one phase on a named "provider:model" (use only when
                      the intent or project context asks for it; otherwise
                      phases are routed by role and you leave this alone)
  declare_items     = name the parts a phase writes ("left bracket", "right
                      bracket"), one item each, so every run revises the same
                      parts instead of inventing new ones

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
      "rationale": "<why these disciplines>"}},
    {{"op": "set_model", "phase": "<phase id>", "value": "anthropic:claude-opus-4-8",
      "rationale": "<why this phase needs that model>"}},
    {{"op": "declare_items", "phase": "<phase id>",
      "value": [{{"type": "cad_model", "name": "left bracket"}}],
      "rationale": "<why the product has these parts>"}}
  ]}}

Every operation needs a rationale. An operation without one is discarded.

This flow is MANUFACTURING-LED. The project context below says what the person
can actually make it with. Use it:

- Every rationale that touches process, material or geometry must say how the
  choice follows from the stated processes, machines and stock (for example
  "plywood on hand and a table saw, so a mechanical design sized to sheet
  stock"). Do not assume a process, machine or material nobody stated.
- If the route is "undecided", do NOT pick one. MetaForge adds a
  route-selection decision phase itself; leave the choice to it.
- Target maturity decides verification. "concept" may stay light but keeps
  its V&V phase; "physically_validated" and "released" should require a
  test_plan (add_deliverable) in addition to simulation.
- If the loads are UNKNOWN, the simulation / V&V phase must stay. Never drop
  it on the grounds that "practical testing will suffice" -- a test with no
  load case verifies nothing, and MetaForge will refuse the flow.
- Dropping simulation is only acceptable when the loads are known AND you add
  a test_plan deliverable to a gated phase as the alternative verification.

You may also return up to {max_questions} product-specific questions whose
answers would change this flow, and the assumptions you had to make. Only ask
what the context below does not already answer.

Reply with ONLY JSON, as above, plus optionally:
  "questions": [{{"id": "<snake_case>", "question": "...", "why": "<why it changes the flow>",
                 "answer_type": "text|choice|number|list", "options": ["..."]}}],
  "assumptions": ["<anything you took as given>"]

Project intent: {intent}
Known requirements: {requirements}
Project context:
{context}
"""

_QUESTIONS_PROMPT = """A person wants to start an engineering design flow for this
product, and MetaForge is already asking them these questions:
{asked}

List at most {max_questions} OTHER questions specific to this product whose
answers would change which phases, deliverables or verification the flow
needs. Skip anything generic or already asked. If there are none, return [].

Reply with ONLY JSON:
{{"questions": [{{"id": "<snake_case>", "question": "...", "why": "<why it changes the flow>",
                 "answer_type": "text|choice|number|list", "options": ["..."]}}]}}

Product intent: {intent}
"""


async def generate_proposal(request: TailoringRequest) -> FlowProposal:
    """Ask the model to tailor a template. Raises if no model answers."""
    from api_gateway.chat.harness_backend import run_chat_turn
    from api_gateway.chat.routes import get_metrics

    context = request.context
    prompt = _PROMPT.format(
        catalogue=_catalogue_for_prompt(),
        intent=request.intent,
        requirements=", ".join(request.requirements or []) or "(none recorded yet)",
        context=(context or FlowContext()).describe_for_prompt(),
        max_questions=MAX_EXTRA_QUESTIONS,
    )
    try:
        # FORGE-468: record which provider/model actually answered. The
        # configured one may not have: the pipeline falls back silently from
        # the caller's point of view, and run_chat_turn returns only text.
        with capture_served() as served, usage_scope(role="flow_generator"):
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
    answered = served.last()
    generated_by = (
        ModelProvenance(
            provider=answered.provider,
            model=answered.model,
            fell_back_from=answered.fell_back_from,
        )
        if answered is not None
        else None
    )

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
    raw_assumptions = parsed.get("assumptions")
    assumptions = (
        [str(a).strip() for a in raw_assumptions if str(a).strip()]
        if isinstance(raw_assumptions, list)
        else []
    )
    proposal = build_proposal(
        get_flow(template_id),
        base_version=load_templates()[template_id].version,
        operations=operations,
        intent=request.intent,
        context=context,
        assumptions=assumptions,
        open_questions=parse_extra_questions(parsed.get("questions")),
        generated_by=generated_by,
    )
    logger.info(
        "flow_generated",
        template=template_id,
        operations=len(proposal.operations),
        valid=proposal.valid,
        provider=generated_by.provider if generated_by else None,
        model=generated_by.model if generated_by else None,
        fell_back_from=generated_by.fell_back_from if generated_by else None,
    )
    return proposal


async def suggest_extra_questions(
    intent: str,
    asked: list[ClarifyingQuestion],
    *,
    provider: str | None = None,
    model: str | None = None,
) -> list[ClarifyingQuestion]:
    """Product-specific questions to add to MetaForge's required ones.

    Raises :class:`GeneratorUnavailableError` if no model answers; the caller
    decides what that means. The required questions never depend on this.
    """
    from api_gateway.chat.harness_backend import run_chat_turn
    from api_gateway.chat.routes import get_metrics

    prompt = _QUESTIONS_PROMPT.format(
        asked="\n".join(f"- {q.question}" for q in asked) or "- (nothing yet)",
        intent=intent,
        max_questions=MAX_EXTRA_QUESTIONS,
    )
    try:
        with usage_scope(role="flow_generator"):
            reply = await run_chat_turn(
                prompt,
                mcp_bridge=None,
                session_id="flow-generator-questions",
                max_steps=1,
                provider=provider,
                model=model,
                metrics=get_metrics(),
            )
    except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
        logger.warning("flow_generator_questions_unreachable", error=str(exc))
        raise GeneratorUnavailableError(str(exc)) from exc
    questions = parse_extra_questions(_parse_reply(reply).get("questions"))
    taken = {q.id for q in asked}
    return [q for q in questions if q.id not in taken]


def parse_extra_questions(raw: Any) -> list[ClarifyingQuestion]:
    """A model's questions, capped and marked as the model's.

    Capped at :data:`MAX_EXTRA_QUESTIONS` whatever the model returned: a
    questionnaire is how people stop answering, and the required questions
    are the ones that must be answered.
    """
    if not isinstance(raw, list):
        return []
    out: list[ClarifyingQuestion] = []
    for entry in raw:
        if len(out) >= MAX_EXTRA_QUESTIONS:
            logger.info("flow_generator_questions_capped", returned=len(raw))
            break
        if not isinstance(entry, dict):
            continue
        question = str(entry.get("question") or "").strip()
        if not question:
            continue
        qid = str(entry.get("id") or "").strip() or f"product_{len(out) + 1}"
        answer_type = str(entry.get("answer_type") or "text").strip()
        if answer_type not in {"text", "choice", "number", "list"}:
            answer_type = "text"
        options = entry.get("options")
        out.append(
            ClarifyingQuestion(
                id=qid,
                question=question,
                why=str(entry.get("why") or "").strip(),
                answer_type=answer_type,
                options=tuple(str(o) for o in options) if isinstance(options, list) else (),
                required=False,
                source="model",
            )
        )
    return out


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
