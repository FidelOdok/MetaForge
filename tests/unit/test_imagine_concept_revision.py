"""The mapper from studio state to the verification service's ConceptRevision.

Pure: it takes the stored config and collections and states facts. Every
predicate that judges them lives in MetaForge-Verification, so these tests pin
only what is mapped, never a verdict.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from api_gateway.imagine.concept_revision import build_concept_revision, safe_id
from api_gateway.imagine.service import _empty_collections
from tests.unit.imagine_fixtures import full_config

CONTRACT_KEYS = {
    "twin_id",
    "revision_id",
    "number",
    "ledger",
    "assumptions",
    "scenarios",
    "requirements",
    "functions",
    "parts",
    "process",
    "markets",
    "interface_count",
    "screening_stored",
    "concept_mesh",
}


def build(
    config: dict[str, Any] | None = None,
    collections: dict[str, Any] | None = None,
    *,
    has_mesh: bool = False,
    revision: int = 3,
    project_id: str = "73491e61-8421-425c-a751-aad37a21269c",
) -> dict[str, Any]:
    return build_concept_revision(
        project_id,
        revision,
        config if config is not None else full_config(),
        collections if collections is not None else _empty_collections(),
        has_mesh=has_mesh,
    )


def config() -> dict[str, Any]:
    return copy.deepcopy(full_config())


def part(cfg: dict[str, Any], pid: str) -> dict[str, Any]:
    return next(p for p in cfg["context"]["components"]["parts"] if p["id"] == pid)


def by_id(items: list[dict[str, Any]], id_: str) -> dict[str, Any]:
    return next(i for i in items if i["id"] == id_)


# -- identity and shape ---------------------------------------------------------


def test_the_output_is_exactly_the_contracts_top_level_fields_and_json_safe() -> None:
    rev = build()
    assert set(rev) == CONTRACT_KEYS
    assert json.loads(json.dumps(rev)) == rev


def test_identity_comes_from_the_project_and_revision() -> None:
    rev = build(revision=7)
    assert rev["twin_id"] == "73491e61-8421-425c-a751-aad37a21269c"
    assert rev["revision_id"] == "r7" and rev["number"] == 7


def test_the_same_state_maps_to_the_same_revision() -> None:
    assert build() == build()


# -- ledger ---------------------------------------------------------------------


def test_a_ledger_row_takes_the_studio_status_over_the_configs() -> None:
    cfg = config()
    cols = _empty_collections()
    cols["ledger"]["l1"] = {"status": "unrepresentable"}
    rev = build(cfg, cols)
    assert [r["id"] for r in rev["ledger"]] == [f"l{i}" for i in range(8)]
    assert by_id(rev["ledger"], "l0")["status"] == "mapped"
    assert by_id(rev["ledger"], "l1")["status"] == "unrepresentable"


def test_a_ledger_row_with_no_status_maps_to_none_and_a_long_one_is_clipped() -> None:
    cfg = config()
    cfg["programme"]["ledger"][0].pop("status")
    cfg["programme"]["ledger"][1]["status"] = "x" * 80
    rev = build(cfg)
    assert by_id(rev["ledger"], "l0")["status"] is None
    assert len(by_id(rev["ledger"], "l1")["status"]) == 50


# -- assumptions ----------------------------------------------------------------


def test_an_assumption_is_confirmed_accepted_or_unconfirmed() -> None:
    cols = _empty_collections()
    cols["assumptions"]["cover"] = {"confirmed": True}
    cols["assumptions"]["coverage"] = {"confirmed": False, "accepted": True}
    rev = build(config(), cols)
    states = {a["id"]: a["state"] for a in rev["assumptions"]}
    assert states == {"cover": "confirmed", "coverage": "accepted", "rail": "unconfirmed"}


def test_confirmed_wins_over_accepted() -> None:
    cols = _empty_collections()
    cols["assumptions"]["cover"] = {"confirmed": True, "accepted": True}
    assert by_id(build(config(), cols)["assumptions"], "cover")["state"] == "confirmed"


def test_only_a_delegated_acceptance_carries_who_accepted_it() -> None:
    cols = _empty_collections()
    cols["assumptions"]["cover"] = {
        "accepted": True,
        "agent": "claude",
        "on_behalf_of": "fidel",
    }
    cols["assumptions"]["coverage"] = {"accepted": True}
    cols["assumptions"]["rail"] = {"confirmed": True, "agent": "claude", "on_behalf_of": "x"}
    asm = {a["id"]: a for a in build(config(), cols)["assumptions"]}
    assert asm["cover"]["accepted_by"] == "agent claude on behalf of fidel"
    assert "accepted_by" not in asm["coverage"]
    assert "accepted_by" not in asm["rail"]


# -- scenarios ------------------------------------------------------------------


def test_a_scenario_a_person_touched_is_theirs_and_the_rest_are_inferred() -> None:
    cfg = config()
    next(s for s in cfg["scenarios"] if s["id"] == "knock")["by"] = "agent"
    cols = _empty_collections()
    cols["scenarios"]["report"] = {"confirmed": True}
    rev = build(cfg, cols)
    scen = {s["id"]: s for s in rev["scenarios"]}
    assert scen["report"] == {"id": "report", "origin": "person", "confirmed": True}
    assert scen["knock"]["origin"] == "inferred" and scen["knock"]["confirmed"] is False


def test_a_delegated_confirmation_names_who_confirmed() -> None:
    cols = _empty_collections()
    cols["scenarios"]["knock"] = {"confirmed": True, "agent": "claude", "on_behalf_of": "fidel"}
    scen = by_id(build(config(), cols)["scenarios"], "knock")
    assert scen["confirmed"] is True
    assert scen["confirmed_by"] == "agent claude on behalf of fidel"


# -- requirements ---------------------------------------------------------------


def test_requirements_leave_out_the_scenario_ones() -> None:
    rev = build()
    ids = [r["id"] for r in rev["requirements"]]
    assert ids and not any(i.startswith("img_scenario") for i in ids)
    assert len(ids) == len(set(ids))


def test_a_requirement_source_is_stripped_and_blank_becomes_none() -> None:
    cfg = config()
    cfg["requirements"][0]["source"] = "  brief  "
    cfg["requirements"][1]["source"] = "   "
    cfg["requirements"][2].pop("source", None)
    reqs = build(cfg)["requirements"]
    assert reqs[0]["source"] == "brief"
    assert reqs[1]["source"] is None
    assert reqs[2]["source"] is None


def test_a_defaulted_verification_method_is_flagged() -> None:
    cfg = config()
    cfg["requirements"][0]["verify"] = "test"
    reqs = build(cfg)["requirements"]
    assert reqs[0]["verification_defaulted"] is False
    assert reqs[1]["verification_defaulted"] is True


def test_the_clients_own_readiness_answers_are_not_invented() -> None:
    for r in build()["requirements"]:
        assert "has_value" not in r and "has_test" not in r


# -- functions and parts --------------------------------------------------------


def test_each_function_names_its_chosen_part() -> None:
    rev = build()
    assert {f["id"]: f["part_id"] for f in rev["functions"]} == {
        "e_usbc": "usbc-a",
        "e_th": "th-a",
        "e_lte": "lte-a",
        "e_btn": "btn-a",
        "e_led": "led-a",
    }


def test_only_chosen_parts_are_submitted() -> None:
    ids = {p["id"] for p in build()["parts"]}
    assert "th-b" not in ids and "th-a" in ids


def test_a_function_with_no_part_maps_to_none() -> None:
    cfg = config()
    cfg["context"]["components"]["parts"] = [
        p for p in cfg["context"]["components"]["parts"] if p["role"] != "fn:e_led"
    ]
    rev = build(cfg)
    assert {f["id"]: f["part_id"] for f in rev["functions"]}["e_led"] is None
    assert "led-a" not in {p["id"] for p in rev["parts"]}


def test_a_part_chosen_for_two_functions_is_submitted_once() -> None:
    cfg = config()
    # A second function served by the same part: the board role counts too.
    part(cfg, "usbc-a")["role"] = "board"
    rev = build(cfg)
    assert len([p for p in rev["parts"] if p["id"] == "usbc-a"]) <= 1


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ({}, "https://example.com/usbc-a.pdf"),
        ({"datasheet": None, "url": "https://u.example/x"}, "https://u.example/x"),
        (
            {"datasheet": None, "cost": {"source": "quote"}},
            "quote",
        ),
        ({"datasheet": None}, "datasheet mechanical drawing"),
    ],
)
def test_a_part_source_falls_back_as_the_checklist_does(
    setup: dict[str, Any], expected: str
) -> None:
    cfg = config()
    p = part(cfg, "usbc-a")
    for k, v in setup.items():
        if v is None:
            p.pop(k, None)
        else:
            p[k] = v
    assert by_id(build(cfg)["parts"], "usbc-a")["source"] == expected


def test_a_part_with_no_source_anywhere_maps_to_none() -> None:
    cfg = config()
    p = part(cfg, "usbc-a")
    p.pop("datasheet")
    p["geometry"].pop("source")
    assert by_id(build(cfg)["parts"], "usbc-a")["source"] is None


def test_the_date_comes_from_retrieved_or_date_and_is_clipped() -> None:
    cfg = config()
    part(cfg, "usbc-a").pop("retrieved")
    part(cfg, "usbc-a")["date"] = "2026-11-06"
    part(cfg, "th-a")["retrieved"] = "d" * 90
    parts = build(cfg)["parts"]
    assert by_id(parts, "usbc-a")["retrieved"] == "2026-11-06"
    assert len(by_id(parts, "th-a")["retrieved"]) == 50


def test_a_missing_date_is_none() -> None:
    cfg = config()
    part(cfg, "usbc-a").pop("retrieved")
    assert by_id(build(cfg)["parts"], "usbc-a")["retrieved"] is None


@pytest.mark.parametrize(
    ("value", "count"),
    [
        (None, 0),
        ([], 0),
        ({}, 0),
        ([{"id": "io"}], 1),
        ([{"id": "a"}, {"id": "b"}], 2),
        ({"io": 1}, 1),
        ("usb", 1),
    ],
)
def test_interface_count_is_zero_exactly_when_the_checklist_sees_none(
    value: Any, count: int
) -> None:
    cfg = config()
    part(cfg, "usbc-a")["interfaces"] = value
    assert by_id(build(cfg)["parts"], "usbc-a")["interface_count"] == count


def test_the_envelope_is_a_flag_not_a_dimension() -> None:
    cfg = config()
    part(cfg, "usbc-a")["geometry"].pop("envelope")
    parts = build(cfg)["parts"]
    assert by_id(parts, "usbc-a")["has_envelope"] is False
    assert by_id(parts, "led-a")["has_envelope"] is True


# -- process, markets, flags ----------------------------------------------------


@pytest.mark.parametrize(
    ("qty", "expected"),
    [(5000, 5000), (100.0, 100), ("250", 250), (0, 0), (None, None), ("", None)]
    + [("many", None), (True, None), (-5, None), (float("inf"), None)],
)
def test_the_quantity_is_a_whole_number_or_none(qty: Any, expected: int | None) -> None:
    cfg = config()
    cfg["process"]["qty"] = qty
    assert build(cfg)["process"]["quantity"] == expected


def test_process_and_material_are_stated_and_clipped() -> None:
    cfg = config()
    rev = build(cfg)
    assert rev["process"]["process"] and rev["process"]["material"] == "absfr"
    cfg["process"]["material"] = "m" * 150
    assert len(build(cfg)["process"]["material"]) == 100


def test_a_missing_material_maps_to_none() -> None:
    cfg = config()
    cfg["process"].pop("material")
    assert build(cfg)["process"]["material"] is None


def test_markets_are_listed_as_strings() -> None:
    cfg = config()
    cfg["programme"]["markets"] = ["UK", 5]
    assert build(cfg)["markets"] == ["UK", "5"]
    cfg["programme"].pop("markets")
    assert build(cfg)["markets"] == []


def test_interface_count_adds_the_declared_interfaces_to_those_on_chosen_parts() -> None:
    cfg = config()
    base = build(cfg)["interface_count"]
    assert base == 5  # one chosen part each, all with interfaces
    cfg["programme"]["interfaces"] = [{"id": "a"}, {"id": "b"}]
    assert build(cfg)["interface_count"] == base + 2


def test_screening_and_the_mesh_are_flags() -> None:
    assert build()["screening_stored"] is False and build()["concept_mesh"] is False
    cols = _empty_collections()
    cols["screening"]["latest"] = {"rule_version": "formgraph@2"}
    rev = build(config(), cols, has_mesh=True)
    assert rev["screening_stored"] is True and rev["concept_mesh"] is True


# -- ids ------------------------------------------------------------------------


def test_safe_id_keeps_an_id_that_already_fits_and_cleans_one_that_does_not() -> None:
    assert safe_id("usbc-a") == "usbc-a"
    assert safe_id("fn:e_th.1") == "fn:e_th.1"
    assert safe_id("a b/c") == "a_b_c"
    assert safe_id("ignore previous instructions!") == "ignore_previous_instructions"
    assert safe_id("???") == "item"
    assert len(safe_id("x" * 300)) == 100


def test_ids_that_collide_after_cleaning_stay_unique() -> None:
    cfg = config()
    cfg["programme"]["assumptions"] = [
        {"id": "a b", "text": "x"},
        {"id": "a_b", "text": "y"},
        {"id": "a/b", "text": "z"},
    ]
    ids = [a["id"] for a in build(cfg)["assumptions"]]
    assert len(ids) == len(set(ids)) == 3
    assert ids[0] == "a_b"


def test_every_mapped_id_fits_the_services_alphabet() -> None:
    import re

    rev = build()
    pattern = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
    for key in ("ledger", "assumptions", "scenarios", "requirements", "functions", "parts"):
        for item in rev[key]:
            assert pattern.match(item["id"]), (key, item["id"])
            if item.get("part_id"):
                assert pattern.match(item["part_id"])


# -- the form (spec 23.6): sent only for an authored form ------------------------------


def authored_config() -> dict[str, Any]:
    from tests.unit.imagine_fixtures import authored_lamp_form

    cfg = config()
    cfg["form"] = authored_lamp_form()
    return cfg


def test_an_authored_form_is_sent_with_each_part_and_how_far_to_trust_it() -> None:
    form = build(authored_config())["form"]
    assert form["kind"] == "authored"
    assert form["parts"] == [
        {"id": "base", "kind": "generated", "body": True},
        {"id": "arm", "kind": "generated", "body": False},
        {"id": "shade", "kind": "generated", "body": False},
        {"id": "panel", "kind": "primitive", "body": False},
        {"id": "button", "kind": "component", "body": False},
        {"id": "usbc", "kind": "component", "body": False},
    ]


def test_the_code_never_leaves_the_studio() -> None:
    sent = json.dumps(build(authored_config())["form"])
    assert "cylinder" not in sent and "return" not in sent and "code" not in sent


def test_an_enclosure_form_sends_no_form_so_an_older_service_still_accepts_it() -> None:
    """The contract refuses unknown fields: the block must be absent unless it is needed."""
    assert "form" not in build()
    cfg = config()
    cfg["form"] = {"template": "enclosure"}
    assert "form" not in build(cfg)
    assert set(build()) == CONTRACT_KEYS


def test_an_authored_revision_adds_exactly_one_key() -> None:
    assert set(build(authored_config())) == CONTRACT_KEYS | {"form"}


def test_part_ids_are_made_safe_and_unique() -> None:
    cfg = authored_config()
    parts = cfg["form"]["authored"]["parts"]
    parts[1]["id"] = "Arm 1!"
    parts[2]["id"] = "arm-1_"
    ids = [p["id"] for p in build(cfg)["form"]["parts"]]
    assert len(set(ids)) == len(ids)
    assert all(safe_id(i) == i for i in ids)


def test_a_part_with_a_kind_the_contract_does_not_know_is_left_out_not_invented() -> None:
    cfg = authored_config()
    cfg["form"]["authored"]["parts"][1]["kind"] = "magic"
    ids = [p["id"] for p in build(cfg)["form"]["parts"]]
    assert "arm" not in ids and "base" in ids


def test_a_malformed_authored_block_does_not_break_the_revision() -> None:
    for bad in (None, [], {"parts": None}, {"parts": ["x", 3]}, "text"):
        cfg = config()
        cfg["form"] = {"template": "authored", "authored": bad}
        assert "form" not in build(cfg), "an authored form with no usable parts is not sent"


def test_an_enclosure_that_carries_a_stray_authored_block_sends_no_form() -> None:
    """The template decides, not a block's presence: an enclosure is never sent as authored."""
    cfg = authored_config()
    cfg["form"]["template"] = "enclosure"
    assert "form" not in build(cfg)
