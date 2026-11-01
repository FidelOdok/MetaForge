"""Sessions say what they left out (gap 2 of the project-scoping audit).

`list_sessions` already filtered correctly, and its own comment admitted the
consequence: "Workflow runs have no project -- only include them in the
unscoped view." They were excluded and never mentioned, so a project with no
external sessions showed an empty page whether that meant "nothing ran" or
"everything that ran was an internal workflow run".

Same treatment the runs list got: count them, return the count, say it.
"""

from __future__ import annotations

from typing import Any

import pytest

_P1 = "11111111-1111-1111-1111-111111111111"


class _Engine:
    """Internal Temporal runs. These never carry a project."""

    def __init__(self, count: int) -> None:
        self._count = count

    async def list_runs(self) -> list[Any]:
        from types import SimpleNamespace

        return [
            SimpleNamespace(
                id=f"wf_{i}",
                status="running",
                started_at=f"2026-10-0{i + 1}T00:00:00Z",
                agent_code="orchestrator",
                goal="internal",
            )
            for i in range(self._count)
        ]


@pytest.fixture
def app_with(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build the sessions app with N internal runs and no external store."""

    def _build(internal: int) -> Any:
        from fastapi import FastAPI

        from api_gateway.sessions import routes

        # No external session store: isolates the internal-run behaviour,
        # which is the only thing this file is about.
        monkeypatch.setattr(routes, "_store", lambda _request: None)
        monkeypatch.setattr(
            routes,
            "_run_to_session",
            lambda run: routes.SessionResponse(
                id=run.id,
                agent_code=run.agent_code,
                task_type="workflow",
                status=run.status,
                started_at=run.started_at,
            ),
        )
        app = FastAPI()
        app.include_router(routes.router)
        app.state.workflow_engine = _Engine(internal)
        return app

    return _build


@pytest.mark.asyncio
class TestExcludedRunsAreCounted:
    async def _get(self, app: Any, query: str = "") -> dict[str, Any]:
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return dict((await client.get(f"/v1/sessions{query}")).json())

    async def test_unscoped_listing_includes_them_and_counts_nothing(self, app_with: Any) -> None:
        body = await self._get(app_with(3))
        assert body["total"] == 3
        assert body["unscoped_count"] == 0

    async def test_scoped_listing_excludes_them_and_says_how_many(self, app_with: Any) -> None:
        """The gap. Three internal runs vanished and the page said nothing."""
        body = await self._get(app_with(3), f"?project_id={_P1}")
        assert body["sessions"] == []
        assert body["unscoped_count"] == 3

    async def test_nothing_to_exclude_reports_zero_not_absence(self, app_with: Any) -> None:
        """Zero is a real answer. A missing key would make "none were hidden"
        and "this build does not report it" look the same."""
        body = await self._get(app_with(0), f"?project_id={_P1}")
        assert body["unscoped_count"] == 0

    async def test_the_count_is_not_added_to_the_total(self, app_with: Any) -> None:
        """`total` must keep meaning "rows on this page". Folding the hidden
        ones in would make the number disagree with the list under it."""
        body = await self._get(app_with(4), f"?project_id={_P1}")
        assert body["total"] == len(body["sessions"]) == 0
        assert body["unscoped_count"] == 4
