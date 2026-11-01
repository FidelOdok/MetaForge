"""FORGE-515: currency units, entity nodes, geometry window scoping."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api_gateway.runs.geometry_constraints import check_geometry_constraints
from twin_core.models.constraint import Constraint
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import ConstraintSeverity
from twin_core.models.quantity import is_currency_code, same_currency


def _constraint(**kw) -> Constraint:
    base = {
        "name": "budget",
        "expression": "True",
        "severity": ConstraintSeverity.ERROR,
        "domain": "mechanical",
        "source": "user",
        "metric": "cost",
        "limit": 100.0,
    }
    base.update(kw)
    return Constraint(**base)


class TestCurrencyUnits:
    def test_iso_codes_are_currency(self) -> None:
        assert is_currency_code("GBP") and is_currency_code("USD") and is_currency_code("EUR")
        assert not is_currency_code("kg") and not is_currency_code("gbpx")

    def test_constraint_accepts_gbp(self) -> None:
        assert _constraint(unit="GBP").unit == "GBP"

    def test_constraint_still_rejects_nonsense(self) -> None:
        with pytest.raises(ValueError):
            _constraint(unit="notaunit")

    def test_same_currency_only(self) -> None:
        assert same_currency("GBP", "GBP")
        assert not same_currency("GBP", "USD")

    def test_same_currency_is_compared(self) -> None:
        c = _constraint(unit="GBP", operator="<=", limit=50.0)
        out = check_geometry_constraints([c], [("m", {"cost": 80, "cost_currency": "GBP"})])
        assert out.evaluated == 1 and len(out.violations) == 1

    def test_other_currency_is_not_evaluated_with_reason(self) -> None:
        c = _constraint(unit="GBP")
        out = check_geometry_constraints([c], [("m", {"cost": 80, "cost_currency": "USD"})])
        assert out.evaluated == 0 and not out.violations
        assert "no exchange rate" in out.not_evaluated[0]


class TestEntityNode:
    @pytest.fixture
    def app(self):
        from api_gateway.twin.routes import router

        app = FastAPI()
        app.include_router(router)
        return app

    async def test_entity_is_returned(self, app) -> None:
        from api_gateway.twin.routes import _twin

        entity = await _twin.create_engineering_entity(
            EngineeringEntity(entity_type="intent", title="Shelf", statement="Hold books")
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.get(f"/v1/twin/nodes/{entity.id}")
        body = resp.json()
        assert resp.status_code == 200
        assert body["type"] == "intent"
        assert body["properties"]["statement"] == "Hold books"

    async def test_missing_node_is_404(self, app) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.get(f"/v1/twin/nodes/{uuid4()}")
        assert resp.status_code == 404


class TestGeometryWindow:
    async def test_geometry_check_uses_window_and_drops_superseded(self) -> None:
        from api_gateway.runs.gate_eval import TwinConstraintChecker

        seen: dict = {}

        class Ev(TwinConstraintChecker):
            def __init__(self) -> None:
                self._twin = SimpleNamespace(list_constraints=self._lc)

            async def _lc(self, project_id):
                return []

            async def _current_cad_entries(
                self, project_id, since_ts=0.0, *, drop_superseded=False
            ):
                seen["args"] = (since_ts, drop_superseded)
                return []

        await Ev()._geometry_check(str(uuid4()), 123.0)
        assert seen["args"] == (123.0, True)
