"""The desk lamp golden path, step 1: the product definition, seeding and the demo manifest.

Seeding goes through the services the routes use (project backend, interface
contracts, manufacturing context, decision recorder, Create store) over
``InMemoryTwinAPI``. It is idempotent, never overwrites, and refuses a
same-named project it did not create. The manifest lists every step that has
not produced anything as missing, with the step that would produce it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.create import routes as create_routes
from api_gateway.create.store import SqliteCreationStore
from api_gateway.golden_path import desk_lamp as lamp
from api_gateway.golden_path.routes import router as golden_router
from api_gateway.projects.backend import InMemoryProjectBackend
from api_gateway.twin import interface_contracts as ic
from api_gateway.twin import routes as twin_routes
from api_gateway.twin.interface_contract_routes import router as contract_router
from api_gateway.twin.manufacturing_context import read_context
from twin_core.api import InMemoryTwinAPI
from twin_core.component_identity import project_identity
from twin_core.impact import cross_domain
from twin_core.items import commit_revision, list_items, plan_revision
from twin_core.manufacturing.context import Authority
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

ROOT = Path(__file__).resolve().parents[2]
DEFINITION = ROOT / "examples" / "desk_lamp" / "definition.json"


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def backend() -> InMemoryProjectBackend:
    return InMemoryProjectBackend.create()


@pytest.fixture(autouse=True)
def create_store() -> Iterator[SqliteCreationStore]:
    store = SqliteCreationStore()
    create_routes.init_create_store(store)
    yield store
    create_routes.init_create_store(None)


@pytest.fixture(autouse=True)
def _no_hooks() -> Iterator[None]:
    cross_domain.set_impact_hooks()
    cross_domain.set_parameter_extractor(None)
    yield
    cross_domain.set_impact_hooks()
    cross_domain.set_parameter_extractor(None)


@pytest.fixture(autouse=True)
def _blobs(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as wpb

    monkeypatch.setattr(
        wpb, "store_work_product_blob", lambda node_id, filename, content, **_: f"k/{node_id}"
    )


async def _seed(twin: Any, backend: Any) -> lamp.SeedResult:
    return await lamp.seed_desk_lamp(twin, backend, actor="local:dashboard")


async def _revision_count(twin: Any, pid: UUID) -> int:
    nodes = await twin.list_work_products(project_id=pid)
    return len(nodes)


# ── the definition ───────────────────────────────────────────────────────


def test_the_definition_is_complete_and_honest() -> None:
    spec = json.loads(DEFINITION.read_text(encoding="utf-8"))
    assert spec["id"] == "desk-lamp"
    assert spec["prompt"].startswith("Design a modern desktop lamp with adjustable brightness")
    # References carry roles; no placeholder image is shipped.
    roles = {r["role"] for r in spec["references"]}
    assert roles == {"specification", "inspiration"}
    image = next(r for r in spec["references"] if r["kind"] == "image")
    assert image["source"] is None and "Omitted" in image["note"]
    names = [p["name"] for p in spec["concept_assembly"]["parts"]]
    assert names == [
        "Base",
        "Arm",
        "Head",
        "Diffuser",
        "LED module",
        "Driver PCB",
        "USB-C connector",
        "Brightness control",
    ]
    assert all(
        s["basis"] == "estimated" for s in spec["concept_assembly"]["estimated_sizes"].values()
    )
    assert all(m["status"] == "estimated" for m in spec["brief"]["measurements"])
    kinds = [c["kind"] for c in spec["contracts"]]
    assert kinds == ["cad_eda", "eda_firmware", "simulation", "manufacturing"]
    for raw in spec["contracts"]:
        ic.contract_from_body(raw)  # every contract is valid
    # The diffuser's translucent material is an assumption, never a statement.
    diffuser = spec["manufacturing_context"]["components"]["Diffuser"]
    assert diffuser["selected_material"]["authority"] == "assumption"


# ── seeding ──────────────────────────────────────────────────────────────


async def test_seeding_creates_the_project_contracts_context_and_components(
    twin: Any, backend: Any, create_store: SqliteCreationStore
) -> None:
    result = await _seed(twin, backend)
    assert result.created_project
    pid = UUID(result.project_id)
    project = await backend.get_project(result.project_id)
    assert project.name == "Desk lamp (golden path)"
    assert lamp.SEED_MARKER in project.description

    keys = {i.key for i in await list_items(twin, project_id=pid, item_type="interface_contract")}
    assert keys == {"IFC-CAD-EDA", "IFC-EDA-FIRMWARE", "IFC-SIMULATION", "IFC-MANUFACTURING"}
    view = await ic.contracts_view(twin, pid)
    assert {c["status"] for c in view["contracts"]} == {"proposed"}
    cad = next(c for c in view["contracts"] if c["key"] == "IFC-CAD-EDA")
    assert cad["create_line"] == "The circuit board must fit inside the base: not checked yet"
    outline = next(p for p in cad["parameters"] if p["name"] == "pcb_outline")
    assert (
        outline["basis"] == "estimated" and outline["unit"] == "mm" and outline["tolerance"] == 0.2
    )

    product = await read_context(twin, pid, None)
    assert product is not None
    assert product.record.get("selected_process").value == "FDM"
    assert product.record.get("selected_process").authority is Authority.USER_STATED
    assert product.record.get("production_goal").value == "functional_prototype"
    assert product.record.get("quantity").value == 1
    assert "USB-C" in product.record.get("intended_use").value
    diffuser = await read_context(twin, pid, "Diffuser")
    assert diffuser is not None
    material = diffuser.record.get("selected_material")
    assert material.value == "Translucent PETG" and material.authority is Authority.ASSUMPTION

    identity = await project_identity(twin, pid)
    ids = set(identity["name_to_id"].values())
    assert {
        "CMP-base",
        "CMP-arm",
        "CMP-head",
        "CMP-diffuser",
        "CMP-led-module",
        "CMP-driver-pcb",
        "CMP-usb-c-connector",
        "CMP-brightness-control",
    } <= ids

    creation = create_store.get(result.project_id)
    assert creation.brief.items["intended_use"].status == "confirmed"
    assert creation.brief.items["materials"].status == "assumed"
    assert {m.status for m in creation.brief.measurements} == {"estimated"}


async def test_seeding_again_changes_nothing(twin: Any, backend: Any) -> None:
    first = await _seed(twin, backend)
    pid = UUID(first.project_id)
    before = await _revision_count(twin, pid)
    second = await _seed(twin, backend)
    assert second.project_id == first.project_id and not second.created_project
    assert {s.outcome for s in second.steps} <= {"unchanged", "kept"}
    assert await _revision_count(twin, pid) == before
    assert len(await backend.list_projects()) == 1


async def test_seeding_never_overwrites_what_a_person_changed(twin: Any, backend: Any) -> None:
    from api_gateway.twin.manufacturing_context import stated, write_context

    first = await _seed(twin, backend)
    pid = UUID(first.project_id)
    # A person picks a material and edits a contract value.
    await write_context(
        twin, pid, {"selected_material": stated("PETG", source="api", actor="p")}, source="api"
    )
    stored = await ic.get_contract(twin, pid, "IFC-CAD-EDA")
    assert stored is not None
    params = [
        p.model_copy(update={"value": 2.0}) if p.name == "pcb_clearance" else p
        for p in stored.contract.parameters
    ]
    edited, _ = await ic.write_contract(
        twin,
        pid,
        stored.contract.model_copy(update={"parameters": params}),
        actor="p",
        source="api",
    )
    await _seed(twin, backend)
    after = await ic.get_contract(twin, pid, "IFC-CAD-EDA")
    assert after is not None and after.ref == edited.ref
    assert after.contract.parameter("pcb_clearance").value == 2.0
    product = await read_context(twin, pid, None)
    assert product.record.get("selected_material").value == "PETG"


async def test_a_same_named_project_the_seeder_did_not_create_is_refused(
    twin: Any, backend: Any
) -> None:
    await backend.create_project(name="Desk lamp (golden path)", description="mine", status="draft")
    with pytest.raises(lamp.GoldenPathError) as refused:
        await _seed(twin, backend)
    assert refused.value.status == 409
    assert await twin.list_work_products() == []


# ── the manifest ─────────────────────────────────────────────────────────


async def test_the_manifest_lists_missing_steps_without_fabrication(
    twin: Any, backend: Any
) -> None:
    result = await _seed(twin, backend)
    pid = UUID(result.project_id)
    checks = await ic.check_contracts(twin, pid, actor="local:dashboard")
    records = {ev.contract_key: rid for ev, rid in checks}
    manifest = await lamp.build_manifest(twin, pid, check_records=records, run_id="run-1")
    assert manifest["run_id"] == "run-1"
    assert all(s["status"] == "missing" for s in manifest["steps"]), [
        s for s in manifest["steps"] if s["status"] != "missing"
    ]
    assert all(s["artifacts"] == [] for s in manifest["steps"])
    missing = {m["step"]: m["produced_by"] for m in manifest["missing"]}
    assert missing["pcb"] == "kicad.generate_pcb"
    assert missing["firmware_build"] == "firmware.build"
    assert missing["thermal"] == "calculix.run_thermal"
    # Nothing is satisfied: every contract was checked and found inputs missing.
    for c in manifest["contracts"]:
        assert c["check_outcome"] == "not_evaluated", c
        assert c["not_evaluated"], c
    assert set(manifest["run_ids"]) == set(records.values())
    assert manifest["known_limitations"]

    # Once a real PCB layout exists, that one step is produced, with its link.
    plan = await plan_revision(
        twin,
        item_type="pcb_layout",
        name="Driver PCB",
        project_id=pid,
        author="t",
        item_key="PCB-MAIN",
    )
    assert plan is not None
    wp = WorkProduct(
        name="Driver PCB",
        type=WorkProductType.PCB_LAYOUT,
        domain="electronics",
        file_path="",
        content_hash=hashlib.sha256(b"pcb").hexdigest(),
        format="json",
        metadata={"run_id": "run-pcb", **plan.stamp()},
        created_by="t",
        project_id=pid,
    )
    await twin.create_work_product(wp)
    await commit_revision(twin, plan, wp.id, name="Driver PCB")
    manifest = await lamp.build_manifest(twin, pid)
    pcb = next(s for s in manifest["steps"] if s["id"] == "pcb")
    assert pcb["status"] == "produced"
    assert pcb["artifacts"][0]["ref"] == "PCB-MAIN@1"
    assert pcb["artifacts"][0]["link"] == f"/v1/twin/nodes/{wp.id}"
    assert "run-pcb" in manifest["run_ids"]
    assert next(s for s in manifest["steps"] if s["id"] == "schematic")["status"] == "missing"


def test_load_definition_reports_a_missing_install(tmp_path: Path) -> None:
    with pytest.raises(lamp.GoldenPathError) as missing:
        lamp.load_definition(tmp_path / "nope.json")
    assert missing.value.status == 503


# ── routes and the demo runner ───────────────────────────────────────────


@pytest.fixture
def client(twin: Any, backend: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from api_gateway.projects import routes as project_routes

    monkeypatch.setattr(twin_routes, "_twin", twin)
    monkeypatch.setattr(project_routes, "_backend", backend)
    app = FastAPI()
    app.include_router(golden_router)
    app.include_router(contract_router)
    return TestClient(app)


def test_routes_seed_check_and_manifest(client: TestClient) -> None:
    listed = client.get("/v1/examples").json()["examples"][0]
    assert listed["id"] == "desk-lamp" and listed["installed"] is True
    seeded = client.post("/v1/examples/desk-lamp/seed")
    assert seeded.status_code == 200, seeded.text
    pid = seeded.json()["project_id"]
    again = client.post("/v1/examples/desk-lamp/seed")
    assert again.json()["project_id"] == pid and again.json()["created_project"] is False
    manifest = client.get(f"/v1/examples/desk-lamp/projects/{pid}/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["missing"]
    assert client.get("/v1/examples/desk-lamp/projects/nope/manifest").status_code == 400


def _runner() -> Any:
    spec = importlib.util.spec_from_file_location("demo_lamp", ROOT / "scripts" / "demo_lamp.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["demo_lamp"] = module
    spec.loader.exec_module(module)
    return module


def test_demo_runner_writes_the_manifest_and_prints_the_table(
    client: TestClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _runner()
    out = runner.run(client, project_dir=tmp_path)
    manifest = json.loads((tmp_path / ".forge" / "demo_manifest.json").read_text(encoding="utf-8"))
    assert out == manifest
    assert manifest["golden_path"] == "desk-lamp"
    assert manifest["seed"]["steps"]
    assert all(c["check_record_id"] for c in manifest["contracts"])
    assert set(manifest["check_records"].values()) <= set(manifest["run_ids"])
    printed = capsys.readouterr().out
    assert "IFC-CAD-EDA" in printed and "proposed" in printed
    assert "Missing" in printed and "kicad.generate_pcb" in printed
