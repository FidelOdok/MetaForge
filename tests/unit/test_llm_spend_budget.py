"""Spend budgets for model calls: attribution and the seam (FORGE-873).

Network-free. A fake invoke stands in for the provider; the real pipeline,
usage store and auth middleware are exercised.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.auth import AuthMiddleware, Principal
from orchestrator.harness.providers import ProviderSpec
from orchestrator.harness.providers import budget as budget_mod
from orchestrator.harness.providers.budget import (
    BudgetConfigurationError,
    BudgetExceeded,
    BudgetUnavailable,
    active_policy,
    configure_budget_policy,
    reset_budget_policy,
)
from orchestrator.harness.providers.pipeline import ProviderPipeline, RoleModelSlots
from orchestrator.harness.providers.usage import (
    UsageContext,
    UsageEvent,
    UsageStore,
    configure_usage_store,
    current_usage_context,
    usage_scope,
)

REQ: dict[str, Any] = {"messages": [{"role": "user", "content": "hi"}]}
USAGE = {"input_tokens": 10, "output_tokens": 5}


@pytest.fixture()
def store() -> Iterator[UsageStore]:
    s = UsageStore(":memory:")
    configure_usage_store(s)
    yield s
    configure_usage_store(None)


@pytest.fixture(autouse=True)
def _no_policy() -> Iterator[None]:
    configure_budget_policy(None)
    yield
    reset_budget_policy()


class RecordingPolicy:
    """Allows until ``limit`` calls were recorded for a project, then refuses."""

    name = "recording"

    def __init__(self, limit: int = 1_000) -> None:
        self.limit = limit
        self.checked: list[UsageContext] = []
        self.recorded: list[UsageEvent] = []

    async def check(self, ctx: UsageContext) -> None:
        self.checked.append(ctx)
        spent = sum(1 for e in self.recorded if e.project_id == ctx.project_id)
        if spent >= self.limit:
            raise BudgetExceeded("Monthly AI budget reached for this account.", scope="account")

    async def record(self, event: UsageEvent) -> None:
        self.recorded.append(event)


class _Calls:
    def __init__(self) -> None:
        self.n = 0

    async def invoke(self, spec: ProviderSpec, request: Any) -> Any:
        self.n += 1
        return {"content": "ok", "usage": USAGE}


def _pipe(*names: str) -> ProviderPipeline:
    specs = [ProviderSpec(name=n, model="claude-sonnet-5") for n in names]
    return ProviderPipeline(RoleModelSlots({"generator": specs}))


# -- attribution ----------------------------------------------------------


def test_scope_carries_project_and_subject_and_inherits() -> None:
    with usage_scope(project_id="p-1", subject="user-1"):
        with usage_scope(run_id="r-1", role="phase_brain"):
            ctx = current_usage_context()
    assert (ctx.project_id, ctx.subject, ctx.run_id) == ("p-1", "user-1", "r-1")
    assert current_usage_context().project_id is None


async def test_recorded_call_carries_project_and_subject(store: UsageStore) -> None:
    calls = _Calls()
    with usage_scope(project_id="p-1", subject="user-1", run_id="r-1"):
        await _pipe("anthropic").complete("generator", REQ, calls.invoke)
    [event] = store._events("WHERE run_id = ?", ("r-1",))  # noqa: SLF001
    assert (event.project_id, event.subject) == ("p-1", "user-1")


def test_old_usage_file_gains_the_attribution_columns(tmp_path: Path) -> None:
    path = tmp_path / "llm_usage.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
        " run_id TEXT, phase TEXT, role TEXT NOT NULL, caller TEXT, provider TEXT NOT NULL,"
        " model TEXT NOT NULL, prompt_tokens INTEGER, completion_tokens INTEGER,"
        " cached_input_tokens INTEGER, cost_usd REAL)"
    )
    conn.execute(
        "INSERT INTO llm_usage (ts, run_id, role, provider, model)"
        " VALUES (1, 'old', 'chat', 'x', 'y')"
    )
    conn.commit()
    conn.close()

    s = UsageStore(str(path))
    try:
        [old] = s._events("WHERE run_id = ?", ("old",))  # noqa: SLF001
        assert old.project_id is None
        cols = {r[1] for r in s._conn.execute("PRAGMA table_info(llm_usage)")}  # noqa: SLF001
        assert {"project_id", "subject"} <= cols
    finally:
        s.close()


# -- the seam -------------------------------------------------------------


async def test_without_a_policy_calls_go_through(store: UsageStore) -> None:
    calls = _Calls()
    await _pipe("anthropic").complete("generator", REQ, calls.invoke)
    assert calls.n == 1


async def test_policy_sees_every_call_and_its_cost(store: UsageStore) -> None:
    policy = RecordingPolicy()
    configure_budget_policy(policy)
    calls = _Calls()
    with usage_scope(project_id="p-1", subject="user-1"):
        await _pipe("anthropic").complete("generator", REQ, calls.invoke)
    assert [c.project_id for c in policy.checked] == ["p-1"]
    assert [(e.project_id, e.prompt_tokens) for e in policy.recorded] == [("p-1", 10)]


async def test_over_budget_is_refused_before_any_provider_is_tried(store: UsageStore) -> None:
    configure_budget_policy(RecordingPolicy(limit=0))
    calls = _Calls()
    with usage_scope(project_id="p-1"), pytest.raises(BudgetExceeded, match="budget reached"):
        await _pipe("anthropic", "openai").complete("generator", REQ, calls.invoke)
    # Not retried, and not handed to the fallback provider either.
    assert calls.n == 0


async def test_budget_is_per_project(store: UsageStore) -> None:
    configure_budget_policy(RecordingPolicy(limit=1))
    calls = _Calls()
    pipe = _pipe("anthropic")
    with usage_scope(project_id="p-1"):
        await pipe.complete("generator", REQ, calls.invoke)
        with pytest.raises(BudgetExceeded):
            await pipe.complete("generator", REQ, calls.invoke)
    with usage_scope(project_id="p-2"):
        await pipe.complete("generator", REQ, calls.invoke)
    assert calls.n == 2


async def test_streaming_paths_are_checked_too(store: UsageStore) -> None:
    configure_budget_policy(RecordingPolicy(limit=0))

    async def stream(spec: ProviderSpec, request: Any):  # type: ignore[no-untyped-def]
        yield "never"

    pipe = _pipe("anthropic")
    with usage_scope(project_id="p-1"):
        with pytest.raises(BudgetExceeded):
            async for _ in pipe.stream_complete("generator", REQ, stream):
                pass
        with pytest.raises(BudgetExceeded):
            async for _ in pipe.stream_events_complete("generator", REQ, stream):
                pass


async def test_a_broken_policy_fails_closed(store: UsageStore) -> None:
    class Broken(RecordingPolicy):
        async def check(self, ctx: UsageContext) -> None:
            raise ConnectionError("database down")

    configure_budget_policy(Broken())
    calls = _Calls()
    with pytest.raises(BudgetUnavailable):
        await _pipe("anthropic").complete("generator", REQ, calls.invoke)
    assert calls.n == 0


async def test_a_failing_record_never_breaks_the_call(store: UsageStore) -> None:
    class BadRecord(RecordingPolicy):
        async def record(self, event: UsageEvent) -> None:
            raise RuntimeError("write failed")

    configure_budget_policy(BadRecord())
    calls = _Calls()
    result = await _pipe("anthropic").complete("generator", REQ, calls.invoke)
    assert result["content"] == "ok"


# -- configuration ----------------------------------------------------------


@pytest.mark.parametrize("mode", ["", "off", "none", "OFF"])
def test_off_means_no_policy(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    reset_budget_policy()
    monkeypatch.setenv(budget_mod.BUDGET_MODE_ENV, mode)
    assert active_policy() is None


def test_a_named_mode_that_is_not_installed_refuses_to_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_budget_policy()
    monkeypatch.setenv(budget_mod.BUDGET_MODE_ENV, "no-such-budget")
    with pytest.raises(BudgetConfigurationError, match="no-such-budget"):
        active_policy()


def test_the_gateway_refuses_to_start_with_a_missing_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_gateway.server import create_app

    reset_budget_policy()
    monkeypatch.setenv(budget_mod.BUDGET_MODE_ENV, "no-such-budget")
    with pytest.raises(BudgetConfigurationError):
        create_app()


# -- the gateway sets attribution per request -------------------------------


class _Provider:
    name = "fake"

    async def verify(self, token: str) -> Principal:
        return Principal(subject="user-42")

    async def aclose(self) -> None:
        return None


def test_requests_attribute_model_calls_to_the_verified_caller() -> None:
    app = FastAPI()

    @app.get("/v1/probe")
    async def probe() -> dict[str, str | None]:
        ctx = current_usage_context()
        return {"subject": ctx.subject, "project_id": ctx.project_id}

    app.add_middleware(AuthMiddleware, provider=_Provider())
    body = TestClient(app).get("/v1/probe", headers={"Authorization": "Bearer t"}).json()
    # A provider without project scope resolves no project; the caller is known.
    assert body == {"subject": "user-42", "project_id": None}
