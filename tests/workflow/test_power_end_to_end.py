"""Power design, end to end (FORGE-682).

``electronics_v1`` and ``power_v1`` composed into ``design_v1`` and run on the
real in-process engine. Every power figure comes from the power adapter's own
code, ``check_budget`` and ``battery_runtime``, the way ``power.check_budget``
and ``power.runtime`` compute them. The ERC count and the transient dip
stand in for the KiCad and SPICE runs. Only the phase work around them is
scripted, and every recorded value carries a source.

The board: a 2S Li-ion pack (7.4 V, 2200 mAh) feeds a 3.3 V rail carrying
450 mA. The first selection makes that rail with an LDO in SOT-223
(theta_JA 60 C/W, Tj max 125 C). The rail carries its load, but the LDO
burns (7.4 - 3.3) x 0.45 W and would sit at about 172 C at a 60 C ambient.
It also turns most of the pack into heat, so the runtime misses the 6 h
requirement. The design gate is not ready. The reviewer sends the run back to
the component selection, a buck (90 % efficient, theta_JA 80 C/W) replaces
the LDO, everything that reads the selection re-runs, the regulator sits
near 73 C, the pack lasts about 7.9 h, and the run verifies.
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
from tool_registry.tools.power.budget import BudgetRequest, check_budget
from tool_registry.tools.power.runtime import Figure, RuntimeRequest, battery_runtime

#: The requirements and the battery the person chose.
REQUIREMENTS = {
    "max_ambient_temp_c": 60.0,
    "required_runtime_h": 6.0,
    "max_load_step_dip_mv": 100.0,
    "max_supply_ripple_mv": 50.0,
    "battery_capacity_wh": 7.4 * 2.2,
    "usable_battery_fraction": 0.8,
}
LOAD_MA = 450.0  # MCU, radio and sensors on +3V3, from their datasheets

LDO = {
    "name": "+3V3",
    "voltage_v": 3.3,
    "source_kind": "ldo",
    "input_rail": "VBAT",
    "rated_current_ma": 1000.0,
    "quiescent_ma": 5.0,
    "theta_ja_c_per_w": 60.0,
    "tj_max_c": 125.0,
    "source": "AMS1117-3.3 datasheet (SOT-223)",
}
BUCK = {
    "name": "+3V3",
    "voltage_v": 3.3,
    "source_kind": "switching",
    "input_rail": "VBAT",
    "rated_current_ma": 1000.0,
    "efficiency": 0.9,
    "theta_ja_c_per_w": 80.0,
    "tj_max_c": 125.0,
    "source": "TPS62160 datasheet (efficiency at 450 mA, 7.4 V in)",
}


def budget(regulator: dict[str, Any]) -> dict[str, Any]:
    """The power tree through power.check_budget's own code."""
    request = BudgetRequest.model_validate(
        {
            "rails": [
                {
                    "name": "VBAT",
                    "voltage_v": 7.4,
                    "source_kind": "supply",
                    "rated_current_ma": 5000.0,
                    "source": "2S Li-ion pack, 10C",
                },
                regulator,
            ],
            "loads": [{"name": "MCU+radio+sensors", "rail": "+3V3", "current_ma": LOAD_MA}],
            "derating": 0.8,  # the design rule: no rail above 80 % of its rating
            "ambient_c": REQUIREMENTS["max_ambient_temp_c"],
        }
    )
    return check_budget(request).model_dump(mode="json")


class ValueStore:
    def __init__(self) -> None:
        self.records: list[tuple[str, float, str]] = []

    def record(self, name: str, value: float, source: str) -> None:
        self.records.append((name, float(value), source))

    async def values(self, project_id: str | None) -> dict[str, float]:
        return {n: v for n, v, s in self.records if s}

    async def history(self, project_id: str | None, names: list[str]) -> dict[str, list[float]]:
        return {n: [v for name, v, s in self.records if name == n and s] for n in names}


class PowerBrain:
    def __init__(self, values: ValueStore) -> None:
        self.values = values
        self.calls: list[str] = []
        self.regulator = LDO

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append(phase.id)
        rec = self.values.record
        now = {n: v for n, v, s in self.values.records if s}
        if phase.id == "requirements":
            for name, value in REQUIREMENTS.items():
                rec(name, value, f"REQ-PWR: {name}")
        elif phase.id == "electronics.component_selection":
            # The reviewer's rework note asks for a switching regulator.
            if "buck" in context.retry_feedback.lower():
                self.regulator = BUCK
        elif phase.id == "electronics.power_budget":
            result = budget(self.regulator)
            rail = next(r for r in result["rails"] if r["name"] == "+3V3")
            rec("rail_load_margin_pct", rail["headroom_pct"], "power.check_budget run")
            rec(
                "power_dissipation_w",
                result["regulator_dissipation_mw"] / 1000.0,
                "power.check_budget run",
            )
        elif phase.id == "electronics.schematic_check":
            rec("erc_error_count", 0, "kicad.run_erc report")
        elif phase.id == "electronics.circuit_simulation":
            rec("supply_ripple_mv", 18.0, "spice.run_simulation result")
        elif phase.id == "power.architecture":
            rec("power_rail_count", 1, f"power tree: VBAT -> {self.regulator['source']}")
        elif phase.id == "power.regulator_thermal":
            result = budget(self.regulator)
            rail = next(r for r in result["rails"] if r["name"] == "+3V3")
            assert result["thermal_verdict"] in ("pass", "fail"), result
            rec("regulator_tj_margin_c", rail["tj_margin_c"], "power.check_budget run")
            rec(
                "regulator_dissipation_w",
                result["regulator_dissipation_mw"] / 1000.0,
                "power.check_budget run",
            )
            rec("electronics_power_w", result["source_power_mw"] / 1000.0, "power.check_budget run")
        elif phase.id == "power.battery_runtime":
            run = battery_runtime(
                RuntimeRequest(
                    capacity_wh=Figure(value=now["battery_capacity_wh"], source="pack label"),
                    usable_fraction=Figure(
                        value=now["usable_battery_fraction"], source="cell datasheet"
                    ),
                    average_load_mw=Figure(
                        value=now["electronics_power_w"] * 1000.0, source="power.check_budget"
                    ),
                    required_runtime_h=Figure(
                        value=now["required_runtime_h"], source="REQ-PWR: required_runtime_h"
                    ),
                )
            )
            assert run.runtime_h is not None
            rec("runtime_h", run.runtime_h, "power.runtime run (electronics load only)")
        elif phase.id == "power.load_transient":
            dip = 140.0 if self.regulator is LDO else 62.0
            rec("load_step_dip_mv", dip, "spice.run_simulation, 0 -> 450 mA step")
        return PhaseOutcome(summary=f"{phase.id} recorded", status="completed")


def _flow() -> FlowDefinition:
    fr = load_fragments()
    return compose(get_flow("design_v1"), [fr["electronics_v1"], fr["power_v1"]]).definition


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


def test_the_ldo_carries_its_load_but_not_its_heat() -> None:
    """The adapter's own numbers for the first selection."""
    ldo, buck = budget(LDO), budget(BUCK)
    rail = next(r for r in ldo["rails"] if r["name"] == "+3V3")
    # The rail budget passes: a load problem it is not.
    assert ldo["passed"] and rail["headroom_pct"] > 0
    # (7.4 - 3.3) x 0.45 + 7.4 x 0.005 = 1.882 W; 60 + 1.882 x 60 = 172.9 C.
    assert ldo["regulator_dissipation_mw"] == pytest.approx(1882.0)
    assert rail["junction_c"] == pytest.approx(172.92)
    assert ldo["thermal_verdict"] == "fail"
    buck_rail = next(r for r in buck["rails"] if r["name"] == "+3V3")
    assert buck["thermal_verdict"] == "pass" and buck_rail["tj_margin_c"] > 40
    # The buck also takes about half the power from the pack.
    assert buck["source_power_mw"] < 0.5 * ldo["source_power_mw"]


@pytest.mark.asyncio
async def test_a_hot_ldo_is_replaced_by_a_buck_and_the_run_verifies() -> None:
    flow = _flow()
    values = ValueStore()
    brain = PowerBrain(values)
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    run = store.create({"goal": "battery sensor board", "flow": flow.id, "project_id": "pwr"})
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
            assert phase == "design", (phase, reason)
            assert "regulator_tj_margin_c" in reason and "runtime_h" in reason, reason
            decisions.append((phase, "rework"))
            coord.note_rework(
                run.id,
                "electronics.component_selection",
                "LDO at 173 C and 3.4 h: replace it with a buck",
            )
            store.submit_approval(run.id, ApprovalDecision.REWORK)
        else:
            decisions.append((phase, "approve"))
            store.submit_approval(run.id, ApprovalDecision.APPROVE)
    await asyncio.wait_for(task, timeout=10.0)

    assert ("design", "rework") in decisions
    assert store.get(run.id).status is RunStatus.COMPLETED
    # The regulator change re-ran everything that reads the selection.
    for pid in (
        "electronics.component_selection",
        "electronics.power_budget",
        "power.architecture",
        "power.regulator_thermal",
        "power.battery_runtime",
    ):
        assert brain.calls.count(pid) == 2, (pid, brain.calls)
    history = await values.history(None, ["regulator_tj_margin_c", "runtime_h"])
    assert history["regulator_tj_margin_c"][0] < 0 < history["regulator_tj_margin_c"][-1]
    assert history["runtime_h"][0] < 6.0 < history["runtime_h"][-1]
    current_values = await values.values(None)
    assert current_values["electronics_power_w"] == pytest.approx(1.65, abs=0.01)
    view = lifecycle_view(
        flow.phases,
        _in_process_state(store.get(run.id)),
        requirements=_requirements(flow, current_values),
    )
    assert view.completion.classification is CompletionClass.COMPLETED_VERIFIED, (
        view.completion.as_dict()
    )


def test_every_power_tool_exists_so_nothing_blocks() -> None:
    from orchestrator.design_flow.capabilities import assess_capabilities

    registered = {
        "component.search_parametric",
        "distributors.resolve_offers",
        "power.check_budget",
        "power.runtime",
        "kicad.run_erc",
        "spice.run_simulation",
        "twin.record_engineering_entity",
        "twin.record_decision",
        "twin.record_evidence",
    }
    report = assess_capabilities(_flow().phases, producers={}, registered=registered)
    assert not [g for g in report.gaps if g.capability.startswith("tool:")]
