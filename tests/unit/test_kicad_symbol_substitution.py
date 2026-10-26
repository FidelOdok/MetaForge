"""A part drawn with another part's symbol is reported, never silent (FORGE-699).

A sandbox run drew a Bosch BMI270 with KiCad's BMI160 symbol and nothing said
so. ERC and the netlist match pass on the stand-in's own pins, so the
substitution has to be named for a person to check against the datasheet.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.test_kicad_generation import LDO_LED, SYMBOLS
from tool_registry.tools.kicad import generation, sexpr
from tool_registry.tools.kicad.circuit import (
    Circuit,
    symbol_matches_part,
    symbol_substitutions,
)
from tool_registry.tools.kicad.schematic_gen import SymbolLibrary, generate_schematic


@pytest.mark.parametrize(
    ("symbol", "part"),
    [
        ("MCU_ST_STM32F4:STM32F407VGTx", "STM32F407VGT6"),
        ("MCU_ST_STM32F4:STM32F407VGTx", "STM32F407"),
        ("Sensor_Pressure:LPS22HB", "LPS22HBTR"),
        ("Battery_Management:MCP73831-2-OT", "MCP73831T-2ACI/OT"),
        ("Regulator_Linear:AMS1117-3.3", "AMS1117-3.3"),
        ("Power_Protection:USBLC6-2SC6", "usblc6-2sc6"),
    ],
)
def test_a_symbol_named_for_the_part_matches(symbol: str, part: str) -> None:
    assert symbol_matches_part(symbol, part)


@pytest.mark.parametrize(
    ("symbol", "part"),
    [
        ("Sensor_Motion:BMI160", "BMI270"),
        ("MCU_ST_STM32F1:STM32F103C8Tx", "STM32F407VGT6"),
        ("Sensor_Pressure:LPS22HB", "LPS22HH"),
        ("Regulator_Linear:AMS1117-3.3", "LM1117MPX-3.3"),
    ],
)
def test_a_symbol_named_for_another_part_does_not(symbol: str, part: str) -> None:
    assert not symbol_matches_part(symbol, part)


def _imu(**extra: Any) -> dict[str, Any]:
    return {
        "ref": "U2",
        "symbol": "Sensor_Motion:BMI160",
        "footprint": "Package_LGA:Bosch_LGA-14_3x2.5mm_P0.5mm",
        "value": "BMI270",
        "fields": {"MPN": "BMI270", "Manufacturer": "Bosch"},
        **extra,
    }


def _circuit(*components: dict[str, Any]) -> Circuit:
    return Circuit.model_validate({"name": "imu", "components": list(components), "nets": []})


def test_the_bmi270_drawn_as_a_bmi160_is_an_unconfirmed_substitution() -> None:
    subs = symbol_substitutions(_circuit(_imu()))
    assert len(subs) == 1
    sub = subs[0]
    assert sub["ref"] == "U2"
    assert sub["part_number"] == "BMI270"
    assert sub["symbol"] == "Sensor_Motion:BMI160"
    assert sub["confirmed"] is False
    assert "BMI270 datasheet" in sub["warning"]


def test_a_recorded_pinout_check_confirms_it_and_drops_the_warning() -> None:
    note = "all 14 pins compared with the BMI270 datasheet pin table"
    sub = symbol_substitutions(_circuit(_imu(symbol_pinout_checked=note)))[0]
    assert sub["confirmed"] is True
    assert sub["pinout_check"] == note
    assert "warning" not in sub


def test_a_blank_pinout_check_does_not_confirm() -> None:
    sub = symbol_substitutions(_circuit(_imu(symbol_pinout_checked="  ")))[0]
    assert sub["confirmed"] is False


def test_the_mpn_field_wins_over_the_value() -> None:
    # Value names the stand-in, the MPN the ordered part: still a substitution.
    comp = _imu(value="BMI160")
    assert symbol_substitutions(_circuit(comp))[0]["part_number"] == "BMI270"


def test_generic_symbols_and_parts_without_a_number_are_not_substitutions() -> None:
    circuit = _circuit(
        {
            "ref": "R1",
            "symbol": "Device:R",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
            "value": "10k",
            "fields": {"MPN": "RC0603FR-0710KL"},
        },
        {
            "ref": "J1",
            "symbol": "Connector_Generic:Conn_01x04",
            "footprint": "Connector_JST:JST_PH_B4B-PH-K_1x04_P2.00mm_Vertical",
            "value": "SWD",
            "fields": {"MPN": "B4B-PH-K-S"},
        },
        {
            "ref": "U3",
            "symbol": "Sensor_Motion:BMI160",
            "footprint": "Package_LGA:Bosch_LGA-14_3x2.5mm_P0.5mm",
            "value": "IMU",
        },
    )
    assert symbol_substitutions(circuit) == []


# -- the schematic and the tool result ------------------------------------------


def _ldo_substituted(**extra: Any) -> Circuit:
    data = copy.deepcopy(LDO_LED)
    u1 = next(c for c in data["components"] if c["ref"] == "U1")
    u1["value"] = "LM1117MPX-3.3"
    u1["fields"] = {"MPN": "LM1117MPX-3.3/NOPB"}
    u1.update(extra)
    return Circuit.model_validate(data)


def _symbol_props(path: Path, ref: str) -> dict[str, str]:
    tree = sexpr.loads(path.read_text(encoding="utf-8"))
    for sym in sexpr.children(tree, "symbol"):
        props = {str(p[1]): str(p[2]) for p in sexpr.children(sym, "property")}
        if props.get("Reference") == ref:
            return props
    raise AssertionError(f"{ref} not in schematic")


def test_the_schematic_names_the_stand_in_and_keeps_the_real_mpn(tmp_path: Path) -> None:
    out = tmp_path / "ldo.kicad_sch"
    gen = generate_schematic(_ldo_substituted(), str(out), SymbolLibrary(str(SYMBOLS)))
    assert [s["ref"] for s in gen.substitutions] == ["U1"]
    props = _symbol_props(out, "U1")
    assert props["MPN"] == "LM1117MPX-3.3/NOPB"
    assert props["Value"] == "LM1117MPX-3.3"
    assert props["Symbol_Substitute"].startswith(
        "Regulator_Linear:AMS1117-3.3 drawn for LM1117MPX-3.3/NOPB"
    )
    assert "not yet checked" in props["Symbol_Substitute"]
    # A part drawn with its own symbol carries no such field.
    assert "Symbol_Substitute" not in _symbol_props(out, "C1")


def test_the_original_circuit_has_no_substitution(tmp_path: Path) -> None:
    gen = generate_schematic(
        Circuit.model_validate(LDO_LED), str(tmp_path / "a.kicad_sch"), SymbolLibrary(str(SYMBOLS))
    )
    assert gen.substitutions == []


class _Server:
    def __init__(self, work_dir: Path) -> None:
        self.config = SimpleNamespace(symbol_dir=str(SYMBOLS), work_dir=str(work_dir))

    async def _execute_erc(self, path: str, severity: str) -> dict[str, Any]:
        return {"errors": 0, "warnings": 0, "violations": [], "passed": True}


@pytest.mark.asyncio
async def test_the_tool_result_warns_and_counts_until_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def matched(server: Any, circuit: Circuit, sch: Path) -> dict[str, Any]:
        return {"matched": [n.name for n in circuit.nets], "mismatched": []}

    monkeypatch.setattr(generation, "_netlist_check", matched)
    server: Any = _Server(tmp_path)

    res = await generation._schematic(server, _ldo_substituted(), tmp_path)
    # ERC and the netlist pass on the stand-in's pins; the verdict says only that.
    assert res["verdict"] == "pass"
    assert res["unconfirmed_symbol_substitutions"] == 1
    assert res["symbol_substitutions"][0]["symbol"] == "Regulator_Linear:AMS1117-3.3"
    assert len(res["warnings"]) == 1 and "U1" in res["warnings"][0]

    checked = _ldo_substituted(symbol_pinout_checked="pins 1-3 and tab match the LM1117 datasheet")
    folder = tmp_path / "again"
    folder.mkdir()
    res = await generation._schematic(server, checked, folder)
    assert res["unconfirmed_symbol_substitutions"] == 0
    assert res["warnings"] == []
    assert res["symbol_substitutions"][0]["confirmed"] is True
