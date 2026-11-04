"""Held tool writes are durable approvals a person can actually answer (FORGE-780).

Observed on the sandbox: a write held outside a run task blocked the tool call
while the client's prompt waited, the client timed the call out at about
120 s, and an approval that came ten minutes later was recorded and never
applied. The retry opened a new hold, the dashboard could not decide the
inline one, and pressing Accept without ticking the form's box was recorded as
a rejection. A project set to ``auto`` still had every concept image held.

Driven against the real gateway routers over ASGI, as the hand-off tests are:
the ledger entry a reviewer and an auditor read is the thing under test.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from api_gateway.approvals.policy import ApprovalPolicy, PolicyStore, reset_policy_store
from mcp_core import elicitation
from mcp_core.approval_hold import approval_key, arguments_hash
from mcp_core.elicitation import (
    ElicitAction,
    ElicitResult,
    LateAnswerSink,
    current_late_answer_sink,
)
from mcp_core.guardrails import Caller, never_auto_approved, policy_may_approve
from metaforge.mcp import server as server_mod
from metaforge.mcp.remote_approvals import RemoteApprovalLedger, build_remote_approval_gate
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

LIST = "/v1/chat/tool_approvals"
APPROVALS = "/v1/approvals"
PROJECT = "p-drone"
OTHER = "p-other"

TOOLS = (
    "twin.commit_geometry",
    "twin.get_node",
    "twin.render_concept_image",
    "twin.generate_concept_mesh",
    "cadquery.create_parametric",
    "project.update",
    "project.create",
    "twin.attempt_promotion",
)


class _Metrics:
    """Counts what the ledger records, by outcome."""

    def __init__(self) -> None:
        self.outcomes: list[str] = []

    def record_tool_approval(self, outcome: str) -> None:
        self.outcomes.append(outcome)

    def record_tool_approval_resolution(self, *_: Any) -> None:
        pass


@pytest.fixture
def metrics() -> _Metrics:
    return _Metrics()


@pytest.fixture(autouse=True)
def _stores(monkeypatch: pytest.MonkeyPatch, metrics: _Metrics) -> Iterator[None]:
    from api_gateway.chat.tool_approvals import reset_approval_store, set_approval_metrics

    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MAX", "auto")
    monkeypatch.delenv("METAFORGE_TOOL_APPROVAL_TTL_S", raising=False)
    monkeypatch.delenv("METAFORGE_AUTO_APPROVE_IMAGE_PER_HOUR", raising=False)
    monkeypatch.setattr(server_mod, "HANDOFF_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(server_mod, "ELICITATION_WAIT_SECONDS", 0.05)
    # Answers in these tests are a person's, however fast they come back.
    monkeypatch.setattr(elicitation, "UNATTENDED_ANSWER_SECONDS", 0.0)
    reset_approval_store()
    reset_policy_store(PolicyStore())
    set_approval_metrics(metrics)  # type: ignore[arg-type]
    yield
    set_approval_metrics(None)
    reset_policy_store(None)
    reset_approval_store()


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    from fastapi import FastAPI

    from api_gateway.approvals.routes import router as approvals_router
    from api_gateway.chat.tool_approvals import router

    app = FastAPI()
    app.include_router(router)
    app.include_router(approvals_router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


def _adapter(ran: list[tuple[str, dict[str, Any]]]) -> McpToolServer:
    class _Spy(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="spy", version="0.1.0")
            for tool in TOOLS:
                self.register_tool(
                    ToolManifest(
                        tool_id=tool,
                        adapter_id="spy",
                        name=tool,
                        description="stub",
                        capability="test",
                    ),
                    self._handler(tool),
                )

        def _handler(self, tool: str) -> Any:
            async def handle(args: dict[str, Any]) -> dict[str, Any]:
                ran.append((tool, dict(args)))
                return {"ok": True}

            return handle

    return _Spy()


async def _server(
    http: httpx.AsyncClient,
    ran: list[tuple[str, dict[str, Any]]],
    *,
    elicitor: Any = None,
) -> UnifiedMcpServer:
    server = UnifiedMcpServer(
        [_adapter(ran)],
        caller=Caller.REMOTE,
        approval_gate=build_remote_approval_gate("http://gateway.test", client=http),
        approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
        elicitor=elicitor,
    )
    await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"elicitation": {}} if elicitor is not None else {},
                    "clientInfo": {"name": "claude-code", "version": "2.1.300"},
                },
            }
        )
    )
    return server


async def _call(
    server: UnifiedMcpServer, tool: str = "twin.commit_geometry", **arguments: Any
) -> dict[str, Any]:
    args = {"project_id": PROJECT, "obj_id": "bracket", **arguments}
    return json.loads(
        await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": args},
                }
            )
        )
    )


def _held(response: dict[str, Any]) -> dict[str, Any]:
    result = response["result"]
    assert result["isError"] is True, response
    data: dict[str, Any] = result["_meta"]["approval"]
    assert data["held"] is True
    return data


def _ran_ok(response: dict[str, Any]) -> bool:
    return "error" not in response and response["result"]["isError"] is False


def _text(response: dict[str, Any]) -> str:
    return " ".join(c.get("text", "") for c in response["result"]["content"])


async def _entries(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = (await http.get(LIST, params={"status": "all"})).json()["runs"]
    return runs


async def _approve(http: httpx.AsyncClient, approval_id: str, decision: str = "approve") -> None:
    answered = await http.post(f"{LIST}/{approval_id}", json={"decision": decision})
    assert answered.status_code == 200, answered.text


async def _policy(mode: str, project: str = PROJECT, *, expires_at: str | None = None) -> None:
    from api_gateway.approvals.policy import get_policy_store

    await get_policy_store().put(
        ApprovalPolicy(
            project_id=project, mode=mode, updated_by="user:fidel", expires_at=expires_at
        )
    )


async def _never_answers(message: str, schema: dict[str, Any]) -> ElicitResult:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


# -- the key ------------------------------------------------------------------


def test_the_key_is_canonical_and_covers_session_and_project() -> None:
    a = approval_key("t", {"b": 1, "a": [1, 2]}, session="s", project="p")
    b = approval_key("t", {"a": [1, 2], "b": 1, "_meta": {"x": 1}}, session="s", project="p")
    assert a == b
    assert approval_key("t", {"a": [1, 2], "b": 2}, session="s", project="p") != a
    assert approval_key("t", {"a": [1, 2], "b": 1}, session="s2", project="p") != a
    assert approval_key("t", {"a": [1, 2], "b": 1}, session="s", project="q") != a
    assert arguments_hash({"__approved_by__": "x", "a": 1}) == arguments_hash({"a": 1})


# -- the hold outlives the call -----------------------------------------------


@pytest.mark.asyncio
class TestTheHoldOutlivesTheCall:
    async def test_a_held_call_returns_at_once_with_held(
        self, gateway: httpx.AsyncClient, metrics: _Metrics
    ) -> None:
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran, elicitor=_never_answers)
            started = time.monotonic()
            response = await _call(server)
            elapsed = time.monotonic() - started
            [entry] = await _entries(http)

        assert ran == []
        assert elapsed < 5.0
        data = _held(response)
        assert data["approval_id"] == entry["id"]
        assert data["route"] == "elicitation"
        assert data["next_step"] == "retry this exact call once it is approved"
        assert data["expires_at"]
        assert data["expires_in_seconds"] > 1700  # 30 min default
        assert entry["status"] == "awaiting_approval"
        assert entry["request"]["approval_key"]
        assert entry["request"]["arguments_hash"] == arguments_hash(
            {"project_id": PROJECT, "obj_id": "bracket"}
        )
        assert "Tell the person" in _text(response)
        assert metrics.outcomes == ["held"]

    async def test_an_approval_after_the_call_returned_runs_the_identical_retry(
        self, gateway: httpx.AsyncClient, metrics: _Metrics
    ) -> None:
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran, elicitor=_never_answers)
            approval_id = _held(await _call(server))["approval_id"]
            await _approve(http, approval_id)
            again = await _call(server)
            [entry] = await _entries(http)

        assert _ran_ok(again), again
        assert [(t, a["obj_id"]) for t, a in ran] == [("twin.commit_geometry", "bracket")]
        assert again["result"]["_meta"]["approval"]["approvalId"] == approval_id
        assert entry["request"]["consumed_at"]
        assert entry["approved_by"] == "local:dashboard"
        assert metrics.outcomes == ["held", "approved_late_honoured"]

    async def test_a_late_answer_in_the_clients_prompt_lands_on_the_record(
        self, gateway: httpx.AsyncClient
    ) -> None:
        sinks: list[LateAnswerSink] = []

        async def keeps_the_prompt_open(message: str, schema: dict[str, Any]) -> ElicitResult:
            sink = current_late_answer_sink()
            assert sink is not None
            sinks.append(sink)
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran, elicitor=keeps_the_prompt_open)
            _held(await _call(server))
            # The person presses Accept after the call has returned.
            sinks[0](ElicitResult(ElicitAction.ACCEPT, {}))
            for _ in range(100):
                [entry] = await _entries(http)
                if entry["status"] != "awaiting_approval":
                    break
                await asyncio.sleep(0.02)
            again = await _call(server)

        assert entry["status"] == "completed"
        assert entry["approved_by"] == "local:elicitation"
        assert _ran_ok(again), again
        assert len(ran) == 1

    async def test_a_changed_argument_needs_a_new_approval(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            approval_id = _held(await _call(server))["approval_id"]
            await _approve(http, approval_id)
            other = await _call(server, obj_id="a_different_part")

        assert ran == []
        assert _held(other)["approval_id"] != approval_id

    async def test_an_approval_is_used_once(self, gateway: httpx.AsyncClient) -> None:
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            approval_id = _held(await _call(server))["approval_id"]
            await _approve(http, approval_id)
            assert _ran_ok(await _call(server))
            third = await _call(server)
            again = await http.post(f"{LIST}/{approval_id}/consume", json={})

        assert len(ran) == 1
        assert _held(third)["approval_id"] != approval_id
        assert again.status_code == 409

    async def test_an_approval_not_used_in_time_expires(
        self, gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, metrics: _Metrics
    ) -> None:
        monkeypatch.setenv("METAFORGE_TOOL_APPROVAL_TTL_S", "0.3")
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            approval_id = _held(await _call(server))["approval_id"]
            await _approve(http, approval_id)
            await asyncio.sleep(0.4)
            later = await _call(server)
            refused = await http.post(f"{LIST}/{approval_id}/consume", json={})

        assert ran == []
        assert _held(later)["approval_id"] != approval_id
        assert refused.status_code == 409
        assert metrics.outcomes == ["held", "expired", "held"]

    async def test_a_pending_hold_expires_and_the_retry_opens_a_new_one(
        self, gateway: httpx.AsyncClient, metrics: _Metrics
    ) -> None:
        from api_gateway.chat.tool_approvals import reset_approval_store

        now = [1_000_000.0]
        reset_approval_store(clock=lambda: now[0])
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            first = _held(await _call(server))["approval_id"]
            now[0] += 1800 + 31  # past the lifetime and the gateway's grace
            [entry] = await _entries(http)
            second = _held(await _call(server))["approval_id"]

        assert entry["status"] == "timed_out"
        assert second != first
        assert ran == []
        assert metrics.outcomes == ["held", "expired", "held"]


# -- decided from anywhere ----------------------------------------------------


@pytest.mark.asyncio
class TestDecideFromAnywhere:
    async def test_the_approvals_page_decides_an_inline_hold_and_first_wins(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran, elicitor=_never_answers)
            approval_id = _held(await _call(server))["approval_id"]
            item = (await http.get(f"{APPROVALS}/tool:{approval_id}")).json()
            page = await http.post(
                f"{APPROVALS}/tool:{approval_id}/decision", json={"decision": "approve"}
            )
            plugin = await http.post(
                f"{APPROVALS}/tool:{approval_id}/inline-decision", json={"decision": "reject"}
            )
            again = await _call(server)

        assert item["decidable"] is True
        assert set(item["allowed_decisions"]) == {"approve", "reject"}
        assert page.status_code == 200, page.text
        assert plugin.status_code == 409  # the first decision wins
        assert _ran_ok(again), again

    async def test_approvals_decide_first_and_the_page_is_refused(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            approval_id = _held(await _call(server))["approval_id"]
            plugin = await http.post(
                f"{APPROVALS}/tool:{approval_id}/inline-decision",
                json={"decision": "reject", "reason": "not now", "approver": "user:ada"},
            )
            page = await http.post(
                f"{APPROVALS}/tool:{approval_id}/decision", json={"decision": "approve"}
            )
            again = await _call(server)

        assert plugin.status_code == 200, plugin.text
        assert page.status_code == 409
        assert ran == []
        assert again["error"]["data"]["outcome"] == "rejected"

    async def test_an_agent_still_cannot_decide_a_human_authority_hold(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            approval_id = _held(await _call(server, "twin.attempt_promotion"))["approval_id"]
            agent = await http.post(
                f"{APPROVALS}/tool:{approval_id}/decision",
                json={"decision": "approve"},
                headers={"X-MetaForge-Surface": "agent", "X-MetaForge-On-Behalf-Of": "ada"},
            )
        assert agent.status_code == 403
        assert ran == []


# -- accept means approve -----------------------------------------------------


@pytest.mark.asyncio
async def test_accept_without_the_box_ticked_is_not_a_rejection(
    gateway: httpx.AsyncClient,
) -> None:
    seen: list[dict[str, Any]] = []

    async def accepts_unticked(message: str, schema: dict[str, Any]) -> ElicitResult:
        seen.append(schema)
        # What a client answering the old form sent for an unticked box.
        return ElicitResult(ElicitAction.ACCEPT, {"approve": False})

    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = await _server(http, ran, elicitor=accepts_unticked)
        response = await _call(server)
        [entry] = await _entries(http)

    assert "approve" not in seen[0]["properties"]
    assert _ran_ok(response), response
    assert entry["status"] == "completed"
    assert entry["approved_by"] == "local:elicitation"


# -- the agent is told --------------------------------------------------------


@pytest.mark.asyncio
async def test_the_next_reply_says_a_held_call_was_approved(gateway: httpx.AsyncClient) -> None:
    ran: list[tuple[str, dict[str, Any]]] = []
    async with gateway as http:
        server = await _server(http, ran)
        approval_id = _held(await _call(server))["approval_id"]
        before = await _call(server, "twin.get_node", node_id="n1")
        await _approve(http, approval_id)
        after = await _call(server, "twin.get_node", node_id="n1")
        later = await _call(server, "twin.get_node", node_id="n1")

    notice = (
        f"approval {approval_id} for twin.commit_geometry was approved; call it again "
        "with the same arguments"
    )
    assert notice not in _text(before)
    assert notice in _text(after)
    assert notice not in _text(later)  # said once


# -- the project's policy decides held writes ---------------------------------


def test_the_never_list() -> None:
    for tool in (
        "twin.attempt_promotion",
        "twin.confirm_sketch_reading",
        "project.update",
        "project.create",
        "flow.start_run",
        "run.start_design_flow",
        "vendor.place_order",
    ):
        assert never_auto_approved(tool), tool
        assert not policy_may_approve(tool, "auto"), tool
    assert policy_may_approve("twin.render_concept_image", "auto_clean")
    assert not policy_may_approve("cadquery.create_parametric", "auto_clean")
    assert policy_may_approve("cadquery.create_parametric", "auto")
    assert not policy_may_approve("twin.render_concept_image", "advise")


@pytest.mark.asyncio
class TestThePolicyDecidesHeldWrites:
    async def test_auto_clean_approves_a_render_and_holds_a_non_additive_write(
        self, gateway: httpx.AsyncClient
    ) -> None:
        await _policy("auto_clean")
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            render = await _call(server, "twin.render_concept_image", prompt="a drone")
            part = await _call(server, "cadquery.create_parametric", shape="box")

        assert _ran_ok(render), render
        assert render["result"]["_meta"]["approval"]["route"] == "policy"
        assert _held(part)
        assert [t for t, _ in ran] == ["twin.render_concept_image"]

    async def test_auto_approves_any_allowed_write_in_its_project(
        self, gateway: httpx.AsyncClient, metrics: _Metrics
    ) -> None:
        await _policy("auto")
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            part = await _call(server, "cadquery.create_parametric", shape="box")
            entries = await _entries(http)

        assert _ran_ok(part), part
        [entry] = entries
        # The audit: who decided, which tool, which arguments, used at once.
        assert entry["approved_by"] == f"policy:auto:{PROJECT}"
        assert entry["request"]["route"] == "policy"
        assert entry["request"]["tool"] == "cadquery.create_parametric"
        assert entry["request"]["arguments_hash"] == arguments_hash(
            {"project_id": PROJECT, "obj_id": "bracket", "shape": "box"}
        )
        assert entry["request"]["consumed_at"]
        assert metrics.outcomes == ["auto_approved"]

    async def test_an_auto_approval_is_listed_as_decided(self, gateway: httpx.AsyncClient) -> None:
        from api_gateway.approvals import service

        await _policy("auto")
        async with gateway as http:
            server = await _server(http, [])
            await _call(server, "twin.render_concept_image", prompt="a drone")
        from api_gateway.chat.tool_approvals import get_approval_store

        items = [service.tool_item(r) for r in get_approval_store().list()]
        assert [i.status for i in items] == ["approved"]
        assert items[0].decision is not None
        assert items[0].decision.approver == f"policy:auto:{PROJECT}"

    @pytest.mark.parametrize("mode", ["off", "advise", "auto_clean", "auto"])
    @pytest.mark.parametrize("tool", ["twin.attempt_promotion", "project.update", "project.create"])
    async def test_never_list_tools_are_held_in_every_mode(
        self, gateway: httpx.AsyncClient, mode: str, tool: str
    ) -> None:
        await _policy(mode)
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            response = await _call(server, tool)
            entries = await _entries(http)
        assert ran == []
        assert _held(response)
        assert [e["status"] for e in entries] == ["awaiting_approval"]

    async def test_the_spend_cap_holds_the_21st_image_in_an_hour(
        self, gateway: httpx.AsyncClient, metrics: _Metrics
    ) -> None:
        await _policy("auto")
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            for i in range(20):
                assert _ran_ok(await _call(server, "twin.render_concept_image", prompt=f"v{i}"))
            over = await _call(server, "twin.render_concept_image", prompt="v20")
            pending = (await http.get(LIST)).json()["runs"]

        assert len(ran) == 20
        data = _held(over)
        assert "spend cap" in data["note"]
        assert "spend cap" in pending[0]["approval_reason"]
        assert metrics.outcomes.count("auto_approved") == 20
        assert metrics.outcomes[-2:] == ["cap_held", "held"]

    async def test_the_mesh_cap_is_configurable(
        self, gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MESH_PER_HOUR", "1")
        await _policy("auto")
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            first = await _call(server, "twin.generate_concept_mesh", image="a")
            second = await _call(server, "twin.generate_concept_mesh", image="b")
        assert _ran_ok(first)
        assert _held(second)
        assert len(ran) == 1

    async def test_another_projects_policy_does_not_apply(self, gateway: httpx.AsyncClient) -> None:
        await _policy("auto", project=OTHER)
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            response = await _call(server, "twin.render_concept_image", prompt="a drone")
        assert ran == []
        assert _held(response)

    async def test_an_expired_auto_policy_no_longer_approves(
        self, gateway: httpx.AsyncClient
    ) -> None:
        ended = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        await _policy("auto", expires_at=ended)
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            response = await _call(server, "twin.render_concept_image", prompt="a drone")
        assert ran == []
        assert _held(response)

    async def test_the_operator_ceiling_caps_the_mode(
        self, gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MAX", "auto_clean")
        await _policy("auto")
        ran: list[tuple[str, dict[str, Any]]] = []
        async with gateway as http:
            server = await _server(http, ran)
            render = await _call(server, "twin.render_concept_image", prompt="a drone")
            part = await _call(server, "cadquery.create_parametric", shape="box")
            entries = await _entries(http)
        assert _ran_ok(render)
        assert _held(part)
        approved = [e for e in entries if e["approved_by"]]
        assert [e["approved_by"] for e in approved] == [f"policy:auto_clean:{PROJECT}"]


# -- restart -----------------------------------------------------------------


def test_a_pending_durable_hold_survives_a_gateway_restart(tmp_path: Any) -> None:
    from api_gateway.chat import tool_approvals as ta
    from orchestrator.harness.ledger import SqliteRunLedger

    ledger = SqliteRunLedger(str(tmp_path / "ledger.db"))
    ta.reset_approval_store()
    ta.init_approval_ledger(ledger)
    durable = ta.create_hold(
        tool="twin.commit_geometry",
        arguments={"obj_id": "bracket"},
        reason="writes",
        timeout_seconds=1800,
        approval_key="k1",
        arguments_hash="h1",
    )
    blocking = ta.create_hold(tool="twin.commit_geometry", arguments={}, reason="writes")

    ta.reset_approval_store()
    ta.init_approval_ledger(ledger)
    store = ta.get_approval_store()
    assert store.get(durable.id).status.value == "awaiting_approval"
    assert store.get(blocking.id).status.value == "failed"  # its waiter died with the process
    found = ta.find_live_hold("k1")
    assert found is not None and found.id == durable.id
    ta.reset_approval_store()


@pytest.mark.asyncio
async def test_the_http_hub_hands_a_late_answer_to_the_record() -> None:
    """A question withdrawn when the call returned still decides its approval."""
    from metaforge.mcp.http_elicitation import ElicitationHub

    hub = ElicitationHub(timeout_seconds=5.0)
    hub.note_initialize("s1", capabilities={"elicitation": {}}, negotiated_protocol="2025-06-18")
    got: list[ElicitResult] = []
    with hub.call_stream("s1") as stream:
        token = elicitation._late_answer_sink.set(got.append)
        try:
            asking = asyncio.ensure_future(hub.elicit("s1", "Run it?", {"type": "object"}))
        finally:
            elicitation._late_answer_sink.reset(token)
        await asyncio.sleep(0.01)
        question = json.loads(stream.queue.get_nowait())
        hub.close_call_stream(stream)  # the call returned; the question is withdrawn
        assert (await asking).action is ElicitAction.CANCEL

    late = {"jsonrpc": "2.0", "id": question["id"], "result": {"action": "accept", "content": {}}}
    assert hub.resolve(late) is True
    assert [r.action for r in got] == [ElicitAction.ACCEPT]
