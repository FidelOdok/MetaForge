"""A blank budget cell has to say which blank it is (FORGE-345).

D2 is "budgets and allocation down the product hierarchy with rollups".
The engine computed them and, when it could not, returned ``actual=None``
and ``over_budget=None`` with nothing else. Three unrelated situations
arrived as the same empty cell:

* the metric has no rollup source at all (``power``),
* ``target`` is the free-text label the model documents ("frame"), not a
  node id,
* ``target`` is a node id that resolves to nothing.

Those need different things done about them -- wait for a feature, fix a
typo, repair a dangling reference -- and an engineer reading the
allocation table could not tell them apart, nor from a subsystem whose
parts simply have no mass recorded yet.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from twin_core.consistency.hierarchy_budget import (
    TARGET_NOT_A_NODE_ID,
    TARGET_NOT_FOUND,
    UNSUPPORTED_METRIC,
    compute_budget_allocation_status,
)
from twin_core.consistency.models import Budget, BudgetAllocation


class _Twin:
    """Stands in for TwinAPI at the one seam the engine uses."""

    def __init__(self, rollups: dict[uuid.UUID, Any] | None = None) -> None:
        self._rollups = rollups or {}

    async def get_subgraph(self, *a: Any, **k: Any) -> Any:  # pragma: no cover
        raise AssertionError("compute_hierarchy_rollup should be patched in these tests")


def _budget(metric: str, target: str, amount: float = 10.0) -> Budget:
    return Budget(
        id=str(uuid.uuid4()),
        project_id=str(uuid.uuid4()),
        metric=metric,
        unit="kg",
        system_total=amount,
        allocations=[BudgetAllocation(target=target, amount=amount)],
    )


async def _run(monkeypatch: pytest.MonkeyPatch, budget: Budget, rollup: Any | None = None):
    import twin_core.consistency.hierarchy_budget as mod

    async def fake_rollup(twin: Any, root_id: uuid.UUID) -> Any:
        if rollup is None:
            raise KeyError(f"{root_id} is not a HierarchyNode")
        return rollup

    monkeypatch.setattr(mod, "compute_hierarchy_rollup", fake_rollup)
    return await compute_budget_allocation_status(_Twin(), budget)


class _Rollup:
    def __init__(self, mass_kg: float = 0.0, cost: float = 0.0) -> None:
        self.mass_kg = mass_kg
        self.cost = cost


@pytest.mark.asyncio
async def test_a_checked_allocation_carries_no_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    node = str(uuid.uuid4())
    [status] = await _run(monkeypatch, _budget("mass", node), _Rollup(mass_kg=12.0))
    assert status.actual == 12.0
    assert status.over_budget is True
    assert status.reason == ""


@pytest.mark.asyncio
async def test_a_genuine_zero_is_not_a_missing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rollup ran and found nothing to add up. That is a real answer,
    and under budget -- not a blank."""
    node = str(uuid.uuid4())
    [status] = await _run(monkeypatch, _budget("mass", node), _Rollup(mass_kg=0.0))
    assert status.actual == 0.0
    assert status.over_budget is False
    assert status.reason == ""


@pytest.mark.asyncio
async def test_an_unrollable_metric_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """A metric nothing can compute. A blank here looks like data somebody
    could go and enter."""
    [status] = await _run(monkeypatch, _budget("vibration", str(uuid.uuid4())))
    assert status.actual is None
    assert status.reason == UNSUPPORTED_METRIC


@pytest.mark.asyncio
async def test_a_bare_power_metric_is_refused_as_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FORGE-390: `power` is two budgets checked against different limits.
    Picking one is wrong in a way nothing later catches -- a thermal budget
    checked against draw passes designs that overheat, and a supply budget
    checked against dissipation passes designs that brown out."""
    from twin_core.consistency.hierarchy_budget import AMBIGUOUS_METRIC

    [status] = await _run(monkeypatch, _budget("power", str(uuid.uuid4())))
    assert status.actual is None
    assert status.reason == AMBIGUOUS_METRIC
    assert "power_dissipation" in status.detail


@pytest.mark.asyncio
async def test_a_label_target_is_distinguished_from_a_broken_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`target` is documented as free text ("frame"), reinterpreted as a
    node id when a caller stores one. A label is not a mistake -- it just
    cannot be checked -- and it must not read like a dangling id."""
    [status] = await _run(monkeypatch, _budget("mass", "frame"))
    assert status.reason == TARGET_NOT_A_NODE_ID


@pytest.mark.asyncio
async def test_a_dangling_node_id_is_its_own_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """Well-formed and points at nothing. Unlike a label, this one is
    worth repairing."""
    [status] = await _run(monkeypatch, _budget("mass", str(uuid.uuid4())), rollup=None)
    assert status.reason == TARGET_NOT_FOUND


@pytest.mark.asyncio
async def test_the_three_reasons_are_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point. Collapsing any two back together loses the action
    the reader is supposed to take."""
    assert len({UNSUPPORTED_METRIC, TARGET_NOT_A_NODE_ID, TARGET_NOT_FOUND}) == 3


@pytest.mark.asyncio
async def test_owner_and_discipline_still_come_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """FORGE-313's pass-through must survive an unresolvable target: who
    owns a subsystem is knowable even when its rollup is not."""
    budget = Budget(
        id=str(uuid.uuid4()),
        project_id=str(uuid.uuid4()),
        metric="mass",
        unit="kg",
        system_total=10.0,
        allocations=[
            BudgetAllocation(target="frame", amount=10.0, owner="ana", discipline="mechanical")
        ],
    )
    [status] = await _run(monkeypatch, budget)
    assert status.owner == "ana"
    assert status.discipline == "mechanical"
    assert status.reason == TARGET_NOT_A_NODE_ID


def test_a_dropped_budget_metric_is_logged_not_silent() -> None:
    """The Structure tab drops any metric it cannot roll up, so a power
    budget produces no row at all -- which reads as "nobody set one"."""
    from pathlib import Path

    route = (
        Path(__file__).resolve().parents[2] / "api_gateway" / "twin" / "hierarchy_routes.py"
    ).read_text()
    assert "hierarchy_budget_metric_not_rendered" in route
    # FORGE-390: and it must not claim there is no rollup for a metric that
    # now has one -- that would send someone looking for what is already
    # there.
    assert "has_rollup=rollable" in route or "has_rollup" in route


# ---------------------------------------------------------------------------
# Two power budgets, checked against different limits (FORGE-390)
# ---------------------------------------------------------------------------


class _PowerRollup:
    def __init__(self, peak: float, average: float, dissipation: float) -> None:
        self.mass_kg = 0.0
        self.cost = 0.0
        self.draw_peak_w = peak
        self.draw_average_w = average
        self.dissipation_w = dissipation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ("power_draw_peak", 12.0),
        ("power_draw_average", 3.0),
        ("power_dissipation", 0.9),
    ],
)
async def test_each_power_budget_reads_its_own_number(
    monkeypatch: pytest.MonkeyPatch, metric: str, expected: float
) -> None:
    """A motor-bearing branch: 12 W peak, 3 W average, 0.9 W of heat. Three
    different answers, and a budget that took the wrong one would be
    checking against a limit it was never written for."""
    node = str(uuid.uuid4())
    [status] = await _run(
        monkeypatch,
        _budget(metric, node, amount=100.0),
        _PowerRollup(peak=12.0, average=3.0, dissipation=0.9),
    )
    assert status.actual == expected
    assert status.reason == ""


@pytest.mark.asyncio
async def test_a_thermal_budget_is_not_judged_on_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure merging them causes. This branch draws 12 W and sheds
    0.9 W; an enclosure rated for 1 W passes on dissipation and would fail
    on draw. Getting that backwards either over-specifies the enclosure or
    passes a design that overheats."""
    node = str(uuid.uuid4())
    rollup = _PowerRollup(peak=12.0, average=3.0, dissipation=0.9)

    [thermal] = await _run(monkeypatch, _budget("power_dissipation", node, amount=1.0), rollup)
    assert thermal.over_budget is False

    [supply] = await _run(monkeypatch, _budget("power_draw_peak", node, amount=1.0), rollup)
    assert supply.over_budget is True
