"""Interface contracts: model, checks, agreement and change propagation (lamp golden path).

The pure checker against real, missing and violated data for each contract
kind; agreement as a person's recorded decision per owning domain; and, over
``InMemoryTwinAPI`` with real items and revisions, a change to the enclosure
envelope that makes the recorded check stale, reaches the electronics domain
in the impact report, and re-checks as violated with the numbers.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.projects.backend import InMemoryProjectBackend
from api_gateway.twin import interface_contracts as ic
from api_gateway.twin import routes as twin_routes
from api_gateway.twin.interface_contract_routes import router as contract_router
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.validation_status import ValidationStatus
from twin_core.contracts.checker import (
    ArtifactSnapshot,
    CheckInputs,
    ItemSnapshot,
    contract_status,
    evaluate_contract,
    outcome_words,
    validation_status_of,
)
from twin_core.impact import cross_domain
from twin_core.impact.cross_domain import list_impacts
from twin_core.impact.interfaces import list_interfaces
from twin_core.items import commit_revision, plan_revision
from twin_core.models.enums import WorkProductType
from twin_core.models.interface import InterfaceStatus, InterfaceType
from twin_core.models.interface_contract import (
    CheckOutcome,
    ContractAgreement,
    ContractStatus,
    InterfaceContract,
)
from twin_core.models.work_product import WorkProduct

PROJECT = "81281281-2812-4812-8812-812812812812"
PID = UUID(PROJECT)


def _cad_eda(**links: str) -> InterfaceContract:
    return InterfaceContract.model_validate(
        {
            "kind": "cad_eda",
            "title": "Base enclosure and driver board",
            "plain": "The circuit board must fit inside the base",
            "owning_domains": ["mechanical", "electronics"],
            "parameters": [
                {
                    "name": "pcb_outline",
                    "label": "PCB outline",
                    "value": {"width": 60, "height": 40},
                    "unit": "mm",
                    "tolerance": 0.2,
                    "owner": "electronics",
                    "basis": "estimated",
                },
                {
                    "name": "pcb_clearance",
                    "label": "Clearance",
                    "value": 1.0,
                    "unit": "mm",
                    "owner": "mechanical",
                    "basis": "assumed",
                },
                {
                    "name": "mounting_hole_positions",
                    "label": "Holes",
                    "value": [{"x": 4, "y": 4}, {"x": 56, "y": 36}],
                    "unit": "mm",
                    "tolerance": 0.3,
                    "owner": "electronics",
                    "basis": "estimated",
                },
                {
                    "name": "mounting_hole_diameter",
                    "label": "Hole diameter",
                    "value": 3.2,
                    "unit": "mm",
                    "tolerance": 0.1,
                    "owner": "electronics",
                    "basis": "estimated",
                },
                {
                    "name": "max_component_height",
                    "label": "Max height",
                    "value": 12,
                    "unit": "mm",
                    "owner": "mechanical",
                    "basis": "estimated",
                },
                {
                    "name": "led_position",
                    "label": "LED",
                    "value": {"x": 0, "y": 0, "z": -8},
                    "unit": "mm",
                    "tolerance": 1.0,
                    "owner": "mechanical",
                    "basis": "estimated",
                },
                {
                    "name": "usb_c_position",
                    "label": "USB-C",
                    "value": {"x": 30, "y": 0},
                    "unit": "mm",
                    "tolerance": 0.5,
                    "owner": "electronics",
                    "basis": "estimated",
                },
                {
                    "name": "usb_c_cutout",
                    "label": "Cut-out",
                    "value": {"width": 9.5, "height": 3.8},
                    "unit": "mm",
                    "tolerance": 0.2,
                    "owner": "mechanical",
                    "basis": "estimated",
                },
            ],
            "links": [
                {
                    "role": "enclosure",
                    "label": "Base (CAD)",
                    "domain": "mechanical",
                    "item_types": ["cad_model"],
                    "item_key": links.get("enclosure"),
                    "produced_by": "the parametric CAD build",
                },
                {
                    "role": "pcb",
                    "label": "PCB layout",
                    "domain": "electronics",
                    "item_types": ["pcb_layout"],
                    "item_key": links.get("pcb"),
                    "produced_by": "kicad.generate_pcb",
                },
            ],
        }
    )


PCB_META: dict[str, Any] = {
    "dimensions": {"width": 60, "height": 40},
    "mounting_holes": [{"x": 4, "y": 4, "diameter": 3.2}, {"x": 56, "y": 36, "diameter": 3.2}],
    "connectors": [{"name": "USB-C", "x": 30, "y": 0, "width": 8.9, "height": 3.2}],
    "component_max_height": 7.5,
    "led_position": {"x": 0, "y": 0.2, "z": -8},
}
ENCLOSURE_META: dict[str, Any] = {
    "subtype": "enclosure",
    "internal_envelope": {"width": 100, "depth": 70, "height": 22},
    "mounting_standoffs": [{"x": 4, "y": 4}, {"x": 56, "y": 36}],
    "cutouts": [{"connector_name": "USB-C", "x": 30.2, "y": 0, "width": 9.5, "height": 3.8}],
}


def _cad_inputs(pcb: dict[str, Any] | None, enclosure: dict[str, Any] | None) -> CheckInputs:
    items = {}
    if pcb is not None:
        items["pcb"] = ItemSnapshot(
            key="PCB-MAIN", revision=1, item_type="pcb_layout", metadata=pcb
        )
    if enclosure is not None:
        items["enclosure"] = ItemSnapshot(
            key="CAD-BASE", revision=1, item_type="cad_model", metadata=enclosure
        )
    return CheckInputs(items=items)


def _evaluate(contract: InterfaceContract, inputs: CheckInputs, **kw: Any) -> Any:
    return evaluate_contract(
        contract, contract_key="IFC-X", contract_revision=1, inputs=inputs, **kw
    )


def _check(evaluation: Any, check_id: str) -> Any:
    return next(c for c in evaluation.checks if c.id == check_id)


# ── the model ────────────────────────────────────────────────────────────


def test_a_parameter_with_a_value_says_how_far_it_is_trusted() -> None:
    with pytest.raises(ValueError, match="no value"):
        InterfaceContract.model_validate(
            {
                "kind": "cad_eda",
                "title": "t",
                "plain": "p",
                "owning_domains": ["mechanical", "electronics"],
                "parameters": [
                    {
                        "name": "pcb_outline",
                        "label": "x",
                        "owner": "electronics",
                        "basis": "estimated",
                    }
                ],
            }
        )
    unknown = InterfaceContract.model_validate(
        {
            "kind": "cad_eda",
            "title": "t",
            "plain": "p",
            "owning_domains": ["mechanical", "electronics"],
            "parameters": [
                {
                    "name": "pcb_outline",
                    "label": "x",
                    "value": 3,
                    "owner": "electronics",
                    "basis": "unknown",
                }
            ],
        }
    )
    assert unknown.parameters[0].value is None  # an unknown value is never shown as one
    with pytest.raises(ValueError, match="recognized unit"):
        InterfaceContract.model_validate(
            {
                "kind": "cad_eda",
                "title": "t",
                "plain": "p",
                "owning_domains": ["mechanical", "electronics"],
                "parameters": [
                    {
                        "name": "a",
                        "label": "x",
                        "value": 3,
                        "unit": "furlongs-ish",
                        "owner": "e",
                        "basis": "stated",
                    }
                ],
            }
        )


# ── CAD to EDA ───────────────────────────────────────────────────────────


def test_cad_eda_real_data_that_agrees_is_satisfied() -> None:
    evaluation = _evaluate(_cad_eda(), _cad_inputs(PCB_META, ENCLOSURE_META))
    assert evaluation.outcome is CheckOutcome.SATISFIED, [c.message for c in evaluation.checks]
    fit = _check(evaluation, "pcb_fits_envelope")
    assert fit.values["margin_width_mm"] == 38 and fit.values["clearance_mm"] == 1.0
    assert evaluation.inputs == ["IFC-X@1", "PCB-MAIN@1", "CAD-BASE@1"]
    assert validation_status_of(evaluation) is ValidationStatus.PASS


def test_cad_eda_missing_items_are_not_evaluated_with_what_would_produce_them() -> None:
    evaluation = _evaluate(_cad_eda(), _cad_inputs(None, None))
    assert evaluation.outcome is CheckOutcome.NOT_EVALUATED
    fit = _check(evaluation, "pcb_fits_envelope")
    assert any("kicad.generate_pcb" in m for m in fit.missing)
    assert any("parametric CAD build" in m for m in fit.missing)
    assert fit.values == {}  # no number stands in for a missing one
    assert validation_status_of(evaluation) is ValidationStatus.NOT_EVALUATED


def test_cad_eda_violations_report_the_values() -> None:
    small = {**ENCLOSURE_META, "internal_envelope": {"width": 61, "depth": 41, "height": 22}}
    moved = {
        **PCB_META,
        "mounting_holes": [{"x": 4, "y": 4, "diameter": 3.2}, {"x": 57, "y": 36, "diameter": 2.5}],
        "connectors": [{"name": "USB-C", "x": 32, "y": 0, "width": 8.9, "height": 3.2}],
        "component_max_height": 14,
    }
    evaluation = _evaluate(_cad_eda(), _cad_inputs(moved, small))
    assert evaluation.outcome is CheckOutcome.VIOLATED
    fit = _check(evaluation, "pcb_fits_envelope")
    assert fit.outcome is CheckOutcome.VIOLATED and fit.values["margin_width_mm"] == -1
    holes = _check(evaluation, "mounting_holes_match")
    assert holes.outcome is CheckOutcome.VIOLATED
    assert holes.values["unmatched"][0]["distance_mm"] == 1
    assert holes.values["diameter_issues"][0]["diameter_mm"] == 2.5
    usb = _check(evaluation, "usb_c_matches_cutout")
    assert usb.outcome is CheckOutcome.VIOLATED and usb.values["offset_mm"] == 1.8
    assert _check(evaluation, "component_height").values["margin_mm"] == -2
    assert validation_status_of(evaluation) is ValidationStatus.FAIL


def test_cad_eda_an_unknown_agreed_value_is_a_missing_input() -> None:
    contract = _cad_eda()
    params = [p for p in contract.parameters if p.name != "pcb_clearance"]
    evaluation = _evaluate(
        contract.model_copy(update={"parameters": params}), _cad_inputs(PCB_META, ENCLOSURE_META)
    )
    fit = _check(evaluation, "pcb_fits_envelope")
    assert fit.outcome is CheckOutcome.NOT_EVALUATED
    assert any("pcb_clearance" in m for m in fit.missing)
    # The checks that could run did; the rollup is partial, never a pass.
    assert validation_status_of(evaluation) is ValidationStatus.INCOMPLETE


# ── EDA to firmware ──────────────────────────────────────────────────────


def _eda_fw(known: bool = True) -> InterfaceContract:
    def p(name: str, value: Any, **kw: Any) -> dict[str, Any]:
        base = {"name": name, "label": name, "owner": "electronics"}
        return (
            {**base, **kw, "value": value, "basis": "stated"}
            if known
            else {**base, "basis": "unknown"}
        )

    return InterfaceContract.model_validate(
        {
            "kind": "eda_firmware",
            "title": "Driver board and firmware",
            "plain": "The brightness control must match the circuit board's wiring",
            "owning_domains": ["electronics", "firmware"],
            "parameters": [
                p("controller", "RP2040"),
                p("led_pwm_pin", {"pin": "GPIO15", "net": "LED_PWM"}),
                p(
                    "control_input_pins",
                    [{"pin": "GPIO2", "net": "ENC_A"}, {"pin": "GPIO3", "net": "ENC_B"}],
                ),
                p("max_led_current", 700, unit="mA"),
                p("pwm_frequency_range", {"min": 1000, "max": 25000}, unit="Hz"),
            ],
            "links": [
                {
                    "role": "schematic",
                    "label": "Schematic",
                    "domain": "electronics",
                    "item_types": ["schematic"],
                },
                {
                    "role": "pinmap",
                    "label": "Pin map",
                    "domain": "electronics",
                    "item_types": ["pinmap"],
                    "produced_by": "kicad.get_pin_mapping",
                },
                {
                    "role": "firmware",
                    "label": "Firmware",
                    "domain": "firmware",
                    "item_types": ["firmware_source"],
                    "produced_by": "firmware.build",
                },
            ],
        }
    )


PINMAP = [
    {"pin": "GPIO15", "net": "LED_PWM", "signal": "", "peripheral": ""},
    {"pin": "GPIO2", "net": "ENC_A", "signal": "", "peripheral": ""},
    {"pin": "GPIO3", "net": "ENC_B", "signal": "", "peripheral": ""},
]


def _fw_inputs(**kw: Any) -> CheckInputs:
    items = {
        "pinmap": ItemSnapshot(
            key="PIN-MAIN", revision=1, item_type="pinmap", metadata={"controller": "RP2040"}
        ),
        "schematic": ItemSnapshot(
            key="SCH-MAIN", revision=1, item_type="schematic", metadata={"led_current_ma": 500}
        ),
        "firmware": ItemSnapshot(
            key="FW-MAIN",
            revision=1,
            item_type="firmware_source",
            metadata={
                "target": "rp2040 pico",
                "pwm_frequency_hz": 20000,
                "interface_version": "IFC-X v1",
            },
        ),
    }
    base: dict[str, Any] = {
        "items": items,
        "pinmap": PINMAP,
        "pin_edge": True,
        "firmware_pins": ["GPIO15", "ENC_A", "GPIO3"],
    }
    base.update(kw)
    return CheckInputs(**base)


def test_eda_firmware_pin_map_matches_the_netlist_and_the_firmware() -> None:
    evaluation = _evaluate(_eda_fw(), _fw_inputs())
    assert evaluation.outcome is CheckOutcome.SATISFIED, [c.message for c in evaluation.checks]
    assert {c.id for c in evaluation.checks} >= {
        "controller_matches",
        "pins_match_netlist",
        "firmware_uses_pins",
        "pwm_frequency",
        "led_current",
        "interface_version",
    }


def test_eda_firmware_a_pin_moved_in_the_netlist_is_violated_with_both_pins() -> None:
    moved = [dict(r) for r in PINMAP]
    moved[0]["pin"] = "GPIO16"
    evaluation = _evaluate(_eda_fw(), _fw_inputs(pinmap=moved))
    pins = _check(evaluation, "pins_match_netlist")
    assert pins.outcome is CheckOutcome.VIOLATED
    assert pins.values["issues"][0] == {
        "net": "LED_PWM",
        "contract_pin": "GPIO15",
        "netlist_pins": ["GPIO16"],
    }


def test_eda_firmware_without_a_pin_maps_to_link_is_not_evaluated() -> None:
    evaluation = _evaluate(_eda_fw(), _fw_inputs(pin_edge=False, firmware_pins=[]))
    uses = _check(evaluation, "firmware_uses_pins")
    assert uses.outcome is CheckOutcome.NOT_EVALUATED
    assert any("pin_maps_to" in m for m in uses.missing)


def test_eda_firmware_unknown_contract_values_are_not_evaluated() -> None:
    evaluation = _evaluate(_eda_fw(known=False), CheckInputs())
    assert evaluation.outcome is CheckOutcome.NOT_EVALUATED
    assert all(c.outcome is CheckOutcome.NOT_EVALUATED for c in evaluation.checks)


def test_eda_firmware_built_against_an_older_interface_version_is_stale() -> None:
    evaluation = _evaluate(_eda_fw(), _fw_inputs(), interface_version=2)
    version = _check(evaluation, "interface_version")
    assert version.outcome is CheckOutcome.STALE
    assert version.values == {"built_against": "IFC-X v1", "current": "IFC-X v2"}
    assert evaluation.outcome is CheckOutcome.STALE


# ── CAD and EDA to simulation ────────────────────────────────────────────


def _sim(heat: bool) -> InterfaceContract:
    heat_params = (
        [
            {
                "name": "led_electrical_power",
                "label": "Power",
                "value": 4,
                "unit": "W",
                "owner": "electronics",
                "basis": "estimated",
            },
            {
                "name": "led_heat_estimate",
                "label": "Heat",
                "value": 2.8,
                "unit": "W",
                "owner": "electronics",
                "basis": "estimated",
            },
        ]
        if heat
        else [
            {
                "name": "led_electrical_power",
                "label": "Power",
                "unit": "W",
                "owner": "electronics",
                "basis": "unknown",
            },
            {
                "name": "led_heat_estimate",
                "label": "Heat",
                "unit": "W",
                "owner": "electronics",
                "basis": "unknown",
            },
        ]
    )
    return InterfaceContract.model_validate(
        {
            "kind": "simulation",
            "title": "Thermal inputs",
            "plain": "The temperature check needs the lamp's shape, materials and LED heat",
            "owning_domains": ["mechanical", "electronics", "simulation"],
            "parameters": [
                *heat_params,
                {
                    "name": "ambient_temperature",
                    "label": "Ambient",
                    "value": 25,
                    "unit": "degC",
                    "owner": "simulation",
                    "basis": "assumed",
                },
                {
                    "name": "convection",
                    "label": "Convection",
                    "value": "natural",
                    "owner": "simulation",
                    "basis": "assumed",
                },
            ],
            "links": [
                {
                    "role": "enclosure",
                    "label": "Base (CAD)",
                    "domain": "mechanical",
                    "item_types": ["cad_model"],
                }
            ],
        }
    )


def test_simulation_needs_the_led_heat_estimate() -> None:
    inputs = CheckInputs(
        items={"enclosure": ItemSnapshot(key="CAD-BASE", revision=2, item_type="cad_model")},
        context={"selected_material": {"value": "PETG", "authority": "assumption"}},
        context_ref="MFC-PROJECT@1",
    )
    missing = _evaluate(_sim(heat=False), inputs)
    heat = _check(missing, "led_heat_estimate")
    assert heat.outcome is CheckOutcome.NOT_EVALUATED
    assert any("LED electrical power" in m for m in heat.missing)
    assert _check(missing, "geometry_revision").values == {"geometry": ["CAD-BASE@2"]}
    assert _check(missing, "materials").values["authority"] == "assumption"
    present = _evaluate(_sim(heat=True), inputs)
    assert present.outcome is CheckOutcome.SATISFIED
    assert _check(present, "led_heat_estimate").values["basis"] == "estimated"


# ── CAD and EDA to manufacturing ─────────────────────────────────────────


def _mfg() -> InterfaceContract:
    return InterfaceContract.model_validate(
        {
            "kind": "manufacturing",
            "title": "Prototype package",
            "plain": "The print files, board files and firmware must belong to the same version",
            "owning_domains": ["mechanical", "electronics", "manufacturing"],
            "artifacts": [
                {
                    "kind": "print_file",
                    "label": "Print file",
                    "parts": ["CMP-base"],
                    "item_types": ["gcode"],
                    "produced_by": "slicer.slice",
                },
                {
                    "kind": "gerbers",
                    "label": "Gerbers",
                    "item_types": ["gerber"],
                    "produced_by": "kicad.export_gerber",
                },
            ],
        }
    )


def test_manufacturing_artifacts_missing_stale_and_current() -> None:
    nothing = _evaluate(_mfg(), CheckInputs())
    assert nothing.outcome is CheckOutcome.NOT_EVALUATED
    assert _check(nothing, "artifact_print_file_CMP-base").missing == [
        "Print file for CMP-base: not produced yet (produced by slicer.slice)"
    ]
    current = CheckInputs(
        artifacts={
            "print_file": [
                ArtifactSnapshot(
                    ref="GC-BASE@1",
                    component_id="CMP-base",
                    pins=[{"pinned": "CAD-BASE@2", "current": "CAD-BASE@2"}],
                )
            ],
            "gerbers": [
                ArtifactSnapshot(
                    ref="GBR-MAIN@1", pins=[{"pinned": "PCB-MAIN@1", "current": "PCB-MAIN@2"}]
                )
            ],
        }
    )
    evaluation = _evaluate(_mfg(), current)
    assert _check(evaluation, "artifact_print_file_CMP-base").outcome is CheckOutcome.SATISFIED
    gerbers = _check(evaluation, "artifact_gerbers")
    assert gerbers.outcome is CheckOutcome.STALE
    assert "PCB-MAIN@1 (now PCB-MAIN@2)" in gerbers.message
    assert evaluation.outcome is CheckOutcome.STALE


# ── status and agreement (pure) ──────────────────────────────────────────


def _agreement(domain: str, version: int = 1) -> ContractAgreement:
    return ContractAgreement(
        domain=domain,
        contract_revision=1,
        interface_version=version,
        decided_by="local:dashboard",
        decision_id="d",
    )


def test_status_is_proposed_until_every_owning_domain_agrees() -> None:
    contract = _cad_eda()
    kw: dict[str, Any] = {"contract_revision": 1, "interface_version": 1, "latest": None}
    assert contract_status(contract, agreements=[], **kw) is ContractStatus.PROPOSED
    assert (
        contract_status(contract, agreements=[_agreement("mechanical")], **kw)
        is ContractStatus.PROPOSED
    )
    both = [_agreement("mechanical"), _agreement("electronics")]
    assert contract_status(contract, agreements=both, **kw) is ContractStatus.AGREED
    # An agreement to an earlier interface version does not carry over.
    older = [_agreement("mechanical", 1), _agreement("electronics", 1)]
    assert (
        contract_status(
            contract, agreements=older, contract_revision=2, interface_version=2, latest=None
        )
        is ContractStatus.PROPOSED
    )
    violated = _evaluate(
        contract,
        _cad_inputs({**PCB_META, "dimensions": {"width": 99, "height": 99}}, ENCLOSURE_META),
    )
    assert (
        contract_status(
            contract, agreements=both, contract_revision=1, interface_version=1, latest=violated
        )
        is ContractStatus.VIOLATED
    )
    assert (
        contract_status(
            contract,
            agreements=both,
            contract_revision=1,
            interface_version=1,
            latest=violated,
            latest_is_stale=True,
        )
        is ContractStatus.STALE
    )
    assert outcome_words(None) == "not checked yet"
    assert outcome_words(CheckOutcome.VIOLATED) == "does not fit yet"


# ── in the twin ──────────────────────────────────────────────────────────


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture(autouse=True)
def _no_hooks() -> Iterator[None]:
    cross_domain.set_impact_hooks()
    cross_domain.set_parameter_extractor(None)
    yield
    cross_domain.set_impact_hooks()
    cross_domain.set_parameter_extractor(None)


@pytest.fixture(autouse=True)
def _blobs(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    stored: dict[str, bytes] = {}
    import digital_twin.storage.work_product_blobs as wpb

    def store(node_id: str, filename: str, content: bytes, **_: Any) -> str:
        key = f"work-products/{node_id}/{filename}"
        stored[key] = content
        return key

    monkeypatch.setattr(wpb, "store_work_product_blob", store)
    monkeypatch.setattr(wpb, "fetch_work_product_blob", lambda key: stored[key])
    return stored


_TYPES = {"cad_model": ("cad_model", "mechanical"), "pcb_layout": ("pcb_layout", "electronics")}


async def _revise(
    twin: Any, item_type: str, name: str, meta: dict[str, Any], key: str | None = None
) -> str:
    plan = await plan_revision(
        twin, item_type=item_type, name=name, project_id=PID, author="test", item_key=key
    )
    assert plan is not None
    wp_type, domain = _TYPES[item_type]
    wp = WorkProduct(
        name=name,
        type=WorkProductType(wp_type),
        domain=domain,
        file_path="",
        content_hash=hashlib.sha256(repr(sorted(meta.items())).encode()).hexdigest(),
        format="json",
        metadata={**meta, **plan.stamp()},
        created_by="test",
        project_id=PID,
    )
    await twin.create_work_product(wp)
    await commit_revision(twin, plan, wp.id, name=name)
    return plan.key


async def _board_and_base(twin: Any) -> tuple[str, str]:
    base = await _revise(twin, "cad_model", "Base", ENCLOSURE_META, key="CAD-BASE")
    pcb = await _revise(twin, "pcb_layout", "Driver PCB", PCB_META, key="PCB-MAIN")
    return base, pcb


async def test_writing_the_same_contract_twice_makes_one_revision(twin: Any) -> None:
    first, created = await ic.write_contract(twin, PID, _cad_eda(), actor="test", source="test")
    again, created_again = await ic.write_contract(
        twin, PID, _cad_eda(), actor="test", source="test"
    )
    assert created and not created_again
    assert first.ref == again.ref == "IFC-CAD-EDA@1"
    assert first.interface_version == 1


async def test_linking_items_keeps_the_interface_version_and_derives_proposed_edges(
    twin: Any,
) -> None:
    await ic.write_contract(twin, PID, _cad_eda(), actor="test", source="test")
    base, pcb = await _board_and_base(twin)
    await ic.link_item(
        twin, PID, "IFC-CAD-EDA", role="enclosure", item_key=base, actor="local:dashboard"
    )
    linked = await ic.link_item(
        twin, PID, "IFC-CAD-EDA", role="pcb", item_key=pcb, actor="local:dashboard"
    )
    assert linked.revision == 3 and linked.interface_version == 1
    edges = [e for e in await list_interfaces(twin, PID) if e.id.startswith("derived:contract:")]
    assert {e.type for e in edges} == {
        InterfaceType.CLEARANCE_TO,
        InterfaceType.MOUNTS_TO,
        InterfaceType.CONNECTS_VIA,
    }
    # Not agreed yet: each implied edge is a proposal, never stale-marking.
    assert all(e.status is InterfaceStatus.PROPOSED for e in edges)
    with pytest.raises(ic.ContractError, match="takes pcb_layout"):
        await ic.link_item(twin, PID, "IFC-CAD-EDA", role="pcb", item_key=base, actor="x")


async def test_agreement_needs_a_person_and_every_owning_domain(twin: Any) -> None:
    await ic.write_contract(twin, PID, _cad_eda(), actor="test", source="test")
    for not_a_person in ("metaforge:inference", "agent:mechanical", "policy:auto", ""):
        with pytest.raises(ic.ContractError) as refused:
            await ic.agree(twin, PID, "IFC-CAD-EDA", domain="mechanical", actor=not_a_person)
        assert refused.value.status == 403
    with pytest.raises(ic.ContractError, match="does not own"):
        await ic.agree(twin, PID, "IFC-CAD-EDA", domain="firmware", actor="local:dashboard")

    first = await ic.agree(twin, PID, "IFC-CAD-EDA", domain="mechanical", actor="local:dashboard")
    assert first["created"]
    again = await ic.agree(twin, PID, "IFC-CAD-EDA", domain="mechanical", actor="local:dashboard")
    assert not again["created"] and again["decision_id"] == first["decision_id"]
    view = (await ic.contracts_view(twin, PID))["contracts"][0]
    assert view["status"] == "proposed" and view["awaiting_agreement"] == ["electronics"]

    await ic.agree(twin, PID, "IFC-CAD-EDA", domain="electronics", actor="local:dashboard")
    view = (await ic.contracts_view(twin, PID))["contracts"][0]
    assert view["status"] == "agreed"
    assert {a["domain"] for a in view["agreements"]} == {"mechanical", "electronics"}
    # The agreement is a recorded design decision pinned to the contract revision.
    decisions = await twin.list_work_products(
        work_product_type=WorkProductType.DESIGN_DECISION, project_id=PID
    )
    agreement = decisions[0].metadata["contract_agreement"]
    assert (
        agreement["contract_key"] == "IFC-CAD-EDA" and agreement["decided_by"] == "local:dashboard"
    )
    assert decisions[0].metadata["depends_on"] == ["IFC-CAD-EDA@1"]

    # A value change is a new interface version: agreement is needed again.
    params = [
        p.model_copy(update={"value": 2.0}) if p.name == "pcb_clearance" else p
        for p in _cad_eda().parameters
    ]
    changed, _ = await ic.write_contract(
        twin, PID, _cad_eda().model_copy(update={"parameters": params}), actor="t", source="t"
    )
    assert changed.interface_version == 2
    view = (await ic.contracts_view(twin, PID))["contracts"][0]
    assert view["status"] == "proposed"


async def test_an_enclosure_change_stales_the_fit_check_and_reaches_electronics(twin: Any) -> None:
    await ic.write_contract(twin, PID, _cad_eda(), actor="test", source="test")
    base, pcb = await _board_and_base(twin)
    await ic.link_item(
        twin, PID, "IFC-CAD-EDA", role="enclosure", item_key=base, actor="local:dashboard"
    )
    await ic.link_item(twin, PID, "IFC-CAD-EDA", role="pcb", item_key=pcb, actor="local:dashboard")
    [(evaluation, record_id)] = await ic.check_contracts(twin, PID, actor="local:dashboard")
    assert evaluation.outcome is CheckOutcome.SATISFIED, [c.message for c in evaluation.checks]
    view = (await ic.contracts_view(twin, PID))["contracts"][0]
    assert view["latest_check"]["record_id"] == record_id
    assert view["latest_check"]["validation"]["status"] == "PASS"
    assert view["create_line"] == "The circuit board must fit inside the base: checked, it fits"

    # The base's inside shrinks: the next revision of CAD-BASE.
    smaller = {**ENCLOSURE_META, "internal_envelope": {"width": 55, "depth": 35, "height": 22}}
    await _revise(twin, "cad_model", "Base", smaller, key="CAD-BASE")

    view = (await ic.contracts_view(twin, PID))["contracts"][0]
    assert view["latest_check"]["stale"] is True
    assert view["status"] == "stale"
    assert view["create_line"].endswith("needs checking again after a change")

    impacts = await list_impacts(twin, PID)
    assert impacts, "the head move recorded an impact"
    impact = impacts[0]
    assert impact.change.item_key == "CAD-BASE"
    assert "electronics" in impact.by_domain and "PCB-MAIN" in impact.by_domain["electronics"]
    assert any(
        s.check_kind == "contract" and s.record_id == record_id for s in impact.stale_results
    )

    [(rechecked, _)] = await ic.check_contracts(twin, PID, actor="local:dashboard")
    fit = _check(rechecked, "pcb_fits_envelope")
    assert fit.outcome is CheckOutcome.VIOLATED
    assert fit.values["envelope_mm"] == [55.0, 35.0] and fit.values["board_mm"] == [60.0, 40.0]
    view = (await ic.contracts_view(twin, PID))["contracts"][0]
    assert view["status"] == "violated"
    assert view["latest_check"]["inputs"] == ["IFC-CAD-EDA@3", "PCB-MAIN@1", "CAD-BASE@2"]


async def test_an_agreed_contract_confirms_its_edges_and_proposes_a_drc_rerun(twin: Any) -> None:
    await ic.write_contract(twin, PID, _cad_eda(), actor="test", source="test")
    base, pcb = await _board_and_base(twin)
    await ic.link_item(
        twin, PID, "IFC-CAD-EDA", role="enclosure", item_key=base, actor="local:dashboard"
    )
    await ic.link_item(twin, PID, "IFC-CAD-EDA", role="pcb", item_key=pcb, actor="local:dashboard")
    for domain in ("mechanical", "electronics"):
        await ic.agree(twin, PID, "IFC-CAD-EDA", domain=domain, actor="local:dashboard")
    edges = [e for e in await list_interfaces(twin, PID) if e.id.startswith("derived:contract:")]
    assert edges and all(e.status is InterfaceStatus.CONFIRMED for e in edges)
    assert all(e.decided_by == "local:dashboard" for e in edges)
    await _revise(twin, "cad_model", "Base", {**ENCLOSURE_META, "cutouts": []}, key="CAD-BASE")
    impact = (await list_impacts(twin, PID))[0]
    pcb_entity = next(e for e in impact.impacted if e.ref == "PCB-MAIN")
    assert pcb_entity.certain
    assert any(
        r.tool == "kicad.run_drc" and r.target_ref == "PCB-MAIN" for r in impact.proposed_reruns
    )


# ── routes ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(twin: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from api_gateway.projects import routes as project_routes

    monkeypatch.setattr(twin_routes, "_twin", twin)
    monkeypatch.setattr(project_routes, "_backend", InMemoryProjectBackend.create())
    app = FastAPI()
    app.include_router(contract_router)
    return TestClient(app)


async def test_routes_list_check_link_and_agree(twin: Any, client: TestClient) -> None:
    base = f"/v1/twin/projects/{PROJECT}/contracts"
    assert client.get(base).json() == {"project_id": PROJECT, "contracts": []}
    await ic.write_contract(twin, PID, _cad_eda(), actor="test", source="test")
    listed = client.get(base).json()["contracts"][0]
    assert listed["key"] == "IFC-CAD-EDA" and listed["status"] == "proposed"
    assert listed["latest_check"] is None
    assert listed["create_line"] == "The circuit board must fit inside the base: not checked yet"

    checked = client.post(f"{base}/check", json={})
    assert checked.status_code == 200, checked.text
    assert checked.json()["checks"][0]["outcome"] == "not_evaluated"
    assert client.post(f"{base}/check", json={"key": "IFC-NONE"}).status_code == 404

    cad, _pcb = await _board_and_base(twin)
    linked = client.post(f"{base}/IFC-CAD-EDA/links", json={"role": "enclosure", "item_key": cad})
    assert linked.status_code == 200, linked.text
    assert linked.json()["ref"] == "IFC-CAD-EDA@2"
    assert (
        client.post(f"{base}/IFC-CAD-EDA/links", json={"role": "nope", "item_key": cad}).status_code
        == 422
    )

    agreed = client.post(f"{base}/IFC-CAD-EDA/agree", json={"domain": "mechanical"})
    assert agreed.status_code == 200, agreed.text
    assert agreed.json()["created"] is True
    assert client.post(f"{base}/IFC-CAD-EDA/agree", json={"domain": "firmware"}).status_code == 422
    assert client.get("/v1/twin/projects/not-a-uuid/contracts").status_code == 400
