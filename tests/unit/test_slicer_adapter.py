"""slicer.slice and slicer.list_profiles: config building and G-code parsing.

tests/fixtures/slicer/cube_20mm.gcode is real PrusaSlicer 2.9.2 output from
the slicer-adapter image (a 20 mm cube, generic_220, PLA, 0.2 mm, 20%), with
the move lines stripped; the parser reads only its comments. PrusaSlicer
itself runs only in that container, so the handler tests here replace the
subprocess with one that copies the fixture.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tool_registry.tools.slicer import adapter as slicer_adapter
from tool_registry.tools.slicer.adapter import (
    SlicerNotAvailableError,
    SlicerServer,
    build_config,
    overrides,
    parse_duration,
    read_gcode_summary,
    render_ini,
    slicer_messages,
)
from tool_registry.tools.slicer.config import SlicerConfig

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "slicer" / "cube_20mm.gcode"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "cube.stl").write_bytes(b"solid cube\nendsolid cube\n")
    return tmp_path


@pytest.fixture
def server(workspace: Path) -> SlicerServer:
    return SlicerServer(SlicerConfig(work_dir=str(workspace)))


class FakeSlicer:
    """Stands in for the PrusaSlicer subprocess; writes the fixture G-code."""

    def __init__(self, rc: int = 0, output: str = "", write: bool = True) -> None:
        self.rc, self.output, self.write = rc, output, write
        self.printed: int | None = None  # objects in objects_info; None keeps the fixture's
        self.cmd: list[str] = []
        self.configs: list[str] = []

    async def __call__(self, cmd: list[str], timeout: float) -> tuple[int, str]:
        self.cmd = cmd
        self.configs = [
            Path(cmd[i + 1]).read_text(encoding="utf-8")
            for i, arg in enumerate(cmd)
            if arg == "--load"
        ]
        if self.write:
            out = Path(cmd[cmd.index("--output") + 1])
            shutil.copyfile(FIXTURE, out)
            if self.printed is not None:
                objects = ",".join(['{"name":"x","polygon":[]}'] * self.printed)
                text = re.sub(
                    r"^; objects_info = .*$",
                    f'; objects_info = {{"objects":[{objects}]}}',
                    out.read_text(),
                    flags=re.MULTILINE,
                )
                out.write_text(text)
        return self.rc, self.output


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSlicer:
    runner = FakeSlicer()
    monkeypatch.setattr(slicer_adapter, "_run", runner)
    monkeypatch.setattr(slicer_adapter.shutil, "which", lambda name: "/usr/bin/prusa-slicer")
    return runner


class TestConfig:
    def test_defaults_presets_and_safety(self) -> None:
        values = build_config("generic_250", "PETG", {}, defaults=True)
        assert values["bed_shape"] == "0x0,250x0,250x210,0x210"
        assert values["max_print_height"] == "220"
        assert values["filament_type"] == "PETG"
        assert values["temperature"] == "240"
        assert values["layer_height"] == "0.2"
        assert values["post_process"] == ""
        assert values["binary_gcode"] == "0"

    def test_user_config_keeps_its_own_settings(self) -> None:
        values = build_config(None, None, {"layer_height": "0.12"}, defaults=False)
        assert values == {"layer_height": "0.12", "post_process": "", "binary_gcode": "0"}

    def test_settings_cannot_unset_safety(self) -> None:
        values = build_config(None, None, {"post_process": "/bin/sh"}, defaults=False)
        assert values["post_process"] == ""

    def test_overrides(self) -> None:
        out = overrides(
            {
                "layer_height": 0.12,
                "nozzle_mm": 0.6,
                "infill_percent": 35,
                "perimeters": 3,
                "supports": True,
                "brim_mm": 5,
                "filament_cost_per_kg": 24.5,
            }
        )
        assert out == {
            "layer_height": "0.12",
            "nozzle_diameter": "0.6",
            "fill_density": "35%",
            "perimeters": "3",
            "support_material": "1",
            "support_material_auto": "1",
            "brim_width": "5",
            "filament_cost": "24.5",
        }

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({"layer_height": 2}, "between"),
            ({"infill_percent": "20%"}, "number"),
            ({"perimeters": 2.5}, "integer"),
            ({"supports": "yes"}, "true or false"),
            ({"layer_height": True}, "number"),
        ],
    )
    def test_bad_overrides_are_refused(self, arguments: dict[str, Any], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            overrides(arguments)

    def test_render_escapes_newlines(self) -> None:
        text = render_ini({"start_gcode": "G28\nG1 Z5", "layer_height": "0.2"})
        assert text == "start_gcode = G28\\nG1 Z5\nlayer_height = 0.2\n"


class TestGcode:
    def test_summary_from_real_output(self) -> None:
        summary = read_gcode_summary(FIXTURE)
        assert summary["slicer"] == "PrusaSlicer 2.9.2"
        assert summary["print_time"] == "17m 31s"
        assert summary["print_time_s"] == 1051
        assert summary["objects_printed"] == 1
        assert summary["first_layer_time_s"] == 46
        assert summary["filament_mm"] == pytest.approx(1301.99)
        assert summary["filament_cm3"] == pytest.approx(3.13)
        assert summary["filament_g"] == pytest.approx(3.88)
        assert summary["layers"] == 100
        assert summary["height_mm"] == pytest.approx(20.0)
        assert summary["settings"]["layer_height"] == "0.2"
        assert summary["settings"]["fill_density"] == "20%"
        assert summary["settings"]["filament_type"] == "PLA"

    def test_multi_extruder_lengths_are_summed(self, tmp_path: Path) -> None:
        gcode = tmp_path / "two.gcode"
        gcode.write_text(
            "; filament used [mm] = 100.5, 20.25\n"
            "; estimated printing time (normal mode) = 1d 2h 3m 4s\n"
        )
        summary = read_gcode_summary(gcode)
        assert summary["filament_mm"] == pytest.approx(120.75)
        assert summary["print_time_s"] == 93784

    @pytest.mark.parametrize(
        ("text", "seconds"), [("46s", 46), ("17m 30s", 1050), ("2h 0m 1s", 7201), ("", None)]
    )
    def test_durations(self, text: str, seconds: int | None) -> None:
        assert parse_duration(text) == seconds

    def test_messages_drop_progress_and_log_prefixes(self) -> None:
        output = (
            "10 => Processing triangulated mesh\n"
            "[2026-11-01 20:50:55.319893] [0x78ed106c54c0] [error]   "
            "stl_open_count_facets: The input is an empty file: empty.stl\n"
            "empty.stl: Loading of a model file failed.\n"
            "Slicing result exported to a.gcode\n"
        )
        assert slicer_messages(output) == [
            "stl_open_count_facets: The input is an empty file: empty.stl",
            "empty.stl: Loading of a model file failed.",
        ]


class TestSlice:
    async def test_slices_with_built_in_presets(
        self, server: SlicerServer, fake: FakeSlicer, workspace: Path
    ) -> None:
        result = await server.slice({"file_path": "parts/cube.stl", "infill_percent": 40})
        assert result["printer"] == "generic_220"
        assert result["material"] == "PLA"
        assert result["print_time_s"] == 1051
        assert result["filament_g"] == pytest.approx(3.88)
        assert "filament_cost" not in result
        assert result["gcode_path"].startswith("output/slicer/")
        assert result["gcode_path"].endswith("/cube.gcode")
        assert (workspace / result["gcode_path"]).is_file()
        assert (workspace / result["config_used"]).is_file()
        assert "generic Marlin" in result["note"]
        assert fake.cmd.count("--load") == 1
        assert "fill_density = 40%" in fake.configs[-1]
        assert "post_process = \n" in fake.configs[-1]
        assert "gcode_label_objects = octoprint" in fake.configs[-1]
        assert "--duplicate" not in fake.cmd
        assert fake.cmd[-1] == str((workspace / "parts" / "cube.stl").resolve())

    async def test_user_config_is_loaded_first_and_overridden_last(
        self, server: SlicerServer, fake: FakeSlicer, workspace: Path
    ) -> None:
        (workspace / "mk4.ini").write_text("layer_height = 0.15\ngcode_label_objects = firmware\n")
        fake.printed = 4
        result = await server.slice(
            {"file_path": "parts/cube.stl", "config_path": "mk4.ini", "copies": 4}
        )
        loads = [fake.cmd[i + 1] for i, arg in enumerate(fake.cmd) if arg == "--load"]
        assert loads[0] == str((workspace / "mk4.ini").resolve())
        assert "layer_height" not in fake.configs[-1]
        assert "post_process = \n" in fake.configs[-1]
        assert "gcode_label_objects = firmware" in fake.configs[-1]
        assert fake.cmd[fake.cmd.index("--duplicate") + 1] == "4"
        assert result["objects_printed"] == 4
        assert result["printer"] == "from config_path"
        assert "note" not in result

    async def test_copies_left_off_the_bed_are_refused(
        self, server: SlicerServer, fake: FakeSlicer
    ) -> None:
        fake.printed = 1
        with pytest.raises(ValueError, match="only 1 of 4 copies fit"):
            await server.slice({"file_path": "parts/cube.stl", "copies": 4})

    async def test_cost_is_reported_when_asked(
        self, server: SlicerServer, fake: FakeSlicer
    ) -> None:
        result = await server.slice({"file_path": "parts/cube.stl", "filament_cost_per_kg": 25})
        assert "filament_cost" in result
        assert "filament_cost = 25" in fake.configs[-1]

    async def test_config_with_a_script_is_refused(
        self, server: SlicerServer, fake: FakeSlicer, workspace: Path
    ) -> None:
        (workspace / "evil.ini").write_text("layer_height = 0.2\npost_process = /bin/sh x\n")
        with pytest.raises(ValueError, match="post_process"):
            await server.slice({"file_path": "parts/cube.stl", "config_path": "evil.ini"})
        assert fake.cmd == []

    async def test_3mf_with_a_script_is_refused(
        self, server: SlicerServer, fake: FakeSlicer, workspace: Path
    ) -> None:
        with zipfile.ZipFile(workspace / "parts" / "evil.3mf", "w") as archive:
            archive.writestr("3D/3dmodel.model", "<model/>")
            archive.writestr("Metadata/Slic3r_PE.config", "; post_process = /bin/touch x\n")
        with pytest.raises(ValueError, match="post-processing script"):
            await server.slice({"file_path": "parts/evil.3mf"})
        assert fake.cmd == []

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            ("../outside.stl", "outside the adapter workspace"),
            ("parts/cube.dxf", "must be one of"),
            ("parts/missing.stl", "no file"),
        ],
    )
    async def test_bad_paths_are_refused(
        self, server: SlicerServer, fake: FakeSlicer, path: str, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            await server.slice({"file_path": path})

    async def test_empty_model_is_refused(
        self, server: SlicerServer, fake: FakeSlicer, workspace: Path
    ) -> None:
        (workspace / "parts" / "empty.stl").write_bytes(b"")
        with pytest.raises(ValueError, match="empty"):
            await server.slice({"file_path": "parts/empty.stl"})

    async def test_unknown_printer_is_refused(self, server: SlicerServer, fake: FakeSlicer) -> None:
        with pytest.raises(ValueError, match="generic_220"):
            await server.slice({"file_path": "parts/cube.stl", "printer": "my_printer"})

    async def test_slicer_failure_carries_its_reason(
        self, server: SlicerServer, fake: FakeSlicer
    ) -> None:
        fake.rc, fake.write = 1, False
        fake.output = "10 => Processing\nAll objects are outside of the print volume.\n"
        with pytest.raises(ValueError, match="outside of the print volume"):
            await server.slice({"file_path": "parts/cube.stl"})

    async def test_timeout_is_reported(self, server: SlicerServer, fake: FakeSlicer) -> None:
        fake.rc, fake.write = -9, False
        with pytest.raises(ValueError, match="longer than 600 s"):
            await server.slice({"file_path": "parts/cube.stl"})

    async def test_without_prusaslicer(
        self, server: SlicerServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(slicer_adapter.shutil, "which", lambda name: None)
        with pytest.raises(SlicerNotAvailableError, match="slicer-adapter container"):
            await server.slice({"file_path": "parts/cube.stl"})


class TestProfiles:
    async def test_lists_presets_and_defaults(self, server: SlicerServer) -> None:
        result = await server.list_profiles({})
        assert [p["printer"] for p in result["printers"]] == [
            "generic_220",
            "generic_250",
            "generic_256",
            "generic_300",
        ]
        assert {m["material"] for m in result["materials"]} == {
            "PLA",
            "PETG",
            "ABS",
            "ASA",
            "TPU",
        }
        assert result["defaults"]["printer"] == "generic_220"

    def test_registers_both_tools(self, server: SlicerServer) -> None:
        assert set(server.tool_ids) == {"slicer.slice", "slicer.list_profiles"}
