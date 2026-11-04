"""Slow tool calls return a job that is polled (FORGE-783).

A client's MCP call times out (Claude Code at 120 s) before a ~150 s Tripo
generation ends, so ``twin.generate_concept_mesh`` returns at once with a job
and runs the paid generation in the background; ``twin.job_status`` reads the
job, the next reply to the same session says it finished, and an identical
call while it runs (or soon after it succeeded) returns the same job instead
of paying twice. Service callers still wait. Nothing here calls Tripo.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from mcp_core.annotations import annotations_for
from mcp_core.context import McpCallContext, with_context
from metaforge.mcp.server import UnifiedMcpServer
from tests.unit.test_concept_mesh import (  # noqa: F401 - _outcomes is an autouse fixture
    Clock,
    FakeTripo,
    Harness,
    _generator,
    _outcomes,
)
from tool_registry import jobs
from tool_registry.jobs import (
    INTERRUPTED_ERROR,
    JobRecord,
    JobRunner,
    JobStore,
    arguments_hash,
    set_default_job_runner,
)
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

SESSION = uuid.UUID("11111111-2222-3333-4444-555555555555")
OTHER = uuid.UUID("99999999-2222-3333-4444-555555555555")
TOOL = "twin.generate_concept_mesh"


@pytest.fixture(autouse=True)
def runner() -> Any:
    r = JobRunner(JobStore(":memory:"))
    set_default_job_runner(r)
    yield r
    set_default_job_runner(None)


def _client(session: uuid.UUID = SESSION, **kw: Any) -> McpCallContext:
    return McpCallContext(session_id=session, **kw)


class Gated:
    """A mesh generator that holds the paid call until released."""

    def __init__(self, fake: FakeTripo) -> None:
        self.gate = asyncio.Event()
        self.calls = 0
        self._real = _generator(fake, Clock())

    async def __call__(self, image: Any, mime: str, prompt: Any, **kw: Any) -> Any:
        self.calls += 1
        await self.gate.wait()
        return await self._real(image, mime, prompt, **kw)


def _gated(fake: FakeTripo | None = None) -> tuple[Harness, Gated]:
    fake = fake or FakeTripo(statuses=["success"])
    gen = Gated(fake)
    return Harness(fake, generator=gen), gen


def _generations(h: Harness) -> int:
    return sum(1 for p in h.fake.paths() if p.startswith("POST /v3/generation/"))


# -- the tool returns a job ---------------------------------------------------------


async def test_a_client_call_returns_a_job_at_once(runner: JobRunner) -> None:
    h, gen = _gated()
    with with_context(_client()):
        out = await h.server.generate_concept_mesh({"prompt": "a microwave"})
    assert set(out) >= {"job_id", "status", "poll", "expected_seconds"}
    assert out["status"] == "running"
    assert out["poll"] == "twin.job_status"
    assert out["expected_seconds"] == 150
    assert out["reused"] is False
    assert "node_id" not in out
    # Nothing paid for yet: the generation waits in the background.
    assert h.fake.requests == []
    job = runner.get(out["job_id"])
    assert job is not None and job.session_id == str(SESSION) and not job.done
    assert job.arguments_hash == arguments_hash({"prompt": "a microwave"})
    gen.gate.set()
    await runner.wait(out["job_id"], timeout=5)


async def test_the_job_completes_and_stores_the_result(runner: JobRunner) -> None:
    h, gen = _gated()
    with with_context(_client()):
        out = await h.server.generate_concept_mesh({"prompt": "a microwave"})
    gen.gate.set()
    job = await runner.wait(out["job_id"], timeout=5)
    assert job is not None and job.status == "succeeded"
    assert job.result is not None and job.result["node_id"]
    assert job.started_at is not None and job.finished_at is not None
    assert job.error is None
    assert [str(wp.id) for wp in await h.meshes()] == [job.result["node_id"]]


async def test_job_status_reads_the_job(runner: JobRunner) -> None:
    h, gen = _gated()
    with with_context(_client()):
        out = await h.server.generate_concept_mesh({"prompt": "a microwave"})
        running = await h.server.job_status({"job_id": out["job_id"]})
    assert running["status"] in {"queued", "running"}
    assert running["tool_id"] == TOOL and running["result"] is None
    gen.gate.set()
    await runner.wait(out["job_id"], timeout=5)
    with with_context(_client()):
        done = await h.server.job_status({"job_id": out["job_id"]})
    assert done["status"] == "succeeded"
    assert done["result"]["node_id"]
    assert done["finished_at"] and done["elapsed_seconds"] >= 0
    with pytest.raises(ValueError, match="no job"):
        await h.server.job_status({"job_id": "not-a-job"})


def test_job_status_is_a_registered_read_only_tool() -> None:
    h = Harness()
    assert "twin.job_status" in h.server._tools  # noqa: SLF001
    manifest = h.server._tools["twin.job_status"].manifest  # noqa: SLF001
    assert manifest.input_schema["required"] == ["job_id"]
    hints = annotations_for("twin.job_status")
    assert hints["readOnlyHint"] is True and hints["destructiveHint"] is False
    assert "wait" in h.server._tools[TOOL].manifest.input_schema["properties"]  # noqa: SLF001


async def test_an_identical_call_returns_the_same_job(runner: JobRunner) -> None:
    h, gen = _gated()
    args = {"prompt": "a microwave", "face_limit": 20_000}
    with with_context(_client()):
        first = await h.server.generate_concept_mesh(dict(args))
        again = await h.server.generate_concept_mesh(dict(reversed(list(args.items()))))
    assert again["job_id"] == first["job_id"] and again["reused"] is True
    gen.gate.set()
    await runner.wait(first["job_id"], timeout=5)
    # Within ten minutes of success, still the same job, now with its result.
    with with_context(_client()):
        after = await h.server.generate_concept_mesh(dict(args))
    assert after["job_id"] == first["job_id"]
    assert after["status"] == "succeeded" and after["result"]["node_id"]
    assert gen.calls == 1 and _generations(h) == 1
    # Another session, or other arguments, is another job.
    with with_context(_client(OTHER)):
        other = await h.server.generate_concept_mesh(dict(args))
    with with_context(_client()):
        changed = await h.server.generate_concept_mesh({"prompt": "a toaster"})
    assert len({first["job_id"], other["job_id"], changed["job_id"]}) == 3
    await runner.wait(other["job_id"], timeout=5)
    await runner.wait(changed["job_id"], timeout=5)


async def test_a_succeeded_job_is_not_reused_after_the_window() -> None:
    now = [1000.0]
    runner = JobRunner(JobStore(":memory:"), clock=lambda: now[0])

    async def work() -> dict[str, Any]:
        return {"node_id": "n-1"}

    job, _ = await runner.start(TOOL, {"prompt": "x"}, work, session_id="s")
    await runner.wait(job.job_id, timeout=5)
    now[0] += 601
    again, reused = await runner.start(TOOL, {"prompt": "x"}, work, session_id="s")
    assert not reused and again.job_id != job.job_id
    await runner.wait(again.job_id, timeout=5)


async def test_a_failure_is_recorded_on_the_job(runner: JobRunner) -> None:
    h = Harness(FakeTripo(statuses=["failed"]))
    with with_context(_client()):
        out = await h.server.generate_concept_mesh({"prompt": "a microwave"})
    job = await runner.wait(out["job_id"], timeout=5)
    assert job is not None and job.status == "failed"
    assert job.error is not None and "ended failed" in job.error
    assert job.result is None
    assert await h.meshes() == []
    # A failed job is not reused: the next identical call tries again.
    with with_context(_client()):
        retry = await h.server.generate_concept_mesh({"prompt": "a microwave"})
    assert retry["job_id"] != out["job_id"]
    await runner.wait(retry["job_id"], timeout=5)


async def test_a_bad_call_is_refused_at_once_and_starts_no_job(runner: JobRunner) -> None:
    h = Harness()
    with with_context(_client()), pytest.raises(ValueError, match="not a node id"):
        await h.server.generate_concept_mesh({"image_node_id": "nope"})
    with with_context(_client()), pytest.raises(ValueError, match="'wait'"):
        await h.server.generate_concept_mesh({"prompt": "x", "wait": "yes"})
    assert runner._tasks == {}  # noqa: SLF001
    assert h.fake.requests == []


# -- restarts --------------------------------------------------------------------------


async def test_a_restart_marks_running_jobs_interrupted(tmp_path: Path) -> None:
    path = tmp_path / "jobs.db"
    before = JobRunner(JobStore(path))
    never = asyncio.Event()

    async def slow() -> dict[str, Any]:
        await never.wait()
        return {}

    async def quick() -> dict[str, Any]:
        return {"node_id": "n-1"}

    running, _ = await before.start(TOOL, {"prompt": "slow"}, slow, session_id="s")
    finished, _ = await before.start(TOOL, {"prompt": "quick"}, quick, session_id="s")
    await before.wait(finished.job_id, timeout=5)
    await asyncio.sleep(0)

    # The process restarts: a new store on the same file.
    after = JobStore(path)
    assert after.interrupted_on_open == [running.job_id]
    job = after.get(running.job_id)
    assert job is not None and job.status == "interrupted"
    assert job.error == INTERRUPTED_ERROR and job.finished_at is not None
    kept = after.get(finished.job_id)
    assert kept is not None and kept.status == "succeeded" and kept.result == {"node_id": "n-1"}

    # And twin.job_status after the restart says so.
    set_default_job_runner(JobRunner(after))
    h = Harness()
    read = await h.server.job_status({"job_id": running.job_id})
    assert read["status"] == "interrupted"

    task = before._tasks[running.job_id]  # noqa: SLF001
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# -- telling the session -----------------------------------------------------------


class _Adapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.register_tool(
            ToolManifest(
                tool_id="twin.get_node",
                adapter_id="twin",
                name="twin.get_node",
                description="stub",
                capability="test",
            ),
            self._ok,
        )

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"node_id": "n-1"}


async def _reply(server: UnifiedMcpServer, session: uuid.UUID) -> dict[str, Any]:
    message = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {"name": "twin.get_node", "arguments": {"node_id": "n-1"}},
    }
    with with_context(_client(session)):
        raw = await server.handle_request(json.dumps(message))
    result: dict[str, Any] = json.loads(raw)["result"]
    return result


def _texts(result: dict[str, Any]) -> list[str]:
    return [c["text"] for c in result["content"] if c.get("type") == "text"]


async def test_a_finished_job_is_told_to_its_session_once(runner: JobRunner) -> None:
    server = UnifiedMcpServer([_Adapter()])

    async def work() -> dict[str, Any]:
        return {"node_id": "n-9"}

    async def broken() -> dict[str, Any]:
        raise RuntimeError("tripo said no")

    ok, _ = await runner.start(TOOL, {"prompt": "a"}, work, session_id=str(SESSION))
    bad, _ = await runner.start(TOOL, {"prompt": "b"}, broken, session_id=str(SESSION))
    await runner.wait(ok.job_id, timeout=5)
    await runner.wait(bad.job_id, timeout=5)

    # Another session hears nothing of them.
    assert len(_texts(await _reply(server, OTHER))) == 1

    told = await _reply(server, SESSION)
    texts = _texts(told)
    assert len(texts) == 2
    assert f"job {ok.job_id} for {TOOL} succeeded (node n-9)" in texts[1]
    assert f"job {bad.job_id} for {TOOL} failed: RuntimeError: tripo said no" in texts[1]
    assert {j["jobId"] for j in told["_meta"]["finishedJobs"]} == {ok.job_id, bad.job_id}

    # Said once.
    assert len(_texts(await _reply(server, SESSION))) == 1


async def test_a_job_read_with_job_status_is_not_repeated_as_a_notice(
    runner: JobRunner,
) -> None:
    server = UnifiedMcpServer([_Adapter()])

    async def work() -> dict[str, Any]:
        return {"node_id": "n-9"}

    job, _ = await runner.start(TOOL, {"prompt": "a"}, work, session_id=str(SESSION))
    await runner.wait(job.job_id, timeout=5)
    h = Harness()
    with with_context(_client()):
        await h.server.job_status({"job_id": job.job_id})
    assert len(_texts(await _reply(server, SESSION))) == 1


async def test_no_runner_means_no_notice_and_nothing_is_opened() -> None:
    set_default_job_runner(None)
    server = UnifiedMcpServer([_Adapter()])
    assert len(_texts(await _reply(server, SESSION))) == 1
    assert jobs.active_job_runner() is None


# -- who waits ------------------------------------------------------------------------


async def test_wait_true_behaves_as_before(runner: JobRunner) -> None:
    h = Harness(FakeTripo(statuses=["success"]))
    with with_context(_client()):
        out = await h.server.generate_concept_mesh({"prompt": "a microwave", "wait": True})
    assert out["node_id"] and "job_id" not in out
    assert runner._tasks == {}  # noqa: SLF001
    found = runner.store.find_reusable(
        TOOL, arguments_hash({"prompt": "a microwave"}), str(SESSION), None, since=0
    )
    assert found is None


async def test_service_and_in_process_callers_wait_by_default(runner: JobRunner) -> None:
    h = Harness(FakeTripo(statuses=["success"]))
    # No MCP context at all: an in-process caller.
    out = await h.server.generate_concept_mesh({"prompt": "a microwave"})
    assert out["node_id"]
    # The design-flow worker: a call claiming a run.
    with with_context(_client(run_id="run-1")):
        out = await h.server.generate_concept_mesh({"prompt": "a toaster"})
    assert out["node_id"]
    # A verified service.
    with with_context(_client(service_verified=True)):
        out = await h.server.generate_concept_mesh({"prompt": "a kettle"})
    assert out["node_id"]
    assert runner._tasks == {}  # noqa: SLF001


async def test_a_client_can_ask_for_a_job_explicitly_without_a_context(
    runner: JobRunner,
) -> None:
    h, gen = _gated()
    out = await h.server.generate_concept_mesh({"prompt": "a microwave", "wait": False})
    assert out["status"] == "running"
    gen.gate.set()
    job = await runner.wait(out["job_id"], timeout=5)
    assert isinstance(job, JobRecord) and job.status == "succeeded"
