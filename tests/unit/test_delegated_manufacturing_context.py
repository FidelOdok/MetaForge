"""A delegated agent states the machine in the manufacturing context (FORGE-837).

A headless agent ran with owner delegation (``METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY``
and the ``X-MetaForge-Agent`` / ``X-MetaForge-On-Behalf-Of`` headers) and called
``twin.state_manufacturing_context`` with ``machine_or_supplier_profile:
"Bambu Lab P1S"``. It was refused as "never assumed", so DFM had no machine and
the release stalled.

Now, with the flag on and the call delegated, the dispatcher records a
delegated approval and hands the tool the agent and the person. The machine is
recorded ``user_stated`` with the agent as the actor, the person in
``on_behalf_of`` and route ``delegated``: never as the person alone, never as
assumed. A machine the library holds is linked, and DFM then reads its limits.
With the flag off, or without the headers, nothing changes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from api_gateway.approvals.policy import ApprovalPolicy, PolicyStore, reset_policy_store
from api_gateway.library import routes as library_routes
from api_gateway.operations import publisher as ops
from api_gateway.twin import dfm_findings as dfm
from api_gateway.twin import manufacturing_context as mc
from api_gateway.twin import manufacturing_tools as tools
from mcp_core.context import context_from_headers, with_context
from mcp_core.guardrails import (
    DELEGATED_AGENT_ARG,
    Caller,
    delegable_human_authority,
    requires_human_authority,
    strip_reserved_arguments,
)
from metaforge.mcp import server as server_mod
from metaforge.mcp.remote_approvals import RemoteApprovalLedger, build_remote_approval_gate
from metaforge.mcp.server import UnifiedMcpServer
from tests.unit.test_dfm_exact import PROJECT, FakeCad, _project, measured
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.record_pins import unregister_head_moved_listener
from twin_core.library.machine_seed import seed_machine_library
from twin_core.library.machines import SqliteMachineLibrary

FLAG = "METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY"
TOOL = "twin.state_manufacturing_context"
HEADERS = {"X-MetaForge-Agent": "claude-code-e2e", "X-MetaForge-On-Behalf-Of": "fidel"}
P1S = "Bambu Lab P1S"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MAX", "auto")
    monkeypatch.setattr(server_mod, "HANDOFF_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )
    monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
    reset_policy_store(PolicyStore())
    ops.init_operations(None)
    dfm.register()
    yield
    unregister_head_moved_listener(dfm.on_head_moved)
    ops.init_operations(None)
    reset_policy_store(None)


@pytest.fixture
def machine_library() -> Iterator[SqliteMachineLibrary]:
    library = SqliteMachineLibrary()
    seed_machine_library(library)
    library_routes.init_machine_library(library)
    yield library
    library_routes.init_machine_library(None)


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def gateway() -> httpx.AsyncClient:
    from fastapi import FastAPI

    from api_gateway.chat.tool_approvals import reset_approval_store, router

    reset_approval_store()
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test")


async def _server(http: httpx.AsyncClient, twin: InMemoryTwinAPI) -> Any:
    from tool_registry.tools.twin.adapter import TwinServer

    server = UnifiedMcpServer(
        [TwinServer(twin=twin, dfm_service=dfm.make_dfm_service(twin))],
        caller=Caller.REMOTE,
        approval_gate=build_remote_approval_gate("http://gateway.test", client=http),
        approval_ledger=RemoteApprovalLedger("http://gateway.test", client=http),
    )
    await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "claude-code", "version": "2.1.316"},
                },
            }
        )
    )
    return server


async def _state(server: Any, headers: dict[str, str], **arguments: Any) -> dict[str, Any]:
    args = {"project_id": PROJECT, "fields": {"machine_or_supplier_profile": P1S}, **arguments}
    with with_context(context_from_headers(headers)):
        reply: dict[str, Any] = json.loads(
            await server.handle_request(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {"name": TOOL, "arguments": args},
                    }
                )
            )
        )
    return reply


def _runs() -> list[Any]:
    from api_gateway.chat.tool_approvals import get_approval_store

    return list(get_approval_store().list())


async def _machine_field(twin: InMemoryTwinAPI) -> Any:
    stored = await mc.effective_context(twin, PROJECT, None)
    return None if stored is None else stored.record.machine_or_supplier_profile


async def _auto_policy() -> None:
    from api_gateway.approvals.policy import get_policy_store

    await get_policy_store().put(
        ApprovalPolicy(project_id=PROJECT, mode="auto", updated_by="user:fidel")
    )


# -- the rule ---------------------------------------------------------------


def test_the_statement_tool_is_delegable_but_not_human_authority() -> None:
    assert delegable_human_authority(TOOL)
    assert not requires_human_authority(TOOL)
    assert delegable_human_authority("twin.imagine_handover")
    assert not delegable_human_authority("project.delete")
    # A client can never name the agent the tool records.
    assert DELEGATED_AGENT_ARG not in strip_reserved_arguments({DELEGATED_AGENT_ARG: "me"})


# -- over MCP ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_the_machine_is_refused(
    gateway: httpx.AsyncClient, twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    # The project's policy approves the write, as on the run that stalled.
    await _auto_policy()
    async with gateway as http:
        server = await _server(http, twin)
        reply = await _state(server, HEADERS)
    assert "never assumed" in json.dumps(reply), reply
    assert await _machine_field(twin) is None
    assert all(r.request.get("route") != "delegated" for r in _runs())


@pytest.mark.asyncio
async def test_flag_on_and_delegated_the_machine_is_stated_for_the_person(
    gateway: httpx.AsyncClient,
    twin: InMemoryTwinAPI,
    machine_library: SqliteMachineLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLAG, "true")
    async with gateway as http:
        server = await _server(http, twin)
        reply = await _state(server, HEADERS)
    assert "error" not in reply, reply
    field = await _machine_field(twin)
    assert field is not None
    assert field.authority.value == "user_stated"
    assert field.confirmed
    # The agent stated it, for the person: never the person alone.
    assert field.provenance.actor == "agent:claude-code-e2e"
    assert field.provenance.on_behalf_of == "fidel"
    assert field.provenance.route == "delegated"
    assert field.provenance.source == "mcp_delegated"
    # The library holds the P1S, so it is linked by id.
    (p1s,) = [m for m in machine_library.list() if f"{m.make} {m.model}" == P1S]
    assert field.value["machine_id"] == p1s.id
    assert field.value["machine"] == P1S
    # The ledger shows the delegated approval.
    (run,) = [r for r in _runs() if r.request.get("route") == "delegated"]
    assert run.request["tool"] == TOOL
    assert run.request["agent"] == "claude-code-e2e"
    assert run.request["on_behalf_of"] == "fidel"


@pytest.mark.parametrize("missing", list(HEADERS))
@pytest.mark.asyncio
async def test_flag_on_but_not_delegated_the_machine_is_refused(
    missing: str,
    gateway: httpx.AsyncClient,
    twin: InMemoryTwinAPI,
    machine_library: SqliteMachineLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLAG, "true")
    await _auto_policy()
    headers = {k: v for k, v in HEADERS.items() if k != missing}
    async with gateway as http:
        server = await _server(http, twin)
        reply = await _state(server, headers)
    assert "never assumed" in json.dumps(reply), reply
    assert await _machine_field(twin) is None
    assert all(r.request.get("route") != "delegated" for r in _runs())


# -- the tool ---------------------------------------------------------------


async def test_a_delegated_proposal_keeps_its_other_values_assumptions(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    out = await tools.state_context(
        twin,
        PROJECT,
        {"selected_process": "FDM", "machine_or_supplier_profile": "Bambu P1S"},
        approved_by="fidel",
        delegated_agent="claude-code-e2e",
    )
    assert out["stated_by"] == "agent:claude-code-e2e"
    assert out["on_behalf_of"] == "fidel" and out["route"] == "delegated"
    record = out["record"]
    assert record["selected_process"]["authority"] == "assumption"
    assert record["selected_process"]["provenance"]["on_behalf_of"] == "fidel"
    machine = record["machine_or_supplier_profile"]
    assert machine["authority"] == "user_stated"
    # Worded differently from the library, still the one P1S.
    assert machine["value"]["machine"] == P1S and machine["value"]["machine_id"]


async def test_an_unknown_machine_is_recorded_by_name(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    out = await tools.state_context(
        twin,
        PROJECT,
        {"machine_or_supplier_profile": "Garage Printer 3000"},
        approved_by="fidel",
        delegated_agent="claude-code-e2e",
    )
    value = out["record"]["machine_or_supplier_profile"]["value"]
    assert value["machine"] == "Garage Printer 3000"
    assert "machine_id" not in value
    assert "machine" not in out["record"]["unknowns"]


async def test_an_ambiguous_name_links_nothing(machine_library: SqliteMachineLibrary) -> None:
    assert tools.find_machine("Bambu Lab") is None
    assert tools.link_machine({"supplier": "JLCPCB"}) == {"supplier": "JLCPCB"}


async def test_the_agent_without_a_person_is_not_delegated(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    # The agent's name alone (no approving person) delegates nothing.
    with pytest.raises(tools.ManufacturingToolError, match="never assumed"):
        await tools.state_context(
            twin,
            PROJECT,
            {"machine_or_supplier_profile": P1S},
            delegated_agent="claude-code-e2e",
        )
    with pytest.raises(tools.ManufacturingToolError, match="never assumed"):
        await tools.state_context(
            twin,
            PROJECT,
            {"machine_or_supplier_profile": P1S},
            approved_by="policy:auto:owner",
            delegated_agent="claude-code-e2e",
        )


# -- DFM then has a machine -------------------------------------------------


async def test_dfm_then_assesses_the_part_against_the_machine(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    from api_gateway.twin import dfm_exact

    _key, node_id = await _project(twin, machine_library, machine=False)
    before = dfm.process_context(await mc.effective_context(twin, PROJECT, None))
    assert before.machine is None

    await tools.state_context(
        twin,
        PROJECT,
        {"machine_or_supplier_profile": P1S},
        approved_by="fidel",
        delegated_agent="claude-code-e2e",
    )
    ctx = dfm.process_context(await mc.effective_context(twin, PROJECT, None))
    assert ctx.machine is not None and ctx.machine.name == P1S
    assert ctx.machine.capabilities["build_volume_mm"].value == [256, 256, 256]

    # Heuristic DFM (twin.evaluate_dfm without measuring) reads it.
    results = await dfm.evaluate_project(twin, PROJECT)
    for _g, _n, fs, _c in results:
        assert not any(n.reason == "the machine is unknown" for n in fs.not_assessed)

    # The exact DFM checks the build volume against the P1S and passes.
    out = await dfm_exact.run_exact(
        twin, PROJECT, node_id=str(node_id), service=FakeCad(measured())
    )
    assert out["status"] == "analysis_passed" and out["complete"], out
