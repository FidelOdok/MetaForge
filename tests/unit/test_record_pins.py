"""Records pinned to revisions, stale on a new revision (FORGE-527).

Component-level: the real record and definition recorders, the item service
and the change-set commit over ``InMemoryTwinAPI`` (MinIO patched), plus the
analysis gate, G8's stale-evidence check, the node read model and the phase
summary fallback.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from uuid import UUID

import pytest

from api_gateway.runs.analysis_constraints import SimResult, check_analysis_constraints
from api_gateway.twin.constraint_recorder import make_constraint_recorder
from api_gateway.twin.decision_recorder import make_decision_recorder
from api_gateway.twin.document_recorder import make_document_recorder
from api_gateway.twin.evidence_recorder import make_evidence_recorder
from api_gateway.twin.geometry_recorder import make_geometry_recorder
from mcp_core.context import McpCallContext, with_context
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.gates import evaluate_g8_release
from twin_core.consistency.record_pins import PIN_EDGE_KIND, describe_staleness
from twin_core.items import ItemError, close_change_set, commit_change_set
from twin_core.models.enums import EdgeType

PROJECT = "52752752-7527-4527-8527-527527527527"


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


@pytest.fixture(autouse=True)
def _no_knowledge_events(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.twin.decision_recorder as decisions

    async def _publish(**_: Any) -> bool:
        return False

    monkeypatch.setattr(decisions, "publish_work_product_created", _publish)


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


def _in_run(run_id: str, phase: str = "design") -> McpCallContext:
    return McpCallContext(
        actor_id="service:design-flow", run_id=run_id, phase=phase, project_id=UUID(PROJECT)
    )


async def _cad(twin, body: str, name: str = "Bracket", run_id: str | None = None) -> dict:
    record = make_geometry_recorder(twin, None)
    if run_id is None:
        return await record(step_base64=_step(body), name=name, project_id=PROJECT)
    with with_context(_in_run(run_id)):
        return await record(step_base64=_step(body), name=name, project_id=PROJECT)


async def _sim(
    twin,
    cad_node: str,
    *,
    name: str = "Bracket FEA",
    run_id: str | None = None,
    stress: float = 120.0,
    depends_on: list[str] | None = None,
) -> dict:
    record = make_document_recorder(twin, None)
    meta = {"max_von_mises_mpa": stress, "analysis_type": "static"}
    kwargs: dict[str, Any] = {
        "content": json.dumps(meta),
        "name": name,
        "wp_type": "simulation_result",
        "domain": "mechanical",
        "fmt": "json",
        "link_type": "simulation_result",
        "source_tool": "twin.record_document",
        "project_id": PROJECT,
        "extra_metadata": meta,
        "source_part_node_ids": [cad_node],
        "source_edge_type": "derives_from",
    }
    if depends_on is not None:
        kwargs["depends_on"] = depends_on
    if run_id is None:
        return await record(**kwargs)
    with with_context(_in_run(run_id, phase="analysis")):
        return await record(**kwargs)


async def _meta(twin, node_id: str) -> dict:
    node = await twin.graph.get_node(UUID(node_id))
    return dict(node.metadata)


async def _status(twin, node_id: str) -> str:
    return (await _meta(twin, node_id))["staleness"]


class TestPinnedAtRecordTime:
    async def test_a_simulation_is_pinned_to_the_revision_it_analysed(self, twin) -> None:
        cad = await _cad(twin, "a")
        sim = await _sim(twin, cad["node_id"])
        assert sim["depends_on"] == ["CAD-BRACKET@1"]
        assert sim["staleness"] == "current"
        meta = await _meta(twin, sim["node_id"])
        assert meta["depends_on_items"][0]["item_ref"] == "CAD-BRACKET@1"
        edges = await twin.graph.get_edges(
            UUID(sim["node_id"]), direction="outgoing", edge_type=EdgeType.DEPENDS_ON
        )
        pins = [e for e in edges if e.metadata.get("kind") == PIN_EDGE_KIND]
        assert [(e.target_id, e.metadata["item_ref"]) for e in pins] == [
            (UUID(cad["node_id"]), "CAD-BRACKET@1")
        ]

    async def test_the_analysed_geometry_is_the_pin(self, twin) -> None:
        """FORGE-532's analysed_geometry_node_id is the simulation's primary pin."""
        cad = await _cad(twin, "a")
        record = make_document_recorder(twin, None)
        sim = await record(
            content="{}",
            name="Bracket FEA",
            wp_type="simulation_result",
            domain="mechanical",
            fmt="json",
            link_type="simulation_result",
            source_tool="twin.record_document",
            project_id=PROJECT,
            analysis={"geometry_node_id": cad["node_id"]},
        )
        assert sim["depends_on"] == ["CAD-BRACKET@1"]
        meta = await _meta(twin, sim["node_id"])
        assert meta["analysed_geometry"]["item_ref"] == "CAD-BRACKET@1"
        await _cad(twin, "b")
        assert await _status(twin, sim["node_id"]) == "stale"

    async def test_the_project_constraint_set_is_pinned_too(self, twin) -> None:
        constraints = make_constraint_recorder(twin, None)
        await constraints(
            title="Bracket reqs",
            constraints=[{"name": "stress", "expression": "True"}],
            project_id=PROJECT,
        )
        cad = await _cad(twin, "a")
        sim = await _sim(twin, cad["node_id"])
        assert sorted(sim["depends_on"]) == ["CAD-BRACKET@1", "CS-BRACKET-REQS@1"]

    async def test_explicit_item_refs_are_accepted(self, twin) -> None:
        await _cad(twin, "a")
        leg = await _cad(twin, "l", name="Leg")
        sim = await _sim(twin, leg["node_id"], depends_on=["CAD-BRACKET@1"])
        assert sorted(sim["depends_on"]) == ["CAD-BRACKET@1", "CAD-LEG@1"]

    async def test_a_bad_explicit_ref_writes_nothing(self, twin) -> None:
        cad = await _cad(twin, "a")
        before = len(await twin.list_work_products())
        with pytest.raises(ItemError):
            await _sim(twin, cad["node_id"], depends_on=["CAD-NOPE@1"])
        assert len(await twin.list_work_products()) == before

    async def test_a_decision_is_pinned_to_the_revisions_it_cites(self, twin) -> None:
        cad = await _cad(twin, "a")
        record = make_decision_recorder(twin, None)
        out = await record(
            title="Use 6061-T6",
            rationale="stiffness",
            parent_refs=[cad["node_id"]],
            project_id=PROJECT,
        )
        assert out["depends_on"] == ["CAD-BRACKET@1"]
        await _cad(twin, "b")
        assert await _status(twin, out["node_id"]) == "stale"

    async def test_evidence_is_pinned_through_valid_against(self, twin) -> None:
        cad = await _cad(twin, "a")
        record = make_evidence_recorder(twin, None)
        out = await record(
            evidence_type="simulation",
            producer={"tool": "calculix.run_fea"},
            inputs={},
            result={"max_von_mises_mpa": 120},
            valid_against=[{"ref": cad["node_id"], "entity_kind": "work_product"}],
            project_id=PROJECT,
        )
        assert out["depends_on"] == ["CAD-BRACKET@1"]
        await _cad(twin, "b")
        assert await _status(twin, out["node_id"]) == "stale"


class TestStaleOnNewRevision:
    async def test_a_new_head_marks_the_old_simulation_stale_never_deleted(self, twin) -> None:
        cad1 = await _cad(twin, "a")
        sim = await _sim(twin, cad1["node_id"])
        await _cad(twin, "b")
        meta = await _meta(twin, sim["node_id"])
        assert meta["staleness"] == "stale"
        assert meta["staleness_reason"] == "it was for CAD-BRACKET@1, and CAD-BRACKET is now @2"
        assert meta["stale_for"] == {"CAD-BRACKET": {"pinned": 1, "current": 2}}
        assert await twin.get_work_product(UUID(sim["node_id"])) is not None

    async def test_only_downstream_records_go_stale(self, twin) -> None:
        bracket = await _cad(twin, "a")
        leg = await _cad(twin, "l", name="Leg")
        sim_bracket = await _sim(twin, bracket["node_id"])
        sim_leg = await _sim(twin, leg["node_id"], name="Leg FEA")
        await _cad(twin, "b")
        assert await _status(twin, sim_bracket["node_id"]) == "stale"
        assert await _status(twin, sim_leg["node_id"]) == "current"

    async def test_a_draft_stales_nothing_until_its_gate_approves(self, twin) -> None:
        cad1 = await _cad(twin, "a")
        sim = await _sim(twin, cad1["node_id"])
        await _cad(twin, "b", run_id="run-1")
        assert await _status(twin, sim["node_id"]) == "current"

        await commit_change_set(twin, "run-1", project_id=PROJECT, gate="design")
        meta = await _meta(twin, sim["node_id"])
        assert meta["staleness"] == "stale"
        assert "CAD-BRACKET is now @2" in meta["staleness_reason"]

    async def test_a_rejected_draft_stales_nothing(self, twin) -> None:
        cad1 = await _cad(twin, "a")
        sim = await _sim(twin, cad1["node_id"])
        await _cad(twin, "b", run_id="run-1")
        await close_change_set(twin, "run-1", status="rejected", project_id=PROJECT)
        assert await _status(twin, sim["node_id"]) == "current"


class TestReRun:
    async def test_a_rerun_on_the_new_revision_is_current_and_the_old_superseded(
        self, twin
    ) -> None:
        cad1 = await _cad(twin, "a")
        old = await _sim(twin, cad1["node_id"])
        cad2 = await _cad(twin, "b")
        new = await _sim(twin, cad2["node_id"])
        assert new["depends_on"] == ["CAD-BRACKET@2"]
        assert new["superseded_records"] == [old["node_id"]]
        assert await _status(twin, new["node_id"]) == "current"
        old_meta = await _meta(twin, old["node_id"])
        assert old_meta["staleness"] == "superseded"
        assert old_meta["superseded_by"] == new["node_id"]
        edges = await twin.graph.get_edges(
            UUID(new["node_id"]), direction="outgoing", edge_type=EdgeType.SUPERSEDES
        )
        assert [e.target_id for e in edges] == [UUID(old["node_id"])]

    async def test_a_different_analysis_is_not_superseded(self, twin) -> None:
        cad1 = await _cad(twin, "a")
        record = make_document_recorder(twin, None)
        thermal = await record(
            content="{}",
            name="Bracket thermal",
            wp_type="simulation_result",
            domain="mechanical",
            fmt="json",
            link_type="simulation_result",
            source_tool="twin.record_document",
            project_id=PROJECT,
            extra_metadata={"analysis_type": "thermal"},
            source_part_node_ids=[cad1["node_id"]],
        )
        cad2 = await _cad(twin, "b")
        await _sim(twin, cad2["node_id"])
        assert await _status(twin, thermal["node_id"]) == "stale"

    async def test_a_rerun_inside_a_run_supersedes_only_when_approved(self, twin) -> None:
        cad1 = await _cad(twin, "a")
        old = await _sim(twin, cad1["node_id"])
        cad2 = await _cad(twin, "b", run_id="run-1")
        new = await _sim(twin, cad2["node_id"], run_id="run-1")
        assert new["depends_on"] == ["CAD-BRACKET@2"]
        assert new["superseded_records"] == []
        assert await _status(twin, old["node_id"]) == "current"

        await commit_change_set(twin, "run-1", project_id=PROJECT, gate="design")
        assert await _status(twin, new["node_id"]) == "current"
        assert await _status(twin, old["node_id"]) == "superseded"

    async def test_a_record_on_a_rejected_draft_is_invalid(self, twin) -> None:
        await _cad(twin, "a")
        cad2 = await _cad(twin, "b", run_id="run-1")
        sim = await _sim(twin, cad2["node_id"], run_id="run-1")
        await close_change_set(twin, "run-1", status="rejected", project_id=PROJECT)
        meta = await _meta(twin, sim["node_id"])
        assert meta["staleness"] == "invalid"
        assert "CAD-BRACKET@2" in meta["staleness_reason"]


class TestGates:
    def test_stale_evidence_does_not_satisfy_an_analysis_constraint(self) -> None:
        class _C:
            name = "max stress"
            metric = "stress"
            unit = "MPa"
            operator = "<="
            limit = 200.0
            severity = "error"
            metadata: dict = {}

        stale_meta = {
            "max_von_mises_mpa": 120.0,
            "staleness": "stale",
            "staleness_reason": "it was for CAD-BRACKET@1, and CAD-BRACKET is now @2",
            "depends_on_items": [{"item_key": "CAD-BRACKET", "revision": 1}],
        }
        sim = SimResult(
            id="s1", name="Bracket FEA", updated_at=1.0, metadata=stale_meta, cad_ids={"old"}
        )
        out = check_analysis_constraints(
            [_C()], [("new", "Bracket")], [sim], model_keys={"new": "CAD-BRACKET"}
        )
        assert out.satisfied == []
        assert out.violations == [
            "max stress: 'Bracket FEA' is stale: it was for CAD-BRACKET@1, and "
            "CAD-BRACKET is now @2; re-run it on the current revision"
        ]

    def test_a_current_rerun_is_preferred_over_the_stale_one(self) -> None:
        class _C:
            name = "max stress"
            metric = "stress"
            unit = "MPa"
            operator = "<="
            limit = 200.0
            severity = "error"
            metadata: dict = {}

        stale = SimResult(
            id="s1",
            name="old",
            updated_at=9.0,
            metadata={"max_von_mises_mpa": 120.0, "staleness": "stale"},
            cad_ids={"new"},
        )
        fresh = SimResult(
            id="s2",
            name="new",
            updated_at=2.0,
            metadata={"max_von_mises_mpa": 150.0, "staleness": "current"},
            cad_ids={"new"},
        )
        out = check_analysis_constraints([_C()], [("new", "Bracket")], [stale, fresh])
        assert out.violations == []
        assert len(out.satisfied) == 1 and "'new'" in out.satisfied[0]

    async def test_g8_names_the_stale_record_and_its_revision(self, twin) -> None:
        cad1 = await _cad(twin, "a")
        await _sim(twin, cad1["node_id"])
        await _cad(twin, "b")
        evaluation = await evaluate_g8_release(twin, UUID(PROJECT))
        check = next(c for c in evaluation.checks if c.id == "stale_evidence_resolved")
        assert check.status.value == "fail"
        assert "'Bracket FEA' is stale: it was for CAD-BRACKET@1" in check.detail

    def test_describe_staleness_is_none_for_current_records(self) -> None:
        assert describe_staleness("x", {"staleness": "current"}) is None
        assert describe_staleness("x", {}) is None
        assert describe_staleness("x", {"staleness": "revalidated"}) is None


class TestReadModel:
    async def test_the_node_response_carries_depends_on_and_staleness(self, twin) -> None:
        from api_gateway.twin.routes import _wp_to_response

        cad1 = await _cad(twin, "a")
        sim = await _sim(twin, cad1["node_id"])
        await _cad(twin, "b")
        resp = _wp_to_response(await twin.get_work_product(UUID(sim["node_id"])))
        assert resp.dependsOn is not None
        assert [p.itemRef for p in resp.dependsOn] == ["CAD-BRACKET@1"]
        assert resp.staleness is not None and resp.staleness.status == "stale"
        assert resp.staleness.staleFor == {"CAD-BRACKET": {"pinned": 1, "current": 2}}

    async def test_a_definition_response_is_unchanged(self, twin) -> None:
        from api_gateway.twin.routes import _wp_to_response

        cad1 = await _cad(twin, "a")
        resp = _wp_to_response(await twin.get_work_product(UUID(cad1["node_id"])))
        assert resp.dependsOn is None and resp.staleness is None


class TestPhaseSummary:
    async def test_the_phase_summary_is_not_recorded_as_a_decision(self) -> None:
        from api_gateway.runs.flow_brain import ReActPhaseBrain
        from orchestrator.design_flow.executor import FlowContext
        from orchestrator.design_flow.spec import get_flow

        calls: list[tuple[str, dict]] = []

        class _Bridge:
            async def invoke(self, tool: str, args: dict) -> dict:
                calls.append((tool, args))
                return {"status": "ok", "data": {}}

        brain = ReActPhaseBrain(mcp_bridge=_Bridge())
        phase = next(p for p in get_flow("hardware_v1").phases if p.id == "electronics")
        assert "design_decision" in phase.required_deliverables
        ctx = FlowContext(goal="a breakout board", project_id="p1", completed=[])
        await brain._backstop_decision(phase, ctx, "Designed 3.3V LDO + I2C IMU topology.")
        assert calls == []

    async def test_the_summary_stays_on_the_run_record(self, monkeypatch) -> None:
        from api_gateway.runs import flow_brain
        from api_gateway.runs.flow_brain import ReActPhaseBrain
        from orchestrator.design_flow.executor import FlowContext
        from orchestrator.design_flow.spec import get_flow

        async def fake_turn(prompt: str, **kwargs: Any) -> str:
            return "Chose the LDO."

        monkeypatch.setattr(flow_brain, "run_chat_turn", fake_turn)
        phase = next(p for p in get_flow("hardware_v1").phases if p.id == "electronics")
        ctx = FlowContext(goal="g", project_id="p1", completed=[])
        outcome = await ReActPhaseBrain(mcp_bridge=None).run_phase(
            goal="g", phase=phase, context=ctx
        )
        assert outcome.summary == "Chose the LDO."
