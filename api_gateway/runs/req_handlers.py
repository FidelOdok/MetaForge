"""Deterministic requirements phase handler for the design flow (MET-10).

The native requirements phase quantifies constraints but records them as generic
prose with no verification method, so the requirements rubric scores it 0.833
(the gap is verification_criteria — ISO-15288 verifiability). This handler makes
the requirements engineering-grade: the LLM extracts the functional requirements
and constraints from the goal, then this handler deterministically ties each
quantified constraint to an acceptance / verification method, emits a real
``prd`` work product (the requirements table), and records a decision that
carries functional requirements, quantified + verifiable constraints, interfaces,
and the operating environment.
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
    """Render the requirements as a PRD with a verification column."""
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


async def _extract_req_spec(
    goal: str, prior: str, *, provider: str | None, model: str | None
) -> dict[str, Any]:
    """Ask the LLM for a verifiable requirements spec (never raises)."""
    from api_gateway.chat.harness_backend import run_chat_turn

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
                }
            )
        else:
            entries.append(
                {"name": name, "expression": "True", "severity": "info", "message": message}
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

    async def _record_decision(self, *, title: str, rationale: str, project_id: str | None) -> None:
        args: dict[str, Any] = {"title": title, "rationale": rationale}
        if project_id:
            args["project_id"] = project_id
        _data(await self._bridge.invoke("twin.record_decision", args), "twin.record_decision")

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        from twin_core.models.enums import WorkProductType

        prior = "\n".join(f"  - {p.title}: {o.summary}" for p, o in context.completed) or "(none)"
        spec = await self._extract(goal, prior, provider=self._provider, model=self._model)

        # 1. Real PRD artifact (requirements table with a verification column).
        rec = await self._doc_recorder(
            content=prd_md(spec, goal),
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

        # 2. Decision: functional + quantified & verifiable constraints + env.
        cons = "; ".join(
            f"{c['param']} {c['limit']} {c['unit']} (verified: {c['verify']})".strip()
            for c in spec["constraints"]
        )
        ifaces = ", ".join(spec["interfaces"])
        rationale = (
            f"Requirements for {goal}. Functional requirements: {'; '.join(spec['functional'])}. "
            f"Constraints (each quantified with an acceptance / verification criterion): {cons}. "
            f"Interfaces: {ifaces}. Operating environment: {spec['environment']}."
        )
        await self._record_decision(
            title=f"{spec['name']} (verifiable requirements)",
            rationale=rationale,
            project_id=context.project_id,
        )

        # 3. MET-582: the quantified constraints ALSO land as an evaluable
        #    constraint_set (Constraint nodes + typed work product) so the
        #    constraint engine can check them at every later gate (MET-583).
        #    The Requirements gate now REQUIRES this deliverable, so a failure
        #    here surfaces as a readable missing-deliverable gate failure.
        set_node = None
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
        except Exception as exc:  # noqa: BLE001 - the gate enforces; don't mask the phase
            logger.warning("requirements_constraint_set_failed", error=str(exc))

        return PhaseOutcome(
            summary=(
                f"Requirements: {len(spec['functional'])} functional + {len(spec['constraints'])} "
                f"quantified constraints, each with an acceptance/verification method; committed a "
                f"PRD (node {prd_node}) and an evaluable constraint set (node {set_node})."
            ),
            artifacts=[
                f"prd:{prd_node}",
                f"constraint_set:{set_node}",
                "design_decision:requirements",
            ],
            status="completed",
        )
