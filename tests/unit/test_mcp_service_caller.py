"""The design-flow worker is an authenticated service caller (FORGE-487).

Every write a server-driven run made was held for a dashboard click and timed
out after 100 s, so no phase could record a deliverable unattended. These tests
drive the **real sidecar HTTP app** (``build_http_app``) against a **real
gateway app** (the runs and design-flows routers), because the properties that
matter are about what the sidecar decides from what the gateway says, and a
double for either would pass while proving nothing.

The story's four acceptance cases are the first four test classes:

1. a service call in a running, approved run records its entity, no hold;
2. the same call from a plugin client is still held;
3. a call naming a run that is not running, or another project, is refused;
4. a missing or wrong service key falls back to untrusted (held).

The rest pin the security properties: no key configured means off in any auth
mode, a short key is no key, headers cannot assert a service, destructive and
admin tools are refused, and an unreachable gateway fails closed.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import api_gateway.runs.routes as run_routes
from api_gateway.design_flows.routes import router as flows_router
from api_gateway.sessions.backend import InMemoryAgentSessionStore
from mcp_core.context import (
    HEADER_MODEL,
    HEADER_PHASE,
    HEADER_PROJECT,
    HEADER_RUN,
    McpCallContext,
    context_from_headers,
    context_to_headers,
)
from mcp_core.guardrails import (
    ApprovalAsk,
    ApprovalOutcome,
    Caller,
    decide,
)
from mcp_core.service_auth import (
    HEADER_SERVICE_KEY,
    MIN_SERVICE_KEY_LENGTH,
    verify_service_key,
)
from metaforge.mcp.__main__ import build_http_app
from metaforge.mcp.capture import SessionCapture
from metaforge.mcp.server import UnifiedMcpServer
from metaforge.mcp.service_runs import GatewayRunVerifier
from orchestrator.design_flow.spec import get_flow
from orchestrator.design_flow.versions import get_version_store, reset_version_store
from orchestrator.harness.runs import RunStatus
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

KEY = "service-key-0123456789-abcdef"
PROJECT = str(uuid.uuid4())
OTHER_PROJECT = str(uuid.uuid4())

INTENT_ENTITY = "twin.record_engineering_entity"


def _manifest(tool_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id=tool_id.split(".")[0],
        name=tool_id,
        description="stub",
        capability="test",
    )


class _Twin(McpToolServer):
    """The tools the cases need, recording what actually ran."""

    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        for tool_id in (INTENT_ENTITY, "twin.approve_engineering_entity"):
            self.register_tool(_manifest(tool_id), self._handler(tool_id))

    def _handler(self, tool_id: str) -> Any:
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((tool_id, args))
            return {"ok": True}

        return run


class _Project(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="project", version="0.1.0")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        for tool_id in ("project.create", "project.delete"):
            self.register_tool(_manifest(tool_id), self._handler(tool_id))

    def _handler(self, tool_id: str) -> Any:
        async def run(args: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((tool_id, args))
            return {"ok": True}

        return run


class _Gate:
    """The dashboard approval gate. Nobody ever answers: it rejects at once."""

    def __init__(self) -> None:
        self.asks: list[ApprovalAsk] = []

    async def __call__(self, ask: ApprovalAsk) -> ApprovalOutcome:
        self.asks.append(ask)
        return ApprovalOutcome.REJECTED


@pytest.fixture(autouse=True)
def _fresh_gateway_state() -> Any:
    run_routes.reset_run_store()
    reset_version_store()
    yield
    run_routes.reset_run_store()
    reset_version_store()


@pytest.fixture
def gateway_http() -> httpx.AsyncClient:
    """A real gateway app: the runs and design-flows routers, in process."""
    app = FastAPI()
    app.include_router(run_routes.router)
    app.include_router(flows_router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw.test")


def _approved_version() -> str:
    store = get_version_store()
    version = store.save(
        get_flow("hardware_v1"), base_template_id="hardware_v1", base_version="1", changes=[]
    )
    store.decide(version.id, approved=True, decided_by="local:dashboard")
    return version.id


def _running_run(*, version_id: str | None, project: str = PROJECT, start: bool = True) -> str:
    request: dict[str, Any] = {"kind": "design_flow", "project_id": project}
    if version_id:
        request["flow_version_id"] = version_id
    store = run_routes.get_run_store()
    run = store.create(request)
    if start:
        store.start(run.id)
    return run.id


class _Sidecar:
    def __init__(
        self,
        verifier: Any,
        *,
        service_key: str | None = KEY,
        capture: SessionCapture | None = None,
    ) -> None:
        self.twin = _Twin()
        self.project = _Project()
        self.gate = _Gate()
        self.server = UnifiedMcpServer(
            [self.twin, self.project],
            caller=Caller.UNTRUSTED,
            approval_gate=self.gate,
            session_capture=capture,
        )
        self.client = TestClient(
            build_http_app(
                self.server,
                enable_sse=False,
                service_key=service_key,
                service_verifier=verifier,
            )
        )

    def call(
        self,
        tool_id: str,
        arguments: dict[str, Any] | None = None,
        *,
        key: str | None = None,
        run: str | None = None,
        project: str | None = PROJECT,
        phase: str | None = "intent",
        model: str | None = "openrouter:test-model",
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if key is not None:
            headers[HEADER_SERVICE_KEY] = key
        if run is not None:
            headers[HEADER_RUN] = run
        if project is not None:
            headers[HEADER_PROJECT] = project
        if phase is not None:
            headers[HEADER_PHASE] = phase
        if model is not None:
            headers[HEADER_MODEL] = model
        response = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_id, "arguments": arguments or {}},
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body


def _error_code(body: dict[str, Any]) -> str | None:
    return (body.get("error") or {}).get("data", {}).get("code")


def _ran(sidecar: _Sidecar) -> list[str]:
    return [t for t, _ in sidecar.twin.calls + sidecar.project.calls]


# ── 1. The intent phase records its entity with no per-call approval ────────


class TestServiceCallRecordsWithoutAHold:
    def test_the_intent_entity_is_recorded_and_nothing_is_held(self, gateway_http) -> None:
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))

        body = sidecar.call(INTENT_ENTITY, {"title": "intent"}, key=KEY, run=run)

        assert "error" not in body, body
        assert _ran(sidecar) == [INTENT_ENTITY]
        assert sidecar.gate.asks == [], "a service write inside an approved run must not be held"

    async def test_provenance_names_the_run_phase_and_model(self, gateway_http) -> None:
        run = _running_run(version_id=_approved_version())
        store = InMemoryAgentSessionStore()
        sidecar = _Sidecar(
            GatewayRunVerifier("http://gw.test", client=gateway_http),
            capture=SessionCapture(store),
        )

        body = sidecar.call(INTENT_ENTITY, {"title": "intent"}, key=KEY, run=run)

        assert "error" not in body, body
        events = (await store.list_sessions())[0].events
        action = next(e for e in events if e.data.get("tool_id") == INTENT_ENTITY)
        assert action.data["caller"] == "service"
        assert action.data["service"] == {
            "run_id": run,
            "phase": "intent",
            "model": "openrouter:test-model",
        }
        # The actor is the server's name for the service, not the header's.
        assert action.data["actor"] == f"service:design-flow:{run}"
        assert action.data["actor_verified"] is False

    def test_it_also_works_when_the_sidecar_has_no_bearer_key(self, gateway_http) -> None:
        """Open auth mode is the sidecar's default. It must not be what enables this."""
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))
        # No api_key was passed to build_http_app: open mode. The key is still
        # what decides, which the next class proves by omitting it.
        assert "error" not in sidecar.call(INTENT_ENTITY, key=KEY, run=run)


# ── 2. The same call from a plugin client is still held ─────────────────────


class TestAPluginClientIsStillHeld:
    def test_the_same_write_without_the_key_goes_to_the_gate(self, gateway_http) -> None:
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))

        body = sidecar.call(INTENT_ENTITY, {"title": "intent"}, run=run)

        assert body["error"]["data"]["code"] == "approval_required"
        assert [a.tool_id for a in sidecar.gate.asks] == [INTENT_ENTITY]
        assert sidecar.gate.asks[0].caller is Caller.UNTRUSTED
        assert _ran(sidecar) == []

    def test_claiming_to_be_the_worker_in_headers_changes_nothing(self, gateway_http) -> None:
        """Run, phase and model headers are claims, not credentials."""
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))

        body = sidecar.call(
            INTENT_ENTITY, run=run, phase="intent", model="anything", project=PROJECT
        )

        assert body["error"]["data"]["code"] == "approval_required"
        assert _ran(sidecar) == []


# ── 3. A run that is not running, or another project, is refused ────────────


class TestOutOfScopeCallsAreRefused:
    def test_a_run_that_is_not_running_is_refused_not_held(self, gateway_http) -> None:
        run = _running_run(version_id=_approved_version())
        run_routes.get_run_store().cancel(run)
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))

        body = sidecar.call(INTENT_ENTITY, key=KEY, run=run)

        assert _error_code(body) == "service_scope"
        assert "not running" in body["error"]["message"]
        assert _ran(sidecar) == []
        assert sidecar.gate.asks == [], "refused, not parked for a person who is not there"

    def test_a_run_that_was_never_started_is_refused(self, gateway_http) -> None:
        run = _running_run(version_id=_approved_version(), start=False)
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))
        assert _error_code(sidecar.call(INTENT_ENTITY, key=KEY, run=run)) == "service_scope"

    def test_a_run_that_does_not_exist_is_refused(self, gateway_http) -> None:
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))
        assert _error_code(sidecar.call(INTENT_ENTITY, key=KEY, run="run_nope")) == "service_scope"

    def test_a_call_aimed_at_another_project_is_refused(self, gateway_http) -> None:
        run = _running_run(version_id=_approved_version())  # belongs to PROJECT
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))

        body = sidecar.call(INTENT_ENTITY, key=KEY, run=run, project=OTHER_PROJECT)

        assert _error_code(body) == "service_scope"
        assert "does not belong" in body["error"]["message"]
        assert _ran(sidecar) == []

    def test_a_project_argument_outside_the_run_is_refused(self, gateway_http) -> None:
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))

        body = sidecar.call(INTENT_ENTITY, {"project_id": OTHER_PROJECT}, key=KEY, run=run)

        assert _error_code(body) == "service_other_project"
        assert _ran(sidecar) == []

    def test_a_project_argument_inside_the_run_is_fine(self, gateway_http) -> None:
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))
        body = sidecar.call(INTENT_ENTITY, {"project_id": PROJECT.upper()}, key=KEY, run=run)
        assert "error" not in body, body

    def test_a_call_that_names_no_run_or_no_project_is_refused(self, gateway_http) -> None:
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))
        assert _error_code(sidecar.call(INTENT_ENTITY, key=KEY, run=None)) == "service_scope"
        run = _running_run(version_id=_approved_version())
        assert (
            _error_code(sidecar.call(INTENT_ENTITY, key=KEY, run=run, project=None))
            == "service_scope"
        )

    def test_a_version_that_was_never_approved_is_refused(self, gateway_http) -> None:
        proposed = get_version_store().save(
            get_flow("hardware_v1"), base_template_id="hardware_v1", base_version="1", changes=[]
        )
        run = _running_run(version_id=proposed.id)
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))

        body = sidecar.call(INTENT_ENTITY, key=KEY, run=run)

        assert _error_code(body) == "service_scope"
        assert "approved" in body["error"]["message"]
        assert _ran(sidecar) == []

    def test_a_template_run_has_no_approval_to_bind_to_and_stays_held(self, gateway_http) -> None:
        """No flow version, nothing a person approved: the old behaviour, unchanged."""
        run = _running_run(version_id=None)
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))

        body = sidecar.call(INTENT_ENTITY, key=KEY, run=run)

        assert body["error"]["data"]["code"] == "approval_required"
        assert [a.tool_id for a in sidecar.gate.asks] == [INTENT_ENTITY]

    def test_an_unreachable_gateway_fails_closed(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("gateway down", request=request)

        down = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://gw.test")
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=down))

        body = sidecar.call(INTENT_ENTITY, key=KEY, run="run_x")

        assert _error_code(body) == "service_scope"
        assert "fail closed" in body["error"]["message"]
        assert _ran(sidecar) == []


class TestAGrantEndsWithTheRun:
    def test_cancelling_the_run_stops_the_next_write_once_the_grant_expires(
        self, gateway_http
    ) -> None:
        now = [0.0]
        verifier = GatewayRunVerifier(
            "http://gw.test", client=gateway_http, ttl_seconds=5.0, clock=lambda: now[0]
        )
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(verifier)

        assert "error" not in sidecar.call(INTENT_ENTITY, key=KEY, run=run)
        run_routes.get_run_store().cancel(run)
        now[0] = 1.0
        assert "error" not in sidecar.call(INTENT_ENTITY, key=KEY, run=run), "inside the window"
        now[0] = 6.0
        assert _error_code(sidecar.call(INTENT_ENTITY, key=KEY, run=run)) == "service_scope"


# ── 4. A missing or wrong key falls back to untrusted ───────────────────────


class TestWithoutTheRightKeyTheWorkerIsUntrusted:
    @pytest.mark.parametrize("presented", [None, "", "wrong-key-0123456789-abcdef", KEY + "x"])
    def test_a_missing_or_wrong_key_is_held(self, gateway_http, presented) -> None:
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))

        body = sidecar.call(INTENT_ENTITY, key=presented, run=run)

        assert body["error"]["data"]["code"] == "approval_required"
        assert _ran(sidecar) == []


# ── The feature is off unless it is configured, in any auth mode ────────────


class TestOffUnlessConfigured:
    def test_no_key_on_the_sidecar_means_the_worker_key_buys_nothing(self, gateway_http) -> None:
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(
            GatewayRunVerifier("http://gw.test", client=gateway_http), service_key=None
        )
        body = sidecar.call(INTENT_ENTITY, key=KEY, run=run)
        assert body["error"]["data"]["code"] == "approval_required"
        assert _ran(sidecar) == []

    def test_an_empty_key_is_not_a_key(self, gateway_http) -> None:
        """Open auth mode's shape: an unset secret must not match an empty header."""
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(
            GatewayRunVerifier("http://gw.test", client=gateway_http), service_key=""
        )
        for presented in ("", None):
            body = sidecar.call(INTENT_ENTITY, key=presented, run=run)
            assert body["error"]["data"]["code"] == "approval_required"

    def test_a_key_that_is_too_short_does_not_enable_it(self, gateway_http) -> None:
        short = "x" * (MIN_SERVICE_KEY_LENGTH - 1)
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(
            GatewayRunVerifier("http://gw.test", client=gateway_http), service_key=short
        )
        body = sidecar.call(INTENT_ENTITY, key=short, run=run)
        assert body["error"]["data"]["code"] == "approval_required"

    def test_a_key_with_no_gateway_to_verify_against_stays_off(self) -> None:
        sidecar = _Sidecar(None)
        body = sidecar.call(INTENT_ENTITY, key=KEY, run="run_x")
        assert body["error"]["data"]["code"] == "approval_required"
        assert _ran(sidecar) == []


# ── What a service may not do, even inside its run ──────────────────────────


class TestDestructiveAndAdminWritesAreRefused:
    @pytest.mark.parametrize(
        "tool_id",
        [
            "project.create",
            "project.delete",
            "twin.approve_engineering_entity",
        ],
    )
    def test_refused_not_held(self, gateway_http, tool_id: str) -> None:
        run = _running_run(version_id=_approved_version())
        sidecar = _Sidecar(GatewayRunVerifier("http://gw.test", client=gateway_http))

        body = sidecar.call(tool_id, {"name": "x"}, key=KEY, run=run)

        assert _error_code(body) == "service_refused"
        assert _ran(sidecar) == []
        assert sidecar.gate.asks == []


class TestDecision:
    def test_service_writes_are_not_held(self) -> None:
        d = decide(INTENT_ENTITY, caller=Caller.SERVICE)
        assert not d.requires_approval and not d.refused

    @pytest.mark.parametrize(
        "tool_id",
        [
            "project.create",
            "project.delete",
            "flow.propose",
            "flow.start_run",
            "run.start_design_flow",
            "twin.attempt_promotion",
            "twin.approve_design_loop",
            "twin.approve_engineering_entity",
            "cadquery.export_geometry",
            "twin.delete_work_product",  # unclassified: inherits the destructive default
        ],
    )
    def test_refused_tools(self, tool_id: str) -> None:
        d = decide(tool_id, caller=Caller.SERVICE, twin_mutations_enabled=True)
        assert d.refused and not d.requires_approval

    def test_a_mutating_cypher_is_refused_and_a_read_is_not(self) -> None:
        write = decide(
            "twin.query_cypher",
            caller=Caller.SERVICE,
            twin_mutations_enabled=True,
            arguments={"cypher": "MATCH (n) DETACH DELETE n"},
        )
        read = decide(
            "twin.query_cypher",
            caller=Caller.SERVICE,
            twin_mutations_enabled=True,
            arguments={"cypher": "MATCH (n) RETURN n LIMIT 1"},
        )
        assert write.refused
        assert not read.refused and not read.requires_approval

    @pytest.mark.parametrize("caller", [Caller.UNTRUSTED, Caller.REMOTE])
    def test_other_callers_are_unchanged(self, caller: Caller) -> None:
        d = decide(INTENT_ENTITY, caller=caller)
        assert d.requires_approval and not d.refused


# ── The credential and the context cannot be forged ─────────────────────────


class TestTheCredential:
    def test_matching_keys(self) -> None:
        assert verify_service_key(KEY, KEY)

    @pytest.mark.parametrize(
        ("provided", "configured"),
        [
            (None, KEY),
            ("", KEY),
            ("other-key-0123456789-abcd", KEY),
            (KEY, None),
            (KEY, ""),
            ("", ""),
            (None, None),
            ("short", "short"),
        ],
    )
    def test_everything_else_is_no(self, provided, configured) -> None:
        assert not verify_service_key(provided, configured)

    def test_non_ascii_does_not_raise(self) -> None:
        assert not verify_service_key("clé-0123456789-abcdefgh", KEY)

    def test_a_service_cannot_be_asserted_through_headers(self) -> None:
        ctx = context_from_headers(
            {
                HEADER_RUN: "run_1",
                HEADER_PHASE: "intent",
                HEADER_MODEL: "m",
                "X-MetaForge-Service-Verified": "true",
                "x-metaforge-service-refusal": "none",
            }
        )
        assert (ctx.run_id, ctx.phase, ctx.model) == ("run_1", "intent", "m")
        assert ctx.service_verified is False
        assert ctx.service_refusal is None

    def test_the_key_never_enters_the_call_context(self) -> None:
        ctx = McpCallContext(run_id="run_1", phase="intent", model="m")
        headers = context_to_headers(ctx)
        assert HEADER_SERVICE_KEY not in headers
        assert headers[HEADER_RUN] == "run_1"
        assert "service_key" not in McpCallContext.model_fields

    def test_oversized_or_unprintable_claims_are_dropped(self) -> None:
        ctx = context_from_headers({HEADER_RUN: "x" * 5000, HEADER_PHASE: "a\x00b"})
        assert ctx.run_id is not None and len(ctx.run_id) <= 200
        assert ctx.phase is None


def test_run_status_the_verifier_requires_is_running() -> None:
    """The word the verifier compares against is the harness's own."""
    assert RunStatus.RUNNING.value == "running"
