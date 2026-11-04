"""Cross-domain impact in the gateway (FORGE-793; FRS XDM-001..007, DEF-011..013, CHG-001).

Proposals from recorded data (the example drone's real pin map and firmware
sources), rerun dispositions under the approval policy, the
``dependencies_invalidated`` operation event, rerun approvals through
``/v1/approvals``, and the interface and impact routes. Over
``InMemoryTwinAPI`` with the blob store patched to a dict.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.twin import impact_service
from api_gateway.twin.interface_proposals import (
    diff_pinmaps,
    firmware_references,
    parse_pinmap,
    pinmap_names,
    propose_interfaces,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.record_pins import link_record, pin_metadata, resolve_pins
from twin_core.impact import cross_domain
from twin_core.impact.cross_domain import ProposedRerun, list_impacts
from twin_core.impact.interfaces import declare_interface, list_interfaces
from twin_core.items import commit_revision, plan_revision
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import WorkProductType
from twin_core.models.interface import DeclaredBy, InterfaceStatus, InterfaceType
from twin_core.models.work_product import WorkProduct

PROJECT = "79479479-4794-4794-8794-794794794794"
PID = UUID(PROJECT)
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "drone_flight_controller" / "firmware"


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def blobs(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    stored: dict[str, bytes] = {}
    import digital_twin.storage.work_product_blobs as wpb

    monkeypatch.setattr(wpb, "fetch_work_product_blob", lambda key: stored[key])
    return stored


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """The hooks installed as at start-up, with a capturing operation publisher."""
    from mcp_core import operation_events

    captured: list[dict[str, Any]] = []

    async def capture(project_id: str, event: dict[str, Any]) -> None:
        captured.append({"project_id": project_id, **event})

    previous = operation_events.operation_event_publisher()
    operation_events.set_operation_event_publisher(capture)

    async def policy_mode(_project_id: str) -> str:
        return "off"

    monkeypatch.setattr(impact_service, "_policy_mode", policy_mode)
    impact_service.install_impact_hooks()
    yield captured
    cross_domain.set_impact_hooks()
    cross_domain.set_parameter_extractor(None)
    operation_events.set_operation_event_publisher(previous)


async def _revise(
    twin: Any,
    item_type: str,
    name: str,
    meta: dict[str, Any] | None = None,
    *,
    files: dict[str, bytes] | None = None,
    blobs: dict[str, bytes] | None = None,
    key: str | None = None,
    content: str = "v1",
) -> str:
    plan = await plan_revision(
        twin, item_type=item_type, name=name, project_id=PID, author="test", item_key=key
    )
    assert plan is not None
    full = dict(meta or {})
    wp = WorkProduct(
        name=name,
        type=WorkProductType(item_type),
        domain="general",
        file_path="",
        content_hash=hashlib.sha256(
            (content + repr(sorted((files or {}).items()))).encode()
        ).hexdigest(),
        format="json",
        metadata={},
        created_by="test",
        project_id=PID,
    )
    if files is not None and blobs is not None:
        design: dict[str, str] = {}
        for rel, data in files.items():
            blob_key = f"work-products/{wp.id}/{rel}"
            blobs[blob_key] = data
            design[rel] = blob_key
        full["design_files"] = design
    wp.metadata = {**full, **plan.stamp()}
    await twin.create_work_product(wp)
    await commit_revision(twin, plan, wp.id, name=name)
    return plan.key


async def _evidence(twin: Any, refs: list[str], tool: str, result: dict[str, Any]) -> UUID:
    pins = await resolve_pins(twin, refs=refs, project_id=PID)
    meta = {
        "evidence_type": "test",
        "producer": {"tool": tool},
        "result": result,
        **pin_metadata(pins),
    }
    entity = EngineeringEntity(
        entity_type="evidence", statement=f"{tool} result", project_id=PID, metadata=meta
    )
    created = await twin.create_engineering_entity(entity)
    await link_record(
        twin, created.id, pins, record_type="evidence", name=entity.statement or "", metadata=meta
    )
    return created.id


def _example_files() -> tuple[dict[str, bytes], dict[str, bytes]]:
    pinmap = {"pinmap.json": (EXAMPLE / "src" / "pinmap.json").read_bytes()}
    firmware = {
        "src/main.c": (EXAMPLE / "src" / "main.c").read_bytes(),
        "src/config.h": (EXAMPLE / "src" / "config.h").read_bytes(),
    }
    return pinmap, firmware


# ---------------------------------------------------------------------------
# Proposals from recorded data
# ---------------------------------------------------------------------------


class TestProposals:
    def test_the_example_pinmap_is_read_into_pins_and_peripherals(self) -> None:
        pinmap, firmware = _example_files()
        entries = parse_pinmap(pinmap)
        names = pinmap_names(entries)
        assert {"SPI1", "PA5", "I2C1", "PB6", "USART1"} <= names
        refs = firmware_references(names, firmware)
        assert "SPI1" in refs and "src/config.h" in refs["SPI1"]
        assert "I2C1" in refs

    async def test_a_pinmap_and_the_firmware_that_names_its_pins_are_proposed(
        self, twin, blobs
    ) -> None:
        pinmap, firmware = _example_files()
        pin_key = await _revise(twin, "pinmap", "FC pin map", files=pinmap, blobs=blobs)
        fw_key = await _revise(twin, "firmware_source", "FC firmware", files=firmware, blobs=blobs)
        outcome = await propose_interfaces(twin, PID)
        [edge] = outcome.proposed
        assert edge.type is InterfaceType.PIN_MAPS_TO
        assert (edge.source.ref, edge.target.ref) == (pin_key, fw_key)
        assert edge.declared_by is DeclaredBy.INFERRED and edge.status is InterfaceStatus.PROPOSED
        assert "SPI1" in edge.coupled_parameters
        assert set(edge.evidence["modules"]) <= {"src/main.c", "src/config.h"}
        assert 0 < edge.confidence <= 0.95
        again = await propose_interfaces(twin, PID)
        assert again.proposed == [] and [e.id for e in again.already] == [edge.id]

    async def test_board_and_enclosure_fields_give_mount_clearance_and_connector_links(
        self, twin
    ) -> None:
        enclosure = await _revise(
            twin,
            "cad_model",
            "Enclosure",
            {
                "subtype": "enclosure",
                "mounting_standoffs": [{"x": 3, "y": 3}, {"x": 47, "y": 3}],
                "dimensions": {"width": 60, "height": 40},
                "cutouts": [{"x": 0, "y": 10}],
            },
        )
        board = await _revise(
            twin,
            "pcb_layout",
            "Main board",
            {
                "mounting_holes": [{"x": 3, "y": 3}, {"x": 47, "y": 3.2}, {"x": 20, "y": 20}],
                "dimensions": {"width": 50, "height": 30},
                "connectors": [{"ref": "J1", "x": 0, "y": 10}],
            },
        )
        outcome = await propose_interfaces(twin, PID)
        by_type = {e.type: e for e in outcome.proposed}
        assert set(by_type) == {
            InterfaceType.MOUNTS_TO,
            InterfaceType.CLEARANCE_TO,
            InterfaceType.CONNECTS_VIA,
        }
        mount = by_type[InterfaceType.MOUNTS_TO]
        assert (mount.source.ref, mount.target.ref) == (enclosure, board)
        assert mount.evidence["matched"] == 2 and mount.evidence["board_holes"] == 3
        assert mount.confidence == 0.8
        assert by_type[InterfaceType.CONNECTS_VIA].source.ref == board
        assert all(e.status is InterfaceStatus.PROPOSED for e in outcome.proposed)

    async def test_no_data_means_no_edge(self, twin) -> None:
        await _revise(twin, "cad_model", "Enclosure", {"subtype": "enclosure"})
        await _revise(twin, "pcb_layout", "Main board")
        outcome = await propose_interfaces(twin, PID)
        assert outcome.proposed == []
        assert any("no mounting holes" in s for s in outcome.skipped)
        assert await list_interfaces(twin, PID) == []


# ---------------------------------------------------------------------------
# Selective scheduling: dispositions, events, approvals
# ---------------------------------------------------------------------------


def _rerun(tool: str, check: str, *, consequential: bool) -> ProposedRerun:
    return ProposedRerun(
        id="r1",
        tool=tool,
        check_kind=check,
        target_ref="X",
        label="l",
        reason="r",
        consequential=consequential,
    )


class TestDispositions:
    def test_a_read_only_check_may_run_unattended(self) -> None:
        disposition, why = impact_service.disposition_for(
            _rerun("kicad.run_drc", "drc", consequential=False), "off"
        )
        assert disposition == "may_run_unattended" and "read-only" in why

    def test_a_consequential_rerun_always_waits_for_a_person(self) -> None:
        for mode in ("off", "auto_clean", "auto"):
            disposition, _ = impact_service.disposition_for(
                _rerun("firmware.build", "firmware_build", consequential=True), mode
            )
            assert disposition == "requires_approval"

    def test_a_producing_analysis_follows_the_project_policy(self) -> None:
        fea = _rerun("calculix.run_fea", "fea", consequential=False)
        assert impact_service.disposition_for(fea, "off")[0] == "requires_approval"
        assert impact_service.disposition_for(fea, "auto")[0] == "may_run_unattended"

    def test_a_never_auto_approved_tool_is_never_unattended(self) -> None:
        disposition, why = impact_service.disposition_for(
            _rerun("project.delete", "validation", consequential=False), "auto"
        )
        assert disposition == "requires_approval" and "no approval policy" in why


class TestEventsAndApprovals:
    async def test_a_pinmap_move_emits_dependencies_invalidated_and_waits_for_a_person(
        self, twin, events
    ) -> None:
        pinmap = await _revise(twin, "pinmap", "Pin map")
        firmware = await _revise(twin, "firmware_source", "Flight firmware")
        await declare_interface(
            twin,
            PID,
            type="pin_maps_to",
            source_ref=pinmap,
            target_ref=firmware,
            declared_by="human",
            actor="alice",
        )
        build = await _evidence(twin, [firmware], "firmware.build", {"success": True})
        await _revise(twin, "pinmap", "Pin map", key=pinmap, content="v2")
        [impact] = await list_impacts(twin, PID)
        op = impact.operation_id
        mine = [e for e in events if e["operation_id"] == op]
        assert [e["type"] for e in mine] == ["accepted", "milestone", "review_required"]
        milestone = mine[1]
        assert milestone["stage_id"] == "dependencies_invalidated"
        assert milestone["domain"] == "cross_domain" and milestone["kind"] == "job"
        assert "target_key" not in milestone  # never supersedes a real job on the item
        ref_ids = {r["id"] for r in milestone["artifact_refs"]}
        assert firmware in ref_ids and str(build) in ref_ids
        review = mine[2]
        assert review["state"] == "waiting_for_user" and review["requires_action"]
        [action] = review["action_refs"]
        assert action["kind"] == "approval"
        assert action["id"] == f"rerun:{impact.id}:r1"
        [rerun] = impact.proposed_reruns
        assert rerun.tool == "firmware.build" and rerun.status == "proposed"
        assert rerun.disposition == "requires_approval"
        assert impact.approval_policy["mode"] == "off"
        # Not run: the build is still the stale one, nothing new was recorded.
        assert (await twin.graph.get_node(build)).metadata["staleness"] == "stale"

        decided, _ = await impact_service.decide_rerun(
            twin, f"{impact.id}:r1", "approve", approver="alice"
        )
        assert decided.proposed_reruns[0].status == "approved"
        assert decided.proposed_reruns[0].decided_by == "alice"
        assert [e["type"] for e in events if e["operation_id"] == op][-1] == "completed"
        with pytest.raises(ValueError, match="already approved"):
            await impact_service.decide_rerun(twin, f"{impact.id}:r1", "reject", approver="bob")

    async def test_a_drc_rerun_is_queued_and_the_operation_completes(self, twin, events) -> None:
        enclosure = await _revise(twin, "cad_model", "Enclosure")
        board = await _revise(twin, "pcb_layout", "Main board")
        await declare_interface(
            twin,
            PID,
            type="mounts_to",
            source_ref=enclosure,
            target_ref=board,
            declared_by="human",
            actor="alice",
        )
        await _evidence(twin, [board], "kicad.run_drc", {"violations": 0})
        await _revise(twin, "cad_model", "Enclosure", key=enclosure, content="v2")
        [impact] = await list_impacts(twin, PID)
        [rerun] = impact.proposed_reruns
        assert rerun.tool == "kicad.run_drc" and not rerun.consequential
        assert rerun.disposition == "may_run_unattended" and rerun.status == "queued"
        types = [e["type"] for e in events if e["operation_id"] == impact.operation_id]
        assert types == ["accepted", "milestone", "completed"]

    async def test_a_pin_the_firmware_does_not_use_leaves_its_build_current(
        self, twin, blobs, events
    ) -> None:
        pinmap, firmware_files = _example_files()
        pin_key = await _revise(twin, "pinmap", "FC pin map", files=pinmap, blobs=blobs)
        fw_key = await _revise(
            twin, "firmware_source", "FC firmware", files=firmware_files, blobs=blobs
        )
        await declare_interface(
            twin,
            PID,
            type="pin_maps_to",
            source_ref=pin_key,
            target_ref=fw_key,
            declared_by="human",
            actor="alice",
            coupled_parameters=["SPI1", "PA5", "PA6", "PA7"],
        )
        build = await _evidence(twin, [fw_key], "firmware.build", {"success": True})
        moved = pinmap["pinmap.json"].replace(b'"pin": "PA2"', b'"pin": "PA0"')
        assert moved != pinmap["pinmap.json"]
        await _revise(
            twin, "pinmap", "FC pin map", files={"pinmap.json": moved}, blobs=blobs, key=pin_key
        )
        assert (await twin.graph.get_node(build)).metadata["staleness"] == "current"
        # Now move an SPI1 pin: the firmware uses it, so its build goes stale.
        spi = moved.replace(b'"pin": "PA5"', b'"pin": "PB3"')
        await _revise(
            twin, "pinmap", "FC pin map", files={"pinmap.json": spi}, blobs=blobs, key=pin_key
        )
        meta = (await twin.graph.get_node(build)).metadata
        assert meta["staleness"] == "stale" and meta["stale_for"][pin_key]["current"] == 3
        impacts = await list_impacts(twin, PID)
        assert [i.change.new_ref for i in impacts] == [f"{pin_key}@3"]
        assert impacts[0].change.changed_parameters == ["PA5", "PB3", "SPI1"]

    def test_the_pinmap_diff_names_the_rows_that_changed(self) -> None:
        pinmap, _ = _example_files()
        old = parse_pinmap(pinmap)
        new = parse_pinmap({"pinmap.json": pinmap["pinmap.json"].replace(b'"PA5"', b'"PB3"')})
        assert diff_pinmaps(old, new) == ["PA5", "PB3", "SPI1"]


# ---------------------------------------------------------------------------
# Routes and the approvals queue
# ---------------------------------------------------------------------------


@pytest.fixture
def client(twin: InMemoryTwinAPI, events: list[dict[str, Any]]) -> Iterator[TestClient]:
    from api_gateway.approvals.routes import router as approvals_router
    from api_gateway.chat import tool_approvals
    from api_gateway.runs import routes as run_routes
    from api_gateway.twin import routes as twin_routes
    from api_gateway.twin.impact_routes import router as impact_router

    run_routes.reset_run_store()
    tool_approvals.reset_approval_store()
    twin_routes.init_twin(twin)
    app = FastAPI()
    app.state.twin = twin
    app.state.proposal_apply = None
    app.include_router(impact_router)
    app.include_router(approvals_router)
    yield TestClient(app)


class TestRoutes:
    async def test_declare_list_confirm_and_remove(self, client, twin) -> None:
        enclosure = await _revise(twin, "cad_model", "Enclosure")
        board = await _revise(twin, "pcb_layout", "Main board")
        base = f"/v1/twin/projects/{PROJECT}/interfaces"
        made = client.post(
            base,
            json={
                "type": "clearance_to",
                "source": enclosure,
                "target": board,
                "coupled_parameters": ["dimensions"],
            },
        )
        assert made.status_code == 200, made.text
        edge = made.json()["edge"]
        assert edge["status"] == "confirmed" and edge["decided_by"] == "local:dashboard"
        assert edge["domain_pair"] == ["mechanical", "electronics"]
        listed = client.get(base).json()
        assert [e["id"] for e in listed["edges"]] == [edge["id"]]
        assert {n["ref"] for n in listed["nodes"]} == {enclosure, board}
        await _revise(twin, "cad_model", "Enclosure", key=enclosure, content="v2")
        behind = client.get(base).json()["edges"][0]
        assert behind["source_behind"] is True
        confirmed = client.post(f"{base}/{edge['id']}/confirm").json()["edge"]
        assert confirmed["source"]["revision"] == 2
        removed = client.delete(f"{base}/{edge['id']}", params={"reason": "not touching"})
        assert removed.json()["edge"]["status"] == "removed"
        assert client.get(base).json()["edges"] == []
        assert len(client.get(base, params={"include_removed": True}).json()["edges"]) == 1

    async def test_refusals(self, client, twin) -> None:
        await _revise(twin, "pcb_layout", "Main board")
        base = f"/v1/twin/projects/{PROJECT}/interfaces"
        assert (
            client.post(base, json={"type": "glued_to", "source": "A", "target": "B"}).status_code
            == 422
        )
        assert (
            client.post(
                base, json={"type": "mounts_to", "source": "CAD-NOPE", "target": "PCB-MAIN-BOARD"}
            ).status_code
            == 404
        )
        assert client.get("/v1/twin/projects/not-a-uuid/interfaces").status_code == 400
        assert client.post(f"{base}/{UUID(int=1)}/confirm").status_code == 404

    async def test_preview_writes_nothing_and_recorded_impact_reads_back(
        self, client, twin
    ) -> None:
        enclosure = await _revise(twin, "cad_model", "Enclosure")
        board = await _revise(twin, "pcb_layout", "Main board")
        firmware = await _revise(twin, "firmware_source", "Flight firmware")
        await declare_interface(
            twin,
            PID,
            type="mounts_to",
            source_ref=enclosure,
            target_ref=board,
            declared_by="human",
            actor="alice",
            coupled_parameters=["mounting_standoffs"],
        )
        drc = await _evidence(twin, [board], "kicad.run_drc", {"violations": 0})
        preview = client.post(
            f"/v1/twin/projects/{PROJECT}/impact/preview",
            json={
                "item_key": enclosure,
                "changed_parameters": ["mounting_standoffs"],
                "include_unaffected": True,
            },
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["preview"] is True and body["summary"] == "This may affect the PCB fit."
        assert [e["ref"] for e in body["impacted"]] == [board]
        assert [s["record_id"] for s in body["stale_results"]] == [str(drc)]
        assert body["stale_results"][0]["marked"] is False
        assert {n["ref"] for n in body["not_affected"]} == {firmware}
        assert body["proposed_reruns"][0]["disposition"] == "may_run_unattended"
        assert body["transaction"]["source_revision"] == f"{enclosure}@1"
        assert body["transaction"]["target_revision"] == f"{enclosure}@2"
        assert body["graph"]["nodes"] and body["graph"]["links"]
        assert (await twin.graph.get_node(drc)).metadata["staleness"] == "current"
        assert await list_impacts(twin, PID) == []
        assert client.get(f"/v1/twin/projects/{PROJECT}/impact/{enclosure}@2").status_code == 404

        await _revise(twin, "cad_model", "Enclosure", key=enclosure, content="v2")
        got = client.get(f"/v1/twin/projects/{PROJECT}/impact/{enclosure}@2")
        assert got.status_code == 200
        assert got.json()["stale_results"][0]["marked"] is True
        assert (
            client.get(f"/v1/twin/projects/{PROJECT}/impact/{got.json()['id']}").json()["id"]
            == got.json()["id"]
        )
        assert len(client.get(f"/v1/twin/projects/{PROJECT}/impact").json()["impacts"]) == 1

    async def test_a_rerun_waits_in_the_approvals_queue_for_a_person(self, client, twin) -> None:
        pinmap = await _revise(twin, "pinmap", "Pin map")
        firmware = await _revise(twin, "firmware_source", "Flight firmware")
        await declare_interface(
            twin,
            PID,
            type="pin_maps_to",
            source_ref=pinmap,
            target_ref=firmware,
            declared_by="human",
            actor="alice",
        )
        await _evidence(twin, [firmware], "firmware.build", {"success": True})
        await _revise(twin, "pinmap", "Pin map", key=pinmap, content="v2")
        [impact] = await list_impacts(twin, PID)
        item_id = f"rerun:{impact.id}:r1"
        listed = client.get("/v1/approvals", params={"status": "pending"}).json()["items"]
        [item] = [i for i in listed if i["kind"] == "rerun"]
        assert item["id"] == item_id and item["allowed_decisions"] == ["approve", "reject"]
        assert item["detail"]["tool"] == "firmware.build" and item["detail"]["consequential"]
        assert item["base_revision"] == f"{pinmap}@2"
        agent = client.post(
            f"/v1/approvals/{item_id}/decision",
            json={"decision": "approve"},
            headers={"X-MetaForge-Surface": "agent", "X-MetaForge-On-Behalf-Of": "alice"},
        )
        assert agent.status_code == 403
        decided = client.post(f"/v1/approvals/{item_id}/decision", json={"decision": "approve"})
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"
        [after] = await list_impacts(twin, PID)
        assert after.proposed_reruns[0].status == "approved"
        assert client.get(f"/v1/approvals/rerun:{impact.id}:r9").status_code == 404
