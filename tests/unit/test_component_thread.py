"""One component followed through Imagine, Develop, Check and Make (FORGE-903)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from twin_core.component_thread import (
    BoardPart,
    MakeLine,
    Selection,
    StudioPart,
    build_threads,
    latest_selections,
    link_selection,
    mpn_key,
    same_family,
    summary,
)

PID = "7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12"


def _lamp_parts() -> list[StudioPart]:
    return [
        StudioPart("al8862-warm", "Driver, warm channel", "fn:e_ldrv", "AL8862", "Diodes"),
        StudioPart("al8862-cool", "Driver, cool channel", "fn:e_ldrv", "AL8862", "Diodes"),
        StudioPart("opt3001", "Ambient light sensor", "fn:e_als", "OPT3001", "TI"),
        StudioPart(
            "veml7700",
            "Ambient light sensor, budget",
            "fn:e_als",
            "VEML7700",
            "Vishay",
            chosen=False,
        ),
        StudioPart("status-led", "Status indicator", "fn:e_led", "<order code not yet chosen>"),
    ]


def _by_key(threads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {t["key"]: t for t in threads}


# ── matching ───────────────────────────────────────────────────────────────


def test_order_codes_compare_on_letters_and_digits_and_a_family_extends_its_stem() -> None:
    assert mpn_key("AL8862SP-13") == "AL8862SP13"
    assert mpn_key("<order code not yet chosen>") == ""
    assert same_family("AL8862", "AL8862SP-13")
    assert same_family("al8862sp-13", "AL8862SP13")
    assert not same_family("OPT3001", "OPT3002")
    # A stem too short to stand for a family matches only itself.
    assert not same_family("AL8", "AL8862")
    assert not same_family("", "AL8862")


def test_a_stored_link_wins_over_order_code_and_role() -> None:
    sel = Selection("n1", "XYZ-1", role="fn:e_als", studio_parts=["al8862-cool"])
    assert link_selection(sel, _lamp_parts()) == (["al8862-cool"], "stored")


def test_one_driver_order_code_fills_both_channels() -> None:
    pids, how = link_selection(Selection("n1", "AL8862SP-13"), _lamp_parts())
    assert (sorted(pids), how) == (["al8862-cool", "al8862-warm"], "mpn")


def test_a_role_links_only_when_exactly_one_chosen_studio_part_has_it() -> None:
    # Two chosen parts share fn:e_ldrv: the role alone cannot say which.
    assert link_selection(Selection("n1", "LM3414", role="e_ldrv"), _lamp_parts()) == ([], "")
    # fn:e_als has one chosen part (the budget one is an alternative nobody took).
    assert link_selection(Selection("n1", "TSL2591", role="fn:e_als"), _lamp_parts()) == (
        ["opt3001"],
        "role",
    )


def test_a_stored_link_to_a_part_the_studio_dropped_is_ignored() -> None:
    sel = Selection("n1", "OPT3001DNPR", studio_parts=["gone"])
    assert link_selection(sel, _lamp_parts()) == (["opt3001"], "mpn")


def test_the_newest_selection_for_a_role_is_the_current_one() -> None:
    old = Selection("old", "AL8862", role="led_driver", recorded_at="2026-11-20T00:00:00")
    new = Selection("new", "AL8862SP-13", role="led_driver", recorded_at="2026-11-21T00:00:00")
    other = Selection("x", "OPT3001")
    assert {s.node_id for s in latest_selections([new, old, other])} == {"new", "x"}


# ── the threads ────────────────────────────────────────────────────────────


def _lamp_threads(studio: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    sels = [
        Selection(
            "bom-1",
            "AL8862SP-13",
            "Diodes Incorporated",
            role="led_driver",
            library={"id": "co_1", "name": "diodes_al8862sp_13", "shared": False},
            margins=2,
        ),
        Selection("bom-2", "OPT3001", "TI", role="fn:e_als", violates=["lux_range"], margins=1),
        Selection("bom-3", "ESP32-C3-WROOM-02", "Espressif", role="mcu"),
    ]
    return build_threads(
        studio_parts=_lamp_parts(),
        studio=studio or {"status": "draft", "revision": 5},
        selections=sels,
        board_parts=[
            BoardPart("LED board", "D1", "LM301B", "Emitter"),
            BoardPart("LED board", "D2", "LM301B", "Emitter"),
            BoardPart("Main board", "U3", "AL8862SP-13"),
            BoardPart("Main board", "U4", "AL8862SP-13"),
        ],
        boards=["LED board", "Main board"],
        make_lines=[
            MakeLine("bom-file", "bom", "AL8862SP-13", 2, ["U3", "U4"], board="Main board"),
            MakeLine("bom-file", "bom", "Emitter", 2, ["D1", "D2"], board="LED board"),
            MakeLine("po-1", "procurement", "OPT3001DNPR", 25, text="Texas Instruments"),
            MakeLine("po-1", "procurement", "LED board PCB", 25, text="LED board, 2 layer"),
        ],
        component_ids={"al8862-warm": "CMP-driver-warm-channel"},
    )


def test_a_refined_part_carries_its_whole_thread() -> None:
    t = _by_key(_lamp_threads())["part:al8862-warm"]
    assert t["relation"] == "refined"
    assert t["imagine"]["status"] == "chosen"
    assert t["imagine"]["component_id"] == "CMP-driver-warm-channel"
    assert t["develop"]["status"] == "selected"
    assert t["develop"]["linked_by"] == "mpn"
    assert t["develop"]["selections"][0]["library"]["name"] == "diodes_al8862sp_13"
    assert {(b["board"], b["ref"]) for b in t["develop"]["boards"]} == {
        ("Main board", "U3"),
        ("Main board", "U4"),
    }
    assert t["check"] == {"status": "pass", "violates": [], "margins": 2, "unchecked": 0}
    assert t["make"]["status"] == "in_bom"
    assert t["adopt"] == {
        "kind": "part",
        "node_id": "bom-1",
        "mpn": "AL8862SP-13",
        "manufacturer": "Diodes Incorporated",
        "library": "diodes_al8862sp_13",
        "datasheet": None,
        "recorded_at": None,
        "needs_rework": False,
    }
    # The other channel is the same selection, so the same thread content.
    assert (
        _by_key(_lamp_threads())["part:al8862-cool"]["develop"]["selections"][0]["node_id"]
        == "bom-1"
    )


def test_check_and_make_follow_the_selection() -> None:
    t = _by_key(_lamp_threads())["part:opt3001"]
    assert t["relation"] == "same"
    assert t["check"]["status"] == "violates"
    assert t["check"]["violates"] == ["lux_range"]
    assert t["make"]["status"] == "ordered"
    assert t["adopt"] is None


def test_what_only_one_stage_knows_gets_its_own_thread() -> None:
    threads = _by_key(_lamp_threads())
    # Develop chose a radio the studio never named.
    radio = next(t for k, t in threads.items() if k.startswith("selection:"))
    assert radio["relation"] == "develop_only"
    assert radio["imagine"] == {"status": "absent"}
    assert radio["adopt"]["kind"] == "part"
    # The studio's status LED has no Develop part yet.
    assert threads["part:status-led"]["relation"] == "imagine_only"
    assert threads["part:status-led"]["develop"]["status"] == "absent"
    # The emitters are on a board with no selection: grouped by order code.
    emitters = threads["board-part:LM301B"]
    assert [b["ref"] for b in emitters["develop"]["boards"]] == ["D1", "D2"]
    assert emitters["make"]["status"] == "in_bom"
    # An alternative nobody took up is not a component.
    assert "part:veml7700" not in threads


def test_a_board_is_a_thread_but_is_never_written_into_the_studio() -> None:
    led = _by_key(_lamp_threads())["board:LED board"]
    assert led["develop"]["status"] == "designed"
    assert led["make"]["status"] == "ordered"
    # The studio's parts are bought parts with a source; a board is designed.
    assert led["adopt"] is None


def test_a_handed_over_studio_needs_reopening_to_adopt() -> None:
    t = _by_key(_lamp_threads(studio={"status": "handed_over", "revision": 6}))["part:al8862-warm"]
    assert t["imagine"]["handed_over"] is True
    assert t["adopt"]["needs_rework"] is True


def test_a_different_part_for_the_role_is_a_replacement() -> None:
    sel = Selection("n9", "TSL2591", role="fn:e_als")
    t = _by_key(build_threads(studio_parts=_lamp_parts(), selections=[sel]))["part:opt3001"]
    assert t["relation"] == "replaced"
    assert t["adopt"]["mpn"] == "TSL2591"


def test_summary_counts_every_relation() -> None:
    counts = summary(_lamp_threads())
    assert counts["refined"] == 2
    assert counts["same"] == 1
    assert counts["imagine_only"] == 1
    assert counts["develop_only"] == 4  # the radio, the emitters, two boards


# ── concept assembly keeps the studio part ────────────────────────────────


def test_a_concept_assembly_part_keeps_its_studio_part() -> None:
    from orchestrator.design_flow.context import parse_concept_assembly

    asm = parse_concept_assembly(
        {
            "parts": [
                {
                    "name": "Driver",
                    "source": "bought",
                    "studio_part": "al8862-warm",
                    "role": "fn:e_ldrv",
                    "mpn": "AL8862",
                },
                {"name": "Shell"},
            ],
            "connections": [],
        }
    )
    driver, shell = (p.as_metadata() for p in asm.parts)
    assert driver["studio_part"] == "al8862-warm"
    assert driver["mpn"] == "AL8862"
    assert "studio_part" not in shell and "library" not in shell


# ── through the gateway ───────────────────────────────────────────────────


@pytest.fixture
def catalog() -> Any:
    from api_gateway.library import catalog_routes
    from api_gateway.operations import publisher as ops
    from twin_core.library.catalog import SqliteCatalog

    ops.init_operations(None)
    catalog_routes.init_catalog(SqliteCatalog())
    yield catalog_routes.get_catalog()
    catalog_routes.init_catalog(None)
    ops.init_operations(None)


async def _studio(twin: Any) -> Any:
    from api_gateway.imagine.service import ImagineService
    from tests.unit.imagine_fixtures import full_config

    svc = ImagineService(twin)
    await svc.put_studio(PID, copy.deepcopy(full_config()), actor="test")
    return svc


async def test_a_selection_is_stored_with_its_studio_parts_and_adopted_back(catalog: Any) -> None:
    from api_gateway.imagine.service import ImagineError
    from api_gateway.twin.component_recorder import make_component_recorder
    from api_gateway.twin.component_thread import gather_threads
    from twin_core.api import InMemoryTwinAPI

    catalog.register(
        "component",
        {
            "name": "example_lte_a_01x",
            "family": "electronic",
            "origin": "purchased",
            "mpn": "LTE-A-01X",
            "manufacturer": "Example Parts",
            "datasheet": "https://example.com/lte-a-01x.pdf",
        },
    )
    twin = InMemoryTwinAPI.create()
    svc = await _studio(twin)
    record = make_component_recorder(twin)
    out = await record(
        mpn="LTE-A-01X",
        manufacturer="Example Parts",
        category="cellular_module",
        purchase_unit="discrete_part",
        role="radio",
        project_id=PID,
    )
    # The link is decided when the part is recorded, and stored with it.
    assert out["thread"]["studio_parts"] == ["lte-a"]
    assert out["thread"]["linked_by"] == "mpn"
    assert out["thread"]["library"]["name"] == "example_lte_a_01x"
    items = await twin.list_bom_items()
    assert items[0].specifications["thread"]["studio_parts"] == ["lte-a"]

    data = await gather_threads(twin, PID, studio_service=svc)
    t = {x["key"]: x for x in data["threads"]}["part:lte-a"]
    assert t["relation"] == "refined"
    assert t["develop"]["linked_by"] == "stored"
    assert data["studio"] == {"status": "draft", "revision": 1}

    # A stale revision is refused and writes nothing.
    with pytest.raises(ImagineError) as stale:
        await svc.adopt_component(PID, 9, t["adopt"], actor="test")
    assert stale.value.status == 409

    studio = await svc.adopt_component(
        PID, 1, {**t["adopt"], "part_id": "lte-a"}, actor="person:test"
    )
    assert studio.revision == 2
    part = next(p for p in studio.config["context"]["components"]["parts"] if p["id"] == "lte-a")
    assert part["mpn"] == "LTE-A-01X"
    assert part["library"] == "example_lte_a_01x"
    assert part["adopted"]["from"] == "develop"

    after = await gather_threads(twin, PID, studio_service=svc)
    t = {x["key"]: x for x in after["threads"]}["part:lte-a"]
    assert t["relation"] == "same"
    assert t["adopt"] is None


async def test_naming_a_studio_part_that_does_not_exist_is_refused(catalog: Any) -> None:
    from api_gateway.twin.component_recorder import make_component_recorder
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()
    await _studio(twin)
    record = make_component_recorder(twin)
    with pytest.raises(ValueError, match="not parts of the studio"):
        await record(
            mpn="X-1",
            manufacturer="Example Parts",
            category="sensor",
            purchase_unit="discrete_part",
            project_id=PID,
            studio_parts=["nope"],
        )
    assert await twin.list_bom_items() == []


async def test_a_board_is_refused_and_a_develop_only_part_is_added(catalog: Any) -> None:
    from api_gateway.imagine.service import ImagineError
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()
    svc = await _studio(twin)
    with pytest.raises(ImagineError) as board:
        await svc.adopt_component(PID, 1, {"kind": "board", "board": "LED board"}, actor="t")
    assert board.value.status == 400

    studio = await svc.adopt_component(
        PID,
        1,
        {
            "kind": "part",
            "node_id": "n1",
            "mpn": "TPS62160DGKR",
            "manufacturer": "Texas Instruments",
            "datasheet": "https://www.ti.com/lit/gpn/tps62160",
            "recorded_at": "2026-11-21T10:00:00+00:00",
            "role": "buck",
            "name": "Buck converter",
        },
        actor="person:test",
    )
    part = next(
        p for p in studio.config["context"]["components"]["parts"] if p["mpn"] == "TPS62160DGKR"
    )
    assert part["role"] == "other"  # "buck" is no studio role
    assert part["retrieved"] == "2026-11-21"
    assert part["adopted"]["node_id"] == "n1"
    # A part whose role the studio does not choose between is in, not an alternative.
    from api_gateway.twin.component_thread import studio_parts_of

    head = await svc._head(PID)
    assert head is not None
    by_id = {p.id: p for p in studio_parts_of(head.state)}
    assert by_id[part["id"]].chosen is True
    assert by_id["th-b"].chosen is False  # the studio picked th-a for this role


async def test_without_a_studio_a_selection_is_recorded_unlinked(catalog: Any) -> None:
    from api_gateway.twin.component_recorder import make_component_recorder
    from api_gateway.twin.component_thread import gather_threads
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()
    out = await make_component_recorder(twin)(
        mpn="OPT3001",
        manufacturer="TI",
        category="light_sensor",
        purchase_unit="discrete_part",
        project_id=PID,
    )
    assert "thread" not in out
    data = await gather_threads(twin, PID)
    assert [t["relation"] for t in data["threads"]] == ["develop_only"]
    assert data["studio"] is None
