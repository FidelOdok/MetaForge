"""Run change sets: drafts until the gate, approval moves heads (FORGE-525).

Component-level: the real recorders, item service and change-set operations
over ``InMemoryTwinAPI`` (MinIO patched), plus the gate decision path
(``routes.decide_run_gate``) for an in-process design-flow run.
"""

from __future__ import annotations

import asyncio
import base64
from uuid import UUID

import pytest

from api_gateway.twin.constraint_recorder import make_constraint_recorder
from api_gateway.twin.geometry_recorder import make_geometry_recorder
from api_gateway.twin.item_revisions import make_item_history_reader
from mcp_core.context import McpCallContext, with_context
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.items import (
    ChangeSetConflictError,
    UnknownItemError,
    close_change_set,
    commit_change_set,
    find_item,
    item_history,
    list_items,
    open_drafts,
    resolve_item_ref,
)
from twin_core.models.enums import EdgeType

PROJECT = "77777777-7777-7777-7777-777777777777"


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


def _in_run(run_id: str, phase: str = "design") -> McpCallContext:
    return McpCallContext(
        actor_id="service:design-flow", run_id=run_id, phase=phase, project_id=UUID(PROJECT)
    )


async def _commit(twin, body: str, name: str = "Leg", run_id: str | None = None) -> dict:
    record = make_geometry_recorder(twin, None)
    if run_id is None:
        return await record(step_base64=_step(body), name=name, project_id=PROJECT)
    with with_context(_in_run(run_id)):
        return await record(step_base64=_step(body), name=name, project_id=PROJECT)


async def _head_targets(twin, item) -> list[UUID]:
    edges = await twin.graph.get_edges(item.id, direction="outgoing", edge_type=EdgeType.HEAD)
    return [e.target_id for e in edges]


async def _supersedes(twin, node_id: str) -> list[UUID]:
    edges = await twin.graph.get_edges(
        UUID(node_id), direction="outgoing", edge_type=EdgeType.SUPERSEDES
    )
    return [e.target_id for e in edges]


class TestDraftWrites:
    async def test_a_draft_does_not_move_the_head(self, twin) -> None:
        v1 = await _commit(twin, "a")
        v2 = await _commit(twin, "b", run_id="run-1")
        assert (v2["revision"], v2["revision_status"]) == (2, "draft")
        item = await find_item(twin, "CAD-LEG", PROJECT)
        assert (item.head_revision, str(item.head_node_id)) == (1, v1["node_id"])
        assert await _head_targets(twin, item) == [UUID(v1["node_id"])]
        # No SUPERSEDES yet: for everyone else the head is still current.
        assert await _supersedes(twin, v2["node_id"]) == []
        assert item.drafts["run-1"]["base_revision"] == 1
        assert item.drafts["run-1"]["phase"] == "design"

    async def test_writes_with_no_run_still_move_the_head(self, twin) -> None:
        v1 = await _commit(twin, "a")
        v2 = await _commit(twin, "b")
        item = await find_item(twin, "CAD-LEG", PROJECT)
        assert item.head_revision == 2
        assert "revision_status" not in v2
        assert await _supersedes(twin, v2["node_id"]) == [UUID(v1["node_id"])]
        statuses = [r.status for r in await item_history(twin, item)]
        assert statuses == ["committed", "committed"]

    async def test_a_second_write_in_the_same_run_revises_its_own_draft(self, twin) -> None:
        await _commit(twin, "a")
        await _commit(twin, "b", run_id="run-1")
        v3 = await _commit(twin, "c", run_id="run-1")
        assert v3["revision"] == 3
        item = await find_item(twin, "CAD-LEG", PROJECT)
        assert item.head_revision == 1
        # The base stays the head the run first drafted on.
        assert (item.drafts["run-1"]["revision"], item.drafts["run-1"]["base_revision"]) == (3, 1)


class TestReads:
    async def test_the_same_run_sees_its_draft_over_the_head(self, twin) -> None:
        await _commit(twin, "a")
        v2 = await _commit(twin, "b", run_id="run-1")
        _, rev = await resolve_item_ref(twin, "CAD-LEG", PROJECT, run_id="run-1")
        assert (rev.revision, str(rev.node_id), rev.status) == (2, v2["node_id"], "draft")
        item = await find_item(twin, "CAD-LEG", PROJECT)
        history = await item_history(twin, item, run_id="run-1")
        assert [(r.revision, r.status) for r in history] == [(1, "committed"), (2, "draft")]

    async def test_other_readers_see_only_the_head(self, twin) -> None:
        v1 = await _commit(twin, "a")
        await _commit(twin, "b", run_id="run-1")
        item = await find_item(twin, "CAD-LEG", PROJECT)
        for run_id in (None, "run-2"):
            _, rev = await resolve_item_ref(twin, "CAD-LEG", PROJECT, run_id=run_id)
            assert str(rev.node_id) == v1["node_id"]
            assert [r.revision for r in await item_history(twin, item, run_id=run_id)] == [1]
        with pytest.raises(UnknownItemError):
            await resolve_item_ref(twin, "CAD-LEG@2", PROJECT)

    async def test_mcp_reads_follow_the_calling_run(self, twin) -> None:
        v1 = await _commit(twin, "a")
        v2 = await _commit(twin, "b", run_id="run-1")
        server = TwinServer(twin=twin, item_history_reader=make_item_history_reader(twin))
        outside = await server.get_node({"item_key": "CAD-LEG"})
        assert outside["node"]["id"] == v1["node_id"]
        with with_context(_in_run("run-1")):
            inside = await server.get_node({"item_key": "CAD-LEG"})
            history = await server.item_history({"item_key": "CAD-LEG"})
        assert inside["node"]["id"] == v2["node_id"]
        assert history["current"]["ref"] == "CAD-LEG@2"
        assert history["item"]["head_ref"] == "CAD-LEG@1"
        assert history["item"]["draft_ref"] == "CAD-LEG@2"
        pinned = await server.get_node({"item_key": "CAD-LEG@1"})
        assert pinned["node"]["id"] == v1["node_id"]

    async def test_an_item_born_in_a_run_is_not_listed_until_approved(self, twin) -> None:
        out = await _commit(twin, "a", name="Bracket", run_id="run-1")
        assert (out["revision"], out["revision_status"]) == (1, "draft")
        assert [i.key for i in await list_items(twin, PROJECT)] == []
        with pytest.raises(UnknownItemError, match="no current revision"):
            await resolve_item_ref(twin, "CAD-BRACKET", PROJECT)
        await commit_change_set(twin, "run-1", project_id=PROJECT, gate="g1")
        (item,) = await list_items(twin, PROJECT)
        assert (item.key, item.head_revision, str(item.head_node_id)) == (
            "CAD-BRACKET",
            1,
            out["node_id"],
        )

    async def test_dashboard_node_list_hides_unapproved_drafts(self, twin) -> None:
        import api_gateway.twin.routes as twin_routes

        v1 = await _commit(twin, "a")
        v2 = await _commit(twin, "b", run_id="run-1")
        previous = twin_routes.get_twin()
        twin_routes.init_twin(twin)
        try:
            listed = await twin_routes.list_twin_nodes(project_id=PROJECT)
            assert [n.id for n in listed.nodes] == [v1["node_id"]]
            everything = await twin_routes.list_twin_nodes(project_id=PROJECT, include_drafts=True)
            assert {n.id for n in everything.nodes} == {v1["node_id"], v2["node_id"]}
            await commit_change_set(twin, "run-1", project_id=PROJECT, gate="g1")
            listed = await twin_routes.list_twin_nodes(project_id=PROJECT)
            assert {n.id for n in listed.nodes} == {v1["node_id"], v2["node_id"]}
        finally:
            twin_routes.init_twin(previous)


class TestApproval:
    async def test_approve_moves_the_heads_of_every_draft_in_the_change_set(self, twin) -> None:
        cad1 = await _commit(twin, "a")
        constraints = make_constraint_recorder(twin, None)
        c = [{"name": "load", "expression": "True"}]
        cs1 = await constraints(title="Shelf reqs", constraints=c, project_id=PROJECT)
        await _commit(twin, "b", run_id="run-1")
        cad3 = await _commit(twin, "c", run_id="run-1")
        with with_context(_in_run("run-1", phase="requirements")):
            cs2 = await constraints(title="Shelf reqs", constraints=c, project_id=PROJECT)
        assert cs2["revision_status"] == "draft"

        result = await commit_change_set(
            twin,
            "run-1",
            project_id=PROJECT,
            gate="Design review",
            decided_by="local:reviewer",
            reason="Approved at gate 'Design review': looks right",
        )
        assert sorted(result.refs) == ["CAD-LEG@3", "CS-SHELF-REQS@2"]
        cad = await find_item(twin, "CAD-LEG", PROJECT)
        cs = await find_item(twin, "CS-SHELF-REQS", PROJECT)
        assert (cad.head_revision, str(cad.head_node_id)) == (3, cad3["node_id"])
        assert (cs.head_revision, str(cs.head_node_id)) == (2, cs2["node_id"])
        assert cad.drafts == {} and cs.drafts == {}
        assert await _head_targets(twin, cad) == [UUID(cad3["node_id"])]
        history = await item_history(twin, cad)
        assert [(r.revision, r.status) for r in history] == [
            (1, "committed"),
            (2, "approved"),
            (3, "approved"),
        ]
        approved = history[-1]
        assert approved.gate == "Design review"
        assert approved.change_reason == "Approved at gate 'Design review': looks right"
        assert approved.is_head
        # SUPERSEDES now records the chain the drafts formed.
        assert await _supersedes(twin, cad3["node_id"]) == [history[1].node_id]
        assert await _supersedes(twin, str(history[1].node_id)) == [UUID(cad1["node_id"])]
        assert await _supersedes(twin, cs2["node_id"]) == [UUID(cs1["node_id"])]

    async def test_a_second_approval_of_the_same_change_set_is_harmless(self, twin) -> None:
        await _commit(twin, "a")
        await _commit(twin, "b", run_id="run-1")
        await commit_change_set(twin, "run-1", project_id=PROJECT, gate="g1")
        again = await commit_change_set(twin, "run-1", project_id=PROJECT, gate="g1")
        assert (again.outcome, again.items) == ("empty", [])

    async def test_concurrent_approval_of_the_same_item_is_refused(self, twin) -> None:
        v1 = await _commit(twin, "a")
        a = await _commit(twin, "b", run_id="run-a")
        b = await _commit(twin, "c", run_id="run-b")
        assert (a["revision"], b["revision"]) == (2, 3)  # numbers never collide
        await commit_change_set(twin, "run-a", project_id=PROJECT, gate="g1")
        with pytest.raises(ChangeSetConflictError, match="PATCH_CONFLICT") as refused:
            await commit_change_set(twin, "run-b", project_id=PROJECT, gate="g1")
        assert "CAD-LEG was @1 when this run drafted CAD-LEG@3, and is now @2" in str(refused.value)
        assert "Retry the phase" in str(refused.value)
        item = await find_item(twin, "CAD-LEG", PROJECT)
        assert (item.head_revision, str(item.head_node_id)) == (2, a["node_id"])
        # run-b's draft is still open, so a retry can close it and redo the work.
        assert "run-b" in item.drafts
        assert v1["node_id"] != a["node_id"]

    async def test_a_direct_write_during_the_run_also_conflicts(self, twin) -> None:
        await _commit(twin, "a")
        await _commit(twin, "b", run_id="run-1")
        await _commit(twin, "c")  # no run: moves the head immediately
        with pytest.raises(ChangeSetConflictError):
            await commit_change_set(twin, "run-1", project_id=PROJECT, gate="g1")

    async def test_a_conflict_moves_nothing_even_for_unconflicted_items(self, twin) -> None:
        await _commit(twin, "a", name="Leg")
        await _commit(twin, "x", name="Foot")
        await _commit(twin, "b", name="Leg", run_id="run-1")
        await _commit(twin, "y", name="Foot", run_id="run-1")
        await _commit(twin, "c", name="Leg")
        with pytest.raises(ChangeSetConflictError):
            await commit_change_set(twin, "run-1", project_id=PROJECT, gate="g1")
        foot = await find_item(twin, "CAD-FOOT", PROJECT)
        assert foot.head_revision == 1


class TestClose:
    @pytest.mark.parametrize("status", ["rejected", "abandoned"])
    async def test_close_leaves_the_head_and_keeps_history(self, twin, status: str) -> None:
        v1 = await _commit(twin, "a")
        v2 = await _commit(twin, "b", run_id="run-1")
        closed = await close_change_set(
            twin, "run-1", status=status, reason="phase retried: wrong hole size"
        )
        assert closed.refs == ["CAD-LEG@2"]
        item = await find_item(twin, "CAD-LEG", PROJECT)
        assert (item.head_revision, str(item.head_node_id), item.drafts) == (1, v1["node_id"], {})
        history = await item_history(twin, item)
        assert [(r.revision, r.status) for r in history] == [(1, "committed"), (2, status)]
        assert history[1].status_reason == "phase retried: wrong hole size"
        assert str(history[1].node_id) == v2["node_id"]
        assert await open_drafts(twin, "run-1", PROJECT) == []

    async def test_a_retry_after_close_gets_a_fresh_number(self, twin) -> None:
        await _commit(twin, "a")
        await _commit(twin, "b", run_id="run-1")
        await close_change_set(twin, "run-1", status="abandoned", reason="retry")
        again = await _commit(twin, "c", run_id="run-1")
        assert again["revision"] == 3
        await commit_change_set(twin, "run-1", project_id=PROJECT, gate="g1")
        item = await find_item(twin, "CAD-LEG", PROJECT)
        assert item.head_revision == 3


# ── the gate decision path (routes.decide_run_gate) ──────────────────────────


@pytest.fixture
def gate_run(twin, monkeypatch: pytest.MonkeyPatch):
    """An in-process design-flow run parked at gate 'g1' of phase 'one'."""
    import api_gateway.runs.routes as routes
    import api_gateway.twin.routes as twin_routes
    from api_gateway.runs.engine import FLOW_ENGINE_ENV, FlowEngine
    from orchestrator.design_flow.spec import FLOWS, FlowDefinition, Gate, Phase

    monkeypatch.setenv(FLOW_ENGINE_ENV, FlowEngine.IN_PROCESS.value)
    flows_before = dict(FLOWS)
    FLOWS["cs_test"] = FlowDefinition(
        id="cs_test",
        name="cs_test",
        phases=(
            Phase(id="one", title="One", objective="a", gate=Gate(name="g1")),
            Phase(id="two", title="Two", objective="b", gate=Gate(name="g2")),
        ),
    )
    previous_twin = twin_routes.get_twin()
    twin_routes.init_twin(twin)
    routes.reset_run_store()

    def make(run_suffix: str = "") -> str:
        store = routes.get_run_store()
        run = store.create(
            {
                "goal": "g",
                "flow": "cs_test",
                "project_id": PROJECT,
                "flow_engine": FlowEngine.IN_PROCESS.value,
            }
        )
        store.start(run.id)
        store.request_approval(run.id, reason="[g1] One complete. bracket drawn")
        routes.get_gate_coordinator().set_gate_state(
            run.id, ready=True, retries_left=3, phase="one", reworks_left=3
        )
        return run.id

    yield make
    routes.reset_run_store()
    twin_routes.init_twin(previous_twin)
    FLOWS.clear()
    FLOWS.update(flows_before)


def _approver():
    from mcp_core.guardrails import Approver

    return Approver(actor_id="local:reviewer")


class TestGateDecisions:
    async def test_approve_commits_with_the_gate_and_its_reason(self, twin, gate_run) -> None:
        from api_gateway.runs.routes import decide_run_gate
        from orchestrator.harness.runs import ApprovalDecision

        run_id = gate_run()
        await _commit(twin, "a")
        await _commit(twin, "b", run_id=run_id)
        await decide_run_gate(run_id, ApprovalDecision.APPROVE, _approver(), reason="ok")
        item = await find_item(twin, "CAD-LEG", PROJECT)
        assert item.head_revision == 2
        head = (await item_history(twin, item))[-1]
        assert (head.status, head.gate) == ("approved", "g1")
        assert head.change_reason == "Approved at gate 'g1' by local:reviewer: ok"

    async def test_conflicting_approval_is_409_and_the_run_stays_parked(
        self, twin, gate_run
    ) -> None:
        from fastapi import HTTPException

        from api_gateway.runs.routes import decide_run_gate, get_gate_coordinator, get_run_store
        from orchestrator.harness.runs import ApprovalDecision, RunStatus

        run_id = gate_run()
        await _commit(twin, "a")
        await _commit(twin, "b", run_id=run_id)
        await _commit(twin, "c")  # the head moves under the run
        with pytest.raises(HTTPException) as refused:
            await decide_run_gate(run_id, ApprovalDecision.APPROVE, _approver())
        assert refused.value.status_code == 409
        assert "PATCH_CONFLICT" in str(refused.value.detail)
        assert get_run_store().get(run_id).status is RunStatus.AWAITING_APPROVAL

        # The retry closes the drafts and hands the phase the rebase message.
        await decide_run_gate(run_id, ApprovalDecision.RETRY, _approver(), reason="rebase")
        reason = get_gate_coordinator().take_retry_reason(run_id)
        assert reason.startswith("rebase | PATCH_CONFLICT")
        item = await find_item(twin, "CAD-LEG", PROJECT)
        assert (item.head_revision, item.drafts) == (3, {})
        assert [r.status for r in await item_history(twin, item)] == [
            "committed",
            "abandoned",
            "committed",
        ]

    @pytest.mark.parametrize(
        ("decision", "status"),
        [("reject", "rejected"), ("retry", "abandoned"), ("rework", "abandoned")],
    )
    async def test_reject_retry_rework_close_without_moving_the_head(
        self, twin, gate_run, decision: str, status: str
    ) -> None:
        from api_gateway.runs.routes import decide_run_gate, get_gate_coordinator
        from orchestrator.harness.runs import ApprovalDecision

        run_id = gate_run()
        if decision == "rework":
            # Rework needs an earlier phase: park the run at phase two's gate.
            get_gate_coordinator().set_gate_state(
                run_id, ready=True, retries_left=3, phase="two", reworks_left=3
            )
        v1 = await _commit(twin, "a")
        await _commit(twin, "b", run_id=run_id)
        await decide_run_gate(
            run_id,
            ApprovalDecision(decision),
            _approver(),
            reason="hole too small",
            to_phase="one" if decision == "rework" else "",
        )
        await asyncio.sleep(0)  # let any transition hook run
        item = await find_item(twin, "CAD-LEG", PROJECT)
        assert (item.head_revision, str(item.head_node_id), item.drafts) == (1, v1["node_id"], {})
        history = await item_history(twin, item)
        assert [r.status for r in history] == ["committed", status]
        assert "hole too small" in (history[1].status_reason or "")

    async def _settled(self, twin):
        for _ in range(100):
            item = await find_item(twin, "CAD-LEG", PROJECT)
            if not item.drafts:
                return item
            await asyncio.sleep(0.01)
        raise AssertionError("the change set was never settled")

    async def test_a_canceled_run_abandons_its_drafts(self, twin, gate_run) -> None:
        from api_gateway.runs.routes import get_run_store

        run_id = gate_run()
        await _commit(twin, "a")
        await _commit(twin, "b", run_id=run_id)
        get_run_store().cancel(run_id, reason="operator stopped it")
        item = await self._settled(twin)
        assert item.head_revision == 1
        assert [r.status for r in await item_history(twin, item)][-1] == "abandoned"

    async def test_a_completed_run_commits_what_its_last_gate_left(self, twin, gate_run) -> None:
        """Phases after the last human gate (no gate, or auto-approve) commit at completion."""
        from api_gateway.runs.routes import decide_run_gate, get_run_store
        from orchestrator.harness.runs import ApprovalDecision

        run_id = gate_run()
        await _commit(twin, "a")
        await decide_run_gate(run_id, ApprovalDecision.APPROVE, _approver())
        await _commit(twin, "b", run_id=run_id)  # an ungated trailing phase
        get_run_store().complete(run_id, result={})
        item = await self._settled(twin)
        assert item.head_revision == 2
        assert (await item_history(twin, item))[-1].gate == "run completed"


class TestInProcessExecutorScope:
    async def test_executor_phase_writes_carry_the_run(self, twin) -> None:
        """The in-process engine's phase scope makes the brain's writes drafts."""
        from api_gateway.runs.change_sets import phase_scope
        from orchestrator.design_flow.executor import (
            DesignFlowExecutor,
            FlowContext,
            GateCoordinator,
            PhaseOutcome,
        )
        from orchestrator.design_flow.spec import FlowDefinition, Phase
        from orchestrator.harness.runs import InMemoryRunStore, RunStatus

        class WritingBrain:
            async def run_phase(self, *, goal: str, phase: Phase, context: FlowContext):
                await _commit(twin, "b")
                return PhaseOutcome(summary="drew it")

        await _commit(twin, "a")
        coord = GateCoordinator()
        store = InMemoryRunStore(on_transition=coord.on_transition)
        run = store.create({"goal": "g", "flow": "x", "project_id": PROJECT})
        flow = FlowDefinition(
            id="x", name="x", phases=(Phase(id="only", title="Only", objective="o"),)
        )
        executor = DesignFlowExecutor(
            store=store, brain=WritingBrain(), coordinator=coord, phase_scope=phase_scope
        )
        await executor.run(run.id, flow)
        assert store.get(run.id).status is RunStatus.COMPLETED
        item = await find_item(twin, "CAD-LEG", PROJECT)
        assert item.head_revision == 1
        assert item.drafts[run.id]["phase"] == "only"
