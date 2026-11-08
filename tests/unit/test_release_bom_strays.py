# ruff: noqa: F811 - the twin fixture is imported from test_release_fit and used by name
"""Fasteners in the assembly, connectors read off the board, the BOM, stray layouts.

The desk lamp's release (0 assembly findings, checklist releasable) held:

* a USB-C receptacle whose mouth faced +Y in the assembly while its slot was
  in the +X wall, which the check never compared because nobody passed the
  connector: the board file says where J1 is and which way it turns;
* no hinge pin and no screw in the assembly, and a BOM of 0 items;
* a second, unrouted board with no item key inside the release package next
  to the released board.

These tests hold the service and checklist side of each: ``twin.build_assembly``
with fasteners, ``twin.check_assembly`` deriving the connector from the board
file and the slot from the part, ``twin.record_design_files`` with ``bom``,
the ``bom_complete`` and ``design_files_released`` items, the release
package refusing and leaving out a stray layout.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from api_gateway.twin import assembly_fit
from api_gateway.twin.design_files_recorder import (
    bom_scope,
    make_design_files_recorder,
    read_bom_lines,
)
from api_gateway.twin.release_package import design_file_manifest, make_release_package_creator
from tests.unit.test_dfa_connectors_fasteners import BOARD_FILE
from tests.unit.test_dfm_exact import VALIDATED, _cad
from tests.unit.test_release_fit import (  # noqa: F401 - fixtures used by name
    PID,
    PROJECT,
    FitCad,
    _blobs,
    _board,
    _item,
    _server,
    listener,
    machine_library,
    publisher,
    twin,
)
from twin_core.dfa.geometry import Box
from twin_core.dfm.release_fit import (
    accepted_waivers,
    evaluate_bom_item,
    evaluate_design_files_item,
    purchased_parts,
    stray_design_files,
)
from twin_core.library.standard_parts import standard_part_by_designation
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

LAMP_BOXES = {
    "CAD-LAMP-BASE": Box((-65, -45, -10), (130, 90, 40)),
    "CAD-DRIVER-BOARD-3D": Box((0, 0, 0), (46, 32, 1.6)),
}


async def _lamp(twin: Any, slots: list[dict[str, Any]] | None = None) -> tuple[str, str]:
    board = await _board(twin)
    meta = {**VALIDATED, **({"connector_slots": slots} if slots else {})}
    await _cad(twin, "Lamp base", meta)
    await _cad(
        twin,
        "Driver board 3D",
        {**VALIDATED, "authored_by": "kicad.export_3d", "board_ref": board},
    )
    return board, "CAD-LAMP-BASE"


LAMP_PARTS = [
    {"item_key": "CAD-LAMP-BASE", "name": "Base"},
    {
        "item_key": "CAD-DRIVER-BOARD-3D",
        "name": "Board",
        "location": {"x": 59.5, "y": 16, "z": 21, "rz": 180},
    },
]
INSIDE = {"a": "Board", "b": "Base", "reason": "the board sits in the base's pocket"}


# ── twin.build_assembly with fasteners ────────────────────────────────────


async def test_fasteners_go_into_the_step_and_the_layout(twin: Any, tmp_path: Path) -> None:
    await _lamp(twin)
    cad = FitCad(LAMP_BOXES)
    server = _server(twin, cad, tmp_path)
    pin = {
        "designation": "ISO 2338 4x30",
        "name": "base hinge pin",
        "location": {"x": -50, "y": -15, "z": 40, "rx": -90},
        "holes": [{"part": "Base", "diameter_mm": 4.4, "length_mm": 30}],
    }
    screw = {
        "designation": "M2.5x6",
        "location": {"x": 20, "y": 10, "z": 22.6},
        "holes": [{"part": "Board", "diameter_mm": 2.7, "length_mm": 1.6}],
        "washers": ["ISO 7089 M2.5"],
        "nut": "ISO 4032 M2.5",
    }
    built = await server.build_assembly(
        {"project_id": PROJECT, "name": "Lamp", "parts": LAMP_PARTS, "fasteners": [pin, screw]}
    )
    layout = built["layout"]
    assert [f["name"] for f in layout["fasteners"]] == ["base hinge pin", "M2.5x6 #2"]
    assert layout["fasteners"][0]["kind"] == "pin"
    assert layout["fasteners"][1]["washers"] == ["ISO 7089 M2.5"]
    # The adapter is given each as a standard part shape at its location.
    tool, params = cad.calls[0]
    assert tool == "cadquery.create_assembly"
    shapes = {p["name"]: p for p in params["parts"] if p.get("shape")}
    assert shapes["base hinge pin"]["shape"]["generator"] == "parallel_pin"
    assert shapes["M2.5x6 #2"]["shape"]["generator"] == "socket_head_cap_screw"
    assert shapes["M2.5x6 #2 washer 1"]["shape"]["z_offset"] == 0.0
    # The nut sits under the washer and the 1.6 mm board.
    washer = standard_part_by_designation("ISO 7089 M2.5")["specs"]["thickness_mm"]["value"]
    assert shapes["M2.5x6 #2 nut"]["shape"]["z_offset"] == pytest.approx(-washer - 1.6)
    assert all("file" not in p for p in shapes.values())
    # What the assembly BOM must list.
    bought = {(p.label, p.qty) for p in purchased_parts(layout)}
    assert bought == {
        ("ISO 2338 4x30", 1),
        ("M2.5x6", 1),
        ("ISO 7089 M2.5", 1),
        ("ISO 4032 M2.5", 1),
    }

    with pytest.raises(ValueError, match="'location' is required"):
        await server.build_assembly(
            {
                "project_id": PROJECT,
                "name": "Lamp",
                "parts": LAMP_PARTS,
                "fasteners": [{"designation": "M2.5x6"}],
            }
        )
    with pytest.raises(ValueError, match="no metric size"):
        await server.build_assembly(
            {
                "project_id": PROJECT,
                "name": "Lamp",
                "parts": LAMP_PARTS,
                "fasteners": [{"designation": "a screw", "location": {}}],
            }
        )
    with pytest.raises(ValueError, match="not a part of this assembly"):
        await server.build_assembly(
            {
                "project_id": PROJECT,
                "name": "Lamp",
                "parts": LAMP_PARTS,
                "fasteners": [{**pin, "holes": [{"part": "Lid", "diameter_mm": 4.4}]}],
            }
        )


# ── twin.check_assembly reads the connector off the board ─────────────────


async def test_the_board_files_usb_c_and_the_bases_slot_make_the_lamp_finding(
    twin: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lamp: J1 faces +Y in the assembly, its slot is in the +X wall."""
    monkeypatch.setattr(assembly_fit, "read_board_text", lambda node: BOARD_FILE)
    slot = {"reference": "J1", "center": [65, 7.5, 21], "normal": [1, 0, 0], "size_mm": [10, 7.8]}
    await _lamp(twin, slots=[slot])
    server = _server(twin, FitCad(LAMP_BOXES), tmp_path)
    built = await server.build_assembly(
        {"project_id": PROJECT, "name": "Lamp", "parts": LAMP_PARTS}
    )
    out = await server.check_assembly(
        {
            "project_id": PROJECT,
            "assembly": built["item_ref"],
            "checks": {"allowed_overlaps": [INSIDE]},
        }
    )
    connector = next(c for c in out["checks"] if c["check"] == "connector")
    assert connector["required"] and connector["status"] == "failed"
    (finding,) = connector["findings"]
    assert finding["severity"] == "high" and finding["id"].endswith("J1:axis")
    assert finding["values"]["mating_axis"] == pytest.approx([0.0, 1.0, 0.0])
    assert finding["values"]["slot_normal"] == [1.0, 0.0, 0.0]
    assert any("slot from Base's connector_slots" in n for n in out["derived"])
    assert finding["id"] in {f["id"] for f in out["open_findings"]}


async def test_an_edge_connector_with_no_slot_makes_the_check_incomplete(
    twin: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(assembly_fit, "read_board_text", lambda node: BOARD_FILE)
    await _lamp(twin)
    server = _server(twin, FitCad(LAMP_BOXES), tmp_path)
    built = await server.build_assembly(
        {"project_id": PROJECT, "name": "Lamp", "parts": LAMP_PARTS}
    )
    out = await server.check_assembly(
        {
            "project_id": PROJECT,
            "assembly": built["item_ref"],
            "checks": {"allowed_overlaps": [INSIDE]},
        }
    )
    connector = next(c for c in out["checks"] if c["check"] == "connector")
    assert connector["required"] and connector["status"] == "not_assessed"
    assert any("J1 (Connector_USB:USB_C_Receptacle" in n for n in out["derived"])
    assert out["outcome"] in ("incomplete", "issues_found")

    # Named by reference, J1 takes its footprint from the board file.
    out = await server.check_assembly(
        {
            "project_id": PROJECT,
            "assembly": built["item_ref"],
            "checks": {
                "allowed_overlaps": [INSIDE],
                "connectors": [
                    {
                        "board": "Board",
                        "reference": "J1",
                        "slot_part": "Base",
                        "slot_normal": [0, 1, 0],
                    }
                ],
            },
        }
    )
    connector = next(c for c in out["checks"] if c["check"] == "connector")
    assert connector["status"] == "passed", connector
    assert any("J1: footprint Connector_USB" in n for n in out["derived"])


# ── the BOM ───────────────────────────────────────────────────────────────


def test_bom_lines_are_read_from_csv_and_json(tmp_path: Path) -> None:
    kicad = tmp_path / "board.csv"
    kicad.write_text(
        '"Refs","Value","Footprint","Qty","DNP"\n'
        '"C1,C2","100n","C_0402","2",""\n'
        '"R9","0R","R_0402","1","DNP"\n'
        '"U1","STM32","QFN","1",""\n'
    )
    lines = read_bom_lines(kicad)
    assert [(line["part"], line["qty"], line["refs"]) for line in lines] == [
        ("100n", 2, ["C1", "C2"]),
        ("STM32", 1, ["U1"]),
    ]
    js = tmp_path / "asm.json"
    js.write_text('{"lines": [{"designation": "ISO 4762 M2.5x8", "qty": 4}]}')
    assert read_bom_lines(js)[0]["qty"] == 4
    assert bom_scope("PCB-LAMP@4") == "electronics"
    assert bom_scope("ASM-LAMP@2") == "mechanical"
    assert bom_scope(None) == "unpinned"


async def test_the_release_needs_the_boards_bom_and_every_bought_part(
    twin: Any, tmp_path: Path
) -> None:
    board, _base = await _lamp(twin)
    item = await evaluate_bom_item(twin, PID)
    assert item.status == "fail"
    assert item.blockers == [f"{board}: no BOM made from this board"]
    assert "kicad.export_bom" in item.next_steps[0] and f"source_ref {board}" in item.next_steps[0]

    record = make_design_files_recorder(twin, workspace_dir=str(tmp_path))
    empty = tmp_path / "empty.csv"
    empty.write_text("Refs,Value,Qty\n")
    out = await record(
        work_product_type="bom",
        file_path=str(empty),
        name="Driver BOM",
        project_id=PROJECT,
        source_ref=board,
    )
    assert out["line_items"] == 0 and out["bom_scope"] == "electronics"
    assert any("lists nothing" in w for w in out["warnings"])
    item = await evaluate_bom_item(twin, PID)
    assert item.status == "fail" and "lists nothing" in item.blockers[0]

    full = tmp_path / "board.csv"
    full.write_text("Refs,Value,Qty\nJ1,USB_C,1\n")
    await record(
        work_product_type="bom",
        file_path=str(full),
        name="Driver BOM",
        project_id=PROJECT,
        source_ref=board,
    )
    assert (await evaluate_bom_item(twin, PID)).status == "pass"

    # An assembly with a hinge pin and four cover screws: its BOM must list them.
    server = _server(twin, FitCad(LAMP_BOXES), tmp_path)
    screws = [
        {"designation": "ISO 4762 M2.5x8", "name": f"cover screw {i}", "location": {"x": i}}
        for i in range(4)
    ]
    pin = {"designation": "ISO 2338 4x30", "location": {"x": -50}}
    built = await server.build_assembly(
        {
            "project_id": PROJECT,
            "name": "Lamp",
            "parts": LAMP_PARTS,
            "fasteners": [*screws, pin],
        }
    )
    asm = built["item_ref"]
    item = await evaluate_bom_item(twin, PID)
    assert item.status == "fail"
    assert item.blockers[0].startswith(f"{asm}: no BOM of the assembly")
    assert "4 x ISO 4762 M2.5x8" in item.blockers[0] and "1 x ISO 2338 4x30" in item.blockers[0]

    short = tmp_path / "asm.csv"
    short.write_text("Part,Qty\nLamp base,1\nM2.5x8 socket head cap screw,2\n")
    await record(
        work_product_type="bom",
        file_path=str(short),
        name="Lamp BOM",
        project_id=PROJECT,
        source_ref=asm,
    )
    item = await evaluate_bom_item(twin, PID)
    assert item.status == "fail"
    assert item.blockers == [
        f"{asm}: the BOM Lamp BOM does not list 4 x ISO 4762 M2.5x8 (lists 2), 1 x ISO 2338 4x30"
    ]

    short.write_text(
        "Part,Qty\nLamp base,1\nM2.5x8 socket head cap screw,4\nISO 2338 4 m6 x 30 pin,1\n"
    )
    await record(
        work_product_type="bom",
        file_path=str(short),
        name="Lamp BOM",
        project_id=PROJECT,
        source_ref=asm,
    )
    item = await evaluate_bom_item(twin, PID)
    assert item.status == "pass", item.blockers
    assert "2 bought part(s) listed" in item.detail


# ── a stray layout in the package ─────────────────────────────────────────


async def _orphan_board(twin: Any, name: str) -> str:
    wp = WorkProduct(
        name=name,
        type=WorkProductType.PCB_LAYOUT,
        domain="electronics",
        file_path="",
        content_hash=hashlib.sha256(f"{name}{uuid4()}".encode()).hexdigest(),
        format="kicad_pcb",
        metadata={"tracks": 0},
        created_by="test",
        project_id=PID,
    )
    await twin.create_work_product(wp)
    return str(wp.id)


async def test_a_board_that_is_no_items_revision_is_left_out_and_refused(
    twin: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board = await _board(twin)
    key = board.split("@")[0]
    await _item(twin, "pcb_layout", WorkProductType.PCB_LAYOUT, "Driver board", key=key)
    orphan = await _orphan_board(twin, "Driver board rev with J1 at left board edge")

    strays = await stray_design_files(twin, PID)
    assert {(s.status, s.node_id) for s in strays if s.status == "orphan"} == {("orphan", orphan)}
    assert [s.reason for s in strays if s.status == "superseded"] == [
        f"is {key}@1; the head is {key}@2"
    ]
    manifest = await design_file_manifest(twin, PID)
    boards = [r for r in manifest if r["kind"] == "pcb_layout"]
    assert [r["ref"] for r in boards] == [f"{key}@2"]
    assert orphan not in {r["node_id"] for r in manifest}

    item = await evaluate_design_files_item(twin, PID, {})
    assert item.status == "fail"
    assert item.blockers == [
        f"pcb_layout 'Driver board rev with J1 at left board edge' ({orphan[:8]}) is not a "
        "revision of any item: it is left out of the package"
    ]
    assert "left out: 1 older revision(s)" in item.detail
    assert f"target {orphan}" in item.next_steps[0]

    import api_gateway.twin.release_package as release_package
    from twin_core.consistency.gates import GateEvaluation, GateStatus

    async def passed(*_a: Any, **_k: Any) -> GateEvaluation:
        return GateEvaluation(gate_id="G8", status=GateStatus.PASSED, checks=[])

    recorded: list[dict[str, Any]] = []

    async def recorder(**kw: Any) -> dict[str, Any]:
        recorded.append(kw)
        return {"node_id": "pkg"}

    monkeypatch.setattr(release_package, "evaluate_g8_release", passed)
    create = make_release_package_creator(twin, engineering_entity_recorder=recorder)
    with pytest.raises(ValueError, match="not the released revision of any item"):
        await create(project_id=PROJECT)
    assert not recorded

    # A person accepts leaving it out: the item passes and the package is cut
    # without it, naming it among the files left out.
    server = _server(twin, FitCad({}), tmp_path)
    fit = server._assembly_fit()
    with pytest.raises(assembly_fit.AssemblyFitError, match="not a stray design file"):
        await fit.accept_release_finding(
            PROJECT,
            item="design_files_released",
            targets=[str(uuid4())],
            reason="r",
            approved_by="lead",
        )
    await fit.accept_release_finding(
        PROJECT,
        item="design_files_released",
        targets=[orphan],
        reason="An abandoned layout experiment; the released board is the routed one",
        approved_by="lead",
    )
    item = await evaluate_design_files_item(twin, PID, await accepted_waivers(twin, PID))
    assert item.status == "pass", item.blockers
    assert "accepted by lead" in item.detail and f"packaged: {key}@2" in item.detail
    await create(project_id=PROJECT)
    (kw,) = recorded
    assert orphan in {r["node_id"] for r in kw["extra"]["excluded_design_files"]}
    assert orphan not in {r["node_id"] for r in kw["extra"]["design_files"]}


def test_the_playbook_and_imagine_name_fasteners_connector_axes_and_the_bom() -> None:
    from mcp_core.workflows import WORKFLOWS

    skill = (
        Path(__file__).resolve().parents[2]
        / "mcp_core"
        / "plugin_skills"
        / "imagine_studio"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    imagine = WORKFLOWS["imagine"][1]
    for text in (skill, imagine):
        for words in (
            "fasteners",
            "ISO 2338 4x30",
            "unfastened joint",
            "mating_axis",
            "slot_normal",
            "slot_size_mm",
            "`work_product_type: bom`",
            "kicad.export_bom",
            "ASM-...@n",
            "item's revision",
        ):
            assert words in text, words
