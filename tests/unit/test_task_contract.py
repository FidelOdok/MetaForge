"""The task contract on the phase model (composable flows, story 1).

A phase that declares no task fields must behave, serialise and hash exactly
as before the contract existed, and a task's fields must survive every
round trip: template mapping, frozen flow, version store.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from orchestrator.design_flow.contract import (
    ContractError,
    check_from,
    checks_from,
    outputs_of,
    parse_ref,
    ref_key,
    slots_from_outputs,
)
from orchestrator.design_flow.frozen import freeze_flow
from orchestrator.design_flow.spec import Check, FlowDefinition, Phase, definition_from_frozen
from orchestrator.design_flow.templates import from_mapping, load_templates, to_mapping
from orchestrator.design_flow.versions import _definition_from_dict

GOLDEN = Path(__file__).parent / "fixtures" / "flow_templates_golden.json"
_TASK_FIELDS = ("fragment", "stage", "inputs", "checks", "capabilities", "coupling")


def _task() -> Phase:
    return Phase(
        id="thermal_v1.heat_transfer",
        title="Heat-transfer analysis",
        objective="Run the steady-state thermal analysis.",
        fragment="thermal_v1",
        stage="simulation",
        inputs=("design_value:heat_load_w", "cad_model:enclosure"),
        checks=(Check(id="tj", value="max_junction_temp_c", op="<=", threshold=85, unit="degC"),),
        capabilities=("calculix.run_thermal",),
        slots=slots_from_outputs(["design_value:max_junction_temp_c"]),
        depends_on=("requirements",),
    )


def _strip_task_fields(definition: dict) -> dict:
    for phase in definition["phases"]:
        for name in _TASK_FIELDS:
            assert not phase.pop(name), f"{phase['id']} unexpectedly carries {name}"
    return definition


# -- references -------------------------------------------------------------


def test_parse_ref_splits_type_and_name() -> None:
    assert parse_ref("design_value:heat_load_w") == ("design_value", "heat_load_w")


@pytest.mark.parametrize("bad", ["heat_load_w", "design_value:", ":x", "Design:x", ""])
def test_parse_ref_refuses_malformed(bad: str) -> None:
    with pytest.raises(ContractError):
        parse_ref(bad)


def test_parse_ref_refuses_a_type_that_is_not_a_revisioned_definition() -> None:
    with pytest.raises(ContractError, match="not a versioned definition type"):
        parse_ref("no_such_type:x")


def test_ref_key_matches_the_slot_the_output_binds() -> None:
    (slot,) = slots_from_outputs(["design_value:heat_load_w"])
    assert slot.item_key == ref_key("design_value:heat_load_w")
    assert slot.item_key.startswith("VAL-")


def test_duplicate_outputs_are_refused() -> None:
    with pytest.raises(ContractError, match="declared twice"):
        slots_from_outputs(["design_value:a", "design_value:a"])


# -- checks -----------------------------------------------------------------


def test_check_from_text() -> None:
    check = check_from("max_junction_temp_c <= 85 degC")
    assert (check.value, check.op, check.threshold, check.unit) == (
        "max_junction_temp_c",
        "<=",
        85.0,
        "degC",
    )
    assert check.describe() == "max_junction_temp_c <= 85 degC"


def test_check_from_mapping() -> None:
    check = check_from({"id": "sf", "value": "safety_factor", "op": ">=", "threshold": 2})
    assert check == Check(id="sf", value="safety_factor", op=">=", threshold=2.0)


@pytest.mark.parametrize(
    "bad",
    [
        "temp ~ 85",
        "85 <= temp",
        {"value": "t", "op": "=<", "threshold": 1},
        {"value": "t", "op": "<=", "threshold": "hot"},
        {"value": "t", "op": "<=", "threshold": float("inf")},
        {"value": "", "op": "<=", "threshold": 1},
    ],
)
def test_malformed_checks_are_refused(bad: object) -> None:
    with pytest.raises(ContractError):
        check_from(bad)


def test_duplicate_check_ids_are_refused() -> None:
    with pytest.raises(ContractError, match="duplicate check id"):
        checks_from(["t <= 1", "t >= 0"])


# -- plain phases are unchanged ---------------------------------------------


def test_templates_hash_exactly_as_before_the_contract() -> None:
    golden = json.loads(GOLDEN.read_text())
    for flow_id, template in load_templates().items():
        frozen = freeze_flow(template.definition, version=template.version)
        assert frozen.content_hash == golden[flow_id]["content_hash"], flow_id


def test_templates_carry_no_task_fields() -> None:
    golden = json.loads(GOLDEN.read_text())
    for flow_id, template in load_templates().items():
        current = json.loads(json.dumps(dataclasses.asdict(template.definition)))
        current = _strip_task_fields(current)
        assert current == golden[flow_id]["definition"], flow_id


# -- tasks survive every round trip -----------------------------------------


def test_task_round_trips_through_a_template_mapping() -> None:
    flow = FlowDefinition(id="f", name="F", phases=(_task(),))
    back = from_mapping(to_mapping(flow, version="1.0.0"))
    assert back == flow


def test_task_round_trips_through_a_frozen_flow() -> None:
    flow = FlowDefinition(id="f", name="F", phases=(_task(),))
    frozen = freeze_flow(flow)
    frozen.verify()
    assert definition_from_frozen(frozen) == flow


def test_task_round_trips_through_the_version_store_serialisation() -> None:
    flow = FlowDefinition(id="f", name="F", phases=(_task(),))
    stored = json.loads(json.dumps(dataclasses.asdict(flow)))
    assert _definition_from_dict(stored) == flow


def test_task_fields_are_part_of_the_hash() -> None:
    base = FlowDefinition(id="f", name="F", phases=(_task(),))
    looser = FlowDefinition(
        id="f",
        name="F",
        phases=(
            dataclasses.replace(
                _task(),
                checks=(Check(id="tj", value="max_junction_temp_c", op="<=", threshold=95),),
            ),
        ),
    )
    assert freeze_flow(base).content_hash != freeze_flow(looser).content_hash


def test_outputs_of_reads_the_slots_back_as_references() -> None:
    assert outputs_of(_task()) == ("design_value:max_junction_temp_c",)
    assert _task().is_task
    assert not Phase(id="p", title="P", objective="o").is_task
