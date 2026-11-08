"""The release requires a passing ERC/DRC, a checked assembly and current outputs.

A desk lamp was released ("Ready for production", G8 passed) with a failing
board DRC, parts that could not be assembled and G-code sliced from older CAD
revisions. These tests hold the three checklist items that close those gaps:

* ``eda_checks``: missing (not assessed), failing (the violations named),
  passing, and accepted by a person or a delegated agent (a waiver);
* ``assembly_dfa``: no assembly, an assembly not checked, open findings,
  accepted findings, a clean check, an assembly of an older revision;
* ``outputs_current``: an output from an older revision or with no source
  fails, one made again from the current revision passes and supersedes it,
  and ``twin.create_release_package`` refuses stale outputs and lists each
  output's ``source_ref``.

Plus the tools: registered, classified, staged and refused without a person.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from api_gateway.library import routes as library_routes
from api_gateway.operations import publisher as ops
from api_gateway.twin import dfm_findings as dfm
from api_gateway.twin.assembly_fit import AssemblyFitError, AssemblyFitService
from api_gateway.twin.design_files_recorder import make_design_files_recorder
from api_gateway.twin.evidence_recorder import make_evidence_recorder
from api_gateway.twin.geometry_recorder import make_geometry_recorder
from api_gateway.twin.release_package import design_file_manifest, make_release_package_creator
from api_gateway.twin.release_path import ReleasePathService
from mcp_core.annotations import ADDITIVE, DESTRUCTIVE, PRODUCING, READ_ONLY
from mcp_core.guardrails import (
    APPROVED_BY_ARG,
    DELEGATED_AGENT_ARG,
    HumanAuthorityRequiredError,
    never_auto_approved,
    requires_human_authority,
    requires_release_delegation,
)
from mcp_core.profiles import CREATE_STAGE_TOOLS
from tests.unit.test_dfm_exact import STEP, VALIDATED, _cad, _project, _run, measured
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.consistency.gates import GateCheckStatus, evaluate_g8_release
from twin_core.consistency.record_pins import unregister_head_moved_listener
from twin_core.dfa.geometry import Body, Box, Pose, SimpleSolidEngine, matrix_from
from twin_core.dfm.release_checklist import GATE_ITEMS, manufacturing_checklist, readiness
from twin_core.dfm.release_fit import (
    accepted_waivers,
    evaluate_assembly_item,
    evaluate_eda_item,
    evaluate_outputs_item,
    output_sources,
)
from twin_core.items import commit_revision, plan_revision
from twin_core.library.machines import SqliteMachineLibrary
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

PROJECT = "7a1c0a52-1b5e-4b7a-9a43-3f1f2b9c0d07"
PID = UUID(PROJECT)


@pytest.fixture
def twin() -> Any:
    from twin_core.api import InMemoryTwinAPI

    return InMemoryTwinAPI.create()


@pytest.fixture(autouse=True)
def publisher() -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


@pytest.fixture(autouse=True)
def listener() -> Iterator[None]:
    dfm.register()
    yield
    unregister_head_moved_listener(dfm.on_head_moved)


@pytest.fixture(autouse=True)
def machine_library() -> Iterator[SqliteMachineLibrary]:
    library = SqliteMachineLibrary()
    library_routes.init_machine_library(library)
    yield library
    library_routes.init_machine_library(None)


@pytest.fixture(autouse=True)
def _blobs(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setenv("METAFORGE_DFM_SLICER_CHECK", "off")
    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )


async def _item(
    twin: Any,
    item_type: str,
    wp_type: WorkProductType,
    name: str,
    meta: dict[str, Any] | None = None,
    key: str | None = None,
) -> tuple[str, int, UUID]:
    plan = await plan_revision(
        twin, item_type=item_type, name=name, project_id=PID, author="test", item_key=key
    )
    assert plan is not None
    wp = WorkProduct(
        name=name,
        type=wp_type,
        domain="electronics",
        file_path="",
        content_hash=hashlib.sha256(f"{name}{uuid4()}".encode()).hexdigest(),
        format="kicad_pcb",
        metadata={**(meta or {}), **plan.stamp()},
        created_by="test",
        project_id=PID,
    )
    await twin.create_work_product(wp)
    await commit_revision(twin, plan, wp.id, name=name)
    return plan.key, plan.revision, wp.id


async def _board(twin: Any, name: str = "Driver board", key: str | None = None) -> str:
    k, rev, _ = await _item(twin, "pcb_layout", WorkProductType.PCB_LAYOUT, name, key=key)
    return f"{k}@{rev}"


DRC_FAIL = {
    "passed": False,
    "errors": 3,
    "warnings": 5,
    "unconnected_items": 0,
    "violations": [
        {
            "type": "copper_edge_clearance",
            "message": "clearance 0.405 mm < 0.5 mm",
            "severity": "error",
        },
        {
            "type": "copper_edge_clearance",
            "message": "clearance 0.468 mm < 0.5 mm",
            "severity": "error",
        },
        {
            "type": "copper_edge_clearance",
            "message": "clearance 0.47 mm < 0.5 mm",
            "severity": "error",
        },
        {"type": "silk_edge_clearance", "message": "H1 label clipped", "severity": "warning"},
    ],
}
DRC_PASS = {"passed": True, "errors": 0, "warnings": 2, "unconnected_items": 0, "violations": []}


async def _drc(twin: Any, ref: str, result: dict[str, Any], tool: str = "kicad.run_drc") -> str:
    out = await make_evidence_recorder(twin)(
        evidence_type="test",
        producer={"tool": tool},
        inputs={"pcb_file": "/workspace/board.kicad_pcb"},
        result=result,
        project_id=PROJECT,
        depends_on=[ref],
    )
    return str(out["node_id"])


# ── ERC and DRC on the released revisions (eda_checks) ────────────────────


async def test_eda_item_missing_failing_passing(twin: Any) -> None:
    item = await evaluate_eda_item(twin, PID, {})
    assert item.status == "not_applicable"

    ref = await _board(twin)
    item = await evaluate_eda_item(twin, PID, {})
    assert item.status == "fail"
    assert item.blockers == [f"{ref}: no DRC result recorded on this revision"]
    assert "kicad.run_drc" in item.next_steps[0] and ref in item.next_steps[0]

    # An ERC result does not count as the board's DRC.
    await _drc(twin, ref, DRC_PASS, tool="kicad.run_erc")
    assert (await evaluate_eda_item(twin, PID, {})).status == "fail"

    await _drc(twin, ref, DRC_FAIL)
    item = await evaluate_eda_item(twin, PID, {})
    assert item.status == "fail"
    (blocker,) = item.blockers
    assert "DRC fails: 3 error(s)" in blocker and "clearance 0.405 mm" in blocker
    assert f"target {ref}:drc" in item.next_steps[0]

    await _drc(twin, ref, DRC_PASS)
    item = await evaluate_eda_item(twin, PID, {})
    assert item.status == "pass" and "DRC passed (2 warning(s))" in item.detail


async def test_a_drc_on_an_older_revision_does_not_count(twin: Any) -> None:
    ref = await _board(twin)
    await _drc(twin, ref, DRC_PASS)
    key = ref.split("@")[0]
    ref2 = await _board(twin, key=key)
    assert ref2 == f"{key}@2"
    item = await evaluate_eda_item(twin, PID, {})
    assert item.status == "fail"
    assert item.blockers == [
        f"{ref2}: no DRC result recorded on this revision (the newest is on {ref})"
    ]


async def test_a_failing_drc_accepted_by_a_person_passes(twin: Any, tmp_path: Path) -> None:
    ref = await _board(twin)
    await _drc(twin, ref, DRC_FAIL)
    fit = AssemblyFitService(twin, cad_service=object(), workspace_dir=tmp_path)
    with pytest.raises(AssemblyFitError, match="not a current ERC or DRC target"):
        await fit.accept_release_finding(
            PROJECT, item="eda_checks", targets=["PCB-OTHER@1:drc"], reason="x", approved_by="lead"
        )
    with pytest.raises(AssemblyFitError, match="never accepted"):
        await fit.accept_release_finding(
            PROJECT, item="outputs_current", targets=[ref], reason="x", approved_by="lead"
        )
    out = await fit.accept_release_finding(
        PROJECT,
        item="eda_checks",
        targets=[f"{ref.lower()}:drc"],
        reason="Edge clearance of 0.4 mm is inside the fab house's 0.3 mm rule",
        approved_by="lead",
    )
    assert out["targets"] == [f"{ref}:drc"] and out["approved_by"] == "lead"
    assert out["depends_on"] == [ref]
    waivers = await accepted_waivers(twin, PID)
    item = await evaluate_eda_item(twin, PID, waivers)
    assert item.status == "pass"
    assert "accepted by lead: Edge clearance" in item.detail
    # The G8 gate counts the approved waiver too.
    g8 = await evaluate_g8_release(twin, PID)
    waivers_check = next(c for c in g8.checks if c.id == "waivers_approved")
    assert waivers_check.status is GateCheckStatus.PASS

    # A new board revision needs a new acceptance.
    await _board(twin, key=ref.split("@")[0])
    item = await evaluate_eda_item(twin, PID, await accepted_waivers(twin, PID))
    assert item.status == "fail"


async def test_the_release_gate_and_package_hold_a_failing_drc(
    twin: Any, machine_library: SqliteMachineLibrary
) -> None:
    from tests.unit.test_dfm_exact import _resolved_project

    await _resolved_project(twin, machine_library)
    checklist = await manufacturing_checklist(twin, PID)
    assert checklist is not None and checklist.releasable, checklist.blockers
    assert [i.id for i in checklist.items if i.status == "not_applicable"][-5:] == [
        "eda_checks",
        "assembly_dfa",
        "outputs_current",
        "bom_complete",
        "design_files_released",
    ]

    ref = await _board(twin)
    await _drc(twin, ref, DRC_FAIL)
    checklist = await manufacturing_checklist(twin, PID)
    assert checklist is not None and not checklist.releasable
    failing = [i.id for i in checklist.items if i.status == "fail"]
    # A part and a board: an assembly is needed too, and the board's BOM.
    assert failing == ["eda_checks", "assembly_dfa", "bom_complete"]
    assert readiness(checklist)["level"] == "in_progress"
    assert {
        "eda_checks",
        "assembly_dfa",
        "outputs_current",
        "bom_complete",
        "design_files_released",
    } <= set(GATE_ITEMS)

    g8 = await evaluate_g8_release(twin, PID)
    check = next(c for c in g8.checks if c.id == "manufacturing_release_checklist")
    assert check.status is GateCheckStatus.FAIL
    assert "DRC fails" in check.detail and "no assembly" in check.detail

    create = make_release_package_creator(twin, engineering_entity_recorder=_noop_recorder)
    with pytest.raises(ValueError, match="DRC fails"):
        await create(project_id=PROJECT)


async def _noop_recorder(**kw: Any) -> dict[str, Any]:  # pragma: no cover - never reached
    raise AssertionError("no package may be recorded")


# ── the assembly (assembly_dfa) ───────────────────────────────────────────


class FitCad:
    """The CAD adapter, stubbed: STEPs are named boxes, measured by the simple engine."""

    def __init__(self, boxes: dict[str, Box]) -> None:
        self.boxes = boxes
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def read_step(self, wp: Any) -> bytes:
        return STEP

    async def invoke_tool(self, tool_id: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_id, params))
        if tool_id == "cadquery.create_assembly":
            # As the adapter does: no output_path from the gateway, so it
            # writes into a folder of its own and returns the path.
            assert "output_path" not in params
            workspace = Path(params["parts"][0]["file"]).parents[2]
            out = workspace / "output" / "cadquery" / "assembly" / str(len(self.calls))
            out.mkdir(parents=True, exist_ok=True)
            (out / "assembly.step").write_bytes(STEP)
            return {"assembly_file": str(out / "assembly.step"), "interferences": []}
        assert tool_id == "cadquery.check_interference"
        bodies = [
            Body(p["name"], "", matrix_from(p["matrix"]), box=self.boxes[Path(p["file"]).stem])
            for p in params["parts"]
        ]
        result = await SimpleSolidEngine().measure(
            bodies,
            tolerance_mm3=params["tolerance_mm3"],
            distance_pairs=[tuple(p) for p in params["distance_pairs"]],
            poses=[
                Pose(
                    p["id"],
                    {k: matrix_from(v) for k, v in p["matrices"].items()},
                    tuple(tuple(x) for x in p["pairs"]),
                )
                for p in params["poses"]
            ],
        )

        def rows(overlaps: list[Any]) -> list[dict[str, Any]]:
            return [{"part_a": o.a, "part_b": o.b, "volume_mm3": o.volume_mm3} for o in overlaps]

        return {
            "interferences": rows(result.overlaps),
            "distances": [
                {"part_a": a, "part_b": b, "distance_mm": d}
                for (a, b), d in result.distances.items()
            ],
            "poses": [{"id": k, "interferences": rows(v)} for k, v in result.poses.items()],
        }


async def _parts(twin: Any) -> tuple[str, str, str]:
    """A base, a cover and the board's 3D export (which says which board it is)."""
    board = await _board(twin)
    base, _r, _n = await _cad(twin, "Base", VALIDATED)
    cover, _r, _n = await _cad(twin, "Cover", VALIDATED)
    body, _r, _n = await _cad(
        twin,
        "Driver board 3D",
        {**VALIDATED, "authored_by": "kicad.export_3d", "board_ref": board},
    )
    return base, cover, body


def _server(twin: Any, cad: Any, tmp_path: Path) -> TwinServer:
    return TwinServer(
        twin=twin,
        geometry_recorder=make_geometry_recorder(twin),
        release_path_service=ReleasePathService(twin, cad_service=cad, workspace_dir=tmp_path),
    )


BOXES = {
    "CAD-BASE": Box((0, 0, 0), (100, 60, 30)),
    "CAD-COVER": Box((0, 0, 0), (52, 37, 2.4)),
    "CAD-DRIVER-BOARD-3D": Box((0, 0, 0), (43, 28, 1.6)),
}
PLACED = [
    {"item_key": "CAD-BASE"},
    {"item_key": "CAD-COVER", "location": {"x": 10, "y": 10, "z": 30}},
    {"item_key": "CAD-DRIVER-BOARD-3D", "name": "Board", "location": {"x": 14, "y": 14, "z": 33}},
]


async def test_an_assembly_is_required_built_checked_and_its_findings_block(
    twin: Any, tmp_path: Path
) -> None:
    await _parts(twin)
    item = await evaluate_assembly_item(twin, PID, {})
    assert item.status == "fail" and item.blockers[0].startswith("no assembly")
    assert "twin.build_assembly" in item.next_steps[0]

    cad = FitCad(BOXES)
    server = _server(twin, cad, tmp_path)
    built = await server.build_assembly({"project_id": PROJECT, "name": "Lamp", "parts": PLACED})
    asm_ref = str(built["item_ref"])
    assert asm_ref.startswith("ASM-") and asm_ref.endswith("@1")
    layout = built["layout"]
    board = next(p for p in layout["parts"] if p["name"] == "Board")
    assert (
        board["item_ref"] == "CAD-DRIVER-BOARD-3D@1" and board["board_ref"] == "PCB-DRIVER-BOARD@1"
    )
    assert cad.calls[0][0] == "cadquery.create_assembly"

    item = await evaluate_assembly_item(twin, PID, {})
    assert item.status == "fail" and item.blockers == [f"{asm_ref}: not checked"]

    # With a board inside, the hole pattern and the outline are required: not
    # given and nothing to derive them from, the check is incomplete.
    out = await server.check_assembly({"project_id": PROJECT, "assembly": asm_ref})
    assert out["outcome"] == "incomplete"
    item = await evaluate_assembly_item(twin, PID, {})
    assert item.status == "fail"
    assert any("Hole patterns line up not assessed" in b for b in item.blockers)

    # The lamp's mismatch: the board's holes miss the cover's, and the board
    # does not fit the lip opening.
    checks = {
        "hole_patterns": [
            {
                "a": "Board",
                "holes_a": [[3.35, 3.35], [39.65, 3.35], [3.35, 24.65], [39.65, 24.65]],
                "b": "CAD-COVER",
                "holes_b": [[3.5, 3.5], [48.5, 3.5], [3.5, 33.5], [48.5, 33.5]],
            }
        ],
        "outlines": [
            {"part": "Board", "size_mm": [43, 28], "host": "Cover", "opening_mm": [36.8, 21.8]}
        ],
    }
    out = await server.check_assembly(
        {"project_id": PROJECT, "assembly": asm_ref, "checks": checks}
    )
    assert out["outcome"] == "issues_found"
    assert out["counts"]["high"] == 2
    # The board sits on the cover's holes with nothing holding it: an
    # unfastened joint (medium).
    assert out["counts"]["medium"] == 1
    ids = sorted(f["id"] for f in out["open_findings"])
    assert ids == [
        "fastener:CAD-COVER@1|CAD-DRIVER-BOARD-3D@1:unfastened",
        "hole_pattern:CAD-COVER@1|CAD-DRIVER-BOARD-3D@1",
        "outline_fit:CAD-COVER@1|CAD-DRIVER-BOARD-3D@1",
    ]
    assert out["checklist_item"] is None  # no exact DFM here: the checklist does not apply
    assert asm_ref in out["depends_on"] and "PCB-DRIVER-BOARD@1" in out["depends_on"]
    item = await evaluate_assembly_item(twin, PID, {})
    assert item.status == "fail" and len(item.blockers) == 3
    assert sum("high:" in b for b in item.blockers) == 2
    assert any("medium: unfastened joint" in b for b in item.blockers)

    # Accepted by a person, the findings no longer block.
    fit = server._assembly_fit()
    with pytest.raises(AssemblyFitError, match="not an open finding"):
        await fit.accept_release_finding(
            PROJECT, item="assembly_dfa", targets=["interference:x"], reason="r", approved_by="p"
        )
    await fit.accept_release_finding(
        PROJECT,
        item="assembly_dfa",
        targets=ids,
        reason="Holes drilled at assembly",
        approved_by="lead",
    )
    item = await evaluate_assembly_item(twin, PID, await accepted_waivers(twin, PID))
    assert item.status == "pass", item.blockers
    assert "3 accepted" in item.detail and "accepted by lead" in item.detail


async def test_a_clean_check_passes_and_a_new_part_revision_reopens_it(
    twin: Any, tmp_path: Path
) -> None:
    base, _cover, _body = await _parts(twin)
    server = _server(twin, FitCad(BOXES), tmp_path)
    built = await server.build_assembly(
        {
            "project_id": PROJECT,
            "name": "Lamp",
            "parts": PLACED,
            "joints": [
                {
                    "name": "cover lift",
                    "type": "revolute",
                    "parent": "Base",
                    "moving": ["Cover", "Board"],
                    "axis_point": [10, 0, 40],
                    "axis_dir": [0, 1, 0],
                    "min_deg": -60,
                    "max_deg": 0,
                    "steps": 4,
                    # A snap hinge: no pin expected on its axis.
                    "retention": "snap",
                }
            ],
        }
    )
    asm_ref = str(built["item_ref"])
    checks = {
        "hole_patterns": [
            {
                "a": "Board",
                "holes_a": [[3.35, 3.35], [39.65, 3.35]],
                "b": "Cover",
                "holes_b": [[7.35, 7.35], [43.65, 7.35]],
                "retention": "snap",
            }
        ],
        "outlines": [
            {"part": "Board", "size_mm": [43, 28], "host": "Cover", "opening_mm": [48, 33]}
        ],
    }
    out = await server.check_assembly(
        {"project_id": PROJECT, "assembly": asm_ref, "checks": checks}
    )
    # The cover lifts away from the base (negative turn about +Y lifts +X).
    motion = next(c for c in out["checks"] if c["check"] == "motion")
    assert motion["status"] == "passed", motion
    assert out["outcome"] == "passed", out["open_findings"]
    assert (await evaluate_assembly_item(twin, PID, {})).status == "pass"

    # The base moves on: the assembly holds an older revision and its check is stale.
    await _cad(twin, "Base", VALIDATED, key=base)
    item = await evaluate_assembly_item(twin, PID, {})
    assert item.status == "fail"
    assert any("holds older revisions: CAD-BASE@1 (now CAD-BASE@2)" in b for b in item.blockers)
    assert any("not checked" in b for b in item.blockers)


async def test_a_product_with_a_board_releases_once_it_is_checked(
    twin: Any, machine_library: SqliteMachineLibrary, tmp_path: Path
) -> None:
    """The whole path: a part and a board, DRC passed, assembly built and checked."""
    from tests.unit.test_dfm_exact import _resolved_project
    from twin_core.geometry_state import set_node_metadata

    enclosure, _node = await _resolved_project(twin, machine_library)
    board = await _board(twin)
    await _drc(twin, board, DRC_PASS)
    await _cad(
        twin, "Driver board 3D", {**VALIDATED, "authored_by": "kicad.export_3d", "board_ref": board}
    )
    server = _server(
        twin,
        FitCad(
            {
                enclosure: Box((0, 0, 0), (80, 60, 40)),
                "CAD-DRIVER-BOARD-3D": BOXES["CAD-DRIVER-BOARD-3D"],
            }
        ),
        tmp_path,
    )
    holes = [[3.35, 3.35], [39.65, 24.65]]
    screws = [
        {
            "designation": "ISO 4762 M2.5x8",
            "name": f"board screw {i + 1}",
            # The underside of the head on the board's top face (z 41 + 1.6).
            "location": {"x": x, "y": y, "z": 42.6},
            "holes": [
                {"part": "Board", "diameter_mm": 2.7, "length_mm": 1.6, "center": [x, y, 0]},
                {
                    "part": enclosure,
                    "diameter_mm": 2.1,
                    "fit": "thread_forming",
                    "depth_mm": 8,
                    "center": [x, y, 40],
                },
            ],
        }
        for i, (x, y) in enumerate(holes)
    ]
    built = await server.build_assembly(
        {
            "project_id": PROJECT,
            "name": "Lamp",
            "parts": [
                {"item_key": enclosure},
                {"item_key": "CAD-DRIVER-BOARD-3D", "name": "Board", "location": {"z": 41}},
            ],
            "fasteners": screws,
        }
    )
    assert [f["designation"] for f in built["layout"]["fasteners"]] == ["ISO 4762 M2.5x8"] * 2
    # The assembly is validated and exported like any CAD item.
    node = await twin.graph.get_node(UUID(built["node_id"]))
    await set_node_metadata(twin, node, **VALIDATED)
    checklist = await manufacturing_checklist(twin, PID)
    assert checklist is not None
    # Neither the board's 3D export nor the assembly needs a DFM analysis.
    assert checklist.item("dfm").status == "pass", checklist.item("dfm").detail
    assert checklist.item("assembly_dfa").status == "fail"

    out = await server.check_assembly(
        {
            "project_id": PROJECT,
            "assembly": built["item_ref"],
            "checks": {
                "hole_patterns": [
                    {"a": "Board", "holes_a": holes, "b": enclosure, "holes_b": holes}
                ],
                "outlines": [
                    {
                        "part": "Board",
                        "size_mm": [43, 28],
                        "host": enclosure,
                        "opening_mm": [76, 56],
                    }
                ],
            },
        }
    )
    assert out["outcome"] == "passed", out["open_findings"]
    fastener = next(c for c in out["checks"] if c["check"] == "fastener")
    assert fastener["status"] == "passed" and fastener["required"], fastener
    assert out["checklist_item"]["status"] == "pass"
    checklist = await manufacturing_checklist(twin, PID)
    assert checklist is not None and not checklist.releasable
    # The board's BOM and the assembly's (with its two screws) are still missing.
    assert [i.id for i in checklist.items if i.status == "fail"] == ["bom_complete"]
    record = make_design_files_recorder(twin, workspace_dir=str(tmp_path))
    (tmp_path / "board-bom.csv").write_text(
        "Refs,Value,Footprint,Qty\nR1 R2,10k,R_0402,2\nJ1,USB_C,USB4105,1\n"
    )
    (tmp_path / "asm-bom.csv").write_text(
        "Part,Description,Qty\nEnclosure,printed PLA,1\nISO 4762 M2.5x8,socket head cap screw,2\n"
    )
    await record(
        work_product_type="bom",
        file_path=str(tmp_path / "board-bom.csv"),
        name="Driver board BOM",
        project_id=PROJECT,
        source_ref=board,
    )
    await record(
        work_product_type="bom",
        file_path=str(tmp_path / "asm-bom.csv"),
        name="Lamp assembly BOM",
        project_id=PROJECT,
        source_ref=built["item_ref"],
    )
    checklist = await manufacturing_checklist(twin, PID)
    assert checklist is not None and checklist.releasable, checklist.blockers
    assert "2 line(s)" in checklist.item("bom_complete").detail
    assert readiness(checklist)["level"] == "production"


async def test_a_single_part_needs_no_assembly(twin: Any) -> None:
    await _cad(twin, "Bracket", VALIDATED)
    item = await evaluate_assembly_item(twin, PID, {})
    assert item.status == "not_applicable"


async def test_the_contract_supplies_the_hole_pattern_and_outline(
    twin: Any, tmp_path: Path
) -> None:
    from api_gateway.twin import interface_contracts as ic
    from twin_core.models.interface_contract import (
        ContractKind,
        ContractLink,
        CoupledParameter,
        InterfaceContract,
        ValueBasis,
    )

    board_ref = await _board(twin)
    cover, _r, _n = await _cad(
        twin,
        "Cover",
        {
            **VALIDATED,
            "mounting_bosses": [[3.5, 3.5], [48.5, 3.5], [3.5, 33.5], [48.5, 33.5]],
            "internal_envelope": {"width": 36.8, "height": 21.8},
        },
    )
    await _cad(
        twin,
        "Driver board 3D",
        {**VALIDATED, "authored_by": "kicad.export_3d", "board_ref": board_ref},
    )
    contract = InterfaceContract(
        kind=ContractKind.CAD_EDA,
        title="Board in cover",
        plain="The circuit board must fit inside the cover",
        owning_domains=["mechanical", "electronics"],
        parameters=[
            CoupledParameter(
                name="mounting_hole_positions",
                label="Holes",
                value=[[3.35, 3.35], [39.65, 3.35], [3.35, 24.65], [39.65, 24.65]],
                unit="mm",
                tolerance=0.2,
                owner="electronics",
                basis=ValueBasis.STATED,
            ),
            CoupledParameter(
                name="pcb_outline",
                label="Outline",
                value={"width": 43, "height": 28},
                unit="mm",
                owner="electronics",
                basis=ValueBasis.STATED,
            ),
        ],
        links=[
            ContractLink(role="enclosure", label="Cover", domain="mechanical", item_key=cover),
        ],
    )
    await ic.write_contract(twin, PROJECT, contract, actor="test", source="test")
    server = _server(
        twin,
        FitCad(
            {"CAD-COVER": BOXES["CAD-COVER"], "CAD-DRIVER-BOARD-3D": BOXES["CAD-DRIVER-BOARD-3D"]}
        ),
        tmp_path,
    )
    built = await server.build_assembly(
        {
            "project_id": PROJECT,
            "name": "Board in cover",
            "parts": [
                {"item_key": cover},
                {"item_key": "CAD-DRIVER-BOARD-3D", "location": {"x": 4, "y": 4, "z": 3}},
            ],
        }
    )
    out = await server.check_assembly({"project_id": PROJECT, "assembly": built["item_ref"]})
    assert any("hole pattern from IFC-CAD-EDA@" in n for n in out["derived"])
    assert any("board outline from IFC-CAD-EDA@" in n for n in out["derived"])
    failed = {c["check"] for c in out["checks"] if c["status"] == "failed"}
    # The contract's holes miss, and nothing fastens the board to them.
    assert failed == {"hole_pattern", "outline_fit", "fastener"}


async def test_check_assembly_refuses_what_is_not_a_built_assembly(
    twin: Any, tmp_path: Path
) -> None:
    base, _c, _b = await _parts(twin)
    server = _server(twin, FitCad(BOXES), tmp_path)
    with pytest.raises(ValueError, match="not an assembly"):
        await server.check_assembly({"project_id": PROJECT, "assembly": base})
    with pytest.raises(ValueError, match="is used twice"):
        await server.build_assembly(
            {
                "project_id": PROJECT,
                "name": "Lamp",
                "parts": [{"item_key": base}, {"item_key": base}],
            }
        )
    with pytest.raises(ValueError, match="not a part of this assembly"):
        await server.build_assembly(
            {
                "project_id": PROJECT,
                "name": "Lamp",
                "parts": [{"item_key": base}],
                "joints": [{"child": "Nope", "min_deg": 0, "max_deg": 10}],
            }
        )


# ── the waiver tool: a person, or a delegated agent for a person ──────────


async def test_accept_release_finding_needs_a_person_and_records_a_delegate(
    twin: Any, tmp_path: Path
) -> None:
    ref = await _board(twin)
    await _drc(twin, ref, DRC_FAIL)
    server = _server(twin, FitCad(BOXES), tmp_path)
    args = {
        "project_id": PROJECT,
        "item": "eda_checks",
        "targets": [f"{ref}:drc"],
        "reason": "Accepted by the fab house",
    }
    with pytest.raises(HumanAuthorityRequiredError):
        await server.accept_release_finding(dict(args))
    with pytest.raises(ValueError, match="approved_by"):
        await server.accept_release_finding({**args, "approved_by": "me"})
    out = await server.accept_release_finding(
        {**args, DELEGATED_AGENT_ARG: "claude-code-e2e", APPROVED_BY_ARG: "owner@example.com"}
    )
    assert out["approved_by"] == "agent:claude-code-e2e"
    assert out["on_behalf_of"] == "owner@example.com"
    item = await evaluate_eda_item(twin, PID, await accepted_waivers(twin, PID))
    assert item.status == "pass"
    assert "accepted by agent claude-code-e2e on behalf of owner@example.com" in item.detail


def test_the_tools_are_registered_classified_and_staged() -> None:
    server = TwinServer(twin=None, release_path_service=object())
    tools = set(server.tool_ids)
    assert {"twin.build_assembly", "twin.check_assembly", "twin.accept_release_finding"} <= tools
    assert "twin.build_assembly" in PRODUCING and "twin.check_assembly" in ADDITIVE
    assert "cadquery.check_interference" in READ_ONLY
    assert "twin.accept_release_finding" in DESTRUCTIVE
    # The waiver is a person's: held for them, or delegated under the release flag.
    assert requires_human_authority("twin.accept_release_finding")
    assert requires_release_delegation("twin.accept_release_finding")
    assert never_auto_approved("twin.accept_release_finding")
    assert not requires_human_authority("twin.check_assembly")
    assert {"twin.build_assembly", "twin.check_assembly", "kicad.export_3d"} <= (
        CREATE_STAGE_TOOLS["check"]
    )
    assert {
        "twin.check_assembly",
        "twin.accept_release_finding",
        "twin.record_design_files",
    } <= CREATE_STAGE_TOOLS["make"]


def test_the_playbook_and_imagine_name_the_fit_steps() -> None:
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
        for tool in (
            "kicad.export_3d",
            "twin.build_assembly",
            "twin.check_assembly",
            "twin.accept_release_finding",
            "source_ref",
            "kicad.run_drc",
        ):
            assert tool in text, tool


# ── fabrication outputs and their sources (outputs_current) ───────────────


async def test_outputs_pinned_to_an_older_revision_fail_and_are_refused(
    twin: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = b"solid head r1"
    sha = hashlib.sha256(model).hexdigest()
    head, _r, _n = await _cad(twin, "Head", {**VALIDATED, "content_sha256": sha})
    record = make_design_files_recorder(twin, workspace_dir=str(tmp_path))

    # slicer.slice's folder: the G-code and the source.json naming the model it sliced.
    run = tmp_path / "output" / "slicer" / "a1"
    run.mkdir(parents=True)
    (run / "head.gcode").write_text("; generated\nG1 X0\n")
    (run / "source.json").write_text(
        json.dumps({"model_path": "/workspace/head.step", "model_sha256": sha})
    )
    out = await record(
        work_product_type="gcode", file_path=str(run), name="Head G-code", project_id=PROJECT
    )
    assert out["source_ref"] == f"{head}@1"
    assert evaluate_outputs_item(await output_sources(twin, PID)).status == "pass"

    # An output that says nothing about its source is unpinned.
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "arm.gcode").write_text("G1\n")
    out = await record(
        work_product_type="gcode", file_path=str(loose), name="Arm G-code", project_id=PROJECT
    )
    assert out["source_ref"] is None
    assert any("no source revision" in w for w in out["warnings"])
    item = evaluate_outputs_item(await output_sources(twin, PID))
    assert item.status == "fail"
    assert item.blockers == ["GC-ARM-G-CODE@1 (gcode) does not say which revision it was made from"]

    # Recorded again with its source, it is current again.
    await record(
        work_product_type="gcode",
        file_path=str(loose),
        name="Arm G-code",
        project_id=PROJECT,
        source_ref=head,
    )
    assert evaluate_outputs_item(await output_sources(twin, PID)).status == "pass"

    # The head moves on: both G-codes were made from CAD-HEAD@1.
    await _cad(twin, "Head", VALIDATED, key=head)
    rows = await output_sources(twin, PID)
    assert {r.status for r in rows} == {"stale"}
    item = evaluate_outputs_item(rows)
    assert item.status == "fail"
    assert f"was made from {head}@1; the current revision is {head}@2" in item.detail

    manifest = await design_file_manifest(twin, PID)
    gcode = [r for r in manifest if r["kind"] == "gcode"]
    assert {r["source_ref"] for r in gcode} == {f"{head}@1"}
    assert {r["source_status"] for r in gcode} == {"stale"}
    cad_row = next(r for r in manifest if r["kind"] == "cad_model")
    assert cad_row["source_ref"] == f"{head}@2"

    # The package is refused even where the manufacturing checklist does not apply.
    import api_gateway.twin.release_package as release_package
    from twin_core.consistency.gates import GateEvaluation, GateStatus

    async def passed(*_a: Any, **_k: Any) -> GateEvaluation:
        return GateEvaluation(gate_id="G8", status=GateStatus.PASSED, checks=[])

    monkeypatch.setattr(release_package, "evaluate_g8_release", passed)
    create = make_release_package_creator(twin, engineering_entity_recorder=_noop_recorder)
    with pytest.raises(ValueError, match="not made from the released revisions"):
        await create(project_id=PROJECT)

    # Sliced again from the current head, the newer output supersedes the
    # older one of the same part; the other is made again under its own name.
    await record(
        work_product_type="gcode",
        file_path=str(run),
        name="Head G-code r2",
        project_id=PROJECT,
        source_ref=f"{head}@2",
    )
    await record(
        work_product_type="gcode",
        file_path=str(loose),
        name="Arm G-code",
        project_id=PROJECT,
        source_ref=head,
    )
    rows = await output_sources(twin, PID)
    assert sorted(r.status for r in rows) == ["current", "current", "superseded"]
    assert evaluate_outputs_item(rows).status == "pass"
    manifest = await design_file_manifest(twin, PID)
    assert sorted(r["source_status"] for r in manifest if r["kind"] == "gcode") == [
        "current",
        "current",
    ]


async def test_record_design_files_refuses_a_source_that_is_not_a_design(
    twin: Any, tmp_path: Path
) -> None:
    record = make_design_files_recorder(twin, workspace_dir=str(tmp_path))
    (tmp_path / "x.gcode").write_text("G1\n")
    with pytest.raises(ValueError, match="no item CAD-NOPE"):
        await record(
            work_product_type="gcode",
            file_path=str(tmp_path / "x.gcode"),
            name="X",
            project_id=PROJECT,
            source_ref="CAD-NOPE@1",
        )


# ── twin.render_work_product records nothing unless asked ─────────────────


async def test_render_work_product_does_not_store_by_default() -> None:
    calls: list[dict[str, Any]] = []

    async def renderer(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"stored": kwargs["store"], "images": []}

    server = TwinServer(twin=object(), work_product_renderer=renderer)
    await server.render_work_product({"work_product_id": "wp-1"})
    await server.render_work_product({"work_product_id": "wp-1", "store": True})
    assert [c["store"] for c in calls] == [False, True]


# keep the import used by the DFM helpers this module shares
_ = (_project, _run, measured)
