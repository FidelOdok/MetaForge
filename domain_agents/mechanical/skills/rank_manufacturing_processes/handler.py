"""Handler for the rank_manufacturing_processes skill (FORGE-611).

Ranks candidate processes for a part before it is designed, so the chosen
process can constrain the design rather than be bolted on afterwards. Pure
arithmetic on the values given: a capability that was not sourced is never
assumed, it makes its criterion ``unknown``. The output is a ranking and a
recommendation; the person picks at the route gate.
"""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import (
    CandidateProcess,
    CriterionResult,
    RankedProcess,
    RankManufacturingProcessesInput,
    RankManufacturingProcessesOutput,
)

_VERDICT_ORDER = {"fits": 0, "incomplete": 1, "does_not_fit": 2}


def _envelope(part: list[float] | None, limit: list[float] | None) -> CriterionResult:
    if part is None or limit is None:
        missing = "part envelope" if part is None else "process max envelope"
        return CriterionResult(name="envelope", status="unknown", detail=f"{missing} not given")
    # Orientation is free: compare sorted dimensions.
    fits = all(p <= m for p, m in zip(sorted(part), sorted(limit), strict=True))
    detail = f"part {_dims(part)} mm vs max {_dims(limit)} mm"
    return CriterionResult(name="envelope", status="pass" if fits else "fail", detail=detail)


def _dims(values: list[float]) -> str:
    return " x ".join(f"{v:g}" for v in values)


def _at_least(name: str, need: float | None, can: float | None, unit: str) -> CriterionResult:
    """The part needs ``need``; the process manages ``can`` or better (smaller)."""
    if need is None or can is None:
        missing = "part requirement" if need is None else "process capability"
        return CriterionResult(name=name, status="unknown", detail=f"{missing} not given")
    ok = can <= need
    return CriterionResult(
        name=name,
        status="pass" if ok else "fail",
        detail=f"part needs {need:g} {unit}, process manages {can:g} {unit}",
    )


def _material(material: str | None, handled: list[str]) -> CriterionResult:
    if not material or not handled:
        missing = "part material class" if not material else "materials the process handles"
        return CriterionResult(name="material", status="unknown", detail=f"{missing} not given")
    ok = material.strip().lower() in {m.strip().lower() for m in handled}
    return CriterionResult(
        name="material",
        status="pass" if ok else "fail",
        detail=f"{material} {'is' if ok else 'is not'} among {', '.join(handled)}",
    )


def _availability(candidate: CandidateProcess) -> CriterionResult:
    if candidate.available is None:
        return CriterionResult(
            name="availability", status="unknown", detail="whether it is at hand was not stated"
        )
    where = candidate.route or "the route"
    return CriterionResult(
        name="availability",
        status="pass" if candidate.available else "fail",
        detail=f"{'available' if candidate.available else 'not available'} via {where}",
    )


def _rank_one(data: RankManufacturingProcessesInput, candidate: CandidateProcess) -> RankedProcess:
    criteria = [
        _envelope(data.part_envelope_mm, candidate.max_envelope_mm),
        _at_least("min_feature", data.min_feature_mm, candidate.min_feature_mm, "mm"),
        _at_least("tolerance", data.tightest_tolerance_mm, candidate.achievable_tolerance_mm, "mm"),
        _material(data.material_class, candidate.materials),
        _availability(candidate),
    ]
    cost: float | None = None
    per_part: float | None = None
    if (
        data.quantity is not None
        and candidate.setup_cost is not None
        and candidate.unit_cost is not None
    ):
        cost = candidate.setup_cost + candidate.unit_cost * data.quantity
        per_part = cost / data.quantity
    failed = [c for c in criteria if c.status == "fail"]
    unknown = [c for c in criteria if c.status == "unknown"]
    verdict = "does_not_fit" if failed else "incomplete" if unknown else "fits"
    reasons = [f"{c.name}: {c.detail}" for c in failed]
    if cost is not None:
        unit = f" {data.currency}" if data.currency else ""
        reasons.append(
            f"cost at {data.quantity} parts: {cost:g}{unit} ({per_part:g}{unit} per part, "
            "setup amortised)"
        )
    else:
        reasons.append("cost at quantity unknown (quantity, setup or unit cost not given)")
    reasons += [f"{c.name}: unknown ({c.detail})" for c in unknown]
    return RankedProcess(
        process=candidate.process,
        route=candidate.route,
        verdict=verdict,
        criteria=criteria,
        cost_at_quantity=cost,
        cost_per_part=per_part,
        reasons=reasons,
        source_refs=list(candidate.source_refs),
    )


class RankManufacturingProcessesHandler(
    SkillBase[RankManufacturingProcessesInput, RankManufacturingProcessesOutput]
):
    """Ranks candidate manufacturing processes for a part. No MCP tools."""

    input_type = RankManufacturingProcessesInput
    output_type = RankManufacturingProcessesOutput

    async def execute(
        self, input_data: RankManufacturingProcessesInput
    ) -> RankManufacturingProcessesOutput:
        self.logger.info(
            "Ranking manufacturing processes",
            candidates=len(input_data.candidates),
            quantity=input_data.quantity,
        )
        ranked = [_rank_one(input_data, c) for c in input_data.candidates]
        ranked.sort(
            key=lambda r: (
                _VERDICT_ORDER[r.verdict],
                r.cost_at_quantity is None,
                r.cost_at_quantity or 0.0,
                r.process,
            )
        )
        recommended = next((r.process for r in ranked if r.verdict != "does_not_fit"), None)
        unknown_inputs = [
            name
            for name, value in (
                ("quantity", input_data.quantity),
                ("material_class", input_data.material_class),
                ("part_envelope_mm", input_data.part_envelope_mm),
                ("min_feature_mm", input_data.min_feature_mm),
                ("tightest_tolerance_mm", input_data.tightest_tolerance_mm),
            )
            if value is None
        ]
        return RankManufacturingProcessesOutput(
            ranked=ranked, recommended=recommended, unknown_inputs=unknown_inputs
        )
