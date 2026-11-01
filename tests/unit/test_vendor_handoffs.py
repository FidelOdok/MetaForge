"""vendor.handoffs: where to send a part when a vendor has no API (FORGE-614)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from api_gateway.twin import routes as twin_routes
from api_gateway.vendors.handoffs import HANDOFFS, handoffs_for
from api_gateway.vendors.mcp_bindings import LocalVendorService
from mcp_core.annotations import OPEN_WORLD, READ_ONLY
from mcp_core.profiles import PROFILES
from tool_registry.tools.vendor.adapter import SERVICES, VendorServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> Any:
    previous = twin_routes.get_twin()
    t = InMemoryTwinAPI.create()
    twin_routes.init_twin(t)
    yield t
    twin_routes.init_twin(previous)


async def _file(twin: Any, name: str, *, stored: bool = True) -> str:
    stem, ext = name.rsplit(".", 1)
    wp = await twin.create_work_product(
        WorkProduct(
            name=stem,
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path=f"/workspace/{name}" if stored else "",
            content_hash="h",
            format=ext,
            created_by="user",
            metadata={"original_filename": name},
        )
    )
    return str(wp.id)


@pytest.mark.asyncio
async def test_cnc_lists_vendors_and_the_files_each_can_take(twin: Any) -> None:
    step = await _file(twin, "bracket.step")
    stl = await _file(twin, "bracket.stl")
    result = await handoffs_for("cnc", [step, stl])
    by_id = {v["id"]: v for v in result["vendors"]}
    assert set(by_id) == {"xometry"}
    assert by_id["xometry"]["usable_files"] == [step, stl]
    assert by_id["xometry"]["ready"] is True
    files = {f["work_product_id"]: f for f in result["files"]}
    assert files[step]["download_path"] == f"/v1/twin/nodes/{step}/file?download=true"
    assert "Nothing was sent" in result["how"]
    assert "not checked against" in result["how"]


@pytest.mark.asyncio
async def test_a_file_in_the_wrong_format_is_not_offered(twin: Any) -> None:
    step = await _file(twin, "plate.step")
    result = await handoffs_for("laser_cutting", [step])
    scs = next(v for v in result["vendors"] if v["id"] == "sendcutsend")
    assert scs["usable_files"] == [] and scs["ready"] is False
    xometry = next(v for v in result["vendors"] if v["id"] == "xometry")
    assert xometry["usable_files"] == [step]


@pytest.mark.asyncio
async def test_missing_and_fileless_work_products_are_named(twin: Any) -> None:
    ghost = str(uuid4())
    empty = await _file(twin, "board.kicad_pcb", stored=False)
    result = await handoffs_for("pcb_fabrication", [ghost, empty, "not-a-uuid"])
    assert result["not_found"] == [ghost, "not-a-uuid"]
    aisler = next(v for v in result["vendors"] if v["id"] == "aisler")
    assert aisler["usable_files"] == []


@pytest.mark.asyncio
async def test_a_service_nobody_hands_off_returns_no_vendors(twin: Any) -> None:
    assert (await handoffs_for("components", []))["vendors"] == []


def test_every_catalogue_service_is_a_known_service() -> None:
    for vendor in HANDOFFS:
        assert set(vendor.services) <= set(SERVICES), vendor.id
        assert vendor.url.startswith("https://")


@pytest.mark.asyncio
async def test_the_tool_reads_only_and_stays_closed_world(twin: Any) -> None:
    assert "vendor.handoffs" in READ_ONLY
    assert "vendor.handoffs" not in OPEN_WORLD
    assert "vendor.handoffs" in PROFILES["manufacturing"]
    server = VendorServer(LocalVendorService())
    result = await server.handoffs({"service": "additive", "work_product_ids": []})
    assert {v["id"] for v in result["vendors"]} == {"craftcloud", "xometry"}
