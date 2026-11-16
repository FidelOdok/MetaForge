"""A studio's wiring as data: a connection list between real parts' interfaces (FORGE-883).

Live evidence (night-light studio, 2026-11-16): the boards are in the studio, but how they
are wired lives only as words in an architecture decision. The studio has no record of
which pin goes to which, so nothing can draw, check, cost or schematise the wiring.
"""

from __future__ import annotations

from typing import Any

from api_gateway.imagine.formgraph import fg_context


def _iface(iid: str, bus: str, dirn: str, **extra: Any) -> dict[str, Any]:
    return {"id": iid, "bus": bus, "dir": dirn, **extra}


PARTS = [
    {
        "id": "board-a",
        "interfaces": [
            _iface("v5", "pwr", "out", v=[4.75, 5.25], imax_mA=500),
            _iface("gnd", "pwr", "bi", v=[0, 0]),
            _iface("d6", "gpio", "out", level_V=3.3),
            _iface("i2c", "i2c", "bi", pins=["SDA", "SCL"]),
        ],
    },
    {
        "id": "ring-a",
        "interfaces": [
            _iface("pwr", "pwr", "in", v=[4.0, 5.5]),
            _iface("gnd", "pwr", "bi", v=[0, 0]),
            _iface("din", "gpio", "in", v=[3.5, 5.5]),
        ],
    },
    {"id": "lux-a", "interfaces": [_iface("i2c", "i2c", "bi", pins=["SDA", "SCL"])]},
    {
        "id": "lvl-a",
        "interfaces": [
            _iface("a1", "gpio", "in", v=[2.0, 5.5]),
            _iface("y1", "gpio", "out", level_V=5.0),
        ],
    },
]


def _check(
    conns: list[dict[str, Any]], parts: list[dict[str, Any]] | None = None
) -> tuple[list[str], list[str]]:
    ctx = {
        "components": {"parts": parts or PARTS},
        "connections": {"schemaVersion": 1, "connections": conns},
    }
    errors: list[str] = []
    warnings: list[str] = []
    fg_context.validate_connections(ctx, errors, warnings)
    return errors, warnings


def test_a_well_formed_wiring_passes() -> None:
    errors, warnings = _check(
        [
            {"id": "c1", "net": "5V", "from": "board-a.v5", "to": "ring-a.pwr"},
            {"id": "c2", "net": "GND", "from": "board-a.gnd", "to": "ring-a.gnd"},
            {"id": "c3", "net": "DATA_3V3", "from": "board-a.d6", "to": "lvl-a.a1"},
            {"id": "c4", "net": "DATA_5V", "from": "lvl-a.y1", "to": "ring-a.din"},
            {"id": "c5", "net": "SDA", "from": "board-a.i2c.SDA", "to": "lux-a.i2c.SDA"},
            {"id": "c6", "net": "SCL", "from": "board-a.i2c.SCL", "to": "lux-a.i2c.SCL"},
        ]
    )
    assert errors == [] and warnings == []


def test_an_end_that_is_not_a_part_or_interface_is_refused() -> None:
    errors, _ = _check(
        [
            {"id": "c1", "net": "X", "from": "board-a.v5", "to": "nope.pwr"},
            {"id": "c2", "net": "Y", "from": "board-a.nope", "to": "ring-a.pwr"},
            {"id": "c3", "net": "Z", "from": "board-a.i2c.MISO", "to": "lux-a.i2c.SDA"},
        ]
    )
    assert any("nope" in e and "part" in e for e in errors)
    assert any("board-a.nope" in e for e in errors)
    assert any("MISO" in e for e in errors)


def test_buses_that_do_not_match_are_named() -> None:
    # a rail into an output is a mismatch; a rail into an input is a tie-off (below)
    _, warnings = _check([{"id": "c1", "net": "X", "from": "board-a.v5", "to": "board-a.d6"}])
    assert any("pwr" in w and "gpio" in w for w in warnings)
    _, warnings = _check([{"id": "c1", "net": "X", "from": "board-a.d6", "to": "lux-a.i2c.SDA"}])
    assert any("like to like" in w for w in warnings)


def test_an_input_tied_to_a_rail_or_ground_is_not_a_mismatch() -> None:
    # a datasheet asks unused and enable inputs to be held at VCC or GND
    _, warnings = _check(
        [
            {"id": "k1", "net": "GND", "from": "board-a.gnd", "to": "lvl-a.a1"},
            {"id": "k2", "net": "5V", "from": "board-a.v5", "to": "ring-a.din"},
        ]
    )
    assert warnings == []


def test_a_tie_off_above_the_inputs_window_is_named() -> None:
    parts = [*PARTS, {"id": "low", "interfaces": [_iface("en", "gpio", "in", v=[0, 3.6])]}]
    _, warnings = _check([{"id": "k1", "net": "5V", "from": "board-a.v5", "to": "low.en"}], parts)
    assert any("k1" in w and "low.en" in w and "window" in w for w in warnings)


def test_two_outputs_driving_each_other_are_named() -> None:
    _, warnings = _check([{"id": "c1", "net": "X", "from": "board-a.d6", "to": "lvl-a.y1"}])
    assert any("two outputs" in w for w in warnings)


def test_a_3v3_signal_into_a_5v_input_is_named() -> None:
    # the ring's data input needs 3.5 V (70 % of 5 V): the reason for the level shifter
    _, warnings = _check([{"id": "c1", "net": "DATA", "from": "board-a.d6", "to": "ring-a.din"}])
    assert any("3.3 V" in w and "ring-a.din" in w for w in warnings)


def test_a_pin_on_two_different_nets_is_refused() -> None:
    errors, _ = _check(
        [
            {"id": "c1", "net": "A", "from": "board-a.d6", "to": "lvl-a.a1"},
            {"id": "c2", "net": "B", "from": "board-a.d6", "to": "ring-a.din"},
        ]
    )
    assert any("board-a.d6" in e and "two nets" in e for e in errors)


def test_a_multi_pin_interface_without_a_pin_is_named() -> None:
    _, warnings = _check([{"id": "c1", "net": "I2C", "from": "board-a.i2c", "to": "lux-a.i2c"}])
    assert any("pin" in w for w in warnings)


def test_no_connection_list_is_no_finding() -> None:
    # negative control: a studio without wiring is not an error
    ctx = {"components": {"parts": PARTS}}
    errors: list[str] = []
    warnings: list[str] = []
    fg_context.validate_connections(ctx, errors, warnings)
    assert errors == [] and warnings == []


def test_the_connection_list_reaches_the_page() -> None:
    ctx = {
        "components": {"schemaVersion": 1, "parts": []},
        "connections": {
            "schemaVersion": 1,
            "connections": [{"id": "c1", "net": "5V", "from": "a.v", "to": "b.v"}],
        },
    }
    assert "connections" in fg_context.FILES
    compiled = fg_context.compile_context({"process": {"id": "fdm"}}, ctx)
    assert compiled["_context"]["connections"][0]["net"] == "5V"


def test_interface_pins_must_be_distinct_names() -> None:
    errors: list[str] = []
    warnings: list[str] = []
    fg_context.validate_interfaces(
        "part x",
        {"interfaces": [_iface("i2c", "i2c", "bi", pins=["SDA", "SDA"])]},
        errors,
        warnings,
    )
    assert any("pins" in e for e in errors)


async def test_a_studio_write_keeps_and_checks_the_wiring() -> None:
    from api_gateway.imagine.service import ImagineService
    from api_gateway.operations import publisher as ops
    from tests.unit.imagine_fixtures import full_config
    from twin_core.api import InMemoryTwinAPI

    ops.init_operations(None)
    try:
        cfg = full_config()
        cfg["context"]["connections"] = {
            "schemaVersion": 1,
            "connections": [
                {"id": "c1", "net": "SDA", "from": "th-a.io", "to": "th-b.io"},
                {"id": "c2", "net": "BTN", "from": "btn-a.io", "to": "usbc-a.io"},
            ],
        }
        svc = ImagineService(InMemoryTwinAPI.create())
        out = await svc.put_studio("7d1f1b5e-5d2a-4c4e-9a59-0d3f8f4a2c12", cfg, actor="test")
        assert len(out.config["context"]["connections"]["connections"]) == 2
        # gpio to usb: a mismatch is named, not refused
        assert any("c2" in w and "like to like" in w for w in out.warnings)
    finally:
        ops.init_operations(None)
