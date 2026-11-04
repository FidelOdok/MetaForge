"""Cross-domain dependency and impact (FORGE-793; FRS XDM-001..007, CHG-004..006, RC-08..10).

Component level over ``InMemoryTwinAPI``: real items and revisions through the
item service, records pinned with the real FORGE-527 pin helpers, interface
edges stored in the twin, and the impact walker. Each head move below goes
through ``commit_revision``, so the cross-domain hook runs exactly as it does
for a recorder.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.record_pins import link_record, pin_metadata, resolve_pins
from twin_core.consistency.validation_status import (
    CheckResult,
    ValidationStatus,
    check_result_for_record,
    map_status,
    verdict_of_result,
)
from twin_core.impact import cross_domain
from twin_core.impact.cross_domain import (
    ChangeSpec,
    analyse_change,
    get_impact,
    list_impacts,
)
from twin_core.impact.interfaces import (
    confirm_interface,
    declare_interface,
    list_interfaces,
    remove_interface,
)
from twin_core.items import commit_revision, plan_revision
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import WorkProductType
from twin_core.models.interface import DeclaredBy, InterfaceEdge, InterfaceStatus, InterfaceType
from twin_core.models.work_product import WorkProduct

PROJECT = UUID("79379379-3793-4793-8793-793793793793")

_WP_TYPE = {"assembly": "cad_model", "purchased_model": "cad_model"}
_DOMAIN = {
    "cad_model": "mechanical",
    "pcb_layout": "electronics",
    "pinmap": "firmware",
    "firmware_source": "firmware",
}


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture(autouse=True)
def _no_hooks() -> Any:
    cross_domain.set_impact_hooks()
    cross_domain.set_parameter_extractor(None)
    yield
    cross_domain.set_impact_hooks()
    cross_domain.set_parameter_extractor(None)


async def _revise(
    twin: Any,
    item_type: str,
    name: str,
    meta: dict[str, Any] | None = None,
    *,
    content: str = "v1",
    key: str | None = None,
) -> tuple[str, int, UUID]:
    """Write one revision of a definition through the item service."""
    plan = await plan_revision(
        twin, item_type=item_type, name=name, project_id=PROJECT, author="test", item_key=key
    )
    assert plan is not None
    wp = WorkProduct(
        name=name,
        type=WorkProductType(_WP_TYPE.get(item_type, item_type)),
        domain=_DOMAIN.get(item_type, "general"),
        file_path="",
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        format="json",
        metadata={**(meta or {}), **plan.stamp()},
        created_by="test",
        project_id=PROJECT,
    )
    await twin.create_work_product(wp)
    await commit_revision(twin, plan, wp.id, name=name)
    return plan.key, plan.revision, wp.id


async def _evidence(twin: Any, refs: list[str], tool: str, result: dict[str, Any]) -> UUID:
    """A check result (DRC, build) pinned to ``refs``, as twin.record_evidence records it."""
    pins = await resolve_pins(twin, refs=refs, project_id=PROJECT)
    meta = {
        "evidence_type": "test",
        "producer": {"tool": tool},
        "inputs": {},
        "result": result,
        **pin_metadata(pins),
    }
    entity = EngineeringEntity(
        entity_type="evidence", statement=f"{tool} result", project_id=PROJECT, metadata=meta
    )
    created = await twin.create_engineering_entity(entity)
    await link_record(
        twin, created.id, pins, record_type="evidence", name=f"{tool} result", metadata=meta
    )
    return created.id


async def _simulation(twin: Any, refs: list[str], meta: dict[str, Any]) -> UUID:
    pins = await resolve_pins(twin, refs=refs, project_id=PROJECT)
    full = {**meta, **pin_metadata(pins)}
    wp = WorkProduct(
        name="Bracket FEA",
        type=WorkProductType.SIMULATION_RESULT,
        domain="mechanical",
        file_path="",
        content_hash="sim",
        format="json",
        metadata=full,
        created_by="calculix.run_fea",
        project_id=PROJECT,
    )
    await twin.create_work_product(wp)
    await link_record(
        twin, wp.id, pins, record_type="simulation_result", name=wp.name, metadata=full
    )
    return wp.id


async def _meta(twin: Any, node_id: UUID) -> dict[str, Any]:
    node = await twin.graph.get_node(node_id)
    return dict(node.metadata)


async def _enclosure_and_board(twin: Any) -> tuple[str, str, InterfaceEdge]:
    enclosure, _, _ = await _revise(
        twin,
        "cad_model",
        "Enclosure",
        {"subtype": "enclosure", "mounting_standoffs": [{"x": 5, "y": 5}]},
    )
    board, _, _ = await _revise(
        twin, "pcb_layout", "Main board", {"subtype": "pcb", "mounting_holes": [{"x": 5, "y": 5}]}
    )
    edge, created = await declare_interface(
        twin,
        PROJECT,
        type="mounts_to",
        source_ref=enclosure,
        target_ref=board,
        declared_by="human",
        actor="alice",
        coupled_parameters=["mounting_holes", "mounting_standoffs"],
        rationale="the board screws onto the enclosure standoffs",
    )
    assert created
    return enclosure, board, edge


# ---------------------------------------------------------------------------
# XDM-001: typed, revision-aware edges
# ---------------------------------------------------------------------------


class TestInterfaceEdges:
    async def test_a_declared_edge_pins_both_ends_and_names_its_domains(self, twin) -> None:
        enclosure, board, edge = await _enclosure_and_board(twin)
        assert edge.type is InterfaceType.MOUNTS_TO
        assert edge.status is InterfaceStatus.CONFIRMED
        assert edge.declared_by is DeclaredBy.HUMAN and edge.decided_by == "alice"
        assert (edge.source.pinned_ref, edge.target.pinned_ref) == (f"{enclosure}@1", f"{board}@1")
        assert edge.domain_pair == ("mechanical", "electronics")
        assert edge.symmetric
        listed = await list_interfaces(twin, PROJECT)
        assert [e.id for e in listed] == [edge.id]

    async def test_an_inferred_edge_is_a_proposal_until_someone_confirms_it(self, twin) -> None:
        enclosure, _, _ = await _revise(twin, "cad_model", "Enclosure")
        board, _, _ = await _revise(twin, "pcb_layout", "Main board")
        edge, _ = await declare_interface(
            twin,
            PROJECT,
            type="clearance_to",
            source_ref=enclosure,
            target_ref=board,
            declared_by="inferred",
            actor="metaforge",
            confidence=0.6,
            evidence={"read": "dimensions"},
        )
        assert edge.status is InterfaceStatus.PROPOSED and edge.confidence == 0.6
        await _revise(twin, "cad_model", "Enclosure", content="v2", key=enclosure)
        confirmed = await confirm_interface(twin, PROJECT, edge.id, actor="bob")
        assert confirmed.status is InterfaceStatus.CONFIRMED and confirmed.decided_by == "bob"
        # Confirming re-pins to the current heads.
        assert confirmed.source.pinned_ref == f"{enclosure}@2"

    async def test_an_inferred_edge_cannot_claim_to_be_confirmed(self) -> None:
        with pytest.raises(ValueError, match="confirmed only by someone"):
            InterfaceEdge.model_validate(
                {
                    "id": "x",
                    "type": "mounts_to",
                    "source": {"ref": "CAD-A", "revision": 1},
                    "target": {"ref": "PCB-B", "revision": 1},
                    "declared_by": "inferred",
                    "status": "confirmed",
                }
            )

    async def test_a_person_declaring_a_proposed_link_confirms_it(self, twin) -> None:
        enclosure, _, _ = await _revise(twin, "cad_model", "Enclosure")
        board, _, _ = await _revise(twin, "pcb_layout", "Main board")
        proposed, _ = await declare_interface(
            twin,
            PROJECT,
            type="mounts_to",
            source_ref=enclosure,
            target_ref=board,
            declared_by="inferred",
            actor="metaforge",
            confidence=0.5,
        )
        again, created = await declare_interface(
            twin,
            PROJECT,
            type="mounts_to",
            source_ref=board,
            target_ref=enclosure,
            declared_by="human",
            actor="alice",
        )
        assert not created and again.id == proposed.id
        assert again.status is InterfaceStatus.CONFIRMED

    async def test_a_removed_edge_is_kept_and_not_re_proposed(self, twin) -> None:
        enclosure, board, edge = await _enclosure_and_board(twin)
        removed = await remove_interface(twin, PROJECT, edge.id, actor="alice", reason="wrong part")
        assert removed.status is InterfaceStatus.REMOVED
        assert await list_interfaces(twin, PROJECT) == []
        kept = await list_interfaces(twin, PROJECT, include_removed=True)
        assert [e.id for e in kept] == [edge.id]
        again, created = await declare_interface(
            twin,
            PROJECT,
            type="mounts_to",
            source_ref=enclosure,
            target_ref=board,
            declared_by="inferred",
            actor="metaforge",
        )
        assert not created and again.status is InterfaceStatus.REMOVED

    async def test_simulation_inputs_are_derived_from_the_records_own_pins(self, twin) -> None:
        bracket, _, _ = await _revise(twin, "cad_model", "Bracket")
        sim_id = await _simulation(twin, [bracket], {"analysis_type": "static", "passed": True})
        edges = await list_interfaces(twin, PROJECT)
        assert len(edges) == 1
        derived = edges[0]
        assert derived.type is InterfaceType.SIMULATION_INPUT_OF and derived.derived
        assert derived.declared_by is DeclaredBy.TOOL
        assert derived.source.pinned_ref == f"{bracket}@1"
        assert derived.target.ref == f"record:{sim_id}"


# ---------------------------------------------------------------------------
# RC-08: mechanical to EDA
# ---------------------------------------------------------------------------


class TestMechanicalToEda:
    async def test_moving_the_enclosure_holes_flags_pcb_mounting_and_clearance(self, twin) -> None:
        enclosure, board, _ = await _enclosure_and_board(twin)
        await declare_interface(
            twin,
            PROJECT,
            type="clearance_to",
            source_ref=enclosure,
            target_ref=board,
            declared_by="human",
            actor="alice",
            coupled_parameters=["dimensions", "mounting_standoffs"],
        )
        result = await analyse_change(
            twin,
            PROJECT,
            ChangeSpec(item_key=enclosure, changed_parameters=["mounting_standoffs"]),
        )
        assert result.preview
        [entity] = result.impacted
        assert entity.ref == board and entity.domain == "electronics" and entity.direct
        assert {c.edge_type for c in entity.couplings} == {
            InterfaceType.MOUNTS_TO,
            InterfaceType.CLEARANCE_TO,
        }
        mounting = next(c for c in entity.couplings if c.edge_type is InterfaceType.MOUNTS_TO)
        assert mounting.rationale == (
            "Enclosure: mounting hole positions changed; Main board mounting depends on them"
        )
        assert result.by_domain == {"electronics": [board]}
        assert {a.kind for a in result.review_actions} == {"review_mounting", "review_clearance"}
        assert [r.tool for r in result.proposed_reruns] == ["kicad.run_drc"]
        assert result.summary == "This may affect the PCB fit."

    async def test_a_head_move_marks_the_boards_drc_stale_and_records_the_impact(
        self, twin
    ) -> None:
        enclosure, board, _ = await _enclosure_and_board(twin)
        drc = await _evidence(twin, [board], "kicad.run_drc", {"violations": 0})
        assert verdict_of_result((await _meta(twin, drc))["result"]) is ValidationStatus.PASS
        await _revise(
            twin,
            "cad_model",
            "Enclosure",
            {"mounting_standoffs": [{"x": 9, "y": 5}]},
            content="v2",
            key=enclosure,
        )
        meta = await _meta(twin, drc)
        assert meta["staleness"] == "stale"
        assert meta["stale_for"][enclosure] == {"pinned": 1, "current": 2}
        # Revision-scoped: still pinned to the board revision it checked.
        assert meta["depends_on_items"][0]["item_ref"] == f"{board}@1"
        status = check_result_for_record(str(drc), meta, check="drc")
        assert status.status is ValidationStatus.STALE
        [impact] = await list_impacts(twin, PROJECT)
        assert (
            impact.change.old_ref == f"{enclosure}@1" and impact.change.new_ref == f"{enclosure}@2"
        )
        [stale] = impact.stale_results
        assert stale.record_id == str(drc) and stale.marked
        assert stale.previous_status is ValidationStatus.PASS
        assert stale.status is ValidationStatus.STALE
        assert stale.pinned_ref == f"{enclosure}@1"
        assert impact.operation_id == f"job:impact-{impact.id}"
        assert await get_impact(twin, PROJECT, f"{enclosure}@2") == impact
        assert await get_impact(twin, PROJECT, impact.id or "") == impact

    async def test_a_proposed_edge_flags_but_never_marks_stale(self, twin) -> None:
        enclosure, _, _ = await _revise(twin, "cad_model", "Enclosure")
        board, _, _ = await _revise(twin, "pcb_layout", "Main board")
        await declare_interface(
            twin,
            PROJECT,
            type="mounts_to",
            source_ref=enclosure,
            target_ref=board,
            declared_by="inferred",
            actor="metaforge",
            confidence=0.7,
        )
        drc = await _evidence(twin, [board], "kicad.run_drc", {"violations": 0})
        await _revise(twin, "cad_model", "Enclosure", content="v2", key=enclosure)
        assert (await _meta(twin, drc))["staleness"] == "current"
        [impact] = await list_impacts(twin, PROJECT)
        [entity] = impact.impacted
        assert not entity.certain and entity.proposed_action.startswith("Confirm or remove")
        assert impact.proposed_reruns == []
        assert impact.next_action == "Confirm or remove the suggested link."


# ---------------------------------------------------------------------------
# RC-09: EDA to firmware
# ---------------------------------------------------------------------------


class TestEdaToFirmware:
    async def test_a_pinmap_change_flags_the_firmware_and_stales_its_build(self, twin) -> None:
        pinmap, _, _ = await _revise(twin, "pinmap", "Pin map")
        firmware, _, _ = await _revise(twin, "firmware_source", "Flight firmware")
        await declare_interface(
            twin,
            PROJECT,
            type="pin_maps_to",
            source_ref=pinmap,
            target_ref=firmware,
            declared_by="human",
            actor="alice",
            coupled_parameters=["PA5", "SPI1"],
            evidence={"modules": ["src/imu_driver.c", "include/board.h"]},
        )
        build = await _evidence(twin, [firmware], "firmware.build", {"success": True})
        await _revise(twin, "pinmap", "Pin map", content="v2", key=pinmap)
        meta = await _meta(twin, build)
        assert meta["staleness"] == "stale"
        assert meta["stale_for"][pinmap] == {"pinned": 1, "current": 2}
        [impact] = await list_impacts(twin, PROJECT)
        [entity] = impact.impacted
        assert entity.ref == firmware and entity.domain == "firmware"
        assert entity.modules == ["src/imu_driver.c", "include/board.h"]
        assert "pin assignment changed" in entity.rationale
        [rerun] = impact.proposed_reruns
        assert rerun.tool == "firmware.build" and rerun.consequential
        assert rerun.disposition == "requires_approval" and rerun.status == "proposed"
        assert impact.summary == "This may affect the firmware."

    async def test_a_pin_the_firmware_does_not_use_does_not_reach_it(self, twin) -> None:
        pinmap, _, _ = await _revise(twin, "pinmap", "Pin map")
        firmware, _, _ = await _revise(twin, "firmware_source", "Flight firmware")
        await declare_interface(
            twin,
            PROJECT,
            type="pin_maps_to",
            source_ref=pinmap,
            target_ref=firmware,
            declared_by="human",
            actor="alice",
            coupled_parameters=["PA5"],
        )
        result = await analyse_change(
            twin,
            PROJECT,
            ChangeSpec(item_key=pinmap, changed_parameters=["PB9"]),
            include_unaffected=True,
        )
        assert result.impacted == []
        [skipped] = result.not_affected
        assert skipped.ref == firmware and "none of its coupled parameters" in skipped.reason


# ---------------------------------------------------------------------------
# RC-10: simulation validity
# ---------------------------------------------------------------------------


class TestSimulationValidity:
    async def test_a_geometry_change_marks_the_fea_stale_not_pass_and_keeps_it(self, twin) -> None:
        bracket, _, _ = await _revise(twin, "cad_model", "Bracket", {"material": "AL6061"})
        sim = await _simulation(twin, [bracket], {"analysis_type": "static", "passed": True})
        await _revise(twin, "cad_model", "Bracket", {"material": "PA12"}, content="v2", key=bracket)
        meta = await _meta(twin, sim)
        assert meta["staleness"] == "stale"
        # Browsable as history, with the revision it was for.
        assert meta["depends_on_items"][0]["item_ref"] == f"{bracket}@1"
        assert (await twin.get_work_product(sim)) is not None
        [impact] = await list_impacts(twin, PROJECT)
        [stale] = impact.stale_results
        assert stale.check_kind == "fea" and stale.previous_status is ValidationStatus.PASS
        assert stale.status is ValidationStatus.STALE
        assert stale.pinned_ref == f"{bracket}@1"
        [entity] = impact.impacted
        assert (
            entity.ref == f"record:{sim}" and entity.edge_type is InterfaceType.SIMULATION_INPUT_OF
        )
        [rerun] = impact.proposed_reruns
        assert rerun.tool == "calculix.run_fea" and rerun.target_ref == bracket
        assert impact.summary == "This may affect the simulation results."
        check = check_result_for_record(str(sim), meta, check="fea")
        assert check.status is ValidationStatus.STALE and check.input_revisions == [f"{bracket}@1"]


# ---------------------------------------------------------------------------
# XDM-006, cycles and depth
# ---------------------------------------------------------------------------


class TestSelectivityAndBounds:
    async def test_unrelated_items_are_reported_not_affected_and_left_alone(self, twin) -> None:
        enclosure, board, _ = await _enclosure_and_board(twin)
        firmware, _, _ = await _revise(twin, "firmware_source", "Flight firmware")
        build = await _evidence(twin, [firmware], "firmware.build", {"success": True})
        result = await analyse_change(
            twin, PROJECT, ChangeSpec(item_key=enclosure), include_unaffected=True
        )
        assert [e.ref for e in result.impacted] == [board]
        unaffected = {n.ref: n.reason for n in result.not_affected}
        assert firmware in unaffected and "no interface links it" in unaffected[firmware]
        await _revise(twin, "cad_model", "Enclosure", content="v2", key=enclosure)
        assert (await _meta(twin, build))["staleness"] == "current"

    async def test_a_cycle_is_detected_and_walked_once(self, twin) -> None:
        a, _, _ = await _revise(twin, "pinmap", "Pins A")
        b, _, _ = await _revise(twin, "firmware_source", "Firmware B")
        c, _, _ = await _revise(twin, "pinmap", "Pins C")
        for src, dst in ((a, b), (b, c), (c, a)):
            await declare_interface(
                twin,
                PROJECT,
                type="pin_maps_to",
                source_ref=src,
                target_ref=dst,
                declared_by="human",
                actor="alice",
            )
        result = await analyse_change(twin, PROJECT, ChangeSpec(item_key=a))
        assert sorted(e.ref for e in result.impacted) == sorted([b, c])
        assert result.cycles == [[a, b, c, a]]
        indirect = next(e for e in result.impacted if e.ref == c)
        assert indirect.depth == 2 and not indirect.direct
        assert indirect.rationale.startswith("if ")

    async def test_the_walk_stops_at_its_depth_bound(self, twin) -> None:
        keys = [
            (await _revise(twin, "pinmap" if i % 2 == 0 else "firmware_source", f"Node {i}"))[0]
            for i in range(5)
        ]
        for src, dst in zip(keys, keys[1:], strict=False):
            await declare_interface(
                twin,
                PROJECT,
                type="pin_maps_to",
                source_ref=src,
                target_ref=dst,
                declared_by="human",
                actor="alice",
            )
        result = await analyse_change(twin, PROJECT, ChangeSpec(item_key=keys[0]), max_depth=2)
        assert [e.ref for e in result.impacted] == keys[1:3]
        assert result.depth_limited and result.max_depth == 2


# ---------------------------------------------------------------------------
# CHG-005/006: the shared status vocabulary
# ---------------------------------------------------------------------------


class TestValidationStatus:
    def test_the_existing_status_words_map_onto_the_six(self) -> None:
        assert {s.value for s in ValidationStatus} == {
            "PASS",
            "WARN",
            "FAIL",
            "INCOMPLETE",
            "NOT_EVALUATED",
            "STALE",
        }
        assert map_status("pass") is ValidationStatus.PASS
        assert map_status("uncertain") is ValidationStatus.WARN
        assert map_status("no_data") is ValidationStatus.NOT_EVALUATED
        assert map_status("unevaluated") is ValidationStatus.NOT_EVALUATED
        assert map_status("unverified") is ValidationStatus.INCOMPLETE
        assert map_status("superseded") is ValidationStatus.STALE
        assert map_status("something new") is ValidationStatus.NOT_EVALUATED

    def test_a_result_is_read_only_for_explicit_verdicts(self) -> None:
        assert verdict_of_result({"violations": 0}) is ValidationStatus.PASS
        assert verdict_of_result({"violations": 0, "warnings": 2}) is ValidationStatus.WARN
        assert verdict_of_result({"violations": [{"rule": "clearance"}]}) is ValidationStatus.FAIL
        assert verdict_of_result({"success": False}) is ValidationStatus.FAIL
        assert verdict_of_result({"max_von_mises_mpa": 120}) is ValidationStatus.INCOMPLETE
        assert verdict_of_result({}) is ValidationStatus.NOT_EVALUATED

    def test_no_pass_without_evidence(self) -> None:
        no_tool = CheckResult(
            check="drc", status=ValidationStatus.PASS, input_revisions=["PCB-A@1"]
        )
        assert no_tool.status is ValidationStatus.NOT_EVALUATED
        assert no_tool.detail == "not evaluated: no tool result"
        no_inputs = CheckResult(check="drc", status=ValidationStatus.PASS, tool_result_ref="r1")
        assert no_inputs.status is ValidationStatus.NOT_EVALUATED
        bound = CheckResult(
            check="drc",
            status=ValidationStatus.PASS,
            tool_result_ref="r1",
            input_revisions=["PCB-A@1"],
        )
        assert bound.status is ValidationStatus.PASS

    def test_an_old_pass_on_a_stale_record_is_stale(self) -> None:
        meta = {
            "staleness": "stale",
            "staleness_reason": "it was for CAD-A@1",
            "result": {"passed": True},
            "depends_on_items": [{"item_key": "CAD-A", "item_ref": "CAD-A@1", "revision": 1}],
        }
        check = check_result_for_record("r1", meta, check="fea")
        assert check.status is ValidationStatus.STALE and check.input_revisions == ["CAD-A@1"]
