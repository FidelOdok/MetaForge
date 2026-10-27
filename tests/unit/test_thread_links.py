"""The electronics thread stays linked when a recorder is not told its source (FORGE-705).

A sandbox run recorded its schematic with no link to the BOM and its board
STEP with no link to the layout, and nothing said so. The real recorders run
here over ``InMemoryTwinAPI`` and a temporary adapter workspace.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.bom_recorder import make_bom_recorder
from api_gateway.twin.design_files_recorder import make_design_files_recorder
from api_gateway.twin.geometry_recorder import make_geometry_recorder
from api_gateway.twin.thread_links import infer_upstream, link_thread
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType

PROJECT = "70570570-5705-4705-8705-705705705705"
_STEP_B64 = base64.b64encode(b"ISO-10303-21;\nHEADER;\nboard\nENDSEC;\n").decode("ascii")


@pytest.fixture(autouse=True)
def _blobs(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda nid, fn, content, content_type="": f"wp/{nid}/{fn}",
    )


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    proj = tmp_path / "fc"
    proj.mkdir()
    (proj / "fc.kicad_sch").write_text("(kicad_sch)")
    (proj / "fc.kicad_pro").write_text("{}")
    (proj / "fc.kicad_pcb").write_text("(kicad_pcb)")
    fab = tmp_path / "gerbers"
    fab.mkdir()
    (fab / "fc-F_Cu.gtl").write_text("G04*")
    (tmp_path / "fc-pos.csv").write_text("Ref,Val\nU1,STM32\n")
    return tmp_path


def _files(twin: Any, ws: Path) -> Any:
    return make_design_files_recorder(twin, None, workspace_dir=str(ws))


async def _bom(twin: Any, name: str = "FC BOM") -> str:
    out = await make_bom_recorder(twin, None)(
        rows=[{"ref": "U1", "part": "STM32F411", "qty": 1}], name=name, project_id=PROJECT
    )
    return str(out["node_id"])


async def _targets(twin: Any, node_id: str) -> list[UUID]:
    edges = await twin.get_edges(UUID(node_id), edge_type=EdgeType.GENERATED_FROM)
    return [e.target_id for e in edges]


async def test_a_schematic_with_no_source_is_linked_to_the_projects_bom(
    twin: InMemoryTwinAPI, ws: Path
) -> None:
    bom = await _bom(twin)
    out = await _files(twin, ws)(
        work_product_type="schematic",
        file_path="fc/fc.kicad_sch",
        name="FC schematic",
        project_id=PROJECT,
        preview=False,
    )
    assert out["generated_from"] == [bom] and out["generated_from_inferred"] is True
    assert out["thread_unlinked_stages"] == 0 and "warnings" not in out
    assert await _targets(twin, out["node_id"]) == [UUID(bom)]
    wp = await twin.get_work_product(UUID(out["node_id"]))
    assert wp is not None and wp.metadata["source_inferred"] is True


async def test_a_named_source_is_kept_and_nothing_is_inferred(
    twin: InMemoryTwinAPI, ws: Path
) -> None:
    await _bom(twin, "BOM A")
    other = await _bom(twin, "BOM B")
    out = await _files(twin, ws)(
        work_product_type="schematic",
        file_path="fc/fc.kicad_sch",
        name="FC schematic",
        project_id=PROJECT,
        source_node_ids=[other],
        preview=False,
    )
    assert out["generated_from"] == [other] and out["generated_from_inferred"] is False
    assert await _targets(twin, out["node_id"]) == [UUID(other)]


async def test_two_candidates_leave_the_stage_unlinked_and_say_which(
    twin: InMemoryTwinAPI, ws: Path
) -> None:
    await _bom(twin, "BOM A")
    await _bom(twin, "BOM B")
    out = await _files(twin, ws)(
        work_product_type="schematic",
        file_path="fc/fc.kicad_sch",
        name="FC schematic",
        project_id=PROJECT,
        preview=False,
    )
    assert out["generated_from"] == [] and out["thread_unlinked_stages"] == 1
    (warning,) = out["warnings"]
    assert "2 current bom records (BOM A, BOM B)" in warning and "source_node_ids" in warning
    assert await _targets(twin, out["node_id"]) == []


async def test_a_stage_recorded_before_its_upstream_is_linked_when_it_arrives(
    twin: InMemoryTwinAPI, ws: Path
) -> None:
    record = _files(twin, ws)
    sch = await record(
        work_product_type="schematic",
        file_path="fc/fc.kicad_sch",
        name="FC schematic",
        project_id=PROJECT,
        preview=False,
    )
    # No BOM yet: reported as missing, not counted as a broken link.
    assert sch["thread_unlinked_stages"] == 0
    assert any("no current bom" in w for w in sch["warnings"])

    bom = await _bom(twin)
    board = await record(
        work_product_type="pcb_layout",
        file_path="fc/fc.kicad_pcb",
        name="FC board",
        project_id=PROJECT,
        preview=False,
    )
    assert board["generated_from"] == [sch["node_id"]]
    assert {"node_id": sch["node_id"], "type": "schematic", "generated_from": bom} in board[
        "inferred_links"
    ]
    assert await _targets(twin, sch["node_id"]) == [UUID(bom)]
    assert "warnings" not in board


async def test_a_new_revision_links_to_the_current_upstream_only(
    twin: InMemoryTwinAPI, ws: Path
) -> None:
    record = _files(twin, ws)
    await _bom(twin)
    sch = await record(
        work_product_type="schematic",
        file_path="fc/fc.kicad_sch",
        name="S",
        project_id=PROJECT,
        preview=False,
    )
    (ws / "fc/fc.kicad_sch").write_text("(kicad_sch rev2)")
    sch2 = await record(
        work_product_type="schematic",
        file_path="fc/fc.kicad_sch",
        name="S",
        project_id=PROJECT,
        preview=False,
    )
    assert sch2["item_ref"].endswith("@2")
    board = await record(
        work_product_type="pcb_layout",
        file_path="fc/fc.kicad_pcb",
        name="B",
        project_id=PROJECT,
        preview=False,
    )
    assert board["generated_from"] == [sch2["node_id"]] != [sch["node_id"]]
    assert board["thread_unlinked_stages"] == 0


async def test_the_whole_board_thread_links_with_no_sources_given(
    twin: InMemoryTwinAPI, ws: Path
) -> None:
    record = _files(twin, ws)
    bom = await _bom(twin)
    sch = await record(
        work_product_type="schematic",
        file_path="fc/fc.kicad_sch",
        name="S",
        project_id=PROJECT,
        preview=False,
    )
    pcb = await record(
        work_product_type="pcb_layout",
        file_path="fc/fc.kicad_pcb",
        name="B",
        project_id=PROJECT,
        preview=False,
    )
    gbr = await record(
        work_product_type="gerber", file_path="gerbers", name="G", project_id=PROJECT
    )
    pnp = await record(
        work_product_type="pick_and_place", file_path="fc-pos.csv", name="P", project_id=PROJECT
    )
    assert await _targets(twin, sch["node_id"]) == [UUID(bom)]
    assert await _targets(twin, pcb["node_id"]) == [UUID(sch["node_id"])]
    assert await _targets(twin, gbr["node_id"]) == [UUID(pcb["node_id"])]
    assert await _targets(twin, pnp["node_id"]) == [UUID(pcb["node_id"])]
    links = await link_thread(twin, PROJECT)
    assert links.inferred == [] and links.unlinked == [] and links.missing == []


async def test_a_board_step_from_kicad_is_linked_to_the_layout(
    twin: InMemoryTwinAPI, ws: Path
) -> None:
    await _bom(twin)
    record = _files(twin, ws)
    await record(
        work_product_type="schematic",
        file_path="fc/fc.kicad_sch",
        name="S",
        project_id=PROJECT,
        preview=False,
    )
    pcb = await record(
        work_product_type="pcb_layout",
        file_path="fc/fc.kicad_pcb",
        name="B",
        project_id=PROJECT,
        preview=False,
    )
    out = await make_geometry_recorder(twin, None)(
        step_base64=_STEP_B64,
        name="FC board 3D",
        project_id=PROJECT,
        source_tool="kicad.export_3d",
    )
    assert out["generated_from"] == [pcb["node_id"]] and out["generated_from_inferred"] is True
    assert out["thread_unlinked_stages"] == 0
    assert await _targets(twin, out["node_id"]) == [UUID(pcb["node_id"])]


async def test_an_enclosure_is_not_a_stage_of_the_board_thread(
    twin: InMemoryTwinAPI, ws: Path
) -> None:
    await _files(twin, ws)(
        work_product_type="pcb_layout",
        file_path="fc/fc.kicad_pcb",
        name="B",
        project_id=PROJECT,
        preview=False,
    )
    out = await make_geometry_recorder(twin, None)(
        step_base64=_STEP_B64,
        name="Enclosure",
        project_id=PROJECT,
        source_tool="cadquery.generate_enclosure",
    )
    assert "generated_from" not in out and "thread_unlinked_stages" not in out
    assert await _targets(twin, out["node_id"]) == []
    found = await infer_upstream(twin, PROJECT, "schematic")
    assert found.node_id is None and not found.ambiguous


async def test_commit_geometry_from_kicads_3d_folder_is_a_board_and_takes_sources(
    twin: InMemoryTwinAPI, ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pcb = await _files(twin, ws)(
        work_product_type="pcb_layout",
        file_path="fc/fc.kicad_pcb",
        name="B",
        project_id=PROJECT,
        preview=False,
    )
    step = ws / "3d" / "fc.step"
    step.parent.mkdir()
    step.write_bytes(base64.b64decode(_STEP_B64))
    server = TwinServer(twin=twin, geometry_recorder=make_geometry_recorder(twin, None))

    inferred = await server.commit_geometry(
        {"file_path": str(step), "name": "Board 3D", "project_id": PROJECT}
    )
    wp = await twin.get_work_product(UUID(inferred["node_id"]))
    assert wp is not None and wp.created_by == "kicad.export_3d"
    assert inferred["generated_from"] == [pcb["node_id"]]

    named = await server.commit_geometry(
        {
            "file_path": str(step),
            "name": "Board 3D other",
            "project_id": PROJECT,
            "source_node_ids": [pcb["node_id"]],
        }
    )
    assert named["generated_from"] == [pcb["node_id"]] and not named["generated_from_inferred"]
    with pytest.raises(ValueError, match="not a UUID"):
        await server.commit_geometry(
            {"file_path": str(step), "name": "Bad", "source_node_ids": ["nope"]}
        )
