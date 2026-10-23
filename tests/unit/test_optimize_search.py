"""``optimize.suggest``: ask-and-tell search over one design variable.

The objective is evaluated by the caller with real analysis; here a closed
form stands in for it, so the search itself is what is tested.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tool_registry.tools.optimize.adapter import OptimizeServer
from tool_registry.tools.optimize.search import SearchRequest, suggest


def _request(**over: object) -> SearchRequest:
    base: dict = {
        "variable": {
            "name": "fin_pitch_mm",
            "lower": 1.0,
            "upper": 9.0,
            "unit": "mm",
            "bounds_source": "REQ-HS-PITCH (process limits)",
        },
        "objective_name": "max_junction_temp_c",
        "tolerance": 0.01,
    }
    base.update(over)
    return SearchRequest.model_validate(base)


def _run(objective, **over: object):  # noqa: ANN001, ANN202
    """Drive the ask-and-tell loop to the end, as an agent would."""
    evaluations: list[dict] = []
    for _ in range(200):
        result = suggest(_request(evaluations=evaluations, **over))
        if result.status != "needs_evaluation":
            return result, evaluations
        x = result.next_x
        value, feasible = objective(x)
        evaluations.append({"x": x, "objective": value, "feasible": feasible, "source": "fixture"})
    raise AssertionError("never finished")


def test_golden_section_finds_a_unimodal_minimum() -> None:
    result, evaluations = _run(lambda x: ((x - 3.7) ** 2 + 60.0, True))
    assert result.status == "converged"
    assert result.best_x == pytest.approx(3.7, abs=0.02)
    assert result.best_objective == pytest.approx(60.0, abs=1e-3)
    assert len(evaluations) < 30  # far fewer than a fine grid


def test_maximize() -> None:
    result, _ = _run(lambda x: (-((x - 6.2) ** 2), True), direction="maximize")
    assert result.best_x == pytest.approx(6.2, abs=0.02)


def test_the_same_evaluations_give_the_same_next_point() -> None:
    evaluations = [{"x": 4.055728, "objective": 61.0}]
    first = suggest(_request(evaluations=evaluations))
    second = suggest(_request(evaluations=evaluations))
    assert first == second and first.status == "needs_evaluation"


def test_an_infeasible_point_is_never_the_best() -> None:
    # Below 3 mm the fins cannot be machined: those points fail their checks.
    result, _ = _run(lambda x: ((x - 2.0) ** 2, x >= 3.0))
    assert result.best_x is not None and result.best_x >= 3.0


def test_all_infeasible_is_reported_not_hidden() -> None:
    result, _ = _run(lambda x: (1.0, False))
    assert result.status == "infeasible"
    assert result.best_x is None


def test_a_budget_of_evaluations_is_respected() -> None:
    result, evaluations = _run(lambda x: ((x - 3.7) ** 2, True), max_evaluations=6)
    assert result.status == "exhausted"
    assert len(evaluations) <= 6
    assert result.best_x is not None


def test_grid_evaluates_every_point_and_picks_the_best() -> None:
    result, evaluations = _run(lambda x: (abs(x - 5.0), True), method="grid", points=9)
    assert result.status == "converged"
    assert len(evaluations) == 9
    assert result.best_x == pytest.approx(5.0)


@pytest.mark.parametrize(
    "variable",
    [
        {"name": "x", "lower": 2.0, "upper": 1.0, "bounds_source": "s"},
        {"name": "x", "lower": 0.0, "upper": float("inf"), "bounds_source": "s"},
        {"name": "x", "lower": 0.0, "upper": 1.0, "bounds_source": ""},
    ],
)
def test_bad_bounds_are_refused(variable: dict) -> None:
    with pytest.raises(ValidationError):
        SearchRequest.model_validate({"variable": variable, "objective_name": "f"})


@pytest.mark.asyncio
async def test_the_adapter_serves_the_tool() -> None:
    server = OptimizeServer()
    assert "optimize.suggest" in server.tool_ids
    out = await server.suggest(_request().model_dump())
    assert out["status"] == "needs_evaluation" and out["next_x"] is not None
