"""The release path an agent can finish on its own.

* DFM by reference: ``twin.measure_dfm`` reads a committed revision's STEP from
  the twin (the agent never passes STEP text) and stores its exact set;
  ``twin.evaluate_dfm`` measures what has no exact analysis yet by itself.
* Concept components: ``twin.resolve_concept_component`` maps a ``CMP-`` to
  the CAD items that realise it, or retires it, reversibly; the maturity and
  the release checklist honour both.
* The FEA chain: ``twin.run_item_fea`` meshes then solves a CAD item.
* The release package pins a manifest of the design files.
* The tools are classified, staged and described so an agent finds them.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from api_gateway.library import routes as library_routes
from api_gateway.operations import publisher as ops
from api_gateway.twin import dfm_exact, exact_cad
from api_gateway.twin import dfm_findings as dfm
from api_gateway.twin import manufacturing_context as mc
from api_gateway.twin.decision_recorder import make_decision_recorder
from api_gateway.twin.release_package import design_file_manifest, manifest_digest
from api_gateway.twin.release_path import (
    ReleasePathError,
    ReleasePathService,
    default_element_size,
    make_release_path_service,
    select_surface,
)
from mcp_core.annotations import ADDITIVE, PRODUCING, annotations_for
from mcp_core.guardrails import never_auto_approved, policy_may_approve, requires_human_authority
from mcp_core.profiles import CREATE_STAGE_TOOLS
from tests.unit.test_dfm_exact import MINI, STEP, VALIDATED, _cad, measured
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.component_resolution import (
    ComponentResolutionError,
    current_resolutions,
    record_resolution,
)
from twin_core.consistency.record_pins import unregister_head_moved_listener
from twin_core.dfm.release_checklist import manufacturing_checklist
from twin_core.library.machines import SqliteMachineLibrary
from twin_core.maturity import compute_maturity
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

PROJECT = "7a1c0a52-1b5e-4b7a-9a43-3f1f2b9c0d07"
PID = UUID(PROJECT)
ENCLOSURE = "USB-C desk lamp enclosure"
LAMP: dict[str, Any] = {"parts": [{"name": ENCLOSURE}], "connections": []}


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


class Cad:
    """The exact CAD service, stubbed: one STEP per revision and canned tool results."""

    def __init__(self, results: dict[str, Any] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def read_step(self, wp: Any) -> bytes:
        return STEP

    async def invoke_tool(self, tool_id: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_id, params))
        result = self.results.get(tool_id)
        if isinstance(result, Exception):
            raise result
        return dict(result or {})


@pytest.fixture
def cad(monkeypatch: pytest.MonkeyPatch) -> Cad:
    service = Cad({"cadquery.dfm_measure": measured()})
    monkeypatch.setattr(exact_cad, "_service", service)
    return service


async def _lamp(
    twin: Any, machine_library: SqliteMachineLibrary, parts: tuple[str, ...]
) -> list[str]:
    """A lamp whose concept names one illustrative enclosure, built as validated parts."""
    await make_decision_recorder(twin)(
        title="Concept", rationale="chosen", assembly=LAMP, project_id=PROJECT
    )
    keys = []
    for name in parts:
        key, _rev, _node = await _cad(twin, name, VALIDATED)
        keys.append(key)
    machine_library.register(MINI)
    await mc.write_context(
        twin,
        PROJECT,
        {
            "selected_process": mc.stated("FDM", source="api"),
            "selected_material": mc.stated("PETG", source="api"),
            "machine_or_supplier_profile": mc.stated({"machine": "Workshop Mini"}, source="api"),
        },
        source="api",
    )
    return keys


def _server(twin: Any) -> TwinServer:
    return TwinServer(
        twin=twin,
        dfm_service=dfm.make_dfm_service(twin),
        release_path_service=make_release_path_service(twin),
    )


# ── DFM by reference ─────────────────────────────────────────────────────


async def test_measure_dfm_reads_the_step_from_the_twin(
    twin: Any, machine_library: SqliteMachineLibrary, cad: Cad
) -> None:
    (key,) = await _lamp(twin, machine_library, ("Lamp head",))
    out = await _server(twin).measure_dfm({"project_id": PROJECT, "item_key": f"{key}@1"})
    assert out["measured"] is True and out["status"] == "analysis_passed"
    assert out["item_ref"] == f"{key}@1" and out["complete"]
    tool, params = cad.calls[0]
    # The gateway sent the stored STEP; the agent sent only a reference.
    assert tool == "cadquery.dfm_measure"
    assert base64.b64decode(params["step_base64"]) == STEP
    (comp,) = [c for c in (await dfm.dfm_view(twin, PROJECT)).components if c.kind == "exact"]
    assert comp.component_id == f"item:{key}" and comp.status == "analysis_passed"


async def test_measure_dfm_refuses_what_it_cannot_measure(
    twin: Any, machine_library: SqliteMachineLibrary, cad: Cad
) -> None:
    await _lamp(twin, machine_library, ())
    key, _rev, _node = await _cad(twin, "Draft arm", {"geometry_state": "exact_ready"})
    server = _server(twin)
    with pytest.raises(ValueError, match="validated exact model"):
        await server.measure_dfm({"project_id": PROJECT, "item_key": key})
    with pytest.raises(ValueError, match="item_key"):
        await server.measure_dfm({"project_id": PROJECT})
    with pytest.raises(ValueError, match="build_direction"):
        await server.measure_dfm({"project_id": PROJECT, "item_key": key, "build_direction": [1]})


async def test_evaluate_dfm_measures_what_has_no_exact_analysis(
    twin: Any, machine_library: SqliteMachineLibrary, cad: Cad
) -> None:
    keys = await _lamp(twin, machine_library, ("Base", "Lower arm"))
    view = await _server(twin).evaluate_dfm({"project_id": PROJECT})
    lines = {m["item_ref"]: m for m in view["measured"]}
    assert set(lines) == {f"{k}@1" for k in keys}
    assert all(m["status"] == "analysis_passed" for m in lines.values())
    exact = [c for c in view["components"] if c["kind"] == "exact"]
    assert len(exact) == 2 and not view["heuristic_only"]
    # Measured once: the next call reuses the exact sets.
    again = await _server(twin).evaluate_dfm({"project_id": PROJECT})
    assert "measured" not in again and len(cad.calls) == 2


async def test_evaluate_dfm_can_skip_measuring_and_says_when_it_cannot(
    twin: Any, machine_library: SqliteMachineLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _lamp(twin, machine_library, ("Base",))
    monkeypatch.setattr(exact_cad, "_service", None)
    server = _server(twin)
    skipped = await server.evaluate_dfm({"project_id": PROJECT, "measure": False})
    assert "measured" not in skipped
    view = await server.evaluate_dfm({"project_id": PROJECT})
    (line,) = view["measured"]
    assert line["status"] == "not_measured" and "not available" in line["reason"]
    # No incomplete exact set replaced the guidance.
    assert all(c["kind"] != "exact" for c in view["components"])
    with pytest.raises(ValueError, match="measure"):
        await server.evaluate_dfm({"project_id": PROJECT, "measure": "yes"})


async def test_measuring_stops_at_its_limit_and_lists_the_rest(
    twin: Any, machine_library: SqliteMachineLibrary, cad: Cad
) -> None:
    await _lamp(twin, machine_library, ("Base", "Head"))
    evaluated = await dfm.evaluate_project(twin, PROJECT)
    out = await dfm_exact.measure_missing(twin, PROJECT, evaluated, limit=1)
    assert out[0]["status"] == "analysis_passed"
    assert out[-1]["status"] == "deferred" and len(out[-1]["item_refs"]) == 1


# ── concept components ───────────────────────────────────────────────────


async def _component_id(twin: Any) -> str:
    from twin_core.component_identity import project_identity

    ident = await project_identity(twin, PID)
    return str(ident["name_to_id"][ENCLOSURE])


async def test_an_illustrative_concept_component_blocks_until_it_is_resolved(
    twin: Any, machine_library: SqliteMachineLibrary, cad: Cad
) -> None:
    keys = await _lamp(twin, machine_library, ("Base", "Lower arm"))
    for key in keys:
        await dfm_exact.measure(twin, PROJECT, item_key=key)
    before = await manufacturing_checklist(twin, PID)
    assert before is not None
    item = before.item("components_exact")
    assert item.status == "fail" and f"{ENCLOSURE} is Illustrative" in item.detail
    assert "realise it or that it is retired" in item.next_steps[0]

    cid = await _component_id(twin)
    out = await _server(twin).resolve_concept_component(
        {
            "project_id": PROJECT,
            "component_id": cid,
            "action": "realised_by",
            "item_keys": [k.lower() for k in keys],
            "reason": "The enclosure was built as a base and an arm",
        }
    )
    assert out["item_keys"] == keys and out["components_exact"]["status"] == "pass"

    maturity = await compute_maturity(twin, PID)
    by_id = {c["component_id"]: c for c in maturity["components"]}
    assert by_id[cid]["required"] is False and by_id[cid]["maturity"] == "Exact"
    assert by_id[cid]["resolution"]["action"] == "realised_by"
    assert all(by_id[f"item:{k}"]["required"] for k in keys)
    after = await manufacturing_checklist(twin, PID)
    assert after is not None
    exact = after.item("components_exact")
    assert exact.status == "pass" and f"{ENCLOSURE} realised by" in exact.detail
    # The parts that realise it are the ones the DFM item now checks.
    assert after.item("dfm").status == "pass", after.item("dfm").detail


async def test_retiring_is_recorded_and_reversible(
    twin: Any, machine_library: SqliteMachineLibrary, cad: Cad
) -> None:
    (key,) = await _lamp(twin, machine_library, ("Lamp head",))
    await dfm_exact.measure(twin, PROJECT, item_key=key)
    cid = await _component_id(twin)
    server = _server(twin)
    retired = await server.resolve_concept_component(
        {
            "project_id": PROJECT,
            "component_id": cid,
            "action": "retired",
            "reason": "An illustrative placeholder; the decision replaced it with real parts",
        }
    )
    assert retired["action"] == "retired" and retired["components_exact"]["status"] == "pass"
    maturity = {c["component_id"]: c for c in (await compute_maturity(twin, PID))["components"]}
    assert maturity[cid]["required"] is False
    # With every concept component retired, the parts built are what is required.
    assert maturity[f"item:{key}"]["required"] is True
    checklist = await manufacturing_checklist(twin, PID)
    assert (
        checklist is not None
        and f"{ENCLOSURE} retired" in checklist.item("components_exact").detail
    )

    restored = await server.resolve_concept_component(
        {"project_id": PROJECT, "component_id": cid, "action": "restored", "reason": "Undo"}
    )
    assert restored["replaces"] == retired["decision_node_id"]
    assert restored["components_exact"]["status"] == "fail"
    assert await current_resolutions(twin, PID) == {}
    # Every resolution stays in the decision log.
    decisions = await twin.list_work_products(
        work_product_type=WorkProductType.DESIGN_DECISION, project_id=PID
    )
    assert sum(1 for d in decisions if "component_resolution" in (d.metadata or {})) == 2


async def test_a_resolution_must_name_real_things(
    twin: Any, machine_library: SqliteMachineLibrary
) -> None:
    await _lamp(twin, machine_library, ("Base",))
    cid = await _component_id(twin)
    with pytest.raises(ComponentResolutionError) as err:
        await record_resolution(twin, PID, component_id="CMP-nothing", action="retired", reason="x")
    assert err.value.status == 404
    with pytest.raises(ComponentResolutionError, match="not an item"):
        await record_resolution(
            twin, PID, component_id=cid, action="realised_by", reason="x", item_keys=["CAD-NONE"]
        )
    with pytest.raises(ComponentResolutionError, match="needs the item_keys"):
        await record_resolution(twin, PID, component_id=cid, action="realised_by", reason="x")
    with pytest.raises(ComponentResolutionError, match="reason"):
        await record_resolution(twin, PID, component_id=cid, action="retired", reason=" ")
    with pytest.raises(ComponentResolutionError, match="no resolution to undo"):
        await record_resolution(twin, PID, component_id=cid, action="restored", reason="x")
    with pytest.raises(ComponentResolutionError, match="action"):
        await record_resolution(twin, PID, component_id=cid, action="deleted", reason="x")


async def test_a_realisation_with_a_missing_item_keeps_the_component_required(
    twin: Any, machine_library: SqliteMachineLibrary
) -> None:
    (key,) = await _lamp(twin, machine_library, ("Base",))
    cid = await _component_id(twin)
    await record_resolution(
        twin, PID, component_id=cid, action="realised_by", reason="x", item_keys=[key]
    )
    # The item loses its head (here: its maturity sees no CAD revision for it).
    from twin_core import maturity as maturity_module

    comps: dict[str, Any] = {cid: maturity_module.ComponentMaturity(cid, ENCLOSURE, True)}
    await maturity_module._apply_resolutions(twin, PID, comps, {})
    assert comps[cid].required is True
    assert comps[cid].resolution["missing_items"] == [key]


# ── the FEA chain ────────────────────────────────────────────────────────


FACES = [
    {"name": "Surface1", "bbox_mm": {"min": [0, 0, 0], "max": [130, 90, 0]}, "area_mm2": 11700},
    {
        "name": "Surface2",
        "bbox_mm": {"min": [0, 0, 47.5], "max": [130, 90, 47.5]},
        "area_mm2": 9000,
    },
    {
        "name": "Surface3",
        "bbox_mm": {"min": [10, 10, 47.5], "max": [20, 20, 47.5]},
        "area_mm2": 100,
    },
    {"name": "Surface4", "bbox_mm": {"min": [0, 0, 0], "max": [0, 90, 47.5]}, "area_mm2": 4275},
]


def test_surfaces_are_picked_by_name_or_by_the_end_of_an_axis() -> None:
    assert select_surface(FACES, {"axis": "z", "side": "min"}, role="fixed") == "Surface1"
    # The largest flat surface at the top, not the small boss face beside it.
    assert select_surface(FACES, {"axis": "z", "side": "max"}, role="loaded") == "Surface2"
    assert select_surface(FACES, {"axis": "x", "side": "min"}, role="fixed") == "Surface4"
    assert select_surface(FACES, "Surface3", role="loaded") == "Surface3"
    with pytest.raises(ReleasePathError, match="Surface1, Surface2"):
        select_surface(FACES, "Surface9", role="fixed")
    with pytest.raises(ReleasePathError, match="axis"):
        select_surface(FACES, {"axis": "w", "side": "min"}, role="fixed")


def test_the_element_size_follows_the_part() -> None:
    assert (
        default_element_size({"bbox_mm": {"min_x": 0, "max_x": 130, "min_y": 0, "max_y": 90}})
        == 3.25
    )
    assert default_element_size({"bbox_mm": [150, 20, 12]}) == 3.75
    assert default_element_size({}) == 2.0


async def test_run_item_fea_meshes_then_solves_a_committed_item(
    twin: Any, machine_library: SqliteMachineLibrary, tmp_path: Path
) -> None:
    (key,) = await _lamp(twin, machine_library, ("Base",))
    cad = Cad(
        {
            "freecad.generate_mesh": {
                "mesh_file": "/workspace/base.inp",
                "num_nodes": 900,
                "num_elements": 3000,
                "faces": FACES,
            },
            "calculix.run_fea": {
                "max_von_mises": {"overall": 4.2},
                "field": {"file": "/workspace/base_field.json"},
                "warnings": [],
            },
        }
    )
    service = ReleasePathService(twin, cad_service=cad, workspace_dir=tmp_path)
    server = TwinServer(twin=twin, release_path_service=service)
    out = await server.run_item_fea(
        {
            "project_id": PROJECT,
            "item_key": key,
            "element_size": 3,
            "load_case": {
                "name": "Lamp arm load",
                "material": {"name": "petg"},
                "fixed": {"axis": "z", "side": "min"},
                "loaded": {"axis": "z", "side": "max"},
                "force_n": [0, 0, -20],
            },
        }
    )
    (mesh_tool, mesh), (fea_tool, fea) = cad.calls
    assert mesh_tool == "freecad.generate_mesh" and fea_tool == "calculix.run_fea"
    staged = Path(mesh["input_file"])
    assert staged.read_bytes() == STEP and tmp_path in staged.parents
    assert mesh["element_size"] == 3.0 and mesh["output_format"] == "inp"
    assert fea["mesh_file"] == "/workspace/base.inp"
    assert fea["fixed_node_set"] == "Surface1" and fea["load_node_set"] == "Surface2"
    assert fea["load_force_n"] == [0.0, 0.0, -20.0] and fea["analysis_type"] == "static_stress"
    assert out["item_ref"] == f"{key}@1" and out["validated"] is True
    assert out["record_with"]["tool"] == "twin.record_document"
    assert out["record_with"]["analysed_geometry_revision"] == 1
    assert out["record_with"]["field_file"] == "/workspace/base_field.json"


async def test_run_item_fea_refuses_an_incomplete_load_case(
    twin: Any, machine_library: SqliteMachineLibrary, tmp_path: Path
) -> None:
    (key,) = await _lamp(twin, machine_library, ("Base",))
    service = ReleasePathService(twin, cad_service=Cad(), workspace_dir=tmp_path)
    base = {"material": {"name": "petg"}, "fixed": "Surface1"}
    with pytest.raises(ReleasePathError, match="force_n"):
        await service.run_item_fea(PROJECT, item_key=key, load_case={**base, "loaded": "Surface2"})
    with pytest.raises(ReleasePathError, match="material"):
        await service.run_item_fea(PROJECT, item_key=key, load_case={"fixed": "Surface1"})
    with pytest.raises(ReleasePathError, match="no CAD revision"):
        await service.run_item_fea(
            PROJECT, item_key="CAD-NONE", load_case={**base, "analysis_type": "modal"}
        )
    failing = Cad({"freecad.generate_mesh": RuntimeError("gmsh crashed")})
    with pytest.raises(ReleasePathError, match="meshing failed"):
        await ReleasePathService(twin, cad_service=failing, workspace_dir=tmp_path).run_item_fea(
            PROJECT, item_key=key, load_case={**base, "analysis_type": "modal"}
        )


# ── the release package manifest ─────────────────────────────────────────


async def test_the_release_manifest_pins_each_current_design_file(
    twin: Any, machine_library: SqliteMachineLibrary
) -> None:
    (key,) = await _lamp(twin, machine_library, ("Base",))
    await _cad(twin, "Base", VALIDATED, key=key)  # revision 2 is the head now
    for name, wp_type in (
        ("lamp-F_Cu.gbr", WorkProductType.GERBER),
        ("lamp-F_Cu.gbr", WorkProductType.GERBER),
        ("lamp-pos.csv", WorkProductType.PICK_AND_PLACE),
        ("lamp.bin", WorkProductType.FIRMWARE_SOURCE),
        ("Base.gcode", WorkProductType.GCODE),
    ):
        await twin.create_work_product(
            WorkProduct(
                name=name,
                type=wp_type,
                domain="manufacturing",
                file_path="",
                content_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
                format=name.rsplit(".", 1)[-1],
                created_by="test",
                project_id=PID,
            )
        )
    rows = await design_file_manifest(twin, PID)
    cad_rows = [r for r in rows if r["kind"] == "cad_model"]
    assert [r["ref"] for r in cad_rows] == [f"{key}@2"]
    assert sorted(r["kind"] for r in rows if r["kind"] != "cad_model") == [
        "firmware_source",
        "gcode",
        "gerber",
        "pick_and_place",
    ]
    assert manifest_digest(rows) == manifest_digest(list(reversed(rows)))
    assert manifest_digest(rows) != manifest_digest(rows[1:])


# ── discoverability and classification ───────────────────────────────────


def test_the_tools_are_registered_and_classified() -> None:
    server = TwinServer(
        twin=None,
        dfm_service=object(),
        release_path_service=object(),
    )
    tools = set(server.tool_ids)
    assert {"twin.measure_dfm", "twin.resolve_concept_component", "twin.run_item_fea"} <= tools
    assert {"twin.measure_dfm", "twin.resolve_concept_component"} <= ADDITIVE
    assert "twin.run_item_fea" in PRODUCING
    for tool in ("twin.measure_dfm", "twin.resolve_concept_component", "twin.run_item_fea"):
        assert annotations_for(tool)["destructiveHint"] is False
        assert not requires_human_authority(tool) and not never_auto_approved(tool)
    # A retire is the agent's design decision: an auto policy may approve it.
    assert policy_may_approve("twin.resolve_concept_component", "auto_clean")
    assert policy_may_approve("twin.run_item_fea", "auto")


def test_the_create_stages_carry_the_release_path() -> None:
    assert {"twin.measure_dfm", "twin.run_item_fea", "twin.resolve_concept_component"} <= (
        CREATE_STAGE_TOOLS["check"]
    )
    assert "cadquery.dfm_measure" not in CREATE_STAGE_TOOLS["check"]
    assert {
        "twin.resolve_concept_component",
        "twin.create_baseline",
        "twin.create_release_package",
        "twin.record_engineering_entity",
    } <= CREATE_STAGE_TOOLS["make"]
    assert "twin.measure_dfm" in CREATE_STAGE_TOOLS["develop"]


def test_the_playbook_and_imagine_name_the_release_steps() -> None:
    from mcp_core.workflows import WORKFLOWS

    skill = (
        Path(__file__).resolve().parents[2]
        / "mcp_core"
        / "plugin_skills"
        / "imagine_studio"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    imagine = WORKFLOWS["imagine"][1]
    fea = WORKFLOWS["fea"][1]
    for text in (skill, imagine):
        for tool in (
            "twin.measure_dfm",
            "twin.resolve_concept_component",
            "twin.run_item_fea",
            "twin.create_baseline",
            "twin.create_release_package",
        ):
            assert tool in text, tool
    assert "twin.run_item_fea" in fea and "freecad.generate_mesh" in fea
