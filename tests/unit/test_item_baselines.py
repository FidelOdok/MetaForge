"""Baselines over items, the current view and item diff (FORGE-526).

Component-level: the real recorders over ``InMemoryTwinAPI``, with only the
MinIO blob store patched (the same setup as ``test_item_revisions.py``).
"""

from __future__ import annotations

import base64
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from api_gateway.twin.baseline import (
    baseline_to_dict,
    make_baseline_creator,
)
from api_gateway.twin.baseline import (
    create_item_baseline as gate_seam,
)
from api_gateway.twin.constraint_recorder import make_constraint_recorder
from api_gateway.twin.geometry_recorder import make_geometry_recorder
from api_gateway.twin.item_diff import make_item_differ, parse_revision
from twin_core.api import InMemoryTwinAPI
from twin_core.items import commit_change_set, find_item
from twin_core.items.current import (
    build_current_view,
    current_revision,
    revision_states,
    revision_status,
)
from twin_core.models.baseline import BaselineItemRef
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct
from twin_core.transactions.baseline import (
    baseline_item_refs,
    create_item_baseline,
    diff_baselines,
)

PROJECT = "66666666-6666-6666-6666-666666666666"


def _step(body: str) -> str:
    return base64.b64encode(f"ISO-10303-21;\n{body}\nENDSEC;\n".encode()).decode("ascii")


@pytest.fixture(autouse=True)
def _blob_store(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


async def _part(twin: InMemoryTwinAPI, name: str, body: str = "a", **kw: Any) -> dict[str, Any]:
    record = make_geometry_recorder(twin, None)
    return await record(step_base64=_step(body), name=name, project_id=PROJECT, **kw)


async def _sim_result(twin: InMemoryTwinAPI, name: str, analysed_node_id: str) -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name=name,
            type=WorkProductType.SIMULATION_RESULT,
            domain="simulation",
            file_path="",
            content_hash="",
            format="json",
            created_by="test",
            project_id=UUID(PROJECT),
            metadata={"source_cad_model_id": analysed_node_id},
        )
    )


class TestRevisionStatus:
    def test_missing_status_is_committed(self) -> None:
        assert revision_status({}) == "committed"
        assert revision_status(None) == "committed"
        assert revision_status({"status": "Draft"}) == "draft"

    async def test_current_revision_is_the_head_not_a_draft(self, twin: InMemoryTwinAPI) -> None:
        await _part(twin, "Bracket", "a")
        await _part(twin, "Bracket", "b", run_id="run-1")
        item = await find_item(twin, "CAD-BRACKET", PROJECT)
        assert item is not None and item.head_revision == 1
        states = await revision_states(twin, item)
        assert [s.status for s in states] == ["committed", "draft"]
        cur = current_revision(states, item.head_node_id)
        assert cur is not None and cur.revision == 1


class TestItemBaseline:
    async def test_pins_every_current_item(self, twin: InMemoryTwinAPI) -> None:
        await _part(twin, "Bracket", "a")
        await _part(twin, "Bracket", "b")
        await _part(twin, "Shelf", "c")
        baseline = await create_item_baseline(
            twin, project_id=PROJECT, approved_by=["alice"], gate_id="G6", run_id="run-1"
        )
        assert sorted(r.ref for r in baseline.items) == ["CAD-BRACKET@2", "CAD-SHELF@1"]
        assert baseline.gate_id == "G6" and baseline.run_id == "run-1"
        assert baseline.source == "gate" and baseline.approved_by == ["alice"]
        assert baseline.includes == []
        stored = await twin.get_baseline(baseline.id)
        assert stored is not None and [r.ref for r in stored.items] == [
            r.ref for r in baseline.items
        ]

    async def test_drafts_never_reach_a_baseline(self, twin: InMemoryTwinAPI) -> None:
        await _part(twin, "Bracket", "a")
        await _part(twin, "Bracket", "b", run_id="run-1")
        await _part(twin, "Shelf", "s", run_id="run-1")
        refs = await baseline_item_refs(twin, PROJECT)
        # The open draft of Bracket and the draft-only Shelf are not current.
        assert [r.ref for r in refs] == ["CAD-BRACKET@1"]
        await commit_change_set(twin, "run-1", project_id=PROJECT, gate="G6", decided_by="a")
        refs = await baseline_item_refs(twin, PROJECT)
        assert sorted(r.ref for r in refs) == ["CAD-BRACKET@2", "CAD-SHELF@1"]

    async def test_immutable_after_later_revisions(self, twin: InMemoryTwinAPI) -> None:
        await _part(twin, "Bracket", "a")
        baseline = await create_item_baseline(twin, project_id=PROJECT, approved_by=["alice"])
        await _part(twin, "Bracket", "b")
        stored = await twin.get_baseline(baseline.id)
        assert stored is not None and [r.ref for r in stored.items] == ["CAD-BRACKET@1"]
        with pytest.raises(ValidationError):
            stored.items[0].revision = 2  # type: ignore[misc]
        assert not hasattr(twin, "update_baseline")

    async def test_idempotent_per_gate_and_run(self, twin: InMemoryTwinAPI) -> None:
        await _part(twin, "Bracket", "a")
        first = await create_item_baseline(
            twin, project_id=PROJECT, approved_by=["a"], gate_id="G6", run_id="r1"
        )
        again = await create_item_baseline(
            twin, project_id=PROJECT, approved_by=["a"], gate_id="G6", run_id="r1"
        )
        other_run = await create_item_baseline(
            twin, project_id=PROJECT, approved_by=["a"], gate_id="G6", run_id="r2"
        )
        assert again.id == first.id
        assert other_run.id != first.id
        assert len(await twin.list_baselines(project_id=UUID(PROJECT))) == 2

    async def test_empty_project_is_refused(self, twin: InMemoryTwinAPI) -> None:
        with pytest.raises(ValueError, match="no current item"):
            await create_item_baseline(twin, project_id=PROJECT, approved_by=["a"])

    async def test_gate_seam_is_best_effort(self, twin: InMemoryTwinAPI) -> None:
        assert await gate_seam(PROJECT, "G6", "alice", "r1", twin=twin) is None
        await _part(twin, "Bracket", "a")
        out = await gate_seam(PROJECT, "G6", "alice", "r1", twin=twin)
        assert out is not None and out["items"][0]["ref"] == "CAD-BRACKET@1"
        assert out["gate_id"] == "G6" and out["approved_by"] == ["alice"]

    async def test_manual_baseline_also_pins_items(self, twin: InMemoryTwinAPI) -> None:
        await _part(twin, "Bracket", "a")
        out = await make_baseline_creator(twin)(
            project_id=PROJECT, approved_by=["bob"], reason="freeze"
        )
        assert out["item_count"] == 1 and out["items"] == ["CAD-BRACKET@1"]
        assert out["member_count"] == 0  # no constraints or entities, items alone


class TestBaselineDiff:
    def _ref(self, key: str, rev: int, node: UUID | None = None) -> BaselineItemRef:
        return BaselineItemRef(
            key=key, item_type="cad_model", revision=rev, node_id=node or uuid4()
        )

    def test_statuses(self) -> None:
        same = uuid4()
        a = [self._ref("CAD-A", 1, same), self._ref("CAD-B", 1), self._ref("CAD-C", 2)]
        b = [self._ref("CAD-A", 1, same), self._ref("CAD-B", 3), self._ref("CAD-D", 1)]
        out = {d.key: d for d in diff_baselines(a, b)}
        assert out["CAD-A"].status == "unchanged"
        assert (out["CAD-B"].status, out["CAD-B"].from_revision, out["CAD-B"].to_revision) == (
            "changed",
            1,
            3,
        )
        assert out["CAD-C"].status == "removed" and out["CAD-C"].to_revision is None
        assert out["CAD-D"].status == "added" and out["CAD-D"].from_revision is None
        assert out["CAD-B"].to_dict()["to_ref"] == "CAD-B@3"


class TestCurrentView:
    async def test_counts_cover_current_items_only(self, twin: InMemoryTwinAPI) -> None:
        b1 = await _part(twin, "Bracket", "a")
        b2 = await _part(twin, "Bracket", "b")
        b3 = await _part(twin, "Bracket", "c")
        shelf = await _part(twin, "Shelf", "d")
        linked = [
            {"id": b1["node_id"], "name": "Bracket", "type": "cad_model", "status": "error"},
            {"id": b2["node_id"], "name": "Bracket", "type": "cad_model", "status": "error"},
            {"id": b3["node_id"], "name": "Bracket", "type": "cad_model", "status": "valid"},
            {"id": shelf["node_id"], "name": "Shelf", "type": "cad_model", "status": "warning"},
        ]
        view = await build_current_view(twin, PROJECT, project_work_products=linked)
        assert [(r["key"], r["revision"]) for r in view.items] == [
            ("CAD-BRACKET", 3),
            ("CAD-SHELF", 1),
        ]
        assert view.counts["items"] == 2
        assert view.counts["superseded_revisions"] == 2
        # Readiness: 1 valid of 2 current items, not 1 of the 4 nodes written.
        assert view.readiness == 50
        assert view.counts["error"] == 0 and view.counts["warning"] == 1
        assert view.groups == [
            {"item_type": "cad_model", "count": 2, "keys": ["CAD-BRACKET", "CAD-SHELF"]}
        ]
        bracket = view.items[0]
        assert bracket["revision_count"] == 3 and bracket["validation_status"] == "valid"

    async def test_drafts_are_listed_separately(self, twin: InMemoryTwinAPI) -> None:
        await _part(twin, "Bracket", "a")
        await _part(twin, "Bracket", "b", run_id="run-9")
        await _part(twin, "Shelf", "s", run_id="run-9")
        view = await build_current_view(twin, PROJECT)
        rows = {r["key"]: r for r in view.items}
        # A draft-only item has a row for the Working view but is not counted.
        assert rows["CAD-SHELF"]["revision"] is None
        assert view.counts["items"] == 1
        row = rows["CAD-BRACKET"]
        assert row["revision"] == 1 and row["revision_status"] == "committed"
        assert [(d["revision"], d["status"], d["run_id"]) for d in row["drafts"]] == [
            (2, "draft", "run-9")
        ]
        assert view.counts["drafts"] == 2

    async def test_out_of_date_results(self, twin: InMemoryTwinAPI) -> None:
        v1 = await _part(twin, "Bracket", "a")
        old = await _sim_result(twin, "FEA v1", v1["node_id"])
        v2 = await _part(twin, "Bracket", "b")
        fresh_shelf = await _part(twin, "Shelf", "s")
        fresh = await _sim_result(twin, "FEA shelf", fresh_shelf["node_id"])
        view = await build_current_view(twin, PROJECT)
        by_id = {r["node_id"]: r for r in view.records}
        assert by_id[str(old.id)]["out_of_date"] is True
        assert by_id[str(old.id)]["analysed"] == [
            {"key": "CAD-BRACKET", "revision": 1, "ref": "CAD-BRACKET@1", "current": False}
        ]
        assert by_id[str(fresh.id)]["out_of_date"] is False
        assert view.counts["out_of_date_results"] == 1
        rows = {r["key"]: r for r in view.items}
        assert rows["CAD-BRACKET"]["evidence_state"] == "out_of_date"
        assert rows["CAD-SHELF"]["evidence_state"] == "current"
        assert v2["revision"] == 2

    async def test_analysed_geometry_pin_marks_result_out_of_date(
        self, twin: InMemoryTwinAPI
    ) -> None:
        """FORGE-532's real simulation_result pin (analysed_geometry) is the dependency."""
        from api_gateway.twin.document_recorder import make_document_recorder

        v1 = await _part(twin, "Bracket", "a")
        record = make_document_recorder(twin)
        sim = await record(
            content="{}",
            name="FEA bracket",
            wp_type=WorkProductType.SIMULATION_RESULT,
            domain="simulation",
            fmt="json",
            link_type="simulation_result",
            source_tool="calculix.run_fea",
            project_id=PROJECT,
            analysis={"geometry_node_id": v1["node_id"], "geometry_revision": 1},
        )
        fresh = await build_current_view(twin, PROJECT)
        assert {r["node_id"]: r["out_of_date"] for r in fresh.records}[sim["node_id"]] is False
        await _part(twin, "Bracket", "b")
        stale = await build_current_view(twin, PROJECT)
        row = {r["node_id"]: r for r in stale.records}[sim["node_id"]]
        assert row["out_of_date"] is True and row["analysed"][0]["ref"] == "CAD-BRACKET@1"

    async def test_recorded_staleness_is_honoured(self, twin: InMemoryTwinAPI) -> None:
        """FORGE-527: revalidated keeps an old pin current; stale marks a result out of date."""
        v1 = await _part(twin, "Bracket", "a")
        checked = await _sim_result(twin, "FEA rechecked", v1["node_id"])
        await twin.graph.update_node(
            checked.id, {"metadata": {**checked.metadata, "staleness": "revalidated"}}
        )
        await _part(twin, "Bracket", "b")
        shelf = await _part(twin, "Shelf", "s")
        flagged = await _sim_result(twin, "FEA flagged", shelf["node_id"])
        await twin.graph.update_node(
            flagged.id, {"metadata": {**flagged.metadata, "staleness": "invalid"}}
        )
        view = await build_current_view(twin, PROJECT)
        rows = {r["node_id"]: r for r in view.records}
        assert rows[str(checked.id)]["out_of_date"] is False
        assert rows[str(checked.id)]["staleness"] == "revalidated"
        assert rows[str(flagged.id)]["out_of_date"] is True

    async def test_gate_attribution_from_baseline(self, twin: InMemoryTwinAPI) -> None:
        await _part(twin, "Bracket", "a")
        baseline = await create_item_baseline(
            twin, project_id=PROJECT, approved_by=["a"], gate_id="G6", run_id="r1"
        )
        view = await build_current_view(twin, PROJECT, baselines=[baseline])
        assert view.items[0]["gate_id"] == "G6"

    async def test_unclassified_work_products_stay_listed(self, twin: InMemoryTwinAPI) -> None:
        await _part(twin, "Bracket", "a")
        pinmap = str(uuid4())
        decision = await twin.create_work_product(
            WorkProduct(
                name="Use 6061",
                type=WorkProductType.DESIGN_DECISION,
                domain="mechanical",
                file_path="",
                content_hash="",
                format="md",
                created_by="test",
                project_id=UUID(PROJECT),
            )
        )
        linked = [
            {"id": pinmap, "name": "Pinmap", "type": "pinmap", "status": "valid"},
            {
                "id": str(decision.id),
                "name": "Use 6061",
                "type": "design_decision",
                "status": "valid",
            },
        ]
        view = await build_current_view(twin, PROJECT, project_work_products=linked)
        assert [o["name"] for o in view.other] == ["Pinmap"]
        assert view.counts["decisions"] == 1
        assert view.counts["total"] == 2 and view.readiness == 50


class TestItemDiff:
    async def test_parameters_and_recorded_geometry(self, twin: InMemoryTwinAPI) -> None:
        await _part(
            twin,
            "Bracket",
            "a",
            parameters={"thickness_mm": 3, "holes": 4},
            properties={
                "volume_mm3": 1000.0,
                "bounding_box": {"x": 10, "y": 20, "z": 5},
                "material": "aluminum_6061",
            },
        )
        await _part(
            twin,
            "Bracket",
            "b",
            parameters={"thickness_mm": 4, "fillet_mm": 1},
            properties={
                "volume_mm3": 1500.0,
                "bounding_box": {"x": 10, "y": 20, "z": 6},
                "mass_kg": 0.01,
            },
        )
        out = await make_item_differ(twin)("CAD-BRACKET", a="@1", b="2")
        assert out["a_ref"] == "CAD-BRACKET@1" and out["b_ref"] == "CAD-BRACKET@2"
        params = {p["name"]: p for p in out["parameters"]}
        assert params["thickness_mm"] == {
            "name": "thickness_mm",
            "status": "changed",
            "from": 3,
            "to": 4,
        }
        assert params["holes"]["status"] == "removed"
        assert params["fillet_mm"]["status"] == "added"
        geo = out["geometry"]
        assert geo["source"] == "recorded"
        assert geo["volume_delta_mm3"] == 500.0
        assert geo["bounding_box_delta"] == {"z": 1.0}
        assert geo["b"]["mass_kg"] == 0.01
        # @1 has no recorded mass: volume x density of its known material.
        assert geo["a"]["mass_kg"] == pytest.approx(1000.0 * 1e-9 * 2700, rel=0.05)

    async def test_defaults_compare_current_with_previous(self, twin: InMemoryTwinAPI) -> None:
        await _part(twin, "Bracket", "a")
        await _part(twin, "Bracket", "b")
        out = await make_item_differ(twin)("CAD-BRACKET")
        assert (out["a"]["revision"], out["b"]["revision"]) == (1, 2)

    async def test_live_geometry_diff_is_used(self, twin: InMemoryTwinAPI) -> None:
        v1 = await _part(twin, "Bracket", "a")
        v2 = await _part(twin, "Bracket", "b")
        calls: list[dict[str, Any]] = []

        async def differ(**kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "previous_volume_mm3": 10.0,
                "current_volume_mm3": 12.5,
                "previous_bounding_box": {"x": 1},
                "current_bounding_box": {"x": 2},
            }

        out = await make_item_differ(twin, geometry_diff_provider=lambda: differ)("CAD-BRACKET")
        assert calls == [
            {"work_product_id": v2["node_id"], "previous_work_product_id": v1["node_id"]}
        ]
        assert out["geometry"]["source"] == "describe_step_file"
        assert out["geometry"]["volume_delta_mm3"] == 2.5

    async def test_dependents_pinned_to_old_revision(self, twin: InMemoryTwinAPI) -> None:
        v1 = await _part(twin, "Bracket", "a")
        sim = await _sim_result(twin, "FEA v1", v1["node_id"])
        await _part(twin, "Bracket", "b")
        out = await make_item_differ(twin)("CAD-BRACKET")
        assert [d["node_id"] for d in out["dependents"]] == [str(sim.id)]

    async def test_requirement_value_changes(self, twin: InMemoryTwinAPI) -> None:
        record = make_constraint_recorder(twin)
        base = {"domain": "mechanical", "severity": "error"}
        await record(
            title="Payload",
            constraints=[
                {
                    **base,
                    "name": "payload",
                    "metric": "payload_kg",
                    "operator": ">=",
                    "limit": 1,
                    "unit": "kg",
                },
                {
                    **base,
                    "name": "reach",
                    "metric": "reach_mm",
                    "operator": ">=",
                    "limit": 500,
                    "unit": "mm",
                },
            ],
            project_id=PROJECT,
        )
        await record(
            title="Payload",
            constraints=[
                {
                    **base,
                    "name": "payload",
                    "metric": "payload_kg",
                    "operator": ">=",
                    "limit": 2,
                    "unit": "kg",
                },
                {
                    **base,
                    "name": "reach",
                    "metric": "reach_mm",
                    "operator": ">=",
                    "limit": 500,
                    "unit": "mm",
                },
            ],
            project_id=PROJECT,
        )
        out = await make_item_differ(twin)("CS-PAYLOAD")
        assert [(r["name"], r["status"], r["from"], r["to"]) for r in out["requirements"]] == [
            ("payload", "changed", ">= 1 kg", ">= 2 kg")
        ]

    def test_parse_revision(self) -> None:
        assert parse_revision("@3") == 3
        assert parse_revision("CAD-X@4") == 4
        assert parse_revision(None) is None
        with pytest.raises(ValueError):
            parse_revision("x")


class TestBaselineDict:
    async def test_shape(self, twin: InMemoryTwinAPI) -> None:
        await _part(twin, "Bracket", "a")
        baseline = await create_item_baseline(twin, project_id=PROJECT, approved_by=["a"])
        out = baseline_to_dict(baseline)
        assert out["items"][0]["ref"] == "CAD-BRACKET@1"
        assert out["source"] == "manual" and out["item_count"] == 1
        assert "items" not in baseline_to_dict(baseline, include_items=False)


class TestRoutes:
    @pytest.fixture
    async def client(self, twin: InMemoryTwinAPI) -> Any:
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from api_gateway.twin import routes
        from api_gateway.twin.baseline_routes import router as baselines_router
        from api_gateway.twin.item_routes import router as items_router

        previous = routes.get_twin()
        routes.init_twin(twin)
        app = FastAPI()
        app.include_router(items_router)
        app.include_router(baselines_router)
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        routes.init_twin(previous)

    async def test_baseline_list_detail_and_diff(self, twin: InMemoryTwinAPI, client: Any) -> None:
        await _part(twin, "Bracket", "a")
        first = await create_item_baseline(
            twin, project_id=PROJECT, approved_by=["a"], gate_id="G5", run_id="r1"
        )
        await _part(twin, "Bracket", "b")
        await _part(twin, "Shelf", "c")
        second = await create_item_baseline(
            twin, project_id=PROJECT, approved_by=["b"], gate_id="G6", run_id="r1"
        )

        listed = (await client.get("/v1/twin/baselines", params={"project_id": PROJECT})).json()
        assert [b["gate_id"] for b in listed["baselines"]] == ["G6", "G5"]

        detail = (await client.get(f"/v1/twin/baselines/{first.id}")).json()
        assert [i["ref"] for i in detail["items"]] == ["CAD-BRACKET@1"]

        diff = (
            await client.get(
                "/v1/twin/baselines/diff", params={"a": str(first.id), "b": str(second.id)}
            )
        ).json()
        assert {i["key"]: (i["status"], i["from_ref"], i["to_ref"]) for i in diff["items"]} == {
            "CAD-BRACKET": ("changed", "CAD-BRACKET@1", "CAD-BRACKET@2"),
            "CAD-SHELF": ("added", None, "CAD-SHELF@1"),
        }
        assert diff["counts"] == {"unchanged": 0, "changed": 1, "added": 1, "removed": 0}

        vs_current = (
            await client.get(
                "/v1/twin/baselines/diff", params={"a": str(second.id), "b": "current"}
            )
        ).json()
        assert vs_current["b_is_current"] is True
        assert vs_current["counts"]["unchanged"] == 2

        missing = await client.get(f"/v1/twin/baselines/{uuid4()}")
        assert missing.status_code == 404

    async def test_current_view_and_history(self, twin: InMemoryTwinAPI, client: Any) -> None:
        await _part(twin, "Bracket", "a")
        await _part(twin, "Bracket", "b", run_id="run-7")
        await commit_change_set(twin, "run-7", project_id=PROJECT, gate="G6", decided_by="a")
        await create_item_baseline(
            twin, project_id=PROJECT, approved_by=["a"], gate_id="G6", run_id="run-7"
        )
        view = (await client.get("/v1/twin/current-view", params={"project_id": PROJECT})).json()
        assert view["counts"]["items"] == 1
        assert view["items"][0]["ref"] == "CAD-BRACKET@2"
        assert view["items"][0]["gate_id"] == "G6"
        assert view["latest_baseline"]["gate_id"] == "G6"

        history = (await client.get("/v1/twin/items/CAD-BRACKET/revisions")).json()
        assert history["current"]["revision"] == 2
        assert [
            (r["revision"], r["status"], r["is_head"], r["gate"], len(r["baselines"]))
            for r in history["revisions"]
        ] == [(1, "committed", False, None, 0), (2, "approved", True, "G6", 1)]

        changes = (
            await client.get("/v1/twin/runs/run-7/changes", params={"project_id": PROJECT})
        ).json()
        assert [(r["ref"], r["baselined_by_gate"]) for r in changes["revisions"]] == [
            ("CAD-BRACKET@2", "G6")
        ]
        assert [b["gate_id"] for b in changes["baselines"]] == ["G6"]

    async def test_revision_index_maps_constraints_to_their_set(
        self, twin: InMemoryTwinAPI, client: Any
    ) -> None:
        record = make_constraint_recorder(twin)
        entry = {"domain": "mechanical", "severity": "error", "name": "payload"}
        first = await record(
            title="Payload", constraints=[{**entry, "expression": "a > 1"}], project_id=PROJECT
        )
        second = await record(
            title="Payload", constraints=[{**entry, "expression": "a > 2"}], project_id=PROJECT
        )
        await _part(twin, "Bracket", "a")
        await _part(twin, "Bracket", "b", run_id="run-1")
        body = (await client.get("/v1/twin/revision-index", params={"project_id": PROJECT})).json()
        nodes = body["nodes"]
        assert nodes[first["node_id"]]["ref"] == "CS-PAYLOAD@1"
        assert nodes[first["node_id"]]["current"] is False
        old_constraint = first["constraint_ids"][0]
        new_constraint = second["constraint_ids"][0]
        assert nodes[old_constraint]["via"] == "constraint_set"
        assert nodes[new_constraint]["ref"] == "CS-PAYLOAD@2"
        # The open draft of the bracket is not in the index.
        assert sorted(v["ref"] for v in nodes.values() if v["key"] == "CAD-BRACKET") == [
            "CAD-BRACKET@1"
        ]

    async def test_item_diff_route(self, twin: InMemoryTwinAPI, client: Any) -> None:
        await _part(twin, "Bracket", "a", parameters={"t": 1})
        await _part(twin, "Bracket", "b", parameters={"t": 2})
        ok = await client.get("/v1/twin/items/CAD-BRACKET/diff", params={"a": "@1", "b": "@2"})
        assert ok.status_code == 200
        assert ok.json()["parameters"][0]["to"] == 2
        assert (await client.get("/v1/twin/items/CAD-NOPE/diff")).status_code == 404
        bad = await client.get("/v1/twin/items/CAD-BRACKET/diff", params={"a": "x"})
        assert bad.status_code == 400
