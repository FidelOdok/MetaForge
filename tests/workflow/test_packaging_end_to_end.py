"""Packaging, end to end (FORGE-672).

``packaging_v1`` composed into ``design_v1`` and run on the real in-process
engine. The compression and dieline numbers come from the real
``packaging.box_compression`` / ``packaging.rsc_dieline`` arithmetic; only the
phase work around them is scripted. Every recorded value carries a source.

The run: a single-wall board is chosen first; the compression estimate for a
10-high stack fails its required safety factor at the V&V gate, the reviewer
sends the run back to material selection, a double-wall board is chosen, and
the run verifies -- with the drop test recorded as evidence from a lab report,
never estimated. Fixture numbers, not design data.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from api_gateway.runs.routes import _in_process_state
from orchestrator.design_flow.checks import CheckOutcome, evaluate_stage
from orchestrator.design_flow.compose import compose
from orchestrator.design_flow.executor import (
    DesignFlowExecutor,
    FlowContext,
    GateCoordinator,
    PhaseOutcome,
)
from orchestrator.design_flow.fragments import load_fragments
from orchestrator.design_flow.lifecycle import CompletionClass, lifecycle_view
from orchestrator.design_flow.spec import FlowDefinition, Phase, get_flow
from orchestrator.harness.runs import ApprovalDecision, InMemoryRunStore, RunStatus
from tool_registry.tools.packaging.carton import (
    CompressionRequest,
    DielineRequest,
    box_compression,
    rsc_dieline,
)

INPUTS = {
    # The product (stated in the intent)
    "inner_length_m": 0.40,
    "inner_width_m": 0.30,
    "inner_depth_m": 0.25,
    "gross_mass_kg": 8.0,
    # The requirements (the shipping test and warehouse the person named)
    "stack_count": 10,
    "required_stacking_safety_factor": 3.0,
    "humidity_derating": 0.6,
    "storage_derating": 0.7,
    "drop_height_m": 0.76,
    "drops_required": 10,
    # The converter's rules
    "score_allowance_m": 0.004,
    "glue_flap_m": 0.035,
    "min_glue_flap_m": 0.03,
    "min_panel_m": 0.05,
    "max_sheet_length_m": 2.2,
    "max_sheet_width_m": 1.4,
}
#: (ECT N/m, caliper m) from the mill's datasheet.
SINGLE_WALL = (7000.0, 0.004)
DOUBLE_WALL = (12000.0, 0.0065)


class ValueStore:
    def __init__(self) -> None:
        self.records: list[tuple[str, float, str]] = []

    def record(self, name: str, value: float, source: str) -> None:
        self.records.append((name, float(value), source))

    async def values(self, project_id: str | None) -> dict[str, float]:
        return {n: v for n, v, s in self.records if s}

    async def history(self, project_id: str | None, names: list[str]) -> dict[str, list[float]]:
        return {n: [v for name, v, s in self.records if name == n and s] for n in names}


def fig(value: float, source: str) -> dict[str, Any]:
    return {"value": value, "source": source}


class PackagingBrain:
    def __init__(self, values: ValueStore) -> None:
        self.values = values
        self.calls: list[str] = []

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append(phase.id)
        rec = self.values.record
        now = {n: v for n, v, s in self.values.records if s}
        if phase.id == "requirements":
            for name, value in INPUTS.items():
                rec(name, value, f"stated: {name}")
        elif phase.id == "packaging.material_selection":
            ect, caliper = (
                DOUBLE_WALL if context.retry_feedback.startswith("REWORK") else SINGLE_WALL
            )
            rec("board_ect_n_per_m", ect, "mill datasheet")
            rec("board_caliper_m", caliper, "mill datasheet")
        elif phase.id in ("packaging.dieline", "packaging.manufacturing_check"):
            result = rsc_dieline(
                DielineRequest.model_validate(
                    {
                        "inner_length_m": fig(now["inner_length_m"], "product"),
                        "inner_width_m": fig(now["inner_width_m"], "product"),
                        "inner_depth_m": fig(now["inner_depth_m"], "product"),
                        "caliper_m": fig(now["board_caliper_m"], "mill datasheet"),
                        **{
                            k: fig(now[k], "converter's rules")
                            for k in (
                                "score_allowance_m",
                                "glue_flap_m",
                                "min_glue_flap_m",
                                "min_panel_m",
                                "max_sheet_length_m",
                                "max_sheet_width_m",
                            )
                        },
                    }
                )
            )
            if phase.id == "packaging.dieline":
                assert result.blank_length_m and result.blank_width_m
                rec("blank_length_m", result.blank_length_m, "packaging.rsc_dieline run")
                rec("blank_width_m", result.blank_width_m, "packaging.rsc_dieline run")
            else:
                rec("dieline_rule_violations", len(result.violations), "packaging.rsc_dieline run")
        elif phase.id == "packaging.compression":
            result = box_compression(
                CompressionRequest.model_validate(
                    {
                        "ect_n_per_m": fig(now["board_ect_n_per_m"], "mill datasheet"),
                        "caliper_m": fig(now["board_caliper_m"], "mill datasheet"),
                        "inner_length_m": fig(now["inner_length_m"], "product"),
                        "inner_width_m": fig(now["inner_width_m"], "product"),
                        "gross_mass_kg": fig(now["gross_mass_kg"], "product"),
                        "stack_count": int(now["stack_count"]),
                        "required_safety_factor": fig(
                            now["required_stacking_safety_factor"], "REQ-STACK"
                        ),
                        "deratings": [
                            {"name": "humidity", "factor": fig(now["humidity_derating"], "REQ")},
                            {"name": "storage", "factor": fig(now["storage_derating"], "REQ")},
                        ],
                    }
                )
            )
            assert result.compression_margin is not None
            rec("compression_margin", result.compression_margin, "packaging.box_compression run")
        elif phase.id == "packaging.drop_test":
            rec("drop_test_failures", 0, "lab report DT-0042, 10 drops from 0.76 m")
        return PhaseOutcome(summary=f"{phase.id} recorded", status="completed")


def _flow() -> FlowDefinition:
    return compose(get_flow("design_v1"), [load_fragments()["packaging_v1"]]).definition


def _requirements(flow: FlowDefinition, values: dict[str, float]) -> list[dict[str, str]]:
    rows = []
    for stage in ("design", "simulation"):
        for result in evaluate_stage(stage, flow.phases, values).results:
            status = {
                CheckOutcome.PASS: "pass",
                CheckOutcome.FAIL: "fail",
                CheckOutcome.NO_DATA: "no_data",
            }[result.outcome]
            rows.append({"id": f"{result.task}:{result.check}", "status": status})
    return rows


@pytest.mark.asyncio
async def test_a_carton_too_weak_for_its_stack_is_reworked_and_verifies() -> None:
    flow = _flow()
    values = ValueStore()
    brain = PackagingBrain(values)
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    run = store.create({"goal": "a shipping carton", "flow": flow.id, "project_id": "box"})
    executor = DesignFlowExecutor(store=store, brain=brain, coordinator=coord, value_reader=values)
    task = asyncio.create_task(executor.run(run.id, flow))
    seen, decisions = 0, []
    while True:
        for _ in range(1600):
            current = store.get(run.id)
            if current.is_terminal or (
                current.status is RunStatus.AWAITING_APPROVAL
                and current.history.count(current.status) > seen
            ):
                break
            await asyncio.sleep(0.005)
        current = store.get(run.id)
        if current.is_terminal:
            break
        seen += 1
        phase = str((coord.gate_state(run.id) or {}).get("phase"))
        reason = current.approval_reason or ""
        if "NOT READY" in reason:
            assert phase == "simulation" and "compression_margin" in reason, reason
            decisions.append((phase, "rework"))
            coord.note_rework(
                run.id, "packaging.material_selection", "stack of 10 fails: stronger board"
            )
            store.submit_approval(run.id, ApprovalDecision.REWORK)
        else:
            decisions.append((phase, "approve"))
            store.submit_approval(run.id, ApprovalDecision.APPROVE)
    await asyncio.wait_for(task, timeout=10.0)

    assert ("simulation", "rework") in decisions
    assert store.get(run.id).status is RunStatus.COMPLETED
    # The rework followed the data: the board change re-ran the dieline,
    # manufacturing check and compression; nothing upstream of the board.
    assert brain.calls.count("packaging.material_selection") == 2
    assert brain.calls.count("packaging.compression") == 2
    assert brain.calls.count("requirements") == 1
    current = await values.values(None)
    assert current["board_ect_n_per_m"] == DOUBLE_WALL[0]
    assert current["compression_margin"] >= 0
    view = lifecycle_view(
        flow.phases,
        _in_process_state(store.get(run.id)),
        requirements=_requirements(flow, current),
    )
    assert view.completion.classification is CompletionClass.COMPLETED_VERIFIED, (
        view.completion.as_dict()
    )


def test_every_packaging_tool_exists_so_nothing_blocks() -> None:
    from orchestrator.design_flow.capabilities import assess_capabilities

    registered = {
        "library.get_material",
        "library.process_limits",
        "twin.record_engineering_entity",
        "twin.record_decision",
        "twin.record_evidence",
        "packaging.box_compression",
        "packaging.rsc_dieline",
    }
    report = assess_capabilities(_flow().phases, producers={}, registered=registered)
    assert not [g for g in report.gaps if g.capability.startswith("tool:")]
