"""Agent context read from the item baseline (FORGE-530).

The shelf project as it looked live (FORGE-521): parts re-committed by
several runs, a constraint set recorded eight times, and a pile of
phase-summary decisions. Component-level: the real recorders over
``InMemoryTwinAPI``, only the MinIO blob store patched.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from api_gateway.projects.baseline_brief import read_baseline
from api_gateway.projects.brief import BRIEF_CHAR_LIMIT, build_project_brief
from api_gateway.twin.constraint_recorder import make_constraint_recorder
from api_gateway.twin.geometry_recorder import make_geometry_recorder
from api_gateway.twin.item_revisions import make_item_history_reader
from digital_twin.context.models import ContextFragment, ContextSourceKind
from digital_twin.context.staleness import (
    annotate_revision_state,
    compute_staleness,
    revision_staleness,
)
from orchestrator.design_flow.rework import build_rework_feedback
from orchestrator.design_flow.rework_context import (
    RevisionNote,
    cad_delta,
    collect_revision_notes,
    requirement_delta,
    revision_note_lines,
    rework_context_lines,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.items import find_item, list_items
from twin_core.items.change_sets import close_change_set
from twin_core.items.facts import format_bbox
from twin_core.items.state import revision_reason, revision_run, revision_status
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct

PROJECT = "66666666-6666-6666-6666-666666666666"
DAY = 24 * 3600.0


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


async def _excerpt(_: str) -> str | None:
    return None


async def _part(
    twin: InMemoryTwinAPI,
    name: str,
    rev: int,
    *,
    thickness: float,
    extra: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    record = make_geometry_recorder(twin, None)
    return await record(
        step_base64=_step(f"{name}-{rev}-{thickness}"),
        name=name,
        project_id=PROJECT,
        extra_metadata={"material": "PLA", **(extra or {})},
        properties={
            "volume_mm3": 1000.0 * thickness,
            "bounding_box": [120, 40, thickness],
            "mass_kg": 0.00124 * thickness,
        },
        run_id=run_id,
    )


async def _reject(twin: InMemoryTwinAPI, run_id: str, reason: str) -> None:
    """Close ``run_id``'s drafts as a gate rejection, the FORGE-525 way."""
    await close_change_set(twin, run_id, status="rejected", reason=reason, project_id=PROJECT)


async def _decision(twin: InMemoryTwinAPI, title: str, rationale: str, age_s: int) -> WorkProduct:
    now = datetime.now(UTC) - timedelta(seconds=age_s)
    wp = WorkProduct(
        id=uuid4(),
        name=title,
        type=WorkProductType.DESIGN_DECISION,
        domain="systems",
        file_path="",
        content_hash="x",
        format="md",
        metadata={"rationale": rationale},
        created_at=now,
        updated_at=now,
        created_by="twin.record_decision",
        project_id=UUID(PROJECT),
    )
    return await twin.create_work_product(wp)


async def _simulation(
    twin: InMemoryTwinAPI, of_node: str, *, pin: bool = True, **meta: Any
) -> UUID:
    """A simulation_result of ``of_node``; ``pin`` adds FORGE-532's analysed_geometry."""
    now = datetime.now(UTC)
    if pin:
        meta = {
            **meta,
            "analysed_geometry": {"node_id": of_node, "revision": None, "content_hash": "h"},
            "analysed_geometry_node_id": of_node,
        }
    wp = await twin.create_work_product(
        WorkProduct(
            id=uuid4(),
            name="FEA result",
            type=WorkProductType.SIMULATION_RESULT,
            domain="mechanical",
            file_path="",
            content_hash=uuid4().hex,
            format="json",
            metadata=meta,
            created_at=now,
            updated_at=now,
            created_by="twin.record_document",
            project_id=UUID(PROJECT),
        )
    )
    await twin.add_edge(wp.id, UUID(of_node), EdgeType.DERIVES_FROM)
    return wp.id


async def _project(twin: InMemoryTwinAPI) -> SimpleNamespace:
    wps = await twin.list_work_products(project_id=UUID(PROJECT))
    rows = [
        SimpleNamespace(
            id=str(wp.id),
            name=wp.name,
            type=getattr(wp.type, "value", wp.type),
            status="draft",
            updated_at=wp.updated_at,
        )
        for wp in wps
    ]
    return SimpleNamespace(
        id=PROJECT, name="Shelf", status="active", description="A wall shelf", work_products=rows
    )


async def _shelf(twin: InMemoryTwinAPI) -> dict[str, Any]:
    """3 parts with 3-4 revisions, 8 constraint-set revisions, 14 phase summaries."""
    heads: dict[str, dict[str, Any]] = {}
    for name, revs in (("Shelf Board", 4), ("Wall Bracket", 3), ("Back Rail", 3)):
        for rev in range(1, revs + 1):
            heads[name] = await _part(twin, name, rev, thickness=6.0 + rev)
            if name == "Wall Bracket" and rev == 2:
                await _simulation(twin, heads[name]["node_id"], safety_factor=2.1)
    await _simulation(twin, heads["Shelf Board"]["node_id"], pin=False, safety_factor=3.0)

    record_cs = make_constraint_recorder(twin, None)
    for rev in range(1, 9):
        await record_cs(
            title="Shelf requirements",
            constraints=[
                {
                    "name": "max load",
                    "expression": "True",
                    "metric": "max_load_kg",
                    "operator": ">=",
                    "limit": 10 + rev,
                    "unit": "kg",
                },
                {
                    "name": "depth",
                    "expression": "True",
                    "metric": "depth_mm",
                    "operator": "<=",
                    "limit": 250,
                    "unit": "mm",
                },
            ],
            project_id=PROJECT,
        )
    for i in range(14):
        await _decision(twin, f"Phase {i} \u2014 phase summary", "summary text " * 30, age_s=i)
    await _decision(twin, "Use PLA for the brackets", "Cheap and stiff enough at 20 kg", 100)
    await _decision(twin, "Two brackets, not three", "Load spreads over 800 mm", 200)
    await _decision(twin, "Use PLA for the brackets", "older duplicate", 300)
    return heads


def _item_lines(brief: str) -> list[str]:
    start = brief.index("Current design baseline")
    block = brief[start:].split("\n\n", 1)[0]
    return [ln for ln in block.splitlines()[1:] if ln.startswith("- ")]


class TestShelfBrief:
    async def test_one_line_per_item_no_duplicates_under_cap(self, twin) -> None:
        await _shelf(twin)
        brief = await build_project_brief(
            await _project(twin), doc_excerpt=_excerpt, twin=twin, full=True
        )

        lines = _item_lines(brief)
        keys = [ln.split()[1].split("@")[0] for ln in lines]
        assert len(lines) == len(await list_items(twin, project_id=PROJECT)) == 4
        assert len(keys) == len(set(keys))
        assert any(ln.startswith("- CAD-SHELF-BOARD@4 cad_model") for ln in lines)
        assert any(ln.startswith("- CS-SHELF-REQUIREMENTS@8 constraint_set") for ln in lines)
        assert len(brief) < BRIEF_CHAR_LIMIT // 2
        # The old revisions and the phase summaries are not in it at all.
        assert "@3 cad_model 'Shelf Board'" not in brief
        assert "phase summary" not in brief

    async def test_key_facts_and_evidence_state(self, twin) -> None:
        await _shelf(twin)
        brief = await build_project_brief(await _project(twin), doc_excerpt=_excerpt, twin=twin)
        board = next(ln for ln in _item_lines(brief) if "SHELF-BOARD" in ln)
        assert "bbox 120x40x10 mm" in board
        assert "PLA" in board
        assert "volume 10000 mm3" in board
        assert "FEA @4 ok" in board
        bracket = next(ln for ln in _item_lines(brief) if "WALL-BRACKET" in ln)
        assert "FEA stale (@2, not re-run on @3)" in bracket
        reqs = next(ln for ln in _item_lines(brief) if "SHELF-REQUIREMENTS" in ln)
        assert "2 constraints" in reqs
        assert "max_load_kg >= 18 kg" in reqs

    async def test_the_analysed_geometry_pin_is_the_dependency(self, twin) -> None:
        """FORGE-532: a result pinned to @2 is not evidence for @3, whatever edges say."""
        await _part(twin, "Clip", 1, thickness=2)
        v2 = await _part(twin, "Clip", 2, thickness=3)
        v3 = await _part(twin, "Clip", 3, thickness=4)
        sim = await _simulation(twin, v2["node_id"], safety_factor=2.0)
        # A provenance edge to the current revision does not make it current evidence.
        await twin.add_edge(sim, UUID(v3["node_id"]), EdgeType.PARENT_OF)
        brief = await build_project_brief(await _project(twin), doc_excerpt=_excerpt, twin=twin)
        clip = next(ln for ln in _item_lines(brief) if "CAD-CLIP@3" in ln)
        assert "FEA stale (@2, not re-run on @3)" in clip
        # Pinned to the current revision, failing: reported as such.
        await _simulation(twin, v3["node_id"], safety_factor=0.8)
        brief = await build_project_brief(await _project(twin), doc_excerpt=_excerpt, twin=twin)
        clip = next(ln for ln in _item_lines(brief) if "CAD-CLIP@3" in ln)
        assert "FEA @3 fail" in clip

    async def test_records_are_real_decisions_deduplicated(self, twin) -> None:
        await _shelf(twin)
        brief = await build_project_brief(await _project(twin), doc_excerpt=_excerpt, twin=twin)
        section = brief[brief.index("Recent design decisions") :].split("\n\n", 1)[0]
        assert section.count("Use PLA for the brackets") == 1
        assert "Cheap and stiff enough" in section
        assert "Two brackets, not three" in section
        assert "older duplicate" not in section

    async def test_keeps_pointer_and_closing_directives_when_capped(
        self, twin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _shelf(twin)
        monkeypatch.setenv("METAFORGE_BRIEF_CHAR_LIMIT", "2500")
        brief = await build_project_brief(await _project(twin), doc_excerpt=_excerpt, twin=twin)
        assert len(brief) <= 2500
        assert "Read the full brief at" in brief
        assert f"metaforge://twin/brief/{PROJECT}" in brief
        assert f'project_id="{PROJECT}"' in brief

    async def test_current_requirement_doc_is_inlined_once(self, twin) -> None:
        await _shelf(twin)
        seen: list[str] = []

        async def excerpt(node_id: str) -> str | None:
            seen.append(node_id)
            return "max load 18 kg"

        await build_project_brief(await _project(twin), doc_excerpt=excerpt, twin=twin)
        item = await find_item(twin, "CS-SHELF-REQUIREMENTS", PROJECT)
        assert seen == [str(item.head_node_id)]


class TestDraftsAndLessons:
    async def test_run_drafts_only_for_that_run(self, twin) -> None:
        await _shelf(twin)
        await _part(
            twin,
            "Shelf Board",
            5,
            thickness=12,
            run_id="run-7",
        )
        project = await _project(twin)

        others = await build_project_brief(project, doc_excerpt=_excerpt, twin=twin)
        assert "CAD-SHELF-BOARD@4 cad_model" in others
        assert "@5" not in others

        mine = await build_project_brief(project, doc_excerpt=_excerpt, twin=twin, run_id="run-7")
        assert "Drafts written by this run (1)" in mine
        assert "- CAD-SHELF-BOARD@5 (draft, not yet approved) cad_model" in mine
        assert "CAD-SHELF-BOARD@4 cad_model" in mine

    async def test_run_id_comes_from_the_call_context(self, twin) -> None:
        from mcp_core.context import McpCallContext, with_context

        await _shelf(twin)
        await _part(twin, "Back Rail", 4, thickness=3, run_id="run-9")
        project = await _project(twin)
        with with_context(McpCallContext(actor_id="svc", run_id="run-9")):
            brief = await build_project_brief(project, doc_excerpt=_excerpt, twin=twin)
        assert "CAD-BACK-RAIL@4 (draft" in brief

    async def test_rejected_revision_is_a_lesson_not_the_baseline(self, twin) -> None:
        await _shelf(twin)
        await _part(
            twin,
            "Wall Bracket",
            4,
            thickness=2,
            run_id="run-r",
        )
        await _reject(twin, "run-r", "SF 0.8 < 1.5")
        brief = await build_project_brief(await _project(twin), doc_excerpt=_excerpt, twin=twin)
        assert "CAD-WALL-BRACKET@3 cad_model" in brief
        assert "already tried CAD-WALL-BRACKET@4, failed because: SF 0.8 < 1.5" in brief

        history = await make_item_history_reader(twin)(item_key="CAD-WALL-BRACKET")
        assert [r["status"] for r in history["revisions"]] == [
            "committed",
            "committed",
            "committed",
            "rejected",
        ]
        (lesson,) = history["lessons"]
        assert lesson["ref"] == "CAD-WALL-BRACKET@4"
        assert lesson["reason"] == "SF 0.8 < 1.5"

    async def test_abandoned_revision_is_neither_baseline_nor_lesson(self, twin) -> None:
        await _shelf(twin)
        await _part(twin, "Back Rail", 4, thickness=1, run_id="run-x")
        await close_change_set(twin, "run-x", status="abandoned", reason="run failed")
        brief = await build_project_brief(
            await _project(twin), doc_excerpt=_excerpt, twin=twin, run_id="run-x"
        )
        assert "CAD-BACK-RAIL@3 cad_model" in brief
        assert "CAD-BACK-RAIL@4" not in brief
        history = await make_item_history_reader(twin)(item_key="CAD-BACK-RAIL")
        assert history["lessons"] == []

    def test_status_uses_forge_525_fields(self) -> None:
        assert revision_status(None) == "approved"
        assert revision_status({}) == "approved"  # missing reads as committed
        assert revision_status({"status": "committed"}) == "approved"
        assert revision_status({"status": "approved"}) == "approved"
        assert revision_status({"status": "draft"}) == "draft"
        assert revision_status({"status": "rejected"}) == "rejected"
        assert revision_status({"status": "abandoned"}) == "abandoned"
        # Unknown is never shown as baseline.
        assert revision_status({"status": "weird"}) == "draft"
        assert revision_reason({"status_reason": " too heavy "}) == "too heavy"
        assert revision_run({"change_set": "run-1", "run_id": "other"}) == "run-1"


class TestLegacyBrief:
    async def test_project_without_items_is_unchanged(self, twin) -> None:
        wps = [
            SimpleNamespace(
                id=f"w{i}", name=f"Part {i}", type="cad_model", status="draft", updated_at=i
            )
            for i in range(5)
        ]
        project = SimpleNamespace(
            id=PROJECT, name="P", status="active", description="d", work_products=wps
        )
        legacy = await build_project_brief(project, doc_excerpt=_excerpt)
        with_twin = await build_project_brief(project, doc_excerpt=_excerpt, twin=twin)
        assert legacy == with_twin
        assert "Existing work products in this project (5, newest first)" in legacy
        assert "Current design baseline" not in legacy

    async def test_twin_without_item_support_is_legacy(self) -> None:
        project = SimpleNamespace(
            id=PROJECT, name="P", status="active", description="", work_products=[]
        )
        out = await build_project_brief(
            project, doc_excerpt=_excerpt, twin=SimpleNamespace(graph=None)
        )
        assert "This project has no work products yet." in out

    async def test_read_baseline_is_none_without_items(self, twin) -> None:
        assert await read_baseline(twin, PROJECT) is None

    def test_format_bbox_shapes(self) -> None:
        assert format_bbox([120, 40, 8]) == "120x40x8 mm"
        assert format_bbox([0, 0, 0, 10, 20, 5]) == "10x20x5 mm"
        assert format_bbox({"x": 1, "y": 2, "z": 3}) == "1x2x3 mm"
        assert format_bbox({"xmin": 0, "xmax": 4, "ymin": 1, "ymax": 3, "zmin": 0, "zmax": 1}) == (
            "4x2x1 mm"
        )
        assert format_bbox("nope") is None


class TestStalenessByRevision:
    def test_scores(self) -> None:
        old = {"created_at": 0}
        base = {"item_key": "CAD-X", "item_current_revision": 3}
        now = 400 * DAY
        assert compute_staleness({**old, **base, "item_revision": 3}, now) == 0.0
        assert compute_staleness({**base, "item_revision": 2, "created_at": now}, now) == 1.0
        rejected = {**base, "item_revision": 4, "revision_status": "rejected"}
        assert compute_staleness(rejected, now) == 1.0
        assert compute_staleness(rejected, now, include_lessons=True) == 0.0
        assert (
            compute_staleness({**base, "item_revision": 4, "revision_status": "abandoned"}) == 1.0
        )

    def test_draft_is_fresh_only_for_its_own_run(self) -> None:
        draft = {
            "item_key": "CAD-X",
            "item_revision": 4,
            "item_current_revision": 3,
            "revision_status": "draft",
            "revision_run": "run-a",
        }
        assert compute_staleness(draft, run_id="run-a") == 0.0
        assert compute_staleness(draft, run_id="run-b") == 1.0
        assert compute_staleness(draft) == 1.0
        # Not annotated (twin unreadable) but stamped with a change set: still a draft.
        stamped = {"item_key": "CAD-X", "item_revision": 4, "change_set": "run-a"}
        assert compute_staleness(stamped, run_id="run-a") == 0.0
        assert compute_staleness(stamped, run_id="run-b") == 1.0

    def test_without_item_info_age_decay_stays(self) -> None:
        assert revision_staleness({"created_at": 0}) is None
        assert compute_staleness({"created_at": 0}, 30 * DAY) == pytest.approx(0.5)
        # Item info but no known state: still age decay.
        md = {"item_key": "CAD-X", "item_revision": 1, "created_at": 0}
        assert compute_staleness(md, 30 * DAY) == pytest.approx(0.5)

    async def test_annotation_from_the_twin(self, twin) -> None:
        v1 = await _part(twin, "Hinge", 1, thickness=2)
        v2 = await _part(twin, "Hinge", 2, thickness=3)
        frags = []
        for out in (v1, v2):
            wp = await twin.get_work_product(UUID(out["node_id"]))
            frags.append(
                ContextFragment(
                    content="x",
                    source_kind=ContextSourceKind.GRAPH_NODE,
                    source_id=f"work_product://{wp.id}",
                    work_product_id=wp.id,
                    metadata=dict(wp.metadata),
                    token_count=1,
                )
            )
        assert await annotate_revision_state(frags, twin) == 2
        assert [compute_staleness(f.metadata) for f in frags] == [1.0, 0.0]

    async def test_other_runs_drafts_do_not_leak_into_assembly(self, twin) -> None:
        from digital_twin.context.assembler import ContextAssembler
        from digital_twin.context.models import ContextAssemblyRequest, ContextScope

        await _part(twin, "Hinge", 1, thickness=2)
        draft = await _part(twin, "Hinge", 2, thickness=3, run_id="run-a")
        assembler = ContextAssembler(twin=twin, knowledge_service=None)  # type: ignore[arg-type]

        async def kept(run_id: str | None) -> int:
            req = ContextAssemblyRequest(
                agent_id="mechanical",
                scope=[ContextScope.WORK_PRODUCT],
                work_product_id=UUID(draft["node_id"]),
                staleness_threshold=0.5,
                run_id=run_id,
            )
            return len((await assembler.assemble(req)).fragments)

        assert await kept("run-a") == 1
        assert await kept("run-b") == 0
        assert await kept(None) == 0


class TestReworkContext:
    async def test_lines_carry_ref_reason_and_cad_diff(self, twin) -> None:
        await _part(twin, "Wall Bracket", 1, thickness=8)
        bad = await _part(
            twin,
            "Wall Bracket",
            2,
            thickness=4,
            run_id="run-r",
        )
        await _reject(twin, "run-r", "SF 0.8 < 1.5")
        lines = await rework_context_lines(
            twin, phase_id="design", revisions=[bad["node_id"]], project_id=PROJECT
        )
        text = "\n".join(lines)
        assert "CAD-WALL-BRACKET@2 cad_model 'Wall Bracket' (rejected)" in text
        assert "Gate's reason: SF 0.8 < 1.5" in text
        assert "Changed from CAD-WALL-BRACKET@1:" in text
        assert "bbox 120x40x8 mm -> 120x40x4 mm" in text
        assert "volume 8000 -> 4000 mm3 (-4000, -50.0%)" in text

    async def test_geometry_diff_is_preferred_when_injected(self, twin) -> None:
        await _part(twin, "Rail", 1, thickness=8)
        await _part(twin, "Rail", 2, thickness=9)

        async def diff(*, work_product_id: str) -> dict[str, Any]:
            return {
                "previous_volume_mm3": 100.0,
                "current_volume_mm3": 150.0,
                "previous_bounding_box": [1, 1, 1],
                "current_bounding_box": [1, 1, 2],
            }

        notes = await collect_revision_notes(
            twin, ["CAD-RAIL@2"], reason="too stiff", project_id=PROJECT, geometry_diff=diff
        )
        (note,) = notes
        assert note.status == "failed"  # no recorded status, the gate failed it
        assert note.reason == "too stiff"
        assert any("measured from STEP" in c for c in note.changes)

    async def test_requirement_value_changes(self, twin) -> None:
        record = make_constraint_recorder(twin, None)
        for limit in (20, 25):
            await record(
                title="Reqs",
                constraints=[
                    {
                        "name": "load",
                        "expression": "True",
                        "metric": "load_kg",
                        "operator": ">=",
                        "limit": limit,
                        "unit": "kg",
                    }
                ],
                project_id=PROJECT,
            )
        lines = await rework_context_lines(
            twin, phase_id="requirements", revisions=["CS-REQS@2"], reason="r", project_id=PROJECT
        )
        assert "      load_kg >= 20 kg -> load_kg >= 25 kg" in lines

    async def test_unknown_refs_are_skipped(self, twin) -> None:
        assert (
            await rework_context_lines(twin, phase_id="p", revisions=[str(uuid4()), "NOPE@1"]) == []
        )

    def test_pure_helpers(self) -> None:
        assert revision_note_lines([]) == []
        assert requirement_delta({"a": "a <= 1"}, {"a": "a <= 2", "b": "b > 0"}) == [
            "a <= 1 -> a <= 2",
            "added b > 0",
        ]
        assert cad_delta({"material": "PLA"}, {"material": "PETG"}) == ["material PLA -> PETG"]

    def test_rework_feedback_includes_the_revision_block(self) -> None:
        note = RevisionNote(
            ref="CAD-WALL-BRACKET@4",
            item_type="cad_model",
            name="Wall Bracket",
            reason="SF 0.8 < 1.5",
            previous_ref="CAD-WALL-BRACKET@3",
            changes=("volume 8000 -> 4000 mm3 (-4000, -50.0%)",),
        )
        text = build_rework_feedback(
            from_phase="verify",
            to_phase="design",
            findings=["safety factor below 1.5"],
            reason="thicken it",
            from_summary="",
            cycle=1,
            revisions=[note],
        )
        assert "CAD-WALL-BRACKET@4 cad_model 'Wall Bracket' (rejected)" in text
        assert "Gate's reason: SF 0.8 < 1.5" in text
        assert text.endswith("Change the work so these problems are resolved before you reply.")
        # Without revisions the block is exactly what it was.
        plain = build_rework_feedback(
            from_phase="verify", to_phase="design", findings=[], reason="", from_summary="", cycle=1
        )
        assert "Revisions this gate turned down" not in plain
