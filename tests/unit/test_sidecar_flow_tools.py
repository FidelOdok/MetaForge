"""The sidecar serves flow.* and run.*, against the gateway's state (FORGE-462).

Every harness plugin talks to the HTTP sidecar (``python -m metaforge.mcp
--transport http``). The six design-flow tools registered only in the gateway,
which passed their bindings to ``bootstrap_tool_registry``; the sidecar passed
none, so the adapters were skipped and tools/list never carried them.

These tests run the sidecar's own ``_bootstrap``, not the in-process server:
the bug lived in that entrypoint, and every earlier test built the adapter
directly with its bindings in hand, which is precisely the case that worked.

The second half drives the bindings the sidecar built against a real gateway
app over ASGI, so "the proposal is in the gateway's ledger" and "the run is in
/v1/runs" are checked against the actual routes rather than a fake of them.
"""

from __future__ import annotations

import functools
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from metaforge.mcp import remote_flows
from metaforge.mcp.__main__ import _bootstrap, _parse_args

FLOW_TOOLS = {
    "flow.list",
    "flow.propose",
    "flow.start_run",
    "flow.status",
    "run.start_design_flow",
    "run.get_status",
}

#: Anything that would make the sidecar reach for a real service.
_SERVICE_ENV = (
    "NEO4J_URI",
    "METAFORGE_NEO4J_URI",
    "METAFORGE_GRAPH_BACKEND",
    "DATABASE_URL",
    "OPEN_ROUTER_API_KEY",
    "METAFORGE_ADAPTERS",
    "METAFORGE_DASHBOARD_URL",
)

GATEWAY = "http://gateway.test"

#: Enough context that a proposal does not stop to ask (FORGE-463), as an MCP
#: client sends it (snake_case) and as the REST route takes it (camelCase).
_MCP_CONTEXT: dict[str, Any] = {
    "manufacturing_context": {
        "route": "in_house",
        "processes": ["woodworking"],
        "machines": ["table saw, 600 mm rip capacity"],
        "stock_materials": ["18 mm birch plywood"],
    },
    "target_maturity": "physically_validated",
    "loads_and_use": "40 kg of dishes per shelf, indoors",
}
_REST_CONTEXT: dict[str, Any] = {
    "manufacturingContext": _MCP_CONTEXT["manufacturing_context"],
    "targetMaturity": _MCP_CONTEXT["target_maturity"],
    "loadsAndUse": _MCP_CONTEXT["loads_and_use"],
}


def _gateway_app() -> FastAPI:
    """The three routers the bindings call, as the gateway mounts them."""
    from api_gateway.chat.tool_approvals import router as approvals_router
    from api_gateway.design_flows.routes import router as flows_router
    from api_gateway.runs.routes import router as runs_router

    app = FastAPI()
    app.include_router(flows_router)
    app.include_router(runs_router)
    app.include_router(approvals_router)
    return app


class _RecordingLauncher:
    """Stands in for Temporal: accepts a start, answers no queries."""

    started: list[str] = []

    async def start(self, *, run_id: str, **_: Any) -> None:
        self.started.append(run_id)


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_gateway_app()), base_url=GATEWAY)


@pytest.fixture
def sidecar_env(monkeypatch: pytest.MonkeyPatch, gateway: httpx.AsyncClient) -> None:
    for key in _SERVICE_ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("METAFORGE_GATEWAY_URL", GATEWAY)
    # The real engine path, which is the one that refuses an unapproved
    # version (the in-process double does not check). Temporal itself is
    # replaced by a launcher that only records what it was asked to start.
    monkeypatch.delenv("METAFORGE_FLOW_ENGINE", raising=False)
    import api_gateway.runs.routes as run_routes

    async def _launcher() -> _RecordingLauncher:
        return _RecordingLauncher()

    monkeypatch.setattr(run_routes, "get_flow_launcher", _launcher)
    # Route the sidecar's gateway calls into the ASGI app instead of a socket.
    monkeypatch.setattr(
        remote_flows,
        "build_remote_flow_bindings",
        functools.partial(remote_flows.build_remote_flow_bindings, client=gateway),
    )


async def _boot(*argv: str) -> Any:
    server, twin, *_ = await _bootstrap(_parse_args(["--transport", "http", *argv]))
    await twin.aclose()
    return server


async def _listed(server: Any) -> set[str]:
    raw = await server.handle_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    )
    return {tool["name"] for tool in json.loads(raw)["result"]["tools"]}


class _LogSpy:
    """Records events by name. ``structlog.testing.capture_logs`` misses them
    once another test has configured structlog with cached loggers."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __getattr__(self, level: str) -> Any:
        def record(event: str, **kw: Any) -> None:
            self.events.append({"event": event, "level": level, **kw})

        return record


def _spy_on(monkeypatch: pytest.MonkeyPatch, module: Any) -> _LogSpy:
    spy = _LogSpy()
    monkeypatch.setattr(module, "logger", spy)
    return spy


def _adapter(server: Any, adapter_id: str) -> Any:
    return next(a for a in server.adapters if a.adapter_id == adapter_id)


def _wire(tool_id: str) -> str:
    """tools/list names are slugged for clients that refuse dots."""
    return tool_id.replace(".", "_")


@pytest.fixture
def fake_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deterministic tailoring, so no model is called."""
    import api_gateway.design_flows.generate as gen
    from orchestrator.design_flow.generator import Operation, OperationKind, build_proposal
    from orchestrator.design_flow.spec import get_flow
    from orchestrator.design_flow.templates import load_templates

    async def fake_generate(request: Any) -> Any:
        return build_proposal(
            get_flow("hardware_v1"),
            base_version=load_templates()["hardware_v1"].version,
            operations=[Operation(OperationKind.DROP_PHASE, "firmware", "no firmware here")],
            intent=request.intent,
        )

    monkeypatch.setattr(gen, "generate_proposal", fake_generate)


class TestTheSidecarListsTheTools:
    @pytest.mark.usefixtures("sidecar_env")
    async def test_tools_list_with_no_profile_carries_all_six(self) -> None:
        """The bug, as one assertion. Fails on main: neither adapter registers."""
        server = await _boot()
        listed = await _listed(server)
        missing = sorted(t for t in FLOW_TOOLS if _wire(t) not in listed and t not in listed)
        assert not missing, f"sidecar tools/list is missing {missing}"

    @pytest.mark.usefixtures("sidecar_env")
    async def test_the_core_profile_carries_them_too(self) -> None:
        """Starting a flow is a core action; the default plugin install is core."""
        server = await _boot("--profile", "core")
        listed = await _listed(server)
        missing = sorted(t for t in FLOW_TOOLS if _wire(t) not in listed and t not in listed)
        assert not missing, f"core profile is missing {missing}"

    @pytest.mark.usefixtures("sidecar_env")
    async def test_naming_them_in_the_allow_list_is_not_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """docker-compose names both ids in --adapters. design_flow used to be
        unknown to the generic loop, which logged it as failed."""
        import tool_registry.bootstrap as bootstrap_mod

        spy = _spy_on(monkeypatch, bootstrap_mod)
        server = await _boot("--adapters", "twin,project,design_flow,run")
        ids = {a.adapter_id for a in server.adapters}
        assert {"design_flow", "run"} <= ids
        summary = next(e for e in spy.events if e["event"] == "Tool registry bootstrap complete")
        assert not {"design_flow", "run"} & set(summary["failed"])

    async def test_without_a_gateway_url_it_binds_in_process_and_says_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same fallback as the approval gate: present, and loudly local."""
        import metaforge.mcp.__main__ as sidecar

        for key in (*_SERVICE_ENV, "METAFORGE_GATEWAY_URL"):
            monkeypatch.delenv(key, raising=False)
        spy = _spy_on(monkeypatch, sidecar)
        server = await _boot()
        assert {"design_flow", "run"} <= {a.adapter_id for a in server.adapters}
        assert any(e["event"] == "mcp_flow_bindings_in_process" for e in spy.events)


class TestTheSidecarActsOnTheGatewaysState:
    @pytest.mark.usefixtures("sidecar_env", "fake_generator")
    async def test_propose_approve_start_and_read_back(self, gateway: httpx.AsyncClient) -> None:
        """The acceptance loop, end to end, through the sidecar's own bindings."""
        server = await _boot()
        flows = _adapter(server, "design_flow")

        proposal = await flows.propose({"intent": "a kitchen cabinet", **_MCP_CONTEXT})
        assert proposal["approval_id"] and proposal["version_id"]
        assert "cannot approve" in proposal["next_step"]

        # Same ledger: the dashboard's queue is the gateway's, and it has it.
        queue = (await gateway.get("/v1/chat/tool_approvals")).json()["runs"]
        assert any(r["id"] == proposal["approval_id"] for r in queue)

        # Before anyone answers, starting is refused, in words.
        with pytest.raises(RuntimeError, match="not a failure"):
            await flows.start_run(
                {"flow_version_id": proposal["version_id"], "goal": "build the cabinet"}
            )

        # A person answers in the dashboard.
        answered = await gateway.post(
            f"/v1/chat/tool_approvals/{proposal['approval_id']}", json={"decision": "approve"}
        )
        assert answered.status_code == 200, answered.text

        started = await flows.start_run(
            {"flow_version_id": proposal["version_id"], "goal": "build the cabinet"}
        )
        runs = (await gateway.get("/v1/runs")).json()["runs"]
        assert any(r["id"] == started["run_id"] for r in runs), "run is not in /v1/runs"

        assert started["run_id"] in _RecordingLauncher.started

        state = await flows.status({"run_id": started["run_id"]})
        assert state["runId"] == started["run_id"]
        assert state["phases"]

        status = await _adapter(server, "run").get_status({"run_id": started["run_id"]})
        assert status["run_id"] == started["run_id"]
        assert status["goal"] == "build the cabinet"

    @pytest.mark.usefixtures("sidecar_env")
    async def test_run_start_design_flow_lands_in_the_gateways_run_store(
        self, gateway: httpx.AsyncClient
    ) -> None:
        server = await _boot()
        runs = _adapter(server, "run")
        started = await runs.start_design_flow({"goal": "a drone frame", "flow": "mech_v1"})
        listed = (await gateway.get("/v1/runs")).json()["runs"]
        assert any(r["id"] == started["run_id"] for r in listed)
        assert started["flow"] == "mech_v1"
        assert started["phases"]

    @pytest.mark.usefixtures("sidecar_env")
    async def test_an_unknown_flow_is_a_value_error_like_in_process(self) -> None:
        server = await _boot()
        with pytest.raises(ValueError, match="unknown flow"):
            await _adapter(server, "run").start_design_flow({"goal": "x", "flow": "nope_v9"})


class TestRemoteAnswersLikeInProcess:
    """A tool that answers differently depending on which process hosts it is
    a tool an agent cannot be taught to read."""

    async def test_flow_list_is_identical(self, gateway: httpx.AsyncClient) -> None:
        from api_gateway.design_flows.mcp_bindings import make_catalogue_reader

        remote = remote_flows.build_remote_flow_bindings(GATEWAY, client=gateway)
        assert await remote.catalogue_reader() == await make_catalogue_reader()()

    @pytest.mark.usefixtures("fake_generator")
    async def test_flow_propose_has_the_same_shape(self, gateway: httpx.AsyncClient) -> None:
        from api_gateway.design_flows.mcp_bindings import make_proposer

        remote = remote_flows.build_remote_flow_bindings(GATEWAY, client=gateway)
        args = {
            "intent": "a kitchen cabinet",
            "project_id": None,
            "requirements": [],
            **_MCP_CONTEXT,
        }
        over_http = await remote.proposer(**args)
        local = await make_proposer()(**args)

        def strip(result: dict[str, Any]) -> dict[str, Any]:
            # Ids differ per proposal; everything else must not.
            out = {k: v for k, v in result.items() if k not in {"approval_id", "version_id"}}
            out["next_step"] = out["next_step"].replace(result["approval_id"], "<id>")
            return out

        assert strip(over_http) == strip(local)

    async def test_needs_input_is_identical(self, gateway: httpx.AsyncClient) -> None:
        """FORGE-463: an intent alone asks rather than guesses, on either host.

        Extra model questions are off (no model in a unit test), so both
        answers are MetaForge's deterministic questions.
        """
        from api_gateway.design_flows.mcp_bindings import make_proposer

        remote = remote_flows.build_remote_flow_bindings(GATEWAY, client=gateway)
        args = {"intent": "a kitchen cabinet", "project_id": None, "requirements": []}
        over_http = await remote.proposer(**args)
        assert over_http["status"] == "needs_input"
        assert over_http["questions"]
        assert "approval_id" not in over_http
        local = await make_proposer()(**args)
        assert over_http["questions"] == local["questions"]
        assert over_http["next_step"] == local["next_step"]

    async def test_the_new_inputs_reach_the_gateway(self, gateway: httpx.AsyncClient) -> None:
        """Answering the questions over MCP must stop them being asked again.
        A binding that dropped manufacturing_context would loop forever."""
        remote = remote_flows.build_remote_flow_bindings(GATEWAY, client=gateway)
        partial = await remote.proposer(
            intent="a kitchen cabinet",
            project_id=None,
            requirements=[],
            target_maturity="concept",
            loads_and_use="unknown",
        )
        asked = {q["field"] for q in partial["questions"]}
        assert not any("maturity" in f.lower() or "loads" in f.lower() for f in asked), asked

    async def test_an_invalid_input_value_says_which(self, gateway: httpx.AsyncClient) -> None:
        remote = remote_flows.build_remote_flow_bindings(GATEWAY, client=gateway)
        with pytest.raises(RuntimeError, match="invalid input"):
            await remote.proposer(
                intent="x",
                project_id=None,
                requirements=[],
                manufacturing_context={"route": "magic"},
            )

    async def test_an_unreachable_gateway_says_so(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
        remote = remote_flows.build_remote_flow_bindings(GATEWAY, client=client)
        with pytest.raises(RuntimeError, match="could not be reached"):
            await remote.catalogue_reader()


class TestAnsweringTheApprovalDecidesTheVersion:
    """Without this, approve-then-start could not be done by anyone: the
    version stayed `proposed` and POST /v1/runs refused it forever."""

    @pytest.mark.usefixtures("fake_generator")
    @pytest.mark.parametrize(
        ("decision", "status"), [("approve", "approved"), ("reject", "rejected")]
    )
    async def test_the_answer_reaches_the_version(
        self, gateway: httpx.AsyncClient, decision: str, status: str
    ) -> None:
        proposal = (
            await gateway.post(
                "/v1/design-flows/propose", json={"intent": "a cabinet", **_REST_CONTEXT}
            )
        ).json()
        await gateway.post(
            f"/v1/chat/tool_approvals/{proposal['approvalId']}", json={"decision": decision}
        )
        version = (await gateway.get(f"/v1/design-flows/versions/{proposal['versionId']}")).json()
        assert version["status"] == status

    async def test_an_ordinary_tool_approval_touches_no_version(
        self, gateway: httpx.AsyncClient
    ) -> None:
        created = (
            await gateway.post(
                "/v1/chat/tool_approvals",
                json={"tool": "twin.record_document", "arguments": {}, "reason": "writes"},
            )
        ).json()
        answered = await gateway.post(
            f"/v1/chat/tool_approvals/{created['id']}", json={"decision": "approve"}
        )
        assert answered.status_code == 200
