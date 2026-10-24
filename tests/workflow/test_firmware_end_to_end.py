"""Firmware, end to end (FORGE-676).

``firmware_v1`` composed into ``design_v1`` and run on the real in-process
engine. The pin map and the RTOS configuration are the real generators'
output (``generate_hal_files``, ``generate_rtos_files``); the build, the static
check and the boot are what the firmware adapter's real tools printed in its
image for that same generated FreeRTOS app on an STM32F4-Discovery, recorded
in ``tests/unit/fixtures/firmware/`` and read back through the adapter's own
parsers. Only the phase work around them is scripted. Every recorded value
carries a source.

The run: the first configuration asks for a 160 KB heap. The F407 has 128 KB
of RAM, so the link fails (``region `RAM' overflowed by 34592 bytes``) and the
V&V gate is not ready. The reviewer sends the run back to the RTOS
configuration, a 16 KB heap is chosen, the build fits at 12.7 % of RAM, the
static check has no high finding, both tasks print their start line on
Renode, and the run verifies.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from api_gateway.runs.routes import _in_process_state
from domain_agents.firmware.codegen import (
    CodegenError,
    RtosTask,
    generate_hal_files,
    generate_rtos_files,
)
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
from tool_registry.tools.firmware.adapter import (
    check_uart,
    parse_check_report,
    parse_diagnostics,
    parse_size,
)

FIXTURES = Path(__file__).resolve().parents[1] / "unit" / "fixtures" / "firmware"

#: The requirements: leave room to grow on the part.
LIMITS = {"max_flash_pct": 50.0, "max_ram_pct": 75.0}
#: The schematic's MCU nets (what kicad.get_pin_mapping returns, as rows).
PIN_MAP = [
    {"signal": "STATUS_LED", "pin": "PD12", "direction": "output"},
    {"signal": "DEBUG_TX", "pin": "PA2", "peripheral": "USART2", "function": "TX"},
]
TASKS = [
    RtosTask(name="sensor", priority=3, stack_size=1024),
    RtosTask(name="telemetry", priority=2, stack_size=1024),
]
#: Heap the person asked for first, and after the review.
FIRST_HEAP_KB, REWORKED_HEAP_KB = 160, 16


class ValueStore:
    def __init__(self) -> None:
        self.records: list[tuple[str, float, str]] = []

    def record(self, name: str, value: float, source: str) -> None:
        self.records.append((name, float(value), source))

    async def values(self, project_id: str | None) -> dict[str, float]:
        return {n: v for n, v, s in self.records if s}

    async def history(self, project_id: str | None, names: list[str]) -> dict[str, list[float]]:
        return {n: [v for name, v, s in self.records if name == n and s] for n in names}


class FirmwareBrain:
    def __init__(self, values: ValueStore) -> None:
        self.values = values
        self.calls: list[str] = []

    async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext) -> PhaseOutcome:
        self.calls.append(phase.id)
        rec = self.values.record
        now = {n: v for n, v, s in self.values.records if s}
        if phase.id == "requirements":
            for name, value in LIMITS.items():
                rec(name, value, f"REQ-FW: {name}")
        elif phase.id == "firmware.pin_map":
            try:
                generate_hal_files("STM32F4", PIN_MAP, "src/board", "schematic rev B")
                conflicts = 0
            except CodegenError:
                conflicts = 1
            rec("pin_conflict_count", conflicts, "kicad.get_pin_mapping, schematic rev B")
        elif phase.id == "firmware.rtos_configuration":
            heap = (
                REWORKED_HEAP_KB if context.retry_feedback.startswith("REWORK") else FIRST_HEAP_KB
            )
            _, ram = generate_rtos_files("FreeRTOS", TASKS, heap, 1000, "src/rtos", 4)
            rec("rtos_ram_estimate_bytes", ram, f"configure_rtos, heap {heap} KB")
        elif phase.id == "firmware.build":
            # The recorded firmware.build output for this configuration.
            overflow = now["rtos_ram_estimate_bytes"] > 128 * 1024
            log = (
                FIXTURES / ("stm32_build_ram_overflow.log" if overflow else "stm32_build_ok.log")
            ).read_text()
            errors = [d for d in parse_diagnostics(log) if d["severity"] == "error"]
            rec("build_error_count", len(errors), "firmware.build run")
            size = parse_size(log)
            if size["flash"] and size["ram"]:
                rec("flash_used_pct", size["flash"]["used_pct"], "firmware.build run")
                rec("ram_used_pct", size["ram"]["used_pct"], "firmware.build run")
        elif phase.id == "firmware.static_analysis":
            report = parse_check_report((FIXTURES / "stm32_freertos_check.log").read_text())
            assert report is not None
            high = sum(1 for f in report["findings"] if f["severity"] == "high")
            rec("static_high_findings", high, "firmware.static_check run (cppcheck)")
        elif phase.id == "firmware.emulated_boot":
            # Nothing to boot when the build did not link: leave it unrecorded.
            if now.get("build_error_count", 1) == 0:
                uart = (FIXTURES / "stm32_freertos_uart.txt").read_text()
                results, _ = check_uart(
                    uart,
                    ["MetaForge boot", "task sensor started", "task telemetry started"],
                    ["HardFault", "task create failed"],
                )
                failed = sum(1 for r in results if r["status"] != "pass")
                rec("boot_checks_failed", failed, "firmware.run_emulated on Renode 1.17.0")
        return PhaseOutcome(summary=f"{phase.id} recorded", status="completed")


def _flow() -> FlowDefinition:
    return compose(get_flow("design_v1"), [load_fragments()["firmware_v1"]]).definition


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
async def test_a_heap_too_big_for_the_part_is_reworked_and_verifies() -> None:
    flow = _flow()
    values = ValueStore()
    brain = FirmwareBrain(values)
    coord = GateCoordinator()
    store = InMemoryRunStore(on_transition=coord.on_transition)
    run = store.create({"goal": "flight controller firmware", "flow": flow.id, "project_id": "fc"})
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
            assert phase == "simulation" and "build_error_count" in reason, reason
            decisions.append((phase, "rework"))
            coord.note_rework(
                run.id, "firmware.rtos_configuration", "RAM overflowed by 34592 B: smaller heap"
            )
            store.submit_approval(run.id, ApprovalDecision.REWORK)
        else:
            decisions.append((phase, "approve"))
            store.submit_approval(run.id, ApprovalDecision.APPROVE)
    await asyncio.wait_for(task, timeout=10.0)

    assert ("simulation", "rework") in decisions
    assert store.get(run.id).status is RunStatus.COMPLETED
    # The rework followed the data: the configuration change re-ran the
    # build and everything that reads it; the pin map was not touched.
    assert brain.calls.count("firmware.rtos_configuration") == 2
    assert brain.calls.count("firmware.build") == 2
    assert brain.calls.count("firmware.emulated_boot") == 2
    assert brain.calls.count("firmware.pin_map") == 1
    current = await values.values(None)
    assert current["build_error_count"] == 0
    assert current["ram_used_pct"] == pytest.approx(12.72)
    assert current["rtos_ram_estimate_bytes"] == 18432
    assert current["boot_checks_failed"] == 0
    view = lifecycle_view(
        flow.phases,
        _in_process_state(store.get(run.id)),
        requirements=_requirements(flow, current),
    )
    assert view.completion.classification is CompletionClass.COMPLETED_VERIFIED, (
        view.completion.as_dict()
    )


def test_the_recorded_overflow_is_what_fails_the_gate() -> None:
    """The first build's numbers, through the fragment's own checks."""
    log = (FIXTURES / "stm32_build_ram_overflow.log").read_text()
    errors = [d["message"] for d in parse_diagnostics(log) if d["severity"] == "error"]
    assert "region `RAM' overflowed by 34592 bytes" in errors
    assert parse_size(log) == {"flash": None, "ram": None}
    results = {
        r.check: r.outcome
        for r in evaluate_stage(
            "simulation", _flow().phases, {"build_error_count": 1.0, **LIMITS}
        ).results
        if r.task == "firmware.build"
    }
    assert results["build_error_count"] is CheckOutcome.FAIL
    # No size from a build that did not link: no data, which never passes.
    assert results["ram_used_pct"] is CheckOutcome.NO_DATA


def test_every_firmware_tool_exists_so_nothing_blocks() -> None:
    from orchestrator.design_flow.capabilities import assess_capabilities

    registered = {
        "kicad.get_pin_mapping",
        "firmware.build",
        "firmware.static_check",
        "firmware.run_emulated",
        "twin.record_engineering_entity",
        "twin.record_decision",
        "twin.record_evidence",
    }
    report = assess_capabilities(_flow().phases, producers={}, registered=registered)
    assert not [g for g in report.gaps if g.capability.startswith("tool:")]
