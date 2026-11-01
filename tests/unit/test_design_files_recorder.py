"""twin.record_design_files: electronics and firmware files as work products (FORGE-687).

Component-level: the real recorder over ``InMemoryTwinAPI`` and a temporary
adapter workspace, with only the blob store and the kicad.export_svg call
faked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.bom_recorder import make_bom_recorder
from api_gateway.twin.design_files_recorder import (
    gerber_layer,
    make_design_files_recorder,
    resolve_in_workspace,
)
from orchestrator.design_flow.fragments import load_fragment
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.items.registry import classify
from twin_core.models.enums import EdgeType, WorkProductType

PROJECT = "66666666-6666-6666-6666-666666666666"


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    import digital_twin.storage.work_product_blobs as blobs

    store: dict[str, bytes] = {}

    def put(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
        key = f"wp/{node_id}/{filename}"
        store[key] = content
        return key

    monkeypatch.setattr(blobs, "store_work_product_blob", put)
    return store


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


class _Links:
    def __init__(self) -> None:
        self.links: list[tuple[str, str, str, str]] = []

    async def link_work_product(self, pid: str, node_id: str, name: str, link: str) -> None:
        self.links.append((pid, node_id, name, link))


class _Kicad:
    """kicad.export_svg, writing its SVGs where the real adapter would."""

    def __init__(self, workspace: Path, fail: bool = False) -> None:
        self.workspace, self.fail = workspace, fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(
        self, tool_id: str, params: dict[str, Any], timeout: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((tool_id, params))
        if self.fail:
            raise RuntimeError("kicad adapter unreachable")
        out = self.workspace / "svg"
        out.mkdir(exist_ok=True)
        names = ["drone-fc.svg", "drone-fc-power.svg"] if "schematic_file" in params else ["b.svg"]
        for n in names:
            (out / n).write_text(f"<svg id='{n}'/>")
        return {"files": [{"file": str(out / n)} for n in names]}


def _kicad_project(ws: Path) -> Path:
    proj = ws / "generated" / "drone-fc"
    proj.mkdir(parents=True)
    (proj / "drone-fc.kicad_sch").write_text("(kicad_sch (version 20250114))")
    (proj / "drone-fc-power.kicad_sch").write_text("(kicad_sch (version 20250114))")
    (proj / "drone-fc.kicad_pro").write_text("{}")
    (proj / "sym-lib-table").write_text("(sym_lib_table)")
    (proj / "drone-fc.kicad_pcb").write_text("(kicad_pcb (version 20241229))")
    return proj


def _recorder(twin: Any, ws: Path, links: Any = None, bridge: Any = None) -> Any:
    return make_design_files_recorder(twin, links, mcp_bridge=bridge, workspace_dir=str(ws))


class TestRegistryAndFragments:
    @pytest.mark.parametrize(
        ("name", "prefix"),
        [
            ("schematic", "SCH"),
            ("pcb_layout", "PCB"),
            ("gerber", "GBR"),
            ("pick_and_place", "PNP"),
            ("pinmap", "PIN"),
            ("firmware_source", "FW"),
            ("purchased_model", "PMD"),
        ],
    )
    def test_types_are_versioned_definitions(self, name: str, prefix: str) -> None:
        spec = classify(name)
        assert spec is not None and spec.kind == "definition" and spec.key_prefix == prefix

    def test_pcb_and_firmware_fragments_declare_their_records(self) -> None:
        pcb, fw = load_fragment("pcb_v1"), load_fragment("firmware_v1")
        assert "schematic" in pcb.task("schematic").required_deliverables
        assert "pcb_layout" in pcb.task("routing").required_deliverables
        fab = pcb.task("fabrication_outputs").required_deliverables
        assert {"bom", "gerber", "pick_and_place"} <= set(fab)
        assert "pinmap" in fw.task("pin_map").required_deliverables
        assert {"simulation_result", "firmware_source"} <= set(
            fw.task("build").required_deliverables
        )


class TestSchematicAndBoard:
    async def test_schematic_keeps_every_sheet_previews_and_links_its_bom(
        self, twin: InMemoryTwinAPI, tmp_path: Path, stored: dict[str, bytes]
    ) -> None:
        proj = _kicad_project(tmp_path)
        bom = await make_bom_recorder(twin, None)(
            rows=[{"ref": "U1", "part": "STM32F405", "qty": 1}], name="FC BOM", project_id=PROJECT
        )
        links, kicad = _Links(), _Kicad(tmp_path)
        record = _recorder(twin, tmp_path, links, kicad)

        out = await record(
            work_product_type="schematic",
            file_path="generated/drone-fc/drone-fc.kicad_sch",
            name="Drone FC schematic",
            project_id=PROJECT,
            source_node_ids=[bom["node_id"]],
            metadata={"erc_errors": 0, "erc_warnings": 1},
        )

        assert out["primary_file"] == "drone-fc.kicad_sch"
        assert out["files"] == 5 - 1  # the board file is not part of a schematic
        assert out["previews"] == ["preview/drone-fc-power.svg", "preview/drone-fc.svg"]
        assert kicad.calls == [
            ("kicad.export_svg", {"schematic_file": str(proj / "drone-fc.kicad_sch")})
        ]
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp is not None and wp.type == WorkProductType.SCHEMATIC
        assert wp.domain == "electronics" and wp.format == "kicad_sch"
        meta = wp.metadata
        assert set(meta["design_files"]) == {
            "drone-fc.kicad_sch",
            "drone-fc-power.kicad_sch",
            "drone-fc.kicad_pro",
            "sym-lib-table",
        }
        assert meta["minio_object_key"] == meta["design_files"]["drone-fc.kicad_sch"]
        assert stored[meta["image_files"]["preview/drone-fc.svg"]] == b"<svg id='drone-fc.svg'/>"
        assert meta["erc_warnings"] == 1 and meta["item_key"].startswith("SCH-")
        edges = await twin.get_edges(UUID(out["node_id"]), edge_type=EdgeType.GENERATED_FROM)
        assert [e.target_id for e in edges] == [UUID(bom["node_id"])]
        assert links.links == [(PROJECT, out["node_id"], "Drone FC schematic", "schematic")]

    async def test_recording_again_is_the_next_revision(
        self, twin: InMemoryTwinAPI, tmp_path: Path, stored: dict[str, bytes]
    ) -> None:
        _kicad_project(tmp_path)
        record = _recorder(twin, tmp_path)
        first = await record(
            work_product_type="pcb_layout",
            file_path="generated/drone-fc/drone-fc.kicad_pcb",
            name="Drone FC board",
            project_id=PROJECT,
            preview=False,
        )
        (tmp_path / "generated/drone-fc/drone-fc.kicad_pcb").write_text("(kicad_pcb routed)")
        second = await record(
            work_product_type="pcb_layout",
            file_path="generated/drone-fc",
            name="Drone FC board",
            project_id=PROJECT,
            preview=False,
        )
        assert first["item_ref"].endswith("@1") and second["item_ref"].endswith("@2")
        assert first["item_ref"].split("@")[0] == second["item_ref"].split("@")[0]
        assert first["content_hash"] != second["content_hash"]

    async def test_preview_failure_still_records_the_files(
        self, twin: InMemoryTwinAPI, tmp_path: Path, stored: dict[str, bytes]
    ) -> None:
        _kicad_project(tmp_path)
        record = _recorder(twin, tmp_path, bridge=_Kicad(tmp_path, fail=True))
        out = await record(
            work_product_type="pcb_layout",
            file_path="generated/drone-fc/drone-fc.kicad_pcb",
            name="Board",
        )
        assert out["previews"] == [] and "unreachable" in out["preview_error"]
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp is not None and "image_files" not in wp.metadata


class TestFabricationAndFirmware:
    async def test_gerber_folder_names_each_layer_front_copper_first(
        self, twin: InMemoryTwinAPI, tmp_path: Path, stored: dict[str, bytes]
    ) -> None:
        fab = tmp_path / "gerbers"
        fab.mkdir()
        for n in (
            "fc-B_Cu.gbl",
            "fc-Edge_Cuts.gm1",
            "fc-F_Cu.gtl",
            "fc-F_Mask.gts",
            "fc-In1_Cu.g1",
            "fc-PTH.drl",
            "fc-job.gbrjob",
            "notes.txt",
        ):
            (fab / n).write_text("G04*")
        out = await _recorder(twin, tmp_path)(
            work_product_type="gerber", file_path="gerbers", name="FC gerbers"
        )
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp is not None
        layers = {row["path"]: row["layer"] for row in wp.metadata["layers"]}
        assert out["primary_file"] == "fc-F_Cu.gtl"
        assert "notes.txt" not in layers
        assert layers == {
            "fc-F_Cu.gtl": "copper",
            "fc-B_Cu.gbl": "copper",
            "fc-Edge_Cuts.gm1": "outline",
            "fc-F_Mask.gts": "soldermask",
            "fc-In1_Cu.g1": "copper",
            "fc-PTH.drl": "drill",
            "fc-job.gbrjob": "job",
        }

    async def test_firmware_project_keeps_sources_and_build_output_only(
        self, twin: InMemoryTwinAPI, tmp_path: Path, stored: dict[str, bytes]
    ) -> None:
        fw = tmp_path / "fw"
        (fw / "src").mkdir(parents=True)
        (fw / "include").mkdir()
        (fw / ".pio" / "build" / "f405" / "src").mkdir(parents=True)
        (fw / "platformio.ini").write_text("[env:f405]")
        (fw / "src" / "board.c").write_text("int b;")
        (fw / "src" / "main.c").write_text("int main(void){}")
        (fw / "include" / "pinmap.h").write_text("#define LED 5")
        (fw / ".pio" / "build" / "f405" / "firmware.elf").write_bytes(b"\x7fELF")
        (fw / ".pio" / "build" / "f405" / "src" / "main.o").write_bytes(b"obj")
        pinmap = tmp_path / "pinmap.csv"
        pinmap.write_text("pin,signal\nPA5,LED\n")

        record = _recorder(twin, tmp_path)
        pins = await record(work_product_type="pinmap", file_path="pinmap.csv", name="FC pins")
        out = await record(
            work_product_type="firmware_source",
            file_path="fw",
            name="FC firmware",
            source_node_ids=[pins["node_id"]],
            metadata={"flash": {"used_pct": 12.5}},
        )
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp is not None and wp.type == WorkProductType.FIRMWARE_SOURCE
        assert out["primary_file"] == "src/main.c" and wp.format == "c"
        assert set(wp.metadata["design_files"]) == {
            "src/main.c",
            "platformio.ini",
            "include/pinmap.h",
            "src/board.c",
            ".pio/build/f405/firmware.elf",
        }
        assert wp.metadata["build_outputs"] == [".pio/build/f405/firmware.elf"]
        assert wp.metadata["flash"] == {"used_pct": 12.5}
        assert out["generated_from"] == [pins["node_id"]]


def _simready_folder(ws: Path, *, licence: str | None = "CC-BY-4.0") -> Path:
    """What simready.import writes (FORGE-711), without needing pxr to make it."""
    folder = ws / "simready" / "simready_gripper-01234567"
    (folder / "usd" / "simready_usd").mkdir(parents=True)
    (folder / "usd" / "simready_usd" / "sm_gripper.usda").write_text("#usda 1.0")
    (folder / "preview.glb").write_bytes(b"glTF\x02\x00\x00\x00")
    (folder / "LICENSE.txt").write_text("Attribution 4.0 International")
    (folder / "ATTRIBUTION.txt").write_text('"gripper" by Robotiq')
    record = {
        "source": "simready",
        "asset": "gripper",
        "licence": licence,
        "package_sha256": "b" * 64,
        "mass_kg": 0.9,
        "extents_m": [0.1, 0.1, 0.12],
        "attribution": '"gripper" by Robotiq',
    }
    (folder / "simready.json").write_text(json.dumps({"record": record}))
    return folder


class TestPurchasedModel:
    async def test_simready_folder_is_a_cad_model_carrying_its_licence_and_mass(
        self, twin: InMemoryTwinAPI, tmp_path: Path, stored: dict[str, bytes]
    ) -> None:
        _simready_folder(tmp_path)
        links = _Links()
        record = _recorder(twin, tmp_path, links)
        out = await record(
            work_product_type="purchased_model",
            file_path="simready/simready_gripper-01234567",
            name="Robotiq 2F-85",
            project_id=PROJECT,
            # A caller cannot restate the licence or the mass.
            metadata={"licence": "Proprietary", "mass_kg": 0.1, "note": "kept"},
        )
        assert out["primary_file"] == "preview.glb" and out["files"] == 5
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp is not None and wp.type == WorkProductType.CAD_MODEL
        assert wp.format == "glb" and wp.domain == "mechanical"
        meta = wp.metadata
        assert meta["licence"] == "CC-BY-4.0" and meta["mass_kg"] == 0.9
        assert meta["note"] == "kept" and meta["item_key"].startswith("PMD-")
        assert "usd/simready_usd/sm_gripper.usda" in meta["design_files"]
        assert meta["minio_object_key"] == meta["design_files"]["preview.glb"]
        assert links.links[0][3] == "purchased_model"

    async def test_a_folder_without_a_licence_is_refused(
        self, twin: InMemoryTwinAPI, tmp_path: Path, stored: dict[str, bytes]
    ) -> None:
        _simready_folder(tmp_path, licence=None)
        with pytest.raises(ValueError, match="missing the licence"):
            await _recorder(twin, tmp_path)(
                work_product_type="purchased_model",
                file_path="simready/simready_gripper-01234567",
                name="x",
            )

    async def test_a_folder_simready_did_not_write_is_refused(
        self, twin: InMemoryTwinAPI, tmp_path: Path
    ) -> None:
        _kicad_project(tmp_path)
        with pytest.raises(ValueError, match="not a simready.import folder"):
            await _recorder(twin, tmp_path)(
                work_product_type="purchased_model", file_path="generated/drone-fc", name="x"
            )


class TestRefusals:
    def test_a_path_outside_the_workspace_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "ws").mkdir()
        (tmp_path / "secret.txt").write_text("x")
        with pytest.raises(ValueError, match="outside the adapter workspace"):
            resolve_in_workspace("../secret.txt", tmp_path / "ws")

    async def test_unknown_type_bad_source_and_wrong_file(
        self, twin: InMemoryTwinAPI, tmp_path: Path
    ) -> None:
        _kicad_project(tmp_path)
        record = _recorder(twin, tmp_path)
        with pytest.raises(ValueError, match="must be one of"):
            await record(work_product_type="netlist", file_path="generated", name="x")
        with pytest.raises(ValueError, match="not a UUID"):
            await record(
                work_product_type="schematic",
                file_path="generated/drone-fc",
                name="x",
                source_node_ids=["bom"],
            )
        with pytest.raises(ValueError, match="is a .kicad_sch file"):
            await record(
                work_product_type="schematic",
                file_path="generated/drone-fc/drone-fc.kicad_pcb",
                name="x",
            )

    @pytest.mark.parametrize(
        ("name", "layer"),
        [("b-F_Silkscreen.gto", "silkscreen"), ("b-NPTH.drl", "drill"), ("b-x.gbr", "other")],
    )
    def test_gerber_layer_names(self, name: str, layer: str) -> None:
        assert gerber_layer(name) == layer


class TestTool:
    async def test_tool_passes_its_arguments_through(self) -> None:
        seen: dict[str, Any] = {}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            seen.update(kwargs)
            return {"node_id": "n"}

        server = TwinServer(twin=InMemoryTwinAPI.create(), design_files_recorder=recorder)
        assert "twin.record_design_files" in server.tool_ids
        out = await server.record_design_files(
            {
                "work_product_type": "gerber",
                "file_path": "fab",
                "name": "G",
                "project_id": PROJECT,
                "source_node_ids": ["a"],
                "preview": False,
            }
        )
        assert out == {"node_id": "n"}
        assert seen["preview"] is False and seen["source_node_ids"] == ["a"]
        assert seen["project_id"] == PROJECT
        with pytest.raises(ValueError, match="'file_path' is required"):
            await server.record_design_files({"work_product_type": "gerber", "name": "G"})
