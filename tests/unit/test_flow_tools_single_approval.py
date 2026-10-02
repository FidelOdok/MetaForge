"""One approval per flow proposal, and none for the call (FORGE-471).

`flow.propose` and `flow.start_run` were held at the call like any write, so:

* an intent-only `flow.propose`, whose answer is `needs_input` questions and
  writes nothing (FORGE-463), timed out waiting for a person before the user
  could even be asked;
* a full proposal needed two approvals: the call, then the version it holds;
* starting an already-approved version needed a third.

These drive the real dispatcher as an untrusted caller, with the sidecar's own
bindings pointed at a real gateway app, and count what the call-level gate is
asked and what lands on the Approvals ledger.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from mcp_core.annotations import annotations_for
from mcp_core.guardrails import DOWNSTREAM_APPROVED, ApprovalOutcome, Caller, decide
from metaforge.mcp.server import UnifiedMcpServer
from tests.unit import test_sidecar_flow_tools as _sidecar

_MCP_CONTEXT = _sidecar._MCP_CONTEXT
_adapter = _sidecar._adapter
_boot = _sidecar._boot

# The sidecar suite's fixtures: a real gateway app over ASGI, the sidecar's
# own bindings pointed at it, and a deterministic flow generator.
gateway = _sidecar.gateway
sidecar_env = _sidecar.sidecar_env
fake_generator = _sidecar.fake_generator


class _CountingGate:
    """The call-level gate. Approves, and remembers every tool it was asked about."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def __call__(self, ask: Any) -> ApprovalOutcome:
        self.asked.append(ask.tool_id)
        return ApprovalOutcome.APPROVED


async def _untrusted_server() -> tuple[UnifiedMcpServer, _CountingGate]:
    sidecar = await _boot()
    gate = _CountingGate()
    server = UnifiedMcpServer(
        adapters=[_adapter(sidecar, "design_flow")],
        caller=Caller.UNTRUSTED,
        approval_gate=gate,
    )
    return server, gate


async def _call(server: UnifiedMcpServer, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )
    )
    reply: dict[str, Any] = json.loads(raw)
    return reply


def _payload(reply: dict[str, Any]) -> dict[str, Any]:
    assert "error" not in reply, reply
    result = reply["result"]
    assert not result.get("isError"), result
    data: dict[str, Any] = json.loads(result["content"][0]["text"])
    return data.get("data", data)


async def _ledger(gateway: httpx.AsyncClient) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = (await gateway.get("/v1/chat/tool_approvals")).json()["runs"]
    return runs


class TestTheGate:
    @pytest.mark.parametrize("tool_id", sorted(DOWNSTREAM_APPROVED))
    @pytest.mark.parametrize("caller", [Caller.UNTRUSTED, Caller.REMOTE])
    def test_the_call_is_not_held(self, tool_id: str, caller: Caller) -> None:
        assert not decide(tool_id, caller=caller).requires_approval

    @pytest.mark.parametrize("tool_id", sorted(DOWNSTREAM_APPROVED))
    def test_but_the_annotation_still_says_it_writes(self, tool_id: str) -> None:
        assert annotations_for(tool_id)["readOnlyHint"] is False

    def test_starting_a_builtin_flow_is_still_held(self) -> None:
        """No version approval stands behind a built-in template, so the call
        hold is the only consent that run gets."""
        assert "run.start_design_flow" not in DOWNSTREAM_APPROVED
        assert decide("run.start_design_flow", caller=Caller.UNTRUSTED).requires_approval

    def test_the_reason_names_the_version(self) -> None:
        assert "flow version" in decide("flow.propose", caller=Caller.UNTRUSTED).reason


@pytest.mark.usefixtures("sidecar_env")
class TestOverTheDispatcher:
    async def test_intent_only_propose_is_not_held_and_asks(
        self, gateway: httpx.AsyncClient
    ) -> None:
        """The live bug: this came back `timed_out` after 100s."""
        server, gate = await _untrusted_server()
        before = len(await _ledger(gateway))

        out = _payload(await _call(server, "flow.propose", {"intent": "a kitchen cabinet"}))

        assert out["status"] == "needs_input"
        assert out["questions"]
        assert gate.asked == []
        assert len(await _ledger(gateway)) == before, "needs_input must hold nothing"

    @pytest.mark.usefixtures("fake_generator")
    async def test_a_full_proposal_creates_exactly_one_approval(
        self, gateway: httpx.AsyncClient
    ) -> None:
        server, gate = await _untrusted_server()
        before = {r["id"] for r in await _ledger(gateway)}

        out = _payload(
            await _call(server, "flow.propose", {"intent": "a kitchen cabinet", **_MCP_CONTEXT})
        )

        assert out["status"] == "proposed"
        assert gate.asked == [], "the call itself must not be held"
        new = [r for r in await _ledger(gateway) if r["id"] not in before]
        assert [r["id"] for r in new] == [out["approval_id"]]

    @pytest.mark.usefixtures("fake_generator")
    async def test_start_on_an_unapproved_version_is_refused(
        self, gateway: httpx.AsyncClient
    ) -> None:
        server, gate = await _untrusted_server()
        proposal = _payload(
            await _call(server, "flow.propose", {"intent": "a kitchen cabinet", **_MCP_CONTEXT})
        )
        runs_before = (await gateway.get("/v1/runs")).json()["runs"]

        reply = await _call(
            server,
            "flow.start_run",
            {"flow_version_id": proposal["version_id"], "goal": "build the cabinet"},
        )

        assert "error" in reply or reply["result"].get("isError"), reply
        assert "not approved" in json.dumps(reply)
        assert gate.asked == []
        assert (await gateway.get("/v1/runs")).json()["runs"] == runs_before

    @pytest.mark.usefixtures("fake_generator")
    async def test_start_on_an_approved_version_is_not_held(
        self, gateway: httpx.AsyncClient
    ) -> None:
        server, gate = await _untrusted_server()
        proposal = _payload(
            await _call(server, "flow.propose", {"intent": "a kitchen cabinet", **_MCP_CONTEXT})
        )
        answered = await gateway.post(
            f"/v1/chat/tool_approvals/{proposal['approval_id']}", json={"decision": "approve"}
        )
        assert answered.status_code == 200, answered.text
        pending_before = [r for r in await _ledger(gateway) if r["status"] == "awaiting_approval"]

        started = _payload(
            await _call(
                server,
                "flow.start_run",
                {"flow_version_id": proposal["version_id"], "goal": "build the cabinet"},
            )
        )

        assert started["run_id"]
        assert gate.asked == []
        pending_after = [r for r in await _ledger(gateway) if r["status"] == "awaiting_approval"]
        assert pending_after == pending_before


@pytest.mark.usefixtures("fake_generator")
class TestTheInProcessEngineRefusesToo:
    """`flow.start_run` relies on POST /v1/runs to refuse an unapproved
    version. That refusal lived only on the Temporal path, so the in-process
    engine would have started one."""

    async def test_an_unapproved_version_is_409(
        self, gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "in_process")
        proposal = (
            await gateway.post(
                "/v1/design-flows/propose",
                json={
                    "intent": "a cabinet",
                    "manufacturingContext": _MCP_CONTEXT["manufacturing_context"],
                    "targetMaturity": _MCP_CONTEXT["target_maturity"],
                    "loadsAndUse": _MCP_CONTEXT["loads_and_use"],
                },
            )
        ).json()
        runs_before = (await gateway.get("/v1/runs")).json()["runs"]

        refused = await gateway.post(
            "/v1/runs",
            json={
                "request": {
                    "kind": "design_flow",
                    "flow_version_id": proposal["versionId"],
                    "goal": "g",
                },
                "start": True,
            },
        )

        assert refused.status_code == 409, refused.text
        assert "not approved" in refused.text
        assert (await gateway.get("/v1/runs")).json()["runs"] == runs_before

    async def test_an_unknown_version_is_404(
        self, gateway: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("METAFORGE_FLOW_ENGINE", "in_process")
        missing = await gateway.post(
            "/v1/runs",
            json={
                "request": {"kind": "design_flow", "flow_version_id": "flowv_nope", "goal": "g"},
                "start": True,
            },
        )
        assert missing.status_code == 404, missing.text
