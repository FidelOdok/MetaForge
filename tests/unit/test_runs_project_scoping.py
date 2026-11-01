"""Runs and approvals are listed per project.

Both lists were unscoped: `/runs` and the approvals queue showed every
project's work together, and the project a run belonged to existed only
inside its untyped `request` blob -- so there was nothing on a row to tell
them apart. On the dev gateway 12 of 17 runs carried one and 5 did not.

The part worth being careful about is the 5. Filtering them out silently is
the failure this codebase keeps producing: something disappears and the
absence reads as "there are none" rather than "these are hidden". So the
count comes back with the list and the pages say it.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.runs.schemas import RunResponse, filter_by_project, project_of
from orchestrator.harness.runs import Run, RunStatus

_P1 = "11111111-1111-1111-1111-111111111111"
_P2 = "22222222-2222-2222-2222-222222222222"


def _run(run_id: str, request: dict[str, Any]) -> Run:
    return Run(
        id=run_id,
        status=RunStatus.AWAITING_APPROVAL,
        request=request,
        created_at=0.0,
        updated_at=0.0,
    )


class TestWhereTheProjectLives:
    def test_a_design_flow_run_carries_it_at_the_top(self) -> None:
        assert project_of({"flow": "hardware_v1", "goal": "x", "project_id": _P1}) == _P1

    def test_a_held_tool_call_carries_it_in_the_arguments(self) -> None:
        """The two shapes seen in practice. Both are the same fact, so both
        resolve in one place rather than at each call site."""
        assert project_of({"tool": "twin.record_decision", "arguments": {"project_id": _P1}}) == _P1

    def test_an_explicit_project_wins_over_a_nested_one(self) -> None:
        assert project_of({"project_id": _P1, "arguments": {"project_id": _P2}}) == _P1

    @pytest.mark.parametrize(
        "request_body",
        [
            {},
            {"goal": "no project here"},
            {"project_id": ""},
            {"project_id": None},
            {"arguments": {}},
            {"arguments": "not a dict"},
        ],
    )
    def test_no_project_is_none_rather_than_a_guess(self, request_body: dict[str, Any]) -> None:
        assert project_of(request_body) is None


class TestFiltering:
    def test_it_returns_only_that_projects_runs(self) -> None:
        runs = [
            _run("a", {"project_id": _P1}),
            _run("b", {"project_id": _P2}),
            _run("c", {"arguments": {"project_id": _P1}}),
        ]
        matching, _ = filter_by_project(runs, _P1)
        assert [r.id for r in matching] == ["a", "c"]

    def test_unscoped_runs_are_counted_not_dropped_quietly(self) -> None:
        """The whole point. Five such runs exist on the dev gateway, and a
        reviewer who selects a project should be told they are not looking at
        them -- not left with a shorter list and no explanation."""
        runs = [
            _run("a", {"project_id": _P1}),
            _run("b", {"goal": "no project"}),
            _run("c", {}),
        ]
        matching, unscoped = filter_by_project(runs, _P1)
        assert [r.id for r in matching] == ["a"]
        assert unscoped == 2

    def test_no_project_asked_for_means_everything_and_no_count(self) -> None:
        """An unscoped listing is not hiding anything, so reporting a count
        would invite a reader to look for runs that are already on screen."""
        runs = [_run("a", {"project_id": _P1}), _run("b", {})]
        matching, unscoped = filter_by_project(runs, None)
        assert len(matching) == 2
        assert unscoped == 0

    def test_a_project_with_no_runs_is_empty_not_everything(self) -> None:
        """The failure mode in the other direction: a filter that matches
        nothing falling back to the full list is worse than an empty one,
        because it looks like it worked."""
        matching, _ = filter_by_project([_run("a", {"project_id": _P1})], _P2)
        assert matching == []


class TestTheResponseExposesIt:
    def test_project_id_is_a_field_not_a_dig_through_the_blob(self) -> None:
        response = RunResponse.from_run(_run("a", {"project_id": _P1}))
        assert response.project_id == _P1

    def test_it_resolves_the_nested_shape_too(self) -> None:
        response = RunResponse.from_run(_run("a", {"arguments": {"project_id": _P2}}))
        assert response.project_id == _P2

    def test_a_run_with_no_project_says_none(self) -> None:
        assert RunResponse.from_run(_run("a", {"goal": "x"})).project_id is None

    def test_the_request_is_still_there_unchanged(self) -> None:
        """Promoting the field must not quietly reshape what callers already
        read."""
        body = {"project_id": _P1, "goal": "x"}
        assert RunResponse.from_run(_run("a", body)).request == body


@pytest.mark.asyncio
class TestTheRoutesAcceptIt:
    """Through the real endpoints, because the filter being right is not the
    same as the query parameter reaching it."""

    @pytest.fixture
    def client(self) -> Any:
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from api_gateway.runs.routes import _store, router

        for existing in list(_store.list()):
            _store._runs.pop(existing.id, None)  # type: ignore[attr-defined]
        for run in (
            _run("scoped-a", {"project_id": _P1, "goal": "a"}),
            _run("scoped-b", {"project_id": _P2, "goal": "b"}),
            _run("unscoped", {"goal": "c"}),
        ):
            _store._runs[run.id] = run  # type: ignore[attr-defined]

        app = FastAPI()
        app.include_router(router)
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def test_unfiltered_lists_everything(self, client: Any) -> None:
        body = (await client.get("/v1/runs")).json()
        assert {r["id"] for r in body["runs"]} == {"scoped-a", "scoped-b", "unscoped"}
        assert body["unscoped_count"] == 0

    async def test_filtered_lists_one_project_and_counts_the_rest(self, client: Any) -> None:
        body = (await client.get(f"/v1/runs?project_id={_P1}")).json()
        assert [r["id"] for r in body["runs"]] == ["scoped-a"]
        assert body["unscoped_count"] == 1

    async def test_each_row_carries_its_project(self, client: Any) -> None:
        body = (await client.get("/v1/runs")).json()
        by_id = {r["id"]: r["project_id"] for r in body["runs"]}
        assert by_id == {"scoped-a": _P1, "scoped-b": _P2, "unscoped": None}
