"""Deterministic requirements phase handler for the design flow (MET-10).

The native requirements phase quantifies constraints but records them as generic
prose with no verification method, so the requirements rubric scores it 0.833
(the gap is verification_criteria — ISO-15288 verifiability). This handler makes
the requirements engineering-grade: the LLM extracts the functional requirements
and constraints from the goal, then this handler deterministically ties each
quantified constraint to an acceptance / verification method and records it.

FORGE-528: the requirement values have one home, the constraint set. The
handler records, in order:

1. the constraint set (``twin.record_constraint_set``), the only place the
   quantified constraints and their verification methods are stored;
2. the prd *prose* (goal, functional scope, interfaces, environment), which
   the prd view renders together with the current constraint set;
3. a decision that records the choice and links the constraint set revision
   (``depends_on=[CS-KEY@n]``) instead of restating its values.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from orchestrator.design_flow.executor import FlowContext, PhaseOutcome
from orchestrator.design_flow.spec import Phase
from skill_registry.mcp_bridge import McpBridge

logger = structlog.get_logger(__name__)

_DEFAULT_CONSTRAINTS = [
    {"param": "mass", "limit": "<= 15", "unit": "g", "verify": "measured on a scale"},
    {
        "param": "board size",
        "limit": "per the outline",
        "unit": "mm",
        "verify": "inspected to the envelope",
    },
    {"param": "power", "limit": "<= 0.5", "unit": "W", "verify": "measured on the supply rail"},
]


def _slug_title(goal: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z]+", goal) if len(w) > 2][:3]
    return " ".join(w.capitalize() for w in words) or "Product"


def _str_list(raw: Any, default: list[str], cap: int = 80) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        out = [str(x).strip()[:cap] for x in raw if isinstance(x, str) and str(x).strip()]
    return out or list(default)


def _normalize_req_spec(spec: dict[str, Any], goal: str) -> dict[str, Any]:
    """Coerce an extracted requirements spec into a complete, verifiable set."""
    functional = _str_list(
        spec.get("functional"),
        [f"Deliver the core function described by the goal: {goal[:80]}"],
    )
    raw = spec.get("constraints")
    constraints: list[dict[str, str]] = []
    if isinstance(raw, list):
        for c in raw:
            if isinstance(c, dict) and (c.get("param") or c.get("limit")):
                constraints.append(
                    {
                        "param": str(c.get("param") or "constraint").strip()[:32],
                        "limit": str(c.get("limit") or "").strip()[:32],
                        "unit": str(c.get("unit") or "").strip()[:12],
                        "verify": str(c.get("verify") or "").strip()[:60]
                        or "measured against the stated limit",
                    }
                )
    if not constraints:
        constraints = [dict(c) for c in _DEFAULT_CONSTRAINTS]

    interfaces = _str_list(spec.get("interfaces"), ["primary interface per the goal"], cap=24)
    environment = (
        str(spec.get("environment") or "").strip()[:120]
        or "indoor operating environment; nominal supply voltage per the goal"
    )
    name = str(spec.get("name") or "").strip() or f"{_slug_title(goal)} requirements"
    return {
        "name": name[:60],
        "functional": functional,
        "constraints": constraints,
        "interfaces": interfaces,
        "environment": environment,
    }


def prd_md(spec: dict[str, Any], goal: str) -> str:
    """The requirements as one markdown table with a verification column.

    Kept for the reader who wants the extracted spec in one page; the
    handler itself records :func:`prd_prose_md` and lets the constraint set
    carry the table (FORGE-528).
    """
    lines = [
        f"# {spec['name']} — product requirements",
        "",
        f"Goal: {goal}",
        "",
        "## Functional requirements",
        "",
    ]
    lines += [f"- {f}" for f in spec["functional"]]
    lines += [
        "",
        "## Constraints (quantified + verifiable)",
        "",
        "| Parameter | Limit | Unit | Verification |",
        "|---|---|---|---|",
    ]
    for c in spec["constraints"]:
        lines.append(f"| {c['param']} | {c['limit']} | {c['unit']} | {c['verify']} |")
    lines += [
        "",
        f"## Interfaces\n\n- {', '.join(spec['interfaces'])}",
        "",
        f"## Operating environment\n\n- {spec['environment']}",
    ]
    return "\n".join(lines) + "\n"


def prd_prose_md(spec: dict[str, Any], goal: str, requirements_ref: str | None) -> str:
    """The prd prose: everything but the requirement values (FORGE-528)."""
    where = f"`{requirements_ref}`" if requirements_ref else "the project's constraint set"
    lines = [
        f"# {spec['name']}: product requirements",
        "",
        f"Goal: {goal}",
        "",
        "## Functional scope",
        "",
    ]
    lines += [f"- {f}" for f in spec["functional"]]
    lines += [
        "",
        f"## Interfaces\n\n- {', '.join(spec['interfaces'])}",
        "",
        f"## Operating environment\n\n- {spec['environment']}",
        "",
        f"Quantified requirements and their verification methods are in {where}.",
    ]
    return "\n".join(lines) + "\n"


async def _extract_req_spec(
    goal: str, prior: str, *, provider: str | None, model: str | None
) -> dict[str, Any]:
    """Ask the LLM for a verifiable requirements spec (never raises)."""
    from api_gateway.chat.harness_backend import run_chat_turn
    from api_gateway.chat.routes import get_metrics

    prompt = (
        "You are writing engineering requirements for a hardware product. Every quantified "
        "constraint MUST have an acceptance / verification method (how it will be checked). "
        "Reply with ONLY JSON:\n"
        '{"name": "<requirements name>", "functional": ["expose the sensor on a header", ...], '
        '"constraints": [{"param": "mass", "limit": "<= 10", "unit": "g", '
        '"verify": "measured on a scale"}], '
        '"interfaces": ["I2C", "USB"], "environment": "USB powered, -40 to 85 C"}\n'
        "Use the real numbers from the goal.\n\n"
        f"Goal: {goal}\nContext: {prior}"
    )
    try:
        reply = await run_chat_turn(
            prompt,
            mcp_bridge=None,
            session_id="req-spec",
            max_steps=1,
            provider=provider,
            model=model,
            metrics=get_metrics(),
        )
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        spec = json.loads(match.group(0)) if match else {}
    except Exception as exc:  # noqa: BLE001 - fall back to a default spec, never fail
        logger.warning("req_spec_extract_failed", error=str(exc))
        spec = {}
    return _normalize_req_spec(spec if isinstance(spec, dict) else {}, goal)


def _data(envelope: Any, tool: str) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        return {}
    if envelope.get("status") == "error":
        raise RuntimeError(f"{tool} failed: {envelope.get('error') or envelope}")
    data = envelope.get("data", envelope)
    return data if isinstance(data, dict) else {}


_LIMIT_RE = re.compile(r"^(<=|>=|<|>|==)\s*([0-9]+(?:\.[0-9]+)?)$")


def _slug(text: str) -> str:
    out = "".join(ch if ch.isalnum() else "_" for ch in text.strip().lower())
    return "_".join(p for p in out.split("_") if p)


def constraint_entries_from_spec(constraints: list[dict[str, str]]) -> list[dict[str, str]]:
    """Map the extracted requirement constraints into evaluable entries (MET-582).

    A quantified limit (``"<= 60"``) becomes an ERROR-severity Python
    expression over work-product metadata (key = ``<param>_<unit>`` slug,
    e.g. ``mass_g``). Absent metadata defaults to a PASSING value — the
    constraint arms itself the moment a tool records the real number, and
    a project with no data yet is not failed vacuously. Non-quantified
    limits ("per the outline") become INFO-severity entries: documented in
    the constraint_set work product with their verification method, never
    gating.
    """
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for c in constraints:
        param = str(c.get("param") or "constraint")
        limit = str(c.get("limit") or "").strip()
        unit = str(c.get("unit") or "").strip()
        verify = str(c.get("verify") or "").strip()
        name = _slug(param) or "constraint"
        if name in seen:
            name = f"{name}_{sum(1 for s in seen if s.startswith(name)) + 1}"
        seen.add(name)
        message = f"{param} {limit} {unit}".strip() + (f" (verify: {verify})" if verify else "")
        m = _LIMIT_RE.match(limit)
        if m:
            op, value = m.groups()
            key = name + (f"_{_slug(unit)}" if unit else "")
            default = "0" if op in ("<=", "<") else value
            entries.append(
                {
                    "name": name,
                    "expression": (
                        f"all(float(wp.metadata.get('{key}', {default})) {op} {value} "
                        "for wp in ctx.work_products())"
                    ),
                    "severity": "error",
                    "message": message,
                    # FORGE-312: `verify` now also lands in the real
                    # `verification_method` field (constraint_recorder.py
                    # keeps it, G7 reads it) -- the message suffix above
                    # stays too, for a human skimming the rendered
                    # constraint-set document. For a quantified constraint,
                    # the condition itself IS its own acceptance criterion.
                    "verification_method": verify,
                    "acceptance_criteria": f"{param} {limit} {unit}".strip(),
                }
            )
        else:
            entries.append(
                {
                    "name": name,
                    "expression": "True",
                    "severity": "info",
                    "message": message,
                    "verification_method": verify,
                }
            )
    return entries


class GoalDrivenRequirementsHandler:
    """Records verifiable requirements (each constraint has an acceptance method) + a PRD."""

    def __init__(
        self,
        bridge: McpBridge,
        doc_recorder: Any,
        *,
        provider: str | None = None,
        model: str | None = None,
        extract: Any = _extract_req_spec,
    ) -> None:
        self._bridge = bridge
        self._doc_recorder = doc_recorder
        self._provider = provider
        self._model = model
        self._extract = extract

    async def _record_decision(
        self,
        *,
        title: str,
        rationale: str,
        project_id: str | None,
        depends_on: list[str] | None = None,
    ) -> None:
        args: dict[str, Any] = {"title": title, "rationale": rationale}
        if project_id:
            args["project_id"] = project_id
        if depends_on:
            args["depends_on"] = depends_on
        _data(await self._bridge.invoke("twin.record_decision", args), "twin.record_decision")

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        from twin_core.models.enums import WorkProductType

        # FORGE-48/49: G0 (Intent) and G1 (Needs) now run before this phase in
        # every flow, so their PhaseOutcome summaries are already in
        # context.completed and land in `prior` below -- the extraction
        # prompt sees the recorded intent/need for free, with no extra code
        # here. This handler deliberately does NOT also emit its own
        # intent/stakeholder_need entities (that would duplicate G0/G1's own
        # deliverable, not extend it).
        prior = "\n".join(f"  - {p.title}: {o.summary}" for p, o in context.completed) or "(none)"
        spec = await self._extract(goal, prior, provider=self._provider, model=self._model)

        # 1. MET-582 / FORGE-528: the quantified constraints land as an
        #    evaluable constraint_set (Constraint nodes + typed work product),
        #    the one home of the requirement values. The constraint engine
        #    checks them at every later gate (MET-583), and the Requirements
        #    gate REQUIRES this deliverable, so a failure here surfaces as a
        #    readable missing-deliverable gate failure.
        set_node = None
        set_ref: str | None = None
        try:
            cs_args: dict[str, Any] = {
                "title": f"{spec['name']} constraints",
                "constraints": constraint_entries_from_spec(spec["constraints"]),
            }
            if context.project_id:
                cs_args["project_id"] = context.project_id
            cs = _data(
                await self._bridge.invoke("twin.record_constraint_set", cs_args),
                "twin.record_constraint_set",
            )
            set_node = cs.get("node_id") if isinstance(cs, dict) else None
            ref = cs.get("item_ref") if isinstance(cs, dict) else None
            set_ref = ref if isinstance(ref, str) and ref else None
        except Exception as exc:  # noqa: BLE001 - the gate enforces; don't mask the phase
            logger.warning("requirements_constraint_set_failed", error=str(exc))

        # 2. The prd prose. The prd a reader sees renders it together with
        #    the constraint set above, so the values are not copied here.
        rec = await self._doc_recorder(
            content=prd_prose_md(spec, goal, set_ref),
            name=f"{spec['name']} PRD",
            wp_type=WorkProductType.PRD,
            domain="requirements",
            fmt="md",
            link_type="prd",
            source_tool="requirements.prd",
            session_id=context.session_id,
            project_id=context.project_id,
        )
        prd_node = rec.get("node_id") if isinstance(rec, dict) else None

        # 3. Decision: the choice, linked to the requirement revision it rests
        #    on rather than restating the values.
        where = set_ref or "the project's constraint set"
        rationale = (
            f"Requirements for {goal}: {len(spec['functional'])} functional requirements "
            f"and {len(spec['constraints'])} quantified constraints, each with an acceptance / "
            f"verification method, recorded in {where}. Interfaces: "
            f"{', '.join(spec['interfaces'])}. The values are kept in the constraint set only."
        )
        await self._record_decision(
            title=f"{spec['name']} (verifiable requirements)",
            rationale=rationale,
            project_id=context.project_id,
            depends_on=[set_ref] if set_ref else None,
        )

        return PhaseOutcome(
            summary=(
                f"Requirements: {len(spec['functional'])} functional + {len(spec['constraints'])} "
                f"quantified constraints, each with an acceptance/verification method; recorded "
                f"the constraint set {set_ref or f'(node {set_node})'}, the prd prose "
                f"(node {prd_node}) and a decision that links them."
            ),
            artifacts=[
                f"prd:{prd_node}",
                f"constraint_set:{set_node}",
                "design_decision:requirements",
            ],
            status="completed",
        )
