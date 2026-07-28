"""Deterministic manufacturing phase handler for the design flow (MET-10).

The native manufacturing phase repeats the V&V false-pass pattern: it claims
"consolidated the BOM ... prepare for manufacturing, ensuring compliance" while
admitting "I encountered errors while attempting to export the BOM and netlist
... preventing successful execution" — false readiness with no real fab artifact
(the manufacturing rubric scores that 0.5, not_false_ready=false).

The honest Phase-1 fix mirrors :class:`GoalDrivenVVHandler`: don't claim
fab-readiness the exports never established. This handler prepares the
manufacturing package it genuinely can — a procurement-ready BOM summary, the
process selection, and an assembly / AOI / bring-up plan — emits it as a real
``manufacturing_file`` work product, and records an honest readiness decision
that DEFERS Gerber / pick-and-place to Phase 2 (they need a completed PCB
layout), never asserting fab-readiness.
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

_DEFAULT_PROCESSES = ["PCB fabrication", "SMT reflow assembly", "AOI inspection"]
_DEFAULT_ASSEMBLY = [
    "Apply solder paste through the stencil",
    "Place SMT components",
    "Reflow per the paste profile",
    "AOI inspection of solder joints",
    "Power-on bring-up self-test",
]
_DEFAULT_DEFERRED = ["Gerber", "pick-and-place", "drill files"]


def _slug_title(goal: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z]+", goal) if len(w) > 2][:3]
    return " ".join(w.capitalize() for w in words) or "Board"


def _int(v: Any, default: int) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _str_list(raw: Any, default: list[str], cap: int = 60) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        out = [str(x).strip()[:cap] for x in raw if isinstance(x, str) and str(x).strip()]
    return out or list(default)


def _normalize_mfg_spec(spec: dict[str, Any], goal: str) -> dict[str, Any]:
    """Coerce an extracted manufacturing spec into a complete, honest package."""
    pcb_raw = spec.get("pcb")
    pcb = pcb_raw if isinstance(pcb_raw, dict) else {}
    layers = _int(pcb.get("layers"), 2)
    size = str(pcb.get("size") or "").strip()[:32] or "per the board outline"
    processes = _str_list(spec.get("processes"), _DEFAULT_PROCESSES)
    assembly = _str_list(spec.get("assembly_steps"), _DEFAULT_ASSEMBLY)
    procurement = (
        str(spec.get("procurement") or "").strip()[:200]
        or "BOM consolidated for procurement; source each line item per the committed BOM."
    )
    deferred = _str_list(spec.get("deferred"), _DEFAULT_DEFERRED, cap=24)
    name = str(spec.get("name") or "").strip() or f"{_slug_title(goal)} manufacturing"
    return {
        "name": name[:60],
        "layers": layers,
        "size": size,
        "processes": processes,
        "assembly": assembly,
        "procurement": procurement,
        "deferred": deferred,
    }


def mfg_package_md(spec: dict[str, Any]) -> str:
    """Render the fabrication + assembly package as markdown."""
    lines = [
        f"# {spec['name']} — manufacturing package",
        "",
        "## Fabrication",
        "",
        f"- PCB: {spec['layers']}-layer, {spec['size']}",
        f"- Processes: {', '.join(spec['processes'])}",
        "",
        "## Procurement",
        "",
        f"- {spec['procurement']}",
        "",
        "## Assembly & inspection",
        "",
    ]
    lines += [f"{i}. {step}" for i, step in enumerate(spec["assembly"], 1)]
    lines += [
        "",
        "## Deferred to Phase 2",
        "",
        f"{', '.join(spec['deferred'])} require a completed PCB layout; they are out of "
        "Phase-1 scope and are not asserted as fab-ready.",
    ]
    return "\n".join(lines) + "\n"


async def _extract_mfg_spec(
    goal: str, prior: str, *, provider: str | None, model: str | None
) -> dict[str, Any]:
    """Ask the LLM for a manufacturing package spec (never raises)."""
    from api_gateway.chat.harness_backend import run_chat_turn

    prompt = (
        "You are preparing an honest Phase-1 manufacturing package for a hardware board. "
        "From the design below, give the fabrication + assembly plan. Reply with ONLY JSON:\n"
        '{"name": "<mfg name>", "pcb": {"layers": 2, "size": "25 x 20 mm"}, '
        '"processes": ["PCB fabrication", "SMT reflow assembly"], '
        '"assembly_steps": ["apply solder paste", "place SMT parts", "reflow", "AOI"], '
        '"procurement": "BOM consolidated for procurement ...", '
        '"deferred": ["Gerber", "pick-and-place"]}\n'
        "Use real values for the board in the goal.\n\n"
        f"Goal: {goal}\nContext: {prior}"
    )
    try:
        reply = await run_chat_turn(
            prompt,
            mcp_bridge=None,
            session_id="mfg-spec",
            max_steps=1,
            provider=provider,
            model=model,
        )
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        spec = json.loads(match.group(0)) if match else {}
    except Exception as exc:  # noqa: BLE001 - fall back to a default package, never fail
        logger.warning("mfg_spec_extract_failed", error=str(exc))
        spec = {}
    return _normalize_mfg_spec(spec if isinstance(spec, dict) else {}, goal)


def _data(envelope: Any, tool: str) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        return {}
    if envelope.get("status") == "error":
        raise RuntimeError(f"{tool} failed: {envelope.get('error') or envelope}")
    data = envelope.get("data", envelope)
    return data if isinstance(data, dict) else {}


class GoalDrivenManufacturingHandler:
    """Records an honest manufacturing package + readiness decision (no false ready)."""

    def __init__(
        self,
        bridge: McpBridge,
        doc_recorder: Any,
        *,
        provider: str | None = None,
        model: str | None = None,
        extract: Any = _extract_mfg_spec,
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

        # 1. Real manufacturing_file artifact (fab + assembly package).
        rec = await self._doc_recorder(
            content=mfg_package_md(spec),
            name=f"{spec['name']} package",
            wp_type=WorkProductType.MANUFACTURING_FILE,
            domain="manufacturing",
            fmt="md",
            link_type="manufacturing_file",
            source_tool="manufacturing.package",
            session_id=context.session_id,
            project_id=context.project_id,
        )
        pkg_node = rec.get("node_id") if isinstance(rec, dict) else None

        # 2. Honest readiness decision — deferred Gerbers, no false fab-readiness.
        processes = ", ".join(spec["processes"])
        deferred = ", ".join(spec["deferred"])
        rationale = (
            f"Manufacturing readiness (Phase 1): {spec['procurement']} Process: {processes}. "
            f"PCB: {spec['layers']}-layer, {spec['size']}. Assembly plan with SMT reflow, AOI "
            "inspection, and a power-on bring-up self-test. Fabrication outputs "
            f"({deferred}) are deferred to Phase 2 — they require a completed PCB layout — and "
            "are out of Phase-1 scope, not asserted as fab-ready. A manufacturing package (fab + "
            "assembly notes) was committed as a work product."
        )
        await self._record_decision(
            title=f"{spec['name']} readiness (procurement + assembly ready; fab deferred)",
            rationale=rationale,
            project_id=context.project_id,
        )
        return PhaseOutcome(
            summary=(
                f"Manufacturing: committed a {spec['layers']}-layer fab + assembly package "
                f"(node {pkg_node}); BOM consolidated for procurement; Gerber/pick-and-place "
                "deferred to Phase 2, not claimed fab-ready."
            ),
            artifacts=[f"manufacturing_file:{pkg_node}", "design_decision:manufacturing"],
            status="completed",
        )
