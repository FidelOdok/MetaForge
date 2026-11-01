"""Vendor connector contract suite (FORGE-427).

Every connector must pass these. A real vendor joins by adding a factory to
``CONTRACT_CONNECTORS`` that builds it against a mock transport.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.twin import routes as twin_routes
from api_gateway.vendors import routes as vendor_routes
from api_gateway.vendors import service
from api_gateway.vendors.connectors import FakeVendorConnector, VendorConnector, VendorError
from api_gateway.vendors.mcp_bindings import LocalVendorService
from mcp_core.annotations import ADDITIVE, DESTRUCTIVE, OPEN_WORLD, READ_ONLY
from metaforge.mcp.remote_vendors import RemoteVendorService
from tool_registry.tools.vendor.adapter import VendorServer
from twin_core.api import InMemoryTwinAPI

CONTRACT_CONNECTORS: dict[str, Callable[[], VendorConnector]] = {
    "fake": lambda: FakeVendorConnector(environ={}),
}

PCB_REQUEST = {
    "service": "pcb_fabrication",
    "items": [{"part_number": "controller-board-rev-a", "quantity": 1}],
    "quantity": 5,
}


@pytest.fixture
def twin() -> Any:
    previous = twin_routes.get_twin()
    t = InMemoryTwinAPI.create()
    twin_routes.init_twin(t)
    yield t
    twin_routes.init_twin(previous)


@pytest.fixture(params=sorted(CONTRACT_CONNECTORS))
def connector(request: pytest.FixtureRequest, twin: Any) -> Any:
    conn = CONTRACT_CONNECTORS[request.param]()
    service.init_connectors({conn.id: conn})
    service.init_vendor_store(service.SqliteVendorStore())
    yield conn
    service.init_connectors(None)
    service.init_vendor_store(None)


class TestContract:
    @pytest.mark.asyncio
    async def test_auth_state_is_reported(self, connector: VendorConnector) -> None:
        status = await connector.auth_status()
        assert status["auth"] == "ok"
        assert status["mode"] in ("sandbox", "production")

    @pytest.mark.asyncio
    async def test_a_quote_has_priced_lines_its_mode_and_a_twin_record(
        self, connector: VendorConnector, twin: Any
    ) -> None:
        result = await service.quote({**PCB_REQUEST, "connector_id": connector.id}, by="t")
        quote = result["quote"]
        assert quote["lines"] and all(ln["unit_cost"] >= 0 for ln in quote["lines"])
        assert quote["mode"] == connector.mode
        assert quote["valid_until"] > time.time()
        node = await twin.get_work_product(UUID(quote["twin_node_id"]))
        assert node is not None
        assert node.type.value == "procurement_record"

    @pytest.mark.asyncio
    async def test_an_order_is_placed_once_recorded_and_tracked(
        self, connector: VendorConnector, twin: Any
    ) -> None:
        quote = (await service.quote({**PCB_REQUEST, "connector_id": connector.id}))["quote"]
        order = (await service.place_order(quote["id"], by="person"))["order"]
        assert order["vendor_order_ref"]
        assert order["mode"] == connector.mode
        assert order["twin_node_id"] and order["twin_node_id"] != quote["twin_node_id"]
        with pytest.raises(service.VendorServiceError, match="already ordered"):
            await service.place_order(quote["id"])
        status = await service.order_status(order["id"])
        assert status["status"]["state"] in ("placed", "in_review", "in_production")

    @pytest.mark.asyncio
    async def test_an_expired_quote_is_not_ordered(self, connector: VendorConnector) -> None:
        quote = (await service.quote({**PCB_REQUEST, "connector_id": connector.id}))["quote"]
        service.get_vendor_store().update(quote["id"], "quote", {"valid_until": time.time() - 1})
        with pytest.raises(service.VendorServiceError, match="expired"):
            await service.place_order(quote["id"])

    @pytest.mark.asyncio
    async def test_an_unsupported_service_is_refused_loudly(
        self, connector: VendorConnector
    ) -> None:
        unsupported = next(
            s for s in ("moulding", "components", "stencil") if s not in connector.services
        )
        with pytest.raises(VendorError) as exc:
            await service.quote(
                {**PCB_REQUEST, "service": unsupported, "connector_id": connector.id}
            )
        assert exc.value.kind == "not_supported"


@pytest.mark.asyncio
async def test_a_refused_key_is_an_auth_error_not_an_empty_success(twin: Any) -> None:
    fake = FakeVendorConnector(environ={})
    fake.refuse_key = True
    assert (await fake.auth_status())["auth"] == "refused"
    with pytest.raises(VendorError) as exc:
        await fake.quote(PCB_REQUEST)
    assert exc.value.kind == "auth"


class _KeyedFake(FakeVendorConnector):
    id = "keyed"

    @property
    def credential_env(self) -> str:
        return "METAFORGE_VENDOR_KEYED_KEY"


@pytest.mark.asyncio
async def test_a_missing_credential_is_reported_as_missing() -> None:
    status = await _KeyedFake(environ={}).auth_status()
    assert status["auth"] == "missing"
    assert "METAFORGE_VENDOR_KEYED_KEY" in status["detail"]
    ok = await _KeyedFake(environ={"METAFORGE_VENDOR_KEYED_KEY": "k"}).auth_status()
    assert ok["auth"] == "ok"


@pytest.mark.asyncio
async def test_mode_is_explicit_and_a_sandbox_quote_cannot_become_a_production_order(
    twin: Any,
) -> None:
    assert FakeVendorConnector(environ={}).mode == "sandbox"
    prod = FakeVendorConnector(environ={"METAFORGE_VENDOR_FAKE_MODE": "production"})
    assert prod.mode == "production"
    # The fake vendor has no production mode, and says so.
    assert (await prod.auth_status())["auth"] == "missing"
    with pytest.raises(VendorError, match="no production mode"):
        await prod.quote(PCB_REQUEST)
    sandbox = FakeVendorConnector(environ={})
    quote = await sandbox.quote(PCB_REQUEST)
    sandbox.mode = "production"
    sandbox.supported_modes = ("sandbox", "production")
    with pytest.raises(VendorError, match="quote again"):
        await sandbox.place_order(quote)


@pytest.mark.asyncio
async def test_calls_are_paced_by_the_rate_limit() -> None:
    fake = FakeVendorConnector(environ={})
    fake.min_interval_seconds = 0.2
    start = time.monotonic()
    await fake.auth_status()
    await fake.auth_status()
    assert time.monotonic() - start >= 0.19


def test_annotations_hold_the_order() -> None:
    assert "vendor.place_order" in DESTRUCTIVE
    assert "vendor.place_order" not in READ_ONLY
    assert "vendor.quote" in ADDITIVE
    assert {"vendor.list_connectors", "vendor.order_status"} <= READ_ONLY
    assert {"vendor.quote", "vendor.place_order"} <= OPEN_WORLD


@pytest.fixture
def gateway(twin: Any) -> Any:
    fake = FakeVendorConnector(environ={})
    service.init_connectors({"fake": fake})
    service.init_vendor_store(service.SqliteVendorStore())
    app = FastAPI()
    app.include_router(vendor_routes.router)
    yield TestClient(app)
    service.init_connectors(None)
    service.init_vendor_store(None)


@pytest.mark.asyncio
async def test_local_and_remote_bindings_answer_alike(gateway: TestClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        response = gateway.request(request.method, str(request.url.raw_path, "ascii"), json=body)
        return httpx.Response(response.status_code, json=response.json())

    local = VendorServer(LocalVendorService())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        remote = VendorServer(RemoteVendorService("http://gw", client=http))
        assert await local.list_connectors({}) == await remote.list_connectors({})
        quote = (await remote.quote({**PCB_REQUEST, "connector_id": "fake"}))["quote"]
        assert quote["connector"] == "fake"
        order = (await remote.place_order({"quote_id": quote["id"]}))["order"]
        assert (await local.order_status({"order_id": order["id"]})) == (
            await remote.order_status({"order_id": order["id"]})
        )
        with pytest.raises(Exception, match="no vendor connector 'nope'"):
            await remote.quote({**PCB_REQUEST, "connector_id": "nope"})


def test_routes_say_why_a_quote_failed(gateway: TestClient) -> None:
    unknown = gateway.post("/v1/vendors/quotes", json={**PCB_REQUEST, "connector_id": "nope"})
    assert unknown.status_code == 422
    wrong = gateway.post(
        "/v1/vendors/quotes", json={**PCB_REQUEST, "service": "moulding", "connector_id": "fake"}
    )
    assert wrong.status_code == 422
    assert wrong.json()["detail"].startswith("not_supported")


@pytest.mark.asyncio
async def test_health_lists_every_connector_and_its_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    from metaforge.mcp.server import UnifiedMcpServer

    monkeypatch.delenv("METAFORGE_GATEWAY_URL", raising=False)
    refused = FakeVendorConnector(environ={})
    refused.refuse_key = True
    service.init_connectors({"fake": refused})
    try:
        report = await UnifiedMcpServer._vendor_connectors_report()
    finally:
        service.init_connectors(None)
    assert report["status"] == "degraded"
    assert report["problems"] == ["fake"]
    assert report["connectors"][0]["auth"] == "refused"


@pytest.mark.asyncio
async def test_bootstrap_registers_vendor_tools_when_bound() -> None:
    from tool_registry.bootstrap import bootstrap_tool_registry

    registry = await bootstrap_tool_registry(
        adapter_ids=["vendor"], vendor_service=LocalVendorService()
    )
    tools = {t.tool_id for t in registry.list_tools()}
    assert {"vendor.quote", "vendor.place_order"} <= tools
