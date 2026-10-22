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
    assert {"thermal_v1", "electronics_v1"} <= set(load_fragments())


@pytest.mark.parametrize("fragment_id", ["thermal_v1", "electronics_v1"])
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
