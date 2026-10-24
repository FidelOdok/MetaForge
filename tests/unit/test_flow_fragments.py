"""Domain fragments: format and registry (composable flows, story 3).

Every rule has a negative control: a fragment that breaks it, asserting the
rule comes back by name. A validator that passes everything makes a fragment
look checked when it is not.
"""

from __future__ import annotations

import copy

import pytest

from orchestrator.design_flow.fragments import (
    FRAGMENT_DIR,
    FragmentError,
    fragment_from_mapping,
    load_fragment,
    load_fragments,
    task_phase_id,
)


def _raw(**over: object) -> dict:
    raw: dict = {
        "id": "demo_v1",
        "version": "1.0.0",
        "domain": "demo",
        "disciplines": ["simulation"],
        "keywords": ["demo"],
        "tasks": [
            {
                "id": "size",
                "stage": "design",
                "objective": "Size it.",
                "inputs": ["design_value:load_n"],
                "outputs": ["design_value:size_mm"],
                "checks": ["size_mm > 0 mm"],
                "capabilities": ["twin.record_engineering_entity"],
            },
            {
                "id": "check",
                "stage": "simulation",
                "objective": "Check it.",
                "inputs": ["design_value:size_mm"],
                "outputs": ["design_value:stress_mpa"],
                "records": ["simulation_result"],
                "checks": ["stress_mpa <= allowable_stress_mpa"],
            },
        ],
    }
    raw.update(over)
    return raw


def _with_task(change: dict, index: int = 0) -> dict:
    raw = _raw()
    raw["tasks"][index] = {**raw["tasks"][index], **change}
    return raw


# -- the shipped fragments --------------------------------------------------


def test_the_registry_is_what_is_on_disk() -> None:
    assert sorted(load_fragments()) == sorted(p.stem for p in FRAGMENT_DIR.glob("*.yaml"))
    assert {
        "thermal_v1",
        "electronics_v1",
        "mobile_robot_v1",
        "packaging_v1",
        "aircraft_v1",
        "firmware_v1",
    } <= set(load_fragments())


@pytest.mark.parametrize(
    "fragment_id",
    [
        "thermal_v1",
        "electronics_v1",
        "mobile_robot_v1",
        "packaging_v1",
        "aircraft_v1",
        "firmware_v1",
    ],
)
def test_every_shipped_fragment_declares_a_version_and_valid_tasks(fragment_id: str) -> None:
    fragment = load_fragment(fragment_id)
    assert fragment.version
    for task in fragment.tasks:
        assert task.is_task and task.fragment == fragment_id
        assert task.id.startswith(f"{fragment.domain}.")
        assert task.slots, f"{task.id} writes nothing"
        assert task.gate is None, "a task never carries its own gate"


def test_thermal_limits_are_inputs_not_numbers() -> None:
    # The allowed junction temperature is a sourced value, never written in.
    heat_transfer = load_fragment("thermal_v1").task("heat_transfer")
    (check,) = heat_transfer.checks
    assert check.limit == "max_allowed_junction_temp_c"


def test_task_outputs_become_keyed_slots_and_records_become_deliverables() -> None:
    fragment = fragment_from_mapping(_raw())
    check = fragment.task("check")
    assert [(s.item_type, s.name) for s in check.slots] == [("design_value", "stress_mpa")]
    assert check.slots[0].item_key.startswith("VAL-")
    assert check.required_deliverables == ("design_value", "simulation_result")
    assert check.disciplines == ("simulation",)
    assert fragment.outputs()[check.slots[0].item_key] == task_phase_id("demo", "check")


def test_as_dict_lists_the_contract() -> None:
    data = fragment_from_mapping(_raw()).as_dict()
    assert data["tasks"][1]["checks"] == ["stress_mpa <= allowable_stress_mpa"]
    assert data["tasks"][0]["outputs"] == ["design_value:size_mm"]


# -- refusals ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        (_raw(version=""), "no 'version'"),
        (_raw(domain="a.b"), "'domain' without dots"),
        (_raw(tasks=[]), "declares no tasks"),
        (_with_task({"id": "a.b"}), "without dots"),
        (_with_task({"outputs": []}), "at least one output"),
        (_with_task({"objective": ""}), "needs an 'objective'"),
        (_with_task({"stage": "nowhere"}), "is not a spine_v1 stage"),
        (_with_task({"inputs": ["load_n"]}), "not a 'type:name' item reference"),
        (_with_task({"outputs": ["design_decision:x"]}), "not a versioned definition type"),
        (_with_task({"records": ["no_such_record"]}), "not a twin type"),
        (_with_task({"checks": ["size_mm ~ 3"]}), "cannot read check"),
        (_with_task({"checks": ["other_value > 0"]}), "which the task does not write"),
    ],
)
def test_a_broken_fragment_is_refused_by_rule(raw: dict, match: str) -> None:
    with pytest.raises(FragmentError, match=match):
        fragment_from_mapping(raw)


def test_two_tasks_writing_one_item_are_refused() -> None:
    raw = _with_task({"outputs": ["design_value:size_mm"]}, index=1)
    raw["tasks"][1]["checks"] = []
    with pytest.raises(FragmentError, match="written by both"):
        fragment_from_mapping(raw)


def test_duplicate_task_ids_are_refused() -> None:
    raw = _raw()
    raw["tasks"].append(copy.deepcopy(raw["tasks"][0]) | {"outputs": ["design_value:other"]})
    raw["tasks"][-1]["checks"] = []
    with pytest.raises(FragmentError, match="duplicate task id"):
        fragment_from_mapping(raw)


def _loop(**block: object) -> dict:
    raw = _raw()
    raw["tasks"] = [
        {
            "id": "battery",
            "stage": "design",
            "objective": "Size the battery.",
            "inputs": ["design_value:hover_power_w"],
            "outputs": ["design_value:battery_capacity_wh"],
        },
        {
            "id": "mass",
            "stage": "design",
            "objective": "Roll up mass.",
            "inputs": ["design_value:battery_capacity_wh"],
            "outputs": ["design_value:total_mass_kg"],
        },
        {
            "id": "power",
            "stage": "design",
            "objective": "Estimate hover power.",
            "inputs": ["design_value:total_mass_kg"],
            "outputs": ["design_value:hover_power_w"],
        },
    ]
    if block is not None:
        raw["blocks"] = [
            {
                "id": "energy",
                "stage": "design",
                "members": ["battery", "mass", "power"],
                "solves_for": ["battery_capacity_wh", "total_mass_kg", "hover_power_w"],
                **block,
            }
        ]
    return raw


def test_a_cycle_outside_a_block_is_refused_and_named() -> None:
    raw = _loop()
    raw.pop("blocks")
    with pytest.raises(FragmentError, match="dependency cycle .* put mutually dependent"):
        fragment_from_mapping(raw)


def test_a_cycle_inside_a_block_is_allowed() -> None:
    fragment = fragment_from_mapping(_loop())
    assert fragment.blocks[0].members == ("battery", "mass", "power")


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ({"members": ["battery"]}, "2\\+ members"),
        ({"members": ["battery", "nope"]}, "unknown member"),
        ({"solves_for": ["wingspan_m"]}, "not written: wingspan_m"),
        ({"solves_for": []}, "solves_for must name"),
        ({"tolerance": 0}, "tolerance must be in"),
        ({"max_iterations": 1}, "max_iterations must be"),
        ({"stage": "simulation"}, "members must share its stage"),
    ],
)
def test_a_broken_block_is_refused(block: dict, match: str) -> None:
    with pytest.raises(FragmentError, match=match):
        fragment_from_mapping(_loop(**block))


def test_the_mobile_robot_energy_loop_is_a_block() -> None:
    # FORGE-671: mass, drive power and battery capacity depend on each other.
    fragment = load_fragment("mobile_robot_v1")
    (block,) = fragment.blocks
    assert block.members == ("mass_rollup", "drive_sizing", "battery_sizing")
    assert set(block.solves_for) == {"total_mass_kg", "drive_power_w", "battery_capacity_wh"}
    # Its limits are inputs, never numbers: the torque margin comes from requirements.
    (check,) = fragment.task("drive_sizing").checks
    assert check.limit == "required_torque_margin"


def test_the_aircraft_power_loop_is_a_block_and_its_limits_are_inputs() -> None:
    # FORGE-670: all-up mass, cruise power and battery capacity depend on each other.
    fragment = load_fragment("aircraft_v1")
    (block,) = fragment.blocks
    assert block.members == ("mass_rollup", "aerodynamics", "battery_sizing")
    assert set(block.solves_for) == {"total_mass_kg", "cruise_power_w", "battery_capacity_wh"}
    # The wing is sized for the stall at MTOW, outside the loop.
    assert "wing_sizing" not in block.members
    named = {"wing_sizing", "mass_rollup", "stability", "flight_simulation"}
    limits = {
        task.id: [c.limit for c in task.checks]
        for task in fragment.tasks
        if task.id.split(".")[-1] in named
    }
    assert limits == {
        "aircraft.wing_sizing": ["max_stall_speed_mps"],
        "aircraft.mass_rollup": ["max_takeoff_mass_kg"],
        "aircraft.stability": ["required_static_margin"],
        "aircraft.flight_simulation": ["required_endurance_min"],
    }


@pytest.mark.parametrize("base", ["design_v1", "mech_v1", "hardware_v1"])
def test_the_aircraft_composes_with_electronics_and_thermal(base: str) -> None:
    from orchestrator.design_flow.compose import compose
    from orchestrator.design_flow.graph import build_graph
    from orchestrator.design_flow.invariants import validate_flow
    from orchestrator.design_flow.spec import get_flow

    fr = load_fragments()
    flow = compose(
        get_flow(base), [fr["aircraft_v1"], fr["electronics_v1"], fr["thermal_v1"]]
    ).definition
    assert validate_flow(flow).ok
    build_graph(flow.phases)  # acyclic
    ids = {p.id for p in flow.phases}
    assert {"aircraft.power_closure", "electronics.power_budget", "thermal.heat_load"} <= ids


def test_the_firmware_checks_name_their_tools_and_take_limits_as_inputs() -> None:
    # FORGE-676: the build, static check and boot are tool results; flash and
    # RAM limits come from the requirements.
    fragment = load_fragment("firmware_v1")
    caps = {t.id: t.capabilities for t in fragment.tasks}
    assert caps["firmware.build"] == ("firmware.build",)
    assert caps["firmware.static_analysis"] == ("firmware.static_check",)
    assert caps["firmware.emulated_boot"] == ("firmware.run_emulated",)
    assert caps["firmware.pin_map"] == ("kicad.get_pin_mapping",)
    limits = [c.limit for c in fragment.task("build").checks]
    assert "max_flash_pct" in limits and "max_ram_pct" in limits


@pytest.mark.parametrize("base", ["design_v1", "mech_v1", "hardware_v1"])
def test_firmware_follows_the_board_when_composed_with_electronics(base: str) -> None:
    from orchestrator.design_flow.compose import compose
    from orchestrator.design_flow.graph import build_graph
    from orchestrator.design_flow.invariants import validate_flow
    from orchestrator.design_flow.spec import get_flow

    fr = load_fragments()
    composed = compose(get_flow(base), [fr["electronics_v1"], fr["firmware_v1"]])
    flow = composed.definition
    assert validate_flow(flow).ok
    graph = build_graph(flow.phases)
    # The MCU comes from the electronics component selection, not from outside.
    external = {e.ref for e in composed.external_inputs}
    assert "component_selection:electronics" not in external
    assert {"design_value:max_flash_pct", "design_value:max_ram_pct"} <= external
    # A changed part re-runs the pin map and every firmware task after it.
    hit = set(graph.affected("electronics.component_selection"))
    assert {
        "firmware.pin_map",
        "firmware.rtos_configuration",
        "firmware.build",
        "firmware.static_analysis",
        "firmware.emulated_boot",
    } <= hit
    # A configuration change does not re-run the pin map.
    assert "firmware.pin_map" not in set(graph.affected("firmware.rtos_configuration"))
