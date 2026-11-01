"""KiCad parsing against real kicad-cli 9 output (FORGE-680).

The fixtures in ``tests/unit/fixtures/kicad/`` are what kicad-cli 9.0.2
wrote for KiCad's own ``ecc83`` demo (a valve preamp): its S-expression
netlist, its grouped BOM, its pick-and-place file and its drill files,
plus two ERC reports on the drone FC reference (FORGE-686).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tool_registry.tools.kicad import sexpr
from tool_registry.tools.kicad.adapter import (
    KicadServer,
    _count_excellon_holes,
    _parse_bom_csv,
    _parse_bom_rows,
    _parse_erc_violations,
    _parse_netlist_stats,
    _parse_pin_mapping_from_netlist,
    _parse_pos_csv,
)
from tool_registry.tools.kicad.config import KicadConfig

FIX = Path(__file__).parent / "fixtures" / "kicad"


class TestSexpr:
    def test_nested_lists_quoted_and_bare_atoms(self) -> None:
        tree = sexpr.loads('(comp (ref "U1") (value "a \\"b\\" c") (at 1.5 -2 90))')
        assert tree[0] == "comp"
        assert sexpr.value(tree, "ref") == "U1"
        assert sexpr.value(tree, "value") == 'a "b" c'
        assert isinstance(sexpr.find(tree, "ref")[1], sexpr.QStr)
        assert sexpr.find(tree, "at") == ["at", "1.5", "-2", "90"]

    def test_round_trip_keeps_quoting(self) -> None:
        text = '(kicad_sch (version 20250114) (uuid "abc") (lib_symbols (symbol "Device:R")))'
        tree = sexpr.loads(text)
        again = sexpr.loads(sexpr.dumps(tree))
        assert again == tree
        assert '"abc"' in sexpr.dumps(tree) and "(version 20250114)" in sexpr.dumps(tree)

    def test_find_all_is_depth_first(self) -> None:
        tree = sexpr.loads("(a (node 1) (b (node 2) (c (node 3))))")
        assert [n[1] for n in sexpr.find_all(tree, "node")] == ["1", "2", "3"]

    @pytest.mark.parametrize("bad", ["(a (b)", "(a))", '(a "x)', "x"])
    def test_malformed_is_refused(self, bad: str) -> None:
        with pytest.raises(sexpr.SExprError):
            sexpr.loads(bad)


class TestRealNetlist:
    def test_counts(self) -> None:
        assert _parse_netlist_stats(str(FIX / "ecc83.net")) == (13, 15)

    def test_pin_mapping_names_and_nets(self) -> None:
        comps = {
            c["reference"]: c for c in _parse_pin_mapping_from_netlist(str(FIX / "ecc83.net"), None)
        }
        valve = comps["U1"]
        assert valve["value"] == "ECC83"
        assert [p["number"] for p in valve["pins"]] == [str(n) for n in range(1, 10)]
        grid = valve["pins"][1]
        assert grid == {"number": "2", "name": "G", "type": "input", "net": "Net-(P1-PM)"}

    def test_filter(self) -> None:
        refs = [
            c["reference"] for c in _parse_pin_mapping_from_netlist(str(FIX / "ecc83.net"), "R")
        ]
        assert refs == ["R1", "R2", "R3", "R4"]


class TestRealFabricationFiles:
    def test_grouped_bom(self) -> None:
        rows = _parse_bom_rows(str(FIX / "ecc83_bom.csv"))
        resistors = next(r for r in rows if r["value"] == "1.5K")
        assert resistors["references"] == ["R1", "R2"] and resistors["quantity"] == 2
        assert _parse_bom_csv(str(FIX / "ecc83_bom.csv")) == (11, 10)

    def test_positions(self) -> None:
        placements = _parse_pos_csv(str(FIX / "ecc83_pos.csv"))
        assert len(placements) == 11
        c1 = placements[0]
        assert c1["reference"] == "C1" and c1["side"] == "top"
        assert c1["x_mm"] == pytest.approx(141.605) and c1["rotation_deg"] == 90.0

    def test_drill_hole_counts(self) -> None:
        assert _count_excellon_holes(FIX / "ecc83-pp-PTH.drl") == 33
        assert _count_excellon_holes(FIX / "ecc83-pp-NPTH.drl") == 0


class TestRealErcReports:
    """kicad-cli 9.0.2 ERC on the drone FC reference (FORGE-686).

    ``drone-fc_erc.json`` is the committed schematic as generated;
    ``drone-fc-vin-open_erc.json`` is a copy with the +5V label on U4 pin 1
    (the regulator's VIN) deleted. KiCad nests violations under each sheet.
    """

    def _load(self, name: str) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((FIX / name).read_text())
        return data

    def test_violations_are_read_from_each_sheet(self) -> None:
        report = self._load("drone-fc-vin-open_erc.json")
        assert "violations" not in report
        violations = _parse_erc_violations(report, "all")
        assert [(v["severity"], v["rule_id"]) for v in violations] == [
            ("warning", "pin_to_pin"),
            ("error", "pin_not_connected"),
            ("error", "power_pin_not_driven"),
        ]
        open_pin = violations[1]
        assert (open_pin["component"], open_pin["pin"], open_pin["sheet"]) == ("U4", "1", "/")
        assert open_pin["items"] == ["Symbol U4 Pin 1 [VIN, Power input, Line]"]

    def test_reference_has_one_warning_and_no_errors(self) -> None:
        violations = _parse_erc_violations(self._load("drone-fc_erc.json"), "all")
        assert [(v["severity"], v["component"], v["pin"]) for v in violations] == [
            ("warning", "U3", "5")
        ]

    async def test_open_vin_fails_the_erc_verdict(self, tmp_path: Path) -> None:
        report = self._load("drone-fc-vin-open_erc.json")
        server = KicadServer(config=KicadConfig(work_dir=str(tmp_path)))

        async def run(
            cli: str, args: list[str], timeout: float, cwd: str | None = None
        ) -> tuple[int, str, str]:
            Path(args[args.index("--output") + 1]).write_text(json.dumps(report))
            return 0, "", ""

        with (
            patch("tool_registry.tools.kicad.adapter._check_kicad_cli", AsyncMock()),
            patch("tool_registry.tools.kicad.adapter._run_kicad_cli", run),
        ):
            result = await server._execute_erc("/designs/drone-fc.kicad_sch", "all")
        assert result["passed"] is False
        assert (result["errors"], result["warnings"]) == (2, 1)
