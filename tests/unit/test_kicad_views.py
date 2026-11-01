"""3D model paths and component updates on KiCad files (FORGE-684).

The S-expression work is tested here on small files in KiCad 9's format; the
kicad-cli exports and renders run live in
``tests/integration/test_kicad_generation_live.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tool_registry.tools.kicad import sexpr
from tool_registry.tools.kicad.views import (
    apply_board_changes,
    apply_schematic_changes,
    missing_models,
    model_paths,
    resolve_model,
)

BOARD = """(kicad_pcb (version 20241229) (generator "pcbnew")
  (footprint "Resistor_SMD:R_0603_1608Metric" (layer "F.Cu")
    (property "Reference" "R1" (at 0 0 0))
    (property "Value" "1k" (at 0 0 0))
    (model "${KICAD9_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603_1608Metric.step"))
  (footprint "Connector_Molex:Molex_KK" (layer "F.Cu")
    (property "Reference" "J1" (at 0 0 0))
    (property "Value" "IO" (at 0 0 0))
    (model "${KICAD9_3DMODEL_DIR}/Connector_Molex.3dshapes/Molex_KK.wrl"))
  (footprint "MountingHole:MountingHole_3.2mm_M3" (layer "F.Cu")
    (property "Reference" "H1" (at 0 0 0))))
"""

SCHEMATIC = """(kicad_sch (version 20250114) (generator "metaforge")
  (symbol (lib_id "Device:R") (at 10 10 0) (unit 1)
    (property "Reference" "R1" (at 10 6 0))
    (property "Value" "1k" (at 10 8 0))
    (property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 10 10 0)))
  (symbol (lib_id "Amplifier_Operational:LM358") (at 40 10 0) (unit 1)
    (property "Reference" "U1" (at 40 6 0)) (property "Value" "LM358" (at 40 8 0)))
  (symbol (lib_id "Amplifier_Operational:LM358") (at 70 10 0) (unit 2)
    (property "Reference" "U1" (at 70 6 0)) (property "Value" "LM358" (at 70 8 0))))
"""


def _props(tree: list, ref: str) -> list[dict[str, str]]:
    out = []
    for item in sexpr.children(tree, "symbol") + sexpr.children(tree, "footprint"):
        props = {str(p[1]): str(p[2]) for p in sexpr.children(item, "property")}
        if props.get("Reference") == ref:
            out.append(props)
    return out


class TestModels:
    def test_model_paths_per_footprint(self) -> None:
        assert model_paths(BOARD) == [
            ("R1", "${KICAD9_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603_1608Metric.step"),
            ("J1", "${KICAD9_3DMODEL_DIR}/Connector_Molex.3dshapes/Molex_KK.wrl"),
        ]

    def test_variables_resolve_to_the_model_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KICAD9_3DMODEL_DIR", raising=False)
        assert resolve_model("${KICAD9_3DMODEL_DIR}/A.3dshapes/x.step", "/m") == Path(
            "/m/A.3dshapes/x.step"
        )

    def test_missing_models_are_named_and_step_substitutes_for_wrl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("KICAD9_3DMODEL_DIR", raising=False)
        models = tmp_path / "models"
        (models / "Resistor_SMD.3dshapes").mkdir(parents=True)
        (models / "Resistor_SMD.3dshapes" / "R_0603_1608Metric.step").write_text("")
        board = tmp_path / "b.kicad_pcb"
        board.write_text(BOARD)
        missing, total = missing_models(str(board), str(models))
        assert total == 2
        assert missing == [
            {"ref": "J1", "model": "${KICAD9_3DMODEL_DIR}/Connector_Molex.3dshapes/Molex_KK.wrl"}
        ]
        # A STEP beside the referenced VRML counts: kicad-cli substitutes it.
        (models / "Connector_Molex.3dshapes").mkdir()
        (models / "Connector_Molex.3dshapes" / "Molex_KK.step").write_text("")
        assert missing_models(str(board), str(models)) == ([], 2)


class TestUpdates:
    def test_value_footprint_and_fields_on_every_unit(self) -> None:
        text, done = apply_schematic_changes(
            SCHEMATIC,
            [
                {"ref": "R1", "value": "2.2k", "footprint": "Resistor_SMD:R_0805_2012Metric"},
                {"ref": "U1", "fields": {"MPN": "LM358DR"}},
            ],
        )
        tree = sexpr.loads(text)
        assert done == ["R1", "U1"]
        (r1,) = _props(tree, "R1")
        assert r1["Value"] == "2.2k" and r1["Footprint"] == "Resistor_SMD:R_0805_2012Metric"
        units = _props(tree, "U1")
        assert len(units) == 2 and all(u["MPN"] == "LM358DR" for u in units)

    def test_unknown_reference_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no such reference in the schematic: R9, C3"):
            apply_schematic_changes(SCHEMATIC, [{"ref": "R9"}, {"ref": "C3"}])

    def test_board_value_and_fields(self) -> None:
        tree = sexpr.loads(
            apply_board_changes(BOARD, [{"ref": "R1", "value": "2.2k", "fields": {"MPN": "X"}}])
        )
        (r1,) = _props(tree, "R1")
        assert r1["Value"] == "2.2k" and r1["MPN"] == "X"
        (j1,) = _props(tree, "J1")
        assert j1["Value"] == "IO"


class TestExportSvg:
    """kicad.export_svg's kicad-cli command lines and results (FORGE-687)."""

    @pytest.fixture
    def server(self, tmp_path: Path) -> object:
        from tool_registry.tools.kicad.adapter import KicadServer
        from tool_registry.tools.kicad.config import KicadConfig

        return KicadServer(KicadConfig(work_dir=str(tmp_path / "work")))

    @pytest.fixture
    def cli(self, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        import tool_registry.tools.kicad.generation as generation

        calls: list[list[str]] = []

        async def run(cmd: list[str], timeout: float, **_: object) -> tuple[int, str, str]:
            calls.append(cmd)
            out = Path(cmd[cmd.index("-o") + 1])
            if cmd[1] == "sch":
                for n in ("fc.svg", "fc-power.svg"):
                    (out / n).write_text("<svg/>")
            else:
                out.write_text("<svg/>")
            return 0, "", ""

        monkeypatch.setattr(generation, "_run", run)
        return calls

    async def test_schematic_gives_one_svg_per_sheet(
        self, server: object, cli: list[list[str]], tmp_path: Path
    ) -> None:
        from tool_registry.tools.kicad.views import export_svg

        sch = tmp_path / "fc.kicad_sch"
        sch.write_text("(kicad_sch)")
        out = await export_svg(server, {"schematic_file": str(sch)})  # type: ignore[arg-type]
        assert out["kind"] == "schematic"
        assert [Path(f["file"]).name for f in out["files"]] == ["fc-power.svg", "fc.svg"]
        assert cli[0][1:4] == ["sch", "export", "svg"] and "--exclude-drawing-sheet" in cli[0]

    async def test_board_is_the_top_side_cropped_to_the_board(
        self, server: object, cli: list[list[str]], tmp_path: Path
    ) -> None:
        from tool_registry.tools.kicad.views import TOP_LAYERS, export_svg

        pcb = tmp_path / "fc.kicad_pcb"
        pcb.write_text("(kicad_pcb)")
        out = await export_svg(server, {"pcb_file": str(pcb)})  # type: ignore[arg-type]
        cmd = cli[0]
        assert out["kind"] == "board" and out["layers"] == TOP_LAYERS.split(",")
        assert cmd[cmd.index("--layers") + 1] == TOP_LAYERS
        assert cmd[cmd.index("--page-size-mode") + 1] == "2" and "--mode-single" in cmd

    async def test_exactly_one_input(self, server: object, cli: list[list[str]]) -> None:
        from tool_registry.tools.kicad.views import export_svg

        with pytest.raises(ValueError, match="exactly one"):
            await export_svg(server, {})  # type: ignore[arg-type]
