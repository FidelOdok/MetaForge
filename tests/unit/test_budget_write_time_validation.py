"""A budget with no numbers must not record successfully (FORGE-414).

Six of the ten budget entities in the live twin had `metadata = {}`. They were
named like real budgets -- "Moving mass budget <= 4.5 kg", "J2 torque budget
<= 25 Nm" -- with the limit present only as prose in the title.

`budget_from_entity` requires metric, unit and system_total, so the hierarchy
route skipped each one, logged `hierarchy_budget_entity_malformed` and carried
on: 14 times in 24 hours, once per page load, forever. The tab showed no
budget row, which reads as "nobody set a budget" rather than "your budget is
unusable".

The validation was on the wrong side. FORGE-311 put the unit check at write
time deliberately, so a nonsense unit is a compile-time error. The required
fields belong in the same place for the same reason: rejected once at
creation, where the caller can still fix it, rather than failing on every read
by a reader who cannot.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from twin_core.consistency.budgets import BUDGET_REQUIRED_METADATA, missing_budget_metadata

_GOOD = {"metric": "mass", "unit": "kg", "system_total": 4.5}


class _Twin:
    """Minimal twin: records what was created, nothing else."""

    def __init__(self) -> None:
        self.created: list[Any] = []

    async def create_engineering_entity(self, entity: Any) -> Any:
        self.created.append(entity)
        return entity

    async def add_edge(self, *_a: Any, **_k: Any) -> None: ...


def _recorder(twin: Any) -> Any:
    from api_gateway.twin.engineering_entity_recorder import make_engineering_entity_recorder

    return make_engineering_entity_recorder(twin)


@pytest.mark.asyncio
class TestABudgetWithoutItsNumbersIsRejected:
    async def test_empty_metadata_is_refused(self) -> None:
        """The exact live case: entity_type budget, nothing in `extra`."""
        twin = _Twin()
        with pytest.raises(ValueError) as err:
            await _recorder(twin)(entity_type="budget", statement="Moving mass budget <= 4.5 kg")
        message = str(err.value)
        for key in BUDGET_REQUIRED_METADATA:
            assert key in message
        assert twin.created == [], "the entity must not be persisted"

    async def test_the_error_says_what_to_pass(self) -> None:
        """A rejection the caller cannot act on just moves the problem."""
        with pytest.raises(ValueError) as err:
            await _recorder(_Twin())(entity_type="budget", statement="x")
        message = str(err.value)
        assert "extra=" in message
        assert '"system_total"' in message

    async def test_the_error_explains_the_consequence(self) -> None:
        """Why this is worth refusing rather than warning: it would have
        looked like no budget at all."""
        with pytest.raises(ValueError) as err:
            await _recorder(_Twin())(entity_type="budget", statement="x")
        assert "dropped from every rollup" in str(err.value)

    @pytest.mark.parametrize("omit", list(BUDGET_REQUIRED_METADATA))
    async def test_each_field_is_required_on_its_own(self, omit: str) -> None:
        extra = {k: v for k, v in _GOOD.items() if k != omit}
        with pytest.raises(ValueError) as err:
            await _recorder(_Twin())(entity_type="budget", statement="x", extra=extra)
        assert omit in str(err.value)

    async def test_a_well_formed_budget_still_records(self) -> None:
        twin = _Twin()
        await _recorder(twin)(
            entity_type="budget", statement="Moving mass <= 4.5 kg", extra=dict(_GOOD)
        )
        assert len(twin.created) == 1
        assert twin.created[0].metadata["system_total"] == 4.5

    async def test_other_entity_types_are_untouched(self) -> None:
        """The check is budget-specific. A risk or an assumption has no
        system_total and must not acquire a requirement for one."""
        twin = _Twin()
        for entity_type in ("risk", "assumption", "objective", "intent"):
            await _recorder(twin)(entity_type=entity_type, statement="something")
        assert len(twin.created) == 4

    async def test_the_unit_check_still_runs_first(self) -> None:
        """FORGE-311's check must not be displaced by this one."""
        with pytest.raises(ValueError, match="not a recognized unit"):
            await _recorder(_Twin())(
                entity_type="budget",
                statement="x",
                extra={"metric": "mass", "unit": "not_a_real_unit_xyz", "system_total": 1},
            )


class TestOneListReadByBothSides:
    def test_the_reader_and_the_writer_use_the_same_requirement(self) -> None:
        """They were two literals in two files. A field added to one and not
        the other recreates this bug in the opposite direction -- recorded
        successfully, rejected on read."""
        import inspect

        from twin_core.consistency import budgets

        source = inspect.getsource(budgets.budget_from_entity)
        assert "missing_budget_metadata" in source
        # The old inline literal must not come back.
        assert '("metric", "unit", "system_total")' not in source

        recorder_source = inspect.getsource(
            __import__(
                "api_gateway.twin.engineering_entity_recorder",
                fromlist=["make_engineering_entity_recorder"],
            ).make_engineering_entity_recorder
        )
        assert "missing_budget_metadata" in recorder_source

    def test_missing_budget_metadata_reports_in_declared_order(self) -> None:
        assert missing_budget_metadata({}) == list(BUDGET_REQUIRED_METADATA)
        assert missing_budget_metadata(dict(_GOOD)) == []
        assert missing_budget_metadata({"metric": "mass"}) == ["unit", "system_total"]


@pytest.mark.asyncio
class TestExistingRowsBecomeVisible:
    """New budgets are refused now, but the six already in the twin are not
    retroactively fixed -- and a person who cannot read a server log still has
    to fix them."""

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from api_gateway.twin.hierarchy_routes import router

        app = FastAPI()
        app.include_router(router)
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.fixture
    def twin(self):
        from api_gateway.twin.hierarchy_routes import _twin

        _twin._graph._nodes.clear()
        _twin._graph._outgoing.clear()
        _twin._graph._incoming.clear()
        return _twin

    async def _record_raw_budget(self, twin: Any, project_id: UUID, title: str) -> Any:
        """A legacy row: written straight to the twin, bypassing the recorder's
        new check -- which is exactly how the live ones got there."""
        from twin_core.models.engineering_entity import EngineeringEntity

        entity = EngineeringEntity(
            entity_type="budget",
            statement=title,
            title=title,
            project_id=project_id,
            metadata={},
        )
        return await twin.create_engineering_entity(entity)

    async def test_the_response_names_the_budget_it_had_to_ignore(self, client, twin) -> None:
        project_id = uuid4()
        await self._record_raw_budget(twin, project_id, "Moving mass budget <= 4.5 kg")

        body = (await client.get(f"/v1/twin/hierarchy?project_id={project_id}")).json()
        malformed = body["malformedBudgets"]
        assert len(malformed) == 1
        assert malformed[0]["title"] == "Moving mass budget <= 4.5 kg"
        assert malformed[0]["missing"] == list(BUDGET_REQUIRED_METADATA)
        assert malformed[0]["entityId"]

    async def test_a_well_formed_budget_is_not_reported_as_malformed(self, client, twin) -> None:
        from twin_core.models.engineering_entity import EngineeringEntity

        project_id = uuid4()
        await twin.create_engineering_entity(
            EngineeringEntity(
                entity_type="budget",
                statement="mass",
                title="mass_budget",
                project_id=project_id,
                metadata=dict(_GOOD),
            )
        )
        body = (await client.get(f"/v1/twin/hierarchy?project_id={project_id}")).json()
        assert body["malformedBudgets"] == []

    async def test_the_field_is_present_and_empty_in_the_normal_case(self, client, twin) -> None:
        """Absent would make a client treat 'no malformed budgets' and 'this
        build does not report them' as the same thing."""
        body = (await client.get(f"/v1/twin/hierarchy?project_id={uuid4()}")).json()
        assert body["malformedBudgets"] == []

    async def test_an_unscoped_listing_reports_none_rather_than_guessing(
        self, client, twin
    ) -> None:
        """Budget lookup is project-scoped on purpose; an unscoped listing does
        not know whose budgets apply, so it must not claim any are broken."""
        await self._record_raw_budget(twin, uuid4(), "Mass Budget")
        body = (await client.get("/v1/twin/hierarchy")).json()
        assert body["malformedBudgets"] == []
