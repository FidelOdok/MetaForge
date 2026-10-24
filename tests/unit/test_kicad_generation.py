"""Schematic generation from a circuit description (FORGE-683).

Uses symbols cut verbatim from KiCad 9's own libraries
(``tests/unit/fixtures/kicad/symbols/``), including two that ``extends`` a
parent (AMS1117-3.3 -> AP1117-15, LM358 -> LM2904, the latter three units).
KiCad's own checks on the output -- ERC and the exported netlist -- run in
``tests/integration/test_kicad_generation_live.py`` in the kicad image.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tool_registry.tools.kicad import sexpr
from tool_registry.tools.kicad.circuit import Circuit
from tool_registry.tools.kicad.schematic_gen import (
    SymbolLibrary,
    SymbolNotFoundError,
    generate_schematic,
    symbol_pins,
)

SYMBOLS = Path(__file__).parent / "fixtures" / "kicad" / "symbols"

LDO_LED: dict[str, Any] = {
    "name": "ldo_led",
    "components": [
        {
            "ref": "J1",
            "symbol": "Connector_Generic:Conn_01x02",
            "footprint": "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical",
            "value": "BATT",
        },
        {
            "ref": "U1",
            "symbol": "Regulator_Linear:AMS1117-3.3",
            "footprint": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
            "value": "AMS1117-3.3",
            "fields": {"MPN": "AMS1117-3.3"},
        },
        {
            "ref": "C1",
            "symbol": "Device:C",
            "footprint": "Capacitor_SMD:C_0805_2012Metric",
            "value": "10uF",
        },
        {
            "ref": "C2",
            "symbol": "Device:C",
            "footprint": "Capacitor_SMD:C_0805_2012Metric",
            "value": "22uF",
        },
        {
            "ref": "R1",
            "symbol": "Device:R",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
            "value": "1k",
        },
        {
            "ref": "D1",
            "symbol": "Device:LED",
            "footprint": "LED_SMD:LED_0603_1608Metric",
            "value": "green",
        },
    ],
    "nets": [
        {"name": "VIN", "nodes": ["J1.1", "U1.3", "C1.1"]},
        {"name": "GND", "nodes": ["J1.2", "U1.1", "C1.2", "C2.2", "D1.1"]},
        {"name": "+3V3", "nodes": ["U1.2", "C2.1", "R1.1"]},
        {"name": "LED_A", "nodes": ["R1.2", "D1.2"]},
    ],
}


def _lib() -> SymbolLibrary:
    return SymbolLibrary(str(SYMBOLS))


def _generate(tmp_path: Path, circuit: dict[str, Any]) -> tuple[Any, list[Any]]:
    c = Circuit.model_validate(circuit)
    result = generate_schematic(c, str(tmp_path / f"{c.name}.kicad_sch"), _lib())
    return result, sexpr.loads(Path(result.path).read_text())


class TestSymbolLibrary:
    def test_extends_is_flattened_with_the_child_properties(self) -> None:
        sym = _lib().symbol("Regulator_Linear:AMS1117-3.3")
        assert sym[1] == "Regulator_Linear:AMS1117-3.3"
        assert sexpr.find(sym, "extends") is None
        props = {str(p[1]): str(p[2]) for p in sexpr.children(sym, "property")}
        assert props["Value"] == "AMS1117-3.3"
        pins = {p.number: (p.name, p.etype) for p in symbol_pins(sym)}
        assert pins == {"1": ("GND", "power_in"), "2": ("VO", "power_out"), "3": ("VI", "power_in")}
        units = [str(u[1]) for u in sexpr.children(sym, "symbol")]
        assert all(u.startswith("AMS1117-3.3_") for u in units)

    def test_multi_unit_symbol(self) -> None:
        pins = symbol_pins(_lib().symbol("Amplifier_Operational:LM358"))
        assert {p.unit for p in pins} == {1, 2, 3}
        assert {p.number for p in pins if p.unit == 3} == {"4", "8"}

    def test_unknown_symbol_and_library_are_refused(self) -> None:
        with pytest.raises(SymbolNotFoundError, match="Device:NoSuch is not in the Device"):
            _lib().symbol("Device:NoSuch")
        with pytest.raises(SymbolNotFoundError, match="no symbol library 'Nope'"):
            _lib().symbol("Nope:R")


class TestGenerateSchematic:
    def test_every_node_gets_a_label_on_its_pin(self, tmp_path: Path) -> None:
        result, tree = _generate(tmp_path, LDO_LED)
        assert result.symbols_placed == 6 + 2  # parts and two PWR_FLAGs
        assert sorted(result.power_flags) == ["GND", "VIN"]  # VO drives +3V3
        assert result.no_connects == 0
        # Every pin of every instance is a label of its net, exactly on the pin.
        lib = {str(s[1]): s for s in sexpr.children(sexpr.find(tree, "lib_symbols"), "symbol")}
        labels = {
            (round(float(sexpr.find(g, "at")[1]), 3), round(float(sexpr.find(g, "at")[2]), 3)): str(
                g[1]
            )
            for g in sexpr.children(tree, "global_label")
        }
        net_of = Circuit.model_validate(LDO_LED).net_of()
        for inst in sexpr.children(tree, "symbol"):
            ref = next(str(p[2]) for p in sexpr.children(inst, "property") if p[1] == "Reference")
            if ref.startswith("#"):
                continue
            at = sexpr.find(inst, "at")
            ox, oy = float(at[1]), float(at[2])
            for pin in symbol_pins(lib[sexpr.value(inst, "lib_id")]):
                spot = (round(ox + pin.x, 3), round(oy - pin.y, 3))
                assert labels.get(spot) == net_of[f"{ref}.{pin.number}"], (ref, pin.number)

    def test_symbols_are_embedded_and_fields_kept(self, tmp_path: Path) -> None:
        _result, tree = _generate(tmp_path, LDO_LED)
        names = {str(s[1]) for s in sexpr.children(sexpr.find(tree, "lib_symbols"), "symbol")}
        assert names == {
            "Connector_Generic:Conn_01x02",
            "Regulator_Linear:AMS1117-3.3",
            "Device:C",
            "Device:R",
            "Device:LED",
            "power:PWR_FLAG",
        }
        u1 = next(
            s
            for s in sexpr.children(tree, "symbol")
            if any(p[1] == "Reference" and p[2] == "U1" for p in sexpr.children(s, "property"))
        )
        props = {str(p[1]): str(p[2]) for p in sexpr.children(u1, "property")}
        assert props["Footprint"] == "Package_TO_SOT_SMD:SOT-223-3_TabPin2"
        assert props["MPN"] == "AMS1117-3.3"

    def test_project_files_name_the_libraries(self, tmp_path: Path) -> None:
        _generate(tmp_path, LDO_LED)
        sym = (tmp_path / "sym-lib-table").read_text()
        fp = (tmp_path / "fp-lib-table").read_text()
        assert '"Regulator_Linear"' in sym and "Regulator_Linear.kicad_sym" in sym
        assert '"Package_TO_SOT_SMD"' in fp and "Package_TO_SOT_SMD.pretty" in fp
        assert (tmp_path / "ldo_led.kicad_pro").is_file()

    def test_multi_unit_part_places_every_unit_and_flags_unused_pins(self, tmp_path: Path) -> None:
        circuit = {
            "name": "buffer",
            "components": [
                {
                    "ref": "U1",
                    "symbol": "Amplifier_Operational:LM358",
                    "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                    "value": "LM358",
                },
            ],
            "nets": [
                {"name": "OUT", "nodes": ["U1.1", "U1.2"]},
                {"name": "IN", "nodes": ["U1.3"]},
                {"name": "+5V", "nodes": ["U1.8"]},
                {"name": "GND", "nodes": ["U1.4"]},
            ],
        }
        result, tree = _generate(tmp_path, circuit)
        units = sorted(
            int(sexpr.value(s, "unit"))
            for s in sexpr.children(tree, "symbol")
            if any(p[1] == "Reference" and p[2] == "U1" for p in sexpr.children(s, "property"))
        )
        assert units == [1, 2, 3]
        assert result.no_connects == 3  # the unused second op-amp: pins 5, 6, 7
        assert sorted(result.power_flags) == ["+5V", "GND"]

    def test_a_pin_the_symbol_does_not_have_is_refused(self, tmp_path: Path) -> None:
        bad = {**LDO_LED, "nets": [{"name": "X", "nodes": ["U1.9"]}]}
        with pytest.raises(ValueError, match=r"U1\.9: Regulator_Linear:AMS1117-3.3 has no pin '9'"):
            _generate(tmp_path, bad)


class TestCircuitModel:
    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"components": LDO_LED["components"] * 2}, "duplicate references"),
            (
                {"nets": [{"name": "A", "nodes": ["U1.1"]}, {"name": "B", "nodes": ["U1.1"]}]},
                "is on both A and B",
            ),
            ({"nets": [{"name": "A", "nodes": ["U9.1"]}]}, "unknown component U9"),
            ({"nets": [{"name": "A", "nodes": ["U1"]}]}, "not REF.PIN"),
        ],
    )
    def test_inconsistent_circuits_are_refused(self, change: dict, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            Circuit.model_validate({**LDO_LED, **change})

    def test_inner_planes_need_a_four_layer_board(self) -> None:
        """FORGE-685: planes on In1/In2, at most two, only on a 4-layer board."""
        board = {"width_mm": 40, "height_mm": 40}
        with pytest.raises(ValueError, match="inner_planes needs a 4-layer board"):
            Circuit.model_validate({**LDO_LED, "board": {**board, "inner_planes": ["GND"]}})
        with pytest.raises(ValueError, match="at most two"):
            Circuit.model_validate(
                {**LDO_LED, "board": {**board, "layers": 4, "inner_planes": ["GND", "A", "B"]}}
            )
        ok = Circuit.model_validate(
            {
                **LDO_LED,
                "board": {
                    **board,
                    "layers": 4,
                    "inner_planes": ["GND", "+3V3"],
                    "hole_clearance_mm": 0.15,
                },
            }
        )
        assert ok.board is not None and ok.board.inner_planes == ["GND", "+3V3"]
        assert ok.board.hole_clearance_mm == 0.15

    def test_a_component_may_name_its_own_3d_model(self) -> None:
        model = "${KICAD9_3DMODEL_DIR}/Package_DFN_QFN.3dshapes/QFN-24.step"
        comps = [{**LDO_LED["components"][0], "model": model}, *LDO_LED["components"][1:]]
        circuit = Circuit.model_validate({**LDO_LED, "components": comps})
        assert circuit.components[0].model == model
        assert circuit.components[1].model is None

    def test_ids_must_be_library_ids(self) -> None:
        bad = [{**LDO_LED["components"][0], "symbol": "R"}, *LDO_LED["components"][1:]]
        with pytest.raises(ValueError, match="'Library:Name'"):
            Circuit.model_validate({**LDO_LED, "components": bad})
