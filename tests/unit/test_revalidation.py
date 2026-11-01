"""Unit tests for make_revalidation_executor / twin.execute_revalidation_plan
(FORGE-316)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api_gateway.twin.evidence_recorder import make_evidence_recorder
from api_gateway.twin.metric_evaluator import make_metric_evaluator
from api_gateway.twin.revalidation import make_revalidation_executor
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_change_transaction import ChangeTrigger
from twin_core.models.enums import ConstraintSeverity, WorkProductType
from twin_core.models.patch import Patch, PatchOp, PatchOperation
from twin_core.models.work_product import WorkProduct
from twin_core.transactions.ect import analyze, approve, commit, propose_change


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


async def _seed_cad(twin, name="upper_arm") -> WorkProduct:
    return await twin.create_work_product(
        WorkProduct(
            name=name,
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="",
            content_hash="deadbeef",
            format="step",
            created_by="test",
            metadata={
                "geometry_features": {
                    "properties": {
                        "bounding_box": {
                            "min_x": -180,
                            "max_x": 180,
                            "min_y": -20,
                            "max_y": 20,
                            "min_z": -30,
                            "max_z": 30,
                        }
                    }
                }
            },
        )
    )


async def _seed_requirement(twin, project_id, name="youngs_modulus") -> Constraint:
    return await twin.create_constraint(
        Constraint(
            name=name,
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mech",
            source="test",
            project_id=project_id,
        )
    )


class TestExecuteRevalidationPlan:
    async def _commit_a_revising_change(self, twin, req, project_id):
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.REVISE,
                    entity_kind="constraint",
                    entity_id=req.id,
                    fields={"message": "material spec revised"},
                    expected_revision=req.revision,
                )
            ],
            reason="revise material constraint",
            created_by="agent",
            project_id=project_id,
        )
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=patch,
            project_id=project_id,
        )
        await analyze(twin, ect.id)
        await approve(twin, ect.id, approver="reviewer")
        return await commit(twin, ect.id)

    async def test_re_runs_evidence_with_replayable_provenance(self, twin, project_id):
        from twin_core.consistency.staleness import Dependency, StalenessEngine

        wp = await _seed_cad(twin)
        req = await _seed_requirement(twin, project_id)

        evidence_recorder = make_evidence_recorder(twin)
        evaluate = make_metric_evaluator(twin, evidence_recorder=evidence_recorder)
        original = await evaluate(
            work_product_id=str(wp.id),
            load_n=1.0,
            youngs_modulus_mpa=70000.0,
            project_id=str(project_id),
        )
        # This evidence's real dependency is the CAD geometry -- also
        # declare a dependency on the material constraint, mirroring what a
        # real caller would do when the calc's material input came from a
        # recorded requirement.
        await StalenessEngine(twin).declare_dependencies(
            "engineering_entity",
            UUID(original["evidence_node_id"]),
            [Dependency(entity_kind="constraint", entity_id=req.id, revision=1)],
        )

        committed = await self._commit_a_revising_change(twin, req, project_id)
        assert len(committed.revalidation_plan) == 1

        executor = make_revalidation_executor(twin, {"twin.evaluate_metric": evaluate})
        result = await executor(str(committed.id))

        assert len(result["re_run"]) == 1
        assert result["manual_review_needed"] == []
        re_run = result["re_run"][0]
        assert re_run["entity_id"] == original["evidence_node_id"]
        assert re_run["tool_id"] == "twin.evaluate_metric"
        assert re_run["new_evidence_node_id"] is not None
        assert re_run["new_evidence_node_id"] != original["evidence_node_id"]

        # The new evidence supersedes the old one and the old one is
        # SUPERSEDED (FORGE-65's flow), not just left dangling STALE.
        old_status = await StalenessEngine(twin).get_status(
            "engineering_entity", UUID(original["evidence_node_id"])
        )
        assert old_status.value == "superseded"

        stored_ect = await twin.get_ect(committed.id)
        assert stored_ect.revalidation_result == result

    async def test_hand_authored_evidence_needs_manual_review(self, twin, project_id):
        from twin_core.consistency.staleness import Dependency, StalenessEngine

        req = await _seed_requirement(twin, project_id)
        evidence_recorder = make_evidence_recorder(twin)
        # No 'replay' -- a plain, hand-authored evidence record.
        ev = await evidence_recorder(
            evidence_type="test",
            producer={"tool": "bench"},
            inputs={},
            result={"pass": True},
            valid_against=[{"ref": str(req.id), "entity_kind": "constraint"}],
            project_id=str(project_id),
        )
        await StalenessEngine(twin).declare_dependencies(
            "engineering_entity",
            UUID(ev["node_id"]),
            [Dependency(entity_kind="constraint", entity_id=req.id, revision=1)],
        )

        committed = await self._commit_a_revising_change(twin, req, project_id)
        executor = make_revalidation_executor(twin, {})
        result = await executor(str(committed.id))

        assert result["re_run"] == []
        assert len(result["manual_review_needed"]) == 1
        assert "no replayable provenance" in result["manual_review_needed"][0]["why"]

    async def test_unwired_tool_id_needs_manual_review(self, twin, project_id):
        from twin_core.consistency.staleness import Dependency, StalenessEngine

        wp = await _seed_cad(twin)
        req = await _seed_requirement(twin, project_id)
        evidence_recorder = make_evidence_recorder(twin)
        evaluate = make_metric_evaluator(twin, evidence_recorder=evidence_recorder)
        original = await evaluate(
            work_product_id=str(wp.id),
            load_n=1.0,
            youngs_modulus_mpa=70000.0,
            project_id=str(project_id),
        )
        await StalenessEngine(twin).declare_dependencies(
            "engineering_entity",
            UUID(original["evidence_node_id"]),
            [Dependency(entity_kind="constraint", entity_id=req.id, revision=1)],
        )
        committed = await self._commit_a_revising_change(twin, req, project_id)

        # Dispatch table deliberately empty -- twin.evaluate_metric not wired.
        executor = make_revalidation_executor(twin, {})
        result = await executor(str(committed.id))
        assert result["re_run"] == []
        assert "not wired" in result["manual_review_needed"][0]["why"]

    async def test_rejects_ect_not_yet_committed(self, twin, project_id):
        req = await _seed_requirement(twin, project_id)
        patch = Patch(
            operations=[
                PatchOperation(
                    op=PatchOp.REVISE,
                    entity_kind="constraint",
                    entity_id=req.id,
                    fields={"message": "x"},
                    expected_revision=req.revision,
                )
            ],
            reason="x",
            created_by="agent",
            project_id=project_id,
        )
        ect = await propose_change(
            twin,
            trigger=ChangeTrigger(type="user_request"),
            observation="x",
            patch=patch,
            project_id=project_id,
        )
        executor = make_revalidation_executor(twin, {})
        with pytest.raises(ValueError, match="committed"):
            await executor(str(ect.id))

    async def test_unknown_ect_raises(self, twin):
        executor = make_revalidation_executor(twin, {})
        with pytest.raises(ValueError, match="no ECT"):
            await executor(str(uuid4()))


class TestExecuteRevalidationPlanAdapter:
    async def test_tool_registered_and_returns_shape(self, twin, project_id):
        executor = make_revalidation_executor(twin, {})
        server = TwinServer(twin=twin, revalidation_executor=executor)
        assert "twin.execute_revalidation_plan" in server.tool_ids

    async def test_not_registered_when_no_executor_supplied(self, twin):
        server = TwinServer(twin=twin)
        assert "twin.execute_revalidation_plan" not in server.tool_ids

    async def test_missing_ect_id_rejected(self, twin):
        executor = make_revalidation_executor(twin, {})
        server = TwinServer(twin=twin, revalidation_executor=executor)
        with pytest.raises(ValueError, match="ect_id"):
            await server.execute_revalidation_plan({})
