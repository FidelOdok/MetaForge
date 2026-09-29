"""Unit tests for the MaturityGate model + Twin CRUD (FORGE-319)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import NodeType
from twin_core.models.maturity_gate import (
    SATISFIED_DECISIONS,
    MaturityGate,
    MaturityLevel,
    RequiredClaimDecision,
    RequiredClaimResult,
)


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def project_id():
    return uuid4()


def test_satisfied_decisions_are_exactly_pass_and_waived():
    assert SATISFIED_DECISIONS == {RequiredClaimDecision.PASS, RequiredClaimDecision.WAIVED}
    assert RequiredClaimDecision.FAIL not in SATISFIED_DECISIONS
    assert RequiredClaimDecision.UNCERTAIN not in SATISFIED_DECISIONS


def test_default_node_type_is_maturity_gate(project_id):
    gate = MaturityGate(level=MaturityLevel.SIM_VALIDATED, project_id=project_id)
    assert gate.node_type == NodeType.MATURITY_GATE
    assert gate.promoted is False
    assert gate.results == []


class TestMaturityGateCrud:
    async def test_create_and_get_round_trips_all_fields(self, twin, project_id):
        req_id = uuid4()
        result = RequiredClaimResult(
            requirement_id=req_id,
            requirement_name="tip_deflection",
            decision=RequiredClaimDecision.PASS,
            detail="value 0.05 within limit 0.5 (margin 0.45)",
        )
        gate = MaturityGate(
            level=MaturityLevel.SIM_VALIDATED,
            project_id=project_id,
            required_claim_ids=[req_id],
            results=[result],
            promoted=True,
            decided_by="reviewer",
            k=1.5,
        )
        created = await twin.create_maturity_gate(gate)

        fetched = await twin.get_maturity_gate(created.id)
        assert fetched is not None
        assert fetched.node_type == NodeType.MATURITY_GATE
        assert fetched.level == MaturityLevel.SIM_VALIDATED
        assert fetched.promoted is True
        assert fetched.decided_by == "reviewer"
        assert fetched.k == 1.5
        assert fetched.required_claim_ids == [req_id]
        assert len(fetched.results) == 1
        assert fetched.results[0].requirement_name == "tip_deflection"
        assert fetched.results[0].decision == RequiredClaimDecision.PASS

    async def test_get_unknown_id_returns_none(self, twin):
        assert await twin.get_maturity_gate(uuid4()) is None

    async def test_list_filters_by_project(self, twin, project_id):
        other_project = uuid4()
        await twin.create_maturity_gate(
            MaturityGate(level=MaturityLevel.CONCEPT, project_id=project_id)
        )
        await twin.create_maturity_gate(
            MaturityGate(level=MaturityLevel.CONCEPT, project_id=other_project)
        )

        gates = await twin.list_maturity_gates(project_id=project_id)
        assert len(gates) == 1
        assert gates[0].project_id == project_id

    async def test_multiple_attempts_are_each_their_own_immutable_record(self, twin, project_id):
        """Each promotion attempt persists as a new record -- there is
        deliberately no update_maturity_gate."""
        first = await twin.create_maturity_gate(
            MaturityGate(level=MaturityLevel.SIM_VALIDATED, project_id=project_id, promoted=False)
        )
        second = await twin.create_maturity_gate(
            MaturityGate(level=MaturityLevel.SIM_VALIDATED, project_id=project_id, promoted=True)
        )
        assert first.id != second.id

        gates = await twin.list_maturity_gates(project_id=project_id)
        assert {g.id for g in gates} == {first.id, second.id}
        assert not hasattr(twin, "update_maturity_gate")
