"""Legacy nodes migrated into items and revisions (FORGE-529).

The fixture is the live shelf project as FORGE-521 found it: four runs left
16 cad_models for 3 parts and 1 assembly (names drifting from run to run, a
few hand-made SUPERSEDES edges), 8 constraint sets, 14 phase-summary
decisions and 8 simulation results, none of them items.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.record_pins import PIN_EDGE_KIND, record_pins, record_staleness
from twin_core.items import item_history, list_items
from twin_core.items.migration import (
    ItemMigration,
    MigrationPlan,
    PlanTamperedError,
    RunInfo,
    StalePlanError,
    is_run_summary,
    name_tokens,
    render_report,
)
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct

PROJECT = UUID("e7b896cc-0000-4000-8000-000000000529")
T0 = datetime(2026, 9, 1, tzinfo=UTC)

#: run -> (outcome, reason)
RUNS = {
    "run-1": ("approved", None),
    "run-2": ("approved", None),
    "run-3": ("approved", None),
    "run-4": ("rejected", "FEA failed: safety factor 0.8 under the 20 kg load"),
}

LEFT = [
    "Left PETG Gusset Bracket",
    "Left Vertical Triangular PETG Gusset Bracket - 220 x 120 x 12 mm",
    "Left Gusset Bracket",
    "Left PETG Gusset Bracket v2",
]
RIGHT = [
    "Right PETG Gusset Bracket",
    "Right Vertical Triangular PETG Gusset Bracket - 220 x 120 x 12 mm",
    "Right Gusset Bracket",
    "Right PETG Gusset Bracket (final)",
]
PLANK = ["Shelf Plank", "Shelf Plank - 600 x 200 x 18 mm", "Shelf Board", "Shelf Board"]
ASSEMBLY = [
    "Wall Shelf Assembly",
    "Shelf Assembly",
    "Wall Shelf Assembly (PETG brackets)",
    "Wall-Mounted Shelf Assembly",
]
SUMMARY_PHASES = [
    "Concept",
    "Requirements",
    "Architecture",
    "Mechanical",
    "Manufacturing",
    "Verification",
    "Release",
]


async def _wp(
    twin: InMemoryTwinAPI,
    name: str,
    wp_type: WorkProductType,
    at: datetime,
    *,
    metadata: dict[str, Any] | None = None,
    project: UUID | None = PROJECT,
) -> WorkProduct:
    node = WorkProduct(
        name=name,
        type=wp_type,
        domain="mechanical",
        file_path=f"wp/{uuid4()}",
        content_hash=uuid4().hex,
        format="step",
        metadata=dict(metadata or {}),
        created_at=at,
        updated_at=at,
        created_by="legacy",
        project_id=project,
    )
    await twin.graph.add_node(node)
    return node


class Shelf:
    """The fixture's node ids, by part and run (index 0..3 = run-1..run-4)."""

    def __init__(self) -> None:
        self.left: list[WorkProduct] = []
        self.right: list[WorkProduct] = []
        self.plank: list[WorkProduct] = []
        self.assembly: list[WorkProduct] = []
        self.constraint_sets: list[WorkProduct] = []
        self.summaries: list[WorkProduct] = []
        self.decisions: list[WorkProduct] = []
        self.sims: dict[str, WorkProduct] = {}


async def build_shelf(twin: InMemoryTwinAPI) -> Shelf:
    shelf = Shelf()
    for run in range(4):
        run_id = f"run-{run + 1}"
        at = T0 + timedelta(days=run)
        bracket_meta = {"bbox_mm": [220 + run * 4, 120, 12 - run], "material": "PETG"}
        for names, bucket in ((LEFT, shelf.left), (RIGHT, shelf.right)):
            bucket.append(
                await _wp(
                    twin,
                    names[run],
                    WorkProductType.CAD_MODEL,
                    at + timedelta(minutes=1 + len(bucket)),
                    metadata={**bracket_meta, "run_id": run_id, "phase": "mechanical"},
                )
            )
        # The plank's nodes carry no run: its newest node is simply the head.
        shelf.plank.append(
            await _wp(
                twin,
                PLANK[run],
                WorkProductType.CAD_MODEL,
                at + timedelta(minutes=5),
                metadata={"bbox_mm": [600, 200, 18]},
            )
        )
        shelf.assembly.append(
            await _wp(
                twin,
                ASSEMBLY[run],
                WorkProductType.CAD_MODEL,
                at + timedelta(minutes=10),
                metadata={
                    "run_id": run_id,
                    "phase": "mechanical",
                    "parts": [
                        {"node_id": str(shelf.left[-1].id)},
                        {"node_id": str(shelf.right[-1].id)},
                        {"node_id": str(shelf.plank[-1].id)},
                    ],
                },
            )
        )
        for n in range(2):
            shelf.constraint_sets.append(
                await _wp(
                    twin,
                    ["Shelf requirements", "Wall shelf constraint set"][n] + f" (run {run + 1})",
                    WorkProductType.CONSTRAINT_SET,
                    at + timedelta(minutes=n),
                )
            )
    # Hand-made SUPERSEDES edges: the plank's rename and two bracket links.
    await twin.add_edge(shelf.plank[2].id, shelf.plank[1].id, EdgeType.SUPERSEDES)
    await twin.add_edge(shelf.plank[3].id, shelf.plank[2].id, EdgeType.SUPERSEDES)
    await twin.add_edge(shelf.left[1].id, shelf.left[0].id, EdgeType.SUPERSEDES)
    # 14 phase summaries (hyphen, en dash and em dash) and 2 real decisions.
    dashes = ["-", "\u2013", "\u2014"]
    for i in range(14):
        phase = SUMMARY_PHASES[i % len(SUMMARY_PHASES)]
        shelf.summaries.append(
            await _wp(
                twin,
                f"{phase} {dashes[i % 3]} phase summary",
                WorkProductType.DESIGN_DECISION,
                T0 + timedelta(hours=i),
            )
        )
    shelf.decisions.append(
        await _wp(twin, "Use PETG for the brackets", WorkProductType.DESIGN_DECISION, T0)
    )
    shelf.decisions.append(
        await _wp(
            twin,
            "Summary of phase trade-offs",
            WorkProductType.DESIGN_DECISION,
            T0 + timedelta(hours=1),
        )
    )
    # 8 simulation results: 3 per bracket (runs 1-3), 1 on the plank head,
    # 1 on the first plank linked by an edge only (no FORGE-532 pin).
    for side, nodes in (("left", shelf.left), ("right", shelf.right)):
        for run in range(3):
            shelf.sims[f"{side}-{run + 1}"] = await _wp(
                twin,
                f"{side} bracket FEA run {run + 1}",
                WorkProductType.SIMULATION_RESULT,
                T0 + timedelta(days=run, minutes=30),
                metadata={
                    "analysis_type": "static",
                    "analysed_geometry": {"node_id": str(nodes[run].id)},
                    "analysed_geometry_node_id": str(nodes[run].id),
                },
            )
    shelf.sims["plank-4"] = await _wp(
        twin,
        "plank FEA",
        WorkProductType.SIMULATION_RESULT,
        T0 + timedelta(days=3, minutes=30),
        metadata={"analysed_geometry_node_id": str(shelf.plank[3].id)},
    )
    shelf.sims["plank-1"] = await _wp(
        twin, "plank FEA (old)", WorkProductType.SIMULATION_RESULT, T0 + timedelta(minutes=30)
    )
    await twin.add_edge(shelf.sims["plank-1"].id, shelf.plank[0].id, EdgeType.DERIVES_FROM)
    return shelf


async def run_lookup(run_id: str) -> RunInfo | None:
    if run_id not in RUNS:
        return None
    outcome, reason = RUNS[run_id]
    return RunInfo(run_id=run_id, outcome=outcome, reason=reason)


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
async def shelf(twin: InMemoryTwinAPI) -> Shelf:
    return await build_shelf(twin)


def _migration(twin: InMemoryTwinAPI) -> ItemMigration:
    return ItemMigration(twin, run_lookup=run_lookup)


def _item_with(plan: MigrationPlan, node: WorkProduct) -> Any:
    return next(p for p in plan.items if any(r.node_id == str(node.id) for r in p.revisions))


class TestNames:
    def test_dimensions_and_version_words_are_ignored(self) -> None:
        assert name_tokens(LEFT[1]) == {
            "left",
            "vertical",
            "triangular",
            "petg",
            "gusset",
            "bracket",
        }
        assert name_tokens(LEFT[3]) == {"left", "petg", "gusset", "bracket"}

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Mechanical - phase summary", True),
            ("Mechanical \u2014 phase summary", True),
            ("Mechanical – Phase Summary ", True),
            ("Summary of phase trade-offs", False),
            ("phase summary of the bracket choice", False),
        ],
    )
    def test_run_summary_titles(self, title: str, expected: bool) -> None:
        assert is_run_summary(title) is expected

    def test_a_flagged_record_is_a_run_summary_whatever_its_title(self) -> None:
        assert is_run_summary("anything", {"run_summary": True})


class TestPlan:
    async def test_groups_the_shelf_into_four_cad_items_and_one_requirements_item(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        plan = await _migration(twin).plan(PROJECT)
        cad = [p for p in plan.items if p.item_type in {"cad_model", "assembly"}]
        assert len(cad) == 4
        assert sorted(p.item_type for p in cad) == [
            "assembly",
            "cad_model",
            "cad_model",
            "cad_model",
        ]
        for nodes in (shelf.left, shelf.right, shelf.plank, shelf.assembly):
            entry = _item_with(plan, nodes[0])
            assert [r.node_id for r in entry.revisions] == [str(n.id) for n in nodes]
            assert [r.revision for r in entry.revisions] == [1, 2, 3, 4]
        requirements = [p for p in plan.items if p.item_type == "constraint_set"]
        assert len(requirements) == 1
        assert len(requirements[0].revisions) == 8
        assert "one_per_project" in requirements[0].rules
        assert requirements[0].head_node_id == str(shelf.constraint_sets[-1].id)
        assert len(plan.items) == 5

    async def test_heads_skip_the_rejected_run_and_keep_its_reason(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        plan = await _migration(twin).plan(PROJECT)
        for nodes in (shelf.left, shelf.right, shelf.assembly):
            entry = _item_with(plan, nodes[0])
            assert entry.head_node_id == str(nodes[2].id)
            assert entry.head_revision == 3
            assert [r.status for r in entry.revisions] == [
                "approved",
                "approved",
                "head",
                "rejected",
            ]
            assert entry.revisions[3].status_reason == RUNS["run-4"][1]
        plank = _item_with(plan, shelf.plank[0])
        # No run on the plank: simply the newest becomes the head.
        assert plank.head_node_id == str(shelf.plank[3].id)
        assert plank.revisions[-1].status == "head"

    async def test_each_revision_says_why_it_was_grouped(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        plan = await _migration(twin).plan(PROJECT)
        left = _item_with(plan, shelf.left[0])
        assert left.revisions[1].rule == "supersedes"
        assert str(shelf.left[0].id) in left.revisions[1].evidence
        assert {r.rule for r in left.revisions[2:]} == {"name_similarity"}
        assert "bbox" in left.revisions[2].evidence
        plank = _item_with(plan, shelf.plank[0])
        # Shelf Plank and Shelf Board share one word in two: only the
        # hand-made SUPERSEDES edge joins them.
        assert "supersedes" in plank.rules
        assert "name_similarity" in plank.rules
        assert all(p.confidence == "high" for p in plan.items), plan.low_confidence

    async def test_left_and_right_are_never_merged(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        plan = await _migration(twin).plan(PROJECT)
        assert _item_with(plan, shelf.left[0]).key != _item_with(plan, shelf.right[0]).key

    async def test_phase_summaries_are_flagged_and_results_pinned(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        plan = await _migration(twin).plan(PROJECT)
        marks = {r.node_id for r in plan.records if r.action == "mark_run_summary"}
        assert marks == {str(n.id) for n in shelf.summaries}
        pins = {r.node_id: r for r in plan.records if r.action == "pin"}
        assert len(pins) == 8
        stale = {k for k, v in shelf.sims.items() if pins[str(v.id)].staleness == "stale"}
        assert stale == {"left-1", "left-2", "right-1", "right-2", "plank-1"}
        left = _item_with(plan, shelf.left[0])
        assert pins[str(shelf.sims["left-1"].id)].item_ref == f"{left.key}@1"
        assert f"{left.key} is now @3" in pins[str(shelf.sims["left-1"].id)].reason

    async def test_counts_before_and_after(self, twin: InMemoryTwinAPI, shelf: Shelf) -> None:
        plan = await _migration(twin).plan(PROJECT)
        before, after = plan.counts_before, plan.counts_after
        assert before["types"]["cad_model"] == {"nodes": 12, "items": 0, "unlinked": 12}
        assert after["types"]["cad_model"] == {"nodes": 12, "items": 3, "unlinked": 0}
        assert after["types"]["assembly"]["items"] == 1
        assert after["types"]["constraint_set"] == {"nodes": 8, "items": 1, "unlinked": 0}
        assert before["records"]["decisions_listed"] == 16
        assert after["records"]["decisions_listed"] == 2
        assert after["records"]["pinned"] == 8
        assert after["records"]["stale"] == 5

    async def test_the_report_is_json_and_a_readable_table(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        plan = await _migration(twin).plan(PROJECT)
        again = MigrationPlan.model_validate_json(plan.model_dump_json())
        assert again.content_hash() == plan.plan_hash
        text = render_report(plan)
        for entry in plan.items:
            assert entry.key in text
        assert str(shelf.left[3].id) in text
        assert "rejected: FEA failed" in text
        assert "decisions listed 16 -> 2" in text

    async def test_planning_writes_nothing(self, twin: InMemoryTwinAPI, shelf: Shelf) -> None:
        await _migration(twin).plan(PROJECT)
        assert await list_items(twin, project_id=PROJECT, include_unheaded=True) == []
        node = await twin.graph.get_node(shelf.summaries[0].id)
        assert "run_summary" not in node.metadata


class TestApply:
    async def test_apply_then_plan_again_proposes_nothing(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        migration = _migration(twin)
        plan = await migration.plan(PROJECT)
        result = await migration.apply(plan, applied_by="local:test")
        assert result.failures == []
        assert result.items_created == 5
        assert result.revisions_linked == 24
        assert result.run_summaries_marked == 14
        assert result.records_pinned == 8
        assert result.records_stale == 5
        assert result.by_type["cad_model"] == {"items": 3, "revisions": 12}

        again = await migration.plan(PROJECT)
        assert again.empty, render_report(again)
        assert again.counts_after == again.counts_before

    async def test_applied_items_have_heads_history_and_lessons(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        migration = _migration(twin)
        await migration.apply(await migration.plan(PROJECT))
        items = {i.item_type: i for i in await list_items(twin, project_id=PROJECT)}
        assert len(await list_items(twin, project_id=PROJECT)) == 5
        assembly = items["assembly"]
        assert assembly.head_node_id == shelf.assembly[2].id
        assert assembly.head_revision == 3
        history = await item_history(twin, assembly)
        assert [h.status for h in history] == ["approved", "approved", "approved", "rejected"]
        assert history[3].status_reason == RUNS["run-4"][1]
        assert all(h.adopted for h in history)
        # SUPERSEDES between consecutive accepted revisions, nothing to the rejected one.
        edges = await twin.graph.get_edges(
            shelf.assembly[2].id, direction="outgoing", edge_type=EdgeType.SUPERSEDES
        )
        assert [e.target_id for e in edges] == [shelf.assembly[1].id]
        rejected_out = await twin.graph.get_edges(
            shelf.assembly[3].id, direction="outgoing", edge_type=EdgeType.SUPERSEDES
        )
        assert rejected_out == []

    async def test_nothing_is_deleted_and_definitions_are_not_edited(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        before = {n.id: n.model_dump() for n in shelf.left + shelf.plank + shelf.constraint_sets}
        migration = _migration(twin)
        await migration.apply(await migration.plan(PROJECT))
        for node_id, dump in before.items():
            node = await twin.graph.get_node(node_id)
            assert node is not None
            assert node.model_dump() == dump
        for node in shelf.summaries + shelf.decisions + list(shelf.sims.values()):
            assert await twin.graph.get_node(node.id) is not None

    async def test_records_are_flagged_and_pinned_the_forge_527_way(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        migration = _migration(twin)
        await migration.apply(await migration.plan(PROJECT))
        summary = await twin.graph.get_node(shelf.summaries[0].id)
        assert summary.metadata["run_summary"] is True
        real = await twin.graph.get_node(shelf.decisions[1].id)
        assert "run_summary" not in real.metadata
        stale = await twin.graph.get_node(shelf.sims["left-1"].id)
        assert record_staleness(stale.metadata) == "stale"
        assert record_pins(stale.metadata)[0]["node_id"] == str(shelf.left[0].id)
        current = await twin.graph.get_node(shelf.sims["left-3"].id)
        assert record_staleness(current.metadata) == "current"
        edges = await twin.graph.get_edges(
            shelf.sims["plank-1"].id, direction="outgoing", edge_type=EdgeType.DEPENDS_ON
        )
        assert [(e.target_id, e.metadata["kind"]) for e in edges] == [
            (shelf.plank[0].id, PIN_EDGE_KIND)
        ]

    async def test_a_changed_twin_refuses_a_stale_plan(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        migration = _migration(twin)
        plan = await migration.plan(PROJECT)
        await _wp(
            twin, "Left PETG Gusset Bracket", WorkProductType.CAD_MODEL, T0 + timedelta(days=9)
        )
        with pytest.raises(StalePlanError):
            await migration.apply(plan)
        assert await list_items(twin, project_id=PROJECT, include_unheaded=True) == []

    async def test_an_altered_plan_is_refused(self, twin: InMemoryTwinAPI, shelf: Shelf) -> None:
        migration = _migration(twin)
        plan = await migration.plan(PROJECT)
        plan.items[0].revisions[0].status = "rejected"
        with pytest.raises(PlanTamperedError):
            await migration.apply(plan)

    async def test_a_later_legacy_node_extends_the_existing_item(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        migration = _migration(twin)
        await migration.apply(await migration.plan(PROJECT))
        late = await _wp(
            twin,
            "Left PETG Gusset Bracket - 228 x 120 x 9 mm",
            WorkProductType.CAD_MODEL,
            T0 + timedelta(days=9),
            metadata={"bbox_mm": [228, 120, 9]},
        )
        plan = await migration.plan(PROJECT)
        assert len(plan.items) == 1
        entry = plan.items[0]
        assert entry.existing_item
        assert [r.revision for r in entry.new_revisions] == [5]
        assert entry.head_node_id == str(late.id)
        # The left FEA on @3 is now behind the new head.
        pins = {r.node_id: r for r in plan.records}
        assert pins[str(shelf.sims["left-3"].id)].staleness == "stale"
        await migration.apply(plan)
        left = next(
            i for i in await list_items(twin, project_id=PROJECT) if i.head_node_id == late.id
        )
        assert left.head_revision == 5
        assert (await migration.plan(PROJECT)).empty


class TestEdgeCases:
    async def test_a_partial_name_match_is_flagged_for_review(self, twin: InMemoryTwinAPI) -> None:
        await _wp(twin, "Left PETG Gusset Bracket", WorkProductType.CAD_MODEL, T0)
        await _wp(twin, "Left PETG Gusset Support", WorkProductType.CAD_MODEL, T0 + timedelta(1))
        plan = await ItemMigration(twin).plan(PROJECT)
        assert len(plan.items) == 1
        assert plan.items[0].confidence == "low"
        assert plan.low_confidence[0]["key"] == plan.items[0].key

    async def test_dissimilar_boxes_block_a_partial_name_match(self, twin: InMemoryTwinAPI) -> None:
        await _wp(
            twin,
            "Left PETG Gusset Bracket",
            WorkProductType.CAD_MODEL,
            T0,
            metadata={"bbox_mm": [220, 120, 12]},
        )
        await _wp(
            twin,
            "Left PETG Gusset Support",
            WorkProductType.CAD_MODEL,
            T0 + timedelta(1),
            metadata={"bbox_mm": [40, 40, 600]},
        )
        plan = await ItemMigration(twin).plan(PROJECT)
        assert len(plan.items) == 2

    async def test_a_node_of_an_open_run_is_left_for_its_gate(self, twin: InMemoryTwinAPI) -> None:
        node = await _wp(
            twin, "Bracket", WorkProductType.CAD_MODEL, T0, metadata={"run_id": "run-open"}
        )

        async def lookup(run_id: str) -> RunInfo:
            return RunInfo(run_id=run_id, outcome="open")

        plan = await ItemMigration(twin, run_lookup=lookup).plan(PROJECT)
        assert plan.items == []
        assert plan.skipped[0]["node_id"] == str(node.id)

    async def test_flow_slot_keys_group_and_name_the_item(self, twin: InMemoryTwinAPI) -> None:
        first = await _wp(
            twin,
            "Wall anchor",
            WorkProductType.CAD_MODEL,
            T0,
            metadata={"run_id": "run-a", "phase": "mechanical"},
        )
        second = await _wp(
            twin,
            "Mounting cleat",
            WorkProductType.CAD_MODEL,
            T0 + timedelta(1),
            metadata={"run_id": "run-b", "phase": "mechanical"},
        )
        slot = {"item_type": "cad_model", "name": "Wall cleat", "item_key": "CAD-WALL-CLEAT"}

        async def lookup(run_id: str) -> RunInfo:
            return RunInfo(run_id=run_id, outcome="approved", slots={"mechanical": [slot]})

        plan = await ItemMigration(twin, run_lookup=lookup).plan(PROJECT)
        assert len(plan.items) == 1
        entry = plan.items[0]
        assert entry.key == "CAD-WALL-CLEAT"
        assert entry.rule == "flow_slot"
        assert [r.node_id for r in entry.revisions] == [str(first.id), str(second.id)]
        assert "slot CAD-WALL-CLEAT" in entry.revisions[1].evidence

    async def test_nodes_known_only_by_project_membership_are_included(
        self, twin: InMemoryTwinAPI
    ) -> None:
        node = await _wp(twin, "Bracket", WorkProductType.CAD_MODEL, T0, project=None)

        async def members(pid: UUID) -> list[UUID]:
            return [node.id]

        plan = await ItemMigration(twin, member_ids=members).plan(PROJECT)
        assert [r.node_id for r in plan.items[0].revisions] == [str(node.id)]
        await ItemMigration(twin, member_ids=members).apply(plan)
        assert (await ItemMigration(twin, member_ids=members).plan(PROJECT)).empty

    async def test_an_empty_project_plans_nothing(self, twin: InMemoryTwinAPI) -> None:
        plan = await ItemMigration(twin).plan(PROJECT)
        assert plan.empty
        assert "Nothing to migrate" in render_report(plan)
        result = await ItemMigration(twin).apply(plan)
        assert result.items_created == 0


class TestRoutes:
    @pytest.fixture
    def client(self, twin: InMemoryTwinAPI, monkeypatch: pytest.MonkeyPatch) -> Any:
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from api_gateway.twin import item_migration_routes, routes

        async def lookup(run_id: str) -> RunInfo | None:
            return await run_lookup(run_id)

        monkeypatch.setattr(item_migration_routes, "run_info", lookup)
        previous = routes.get_twin()
        routes.init_twin(twin)
        app = FastAPI()
        app.include_router(item_migration_routes.router)
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        routes.init_twin(previous)

    async def test_plan_then_apply_with_approval(self, client: Any, shelf: Shelf) -> None:
        base = f"/v1/twin/projects/{PROJECT}/item-migration"
        resp = await client.post(f"{base}/plan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["empty"] is False
        assert len(body["plan"]["items"]) == 5
        assert "plan_hash" in body["report"]

        refused = await client.post(f"{base}/apply", json={"plan": body["plan"]})
        assert refused.status_code == 403

        applied = await client.post(
            f"{base}/apply",
            json={"plan": body["plan"], "approve": True, "reason": "reviewed the dry run"},
        )
        assert applied.status_code == 200, applied.text
        out = applied.json()
        assert out["result"]["items_created"] == 5
        assert out["approved_by"] == "local:dashboard"
        assert out["approver_verified"] is False

        # The same plan again: the twin has changed since it was made.
        again = await client.post(
            f"{base}/apply", json={"plan": body["plan"], "approve": True, "reason": "again"}
        )
        assert again.status_code == 409
        assert (await client.post(f"{base}/plan")).json()["empty"] is True

    async def test_a_plan_for_another_project_is_refused(self, client: Any, shelf: Shelf) -> None:
        base = f"/v1/twin/projects/{PROJECT}/item-migration"
        plan = (await client.post(f"{base}/plan")).json()["plan"]
        other = f"/v1/twin/projects/{uuid4()}/item-migration/apply"
        resp = await client.post(other, json={"plan": plan, "approve": True})
        assert resp.status_code == 400

    async def test_run_info_reads_the_run_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        from api_gateway.runs import routes as run_routes
        from api_gateway.twin import item_migration_routes

        runs = {
            "ok": SimpleNamespace(status="completed", request={}, approval_reason=None, error=None),
            "bad": SimpleNamespace(
                status="rejected", request={}, approval_reason="too heavy", error=None
            ),
            "live": SimpleNamespace(
                status="awaiting_approval", request={}, approval_reason=None, error=None
            ),
        }

        class Store:
            def get(self, run_id: str) -> Any:
                if run_id not in runs:
                    raise KeyError(run_id)
                return runs[run_id]

        monkeypatch.setattr(run_routes, "get_run_store", lambda: Store())
        ok = await item_migration_routes.run_info("ok")
        assert ok is not None and ok.outcome == "approved"
        assert ok.slots  # the default flow's phases
        bad = await item_migration_routes.run_info("bad")
        assert bad is not None and (bad.outcome, bad.reason) == ("rejected", "too heavy")
        live = await item_migration_routes.run_info("live")
        assert live is not None and live.outcome == "open"
        assert await item_migration_routes.run_info("missing") is None


class TestCurrentView:
    async def test_a_migrated_project_renders_cleanly(
        self, twin: InMemoryTwinAPI, shelf: Shelf
    ) -> None:
        from twin_core.items.current import build_current_view

        migration = _migration(twin)
        await migration.apply(await migration.plan(PROJECT))
        linked = [
            {"id": str(n.id), "name": n.name, "type": n.type.value, "status": "valid"}
            for n in await twin.list_work_products(project_id=PROJECT)
        ]
        view = await build_current_view(twin, PROJECT, project_work_products=linked)
        assert view.counts["items"] == 5
        assert view.counts["by_type"] == {"assembly": 1, "cad_model": 3, "constraint_set": 1}
        # No legacy duplicate is left over as an unclassified "other" row.
        assert view.other == []
        # Phase summaries are run summaries, not decisions.
        assert view.counts["decisions"] == 2
        assert view.counts["simulation_results"] == 8
        assert view.counts["out_of_date_results"] == 5
        refs = {r["ref"] for r in view.items}
        assembly = next(r for r in view.items if r["item_type"] == "assembly")
        assert assembly["node_id"] == str(shelf.assembly[2].id)
        assert assembly["revision_count"] == 4
        assert all(ref and "@" in ref for ref in refs)
