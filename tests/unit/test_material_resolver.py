"""Material names resolve only against the library (FORGE-446, FORGE-457)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.library import catalog_routes
from metaforge.mcp.remote_library import remote_material_source
from tool_registry.material_context import extract, using
from tool_registry.tools.cadquery.materials import resolve_density_kg_m3
from twin_core.library.catalog import SqliteCatalog
from twin_core.library.material_resolver import (
    MaterialResolutionError,
    catalog_source,
    fill_material_arguments,
    install_material_source,
    resolve_material,
)

pytestmark = pytest.mark.no_material_library

DS = "https://example.com/6061.pdf"


def _material(name: str, title: str, designation: str, density: float) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "family": "metal",
        "designation": designation,
        "properties": {
            "density_kg_m3": {
                "value": density,
                "unit": "kg/m3",
                "source_kind": "datasheet",
                "source_ref": DS,
            }
        },
    }


@pytest.fixture
def catalog() -> Any:
    cat = SqliteCatalog()
    cat.register(
        "material", _material("aluminium_6061_t6", "Aluminium 6061-T6", "EN AW-6061 T6", 2700)
    )
    cat.register(
        "material", _material("aluminium_7075_t6", "Aluminium 7075-T6", "EN AW-7075 T6", 2810)
    )
    cat.register("material", _material("steel_s235jr", "Structural steel S235JR", "S235JR", 7850))
    install_material_source(catalog_source(lambda: cat))
    catalog_routes.init_catalog(cat)
    yield cat
    install_material_source(None)
    catalog_routes.init_catalog(None)


@pytest.mark.asyncio
async def test_a_name_title_or_designation_resolves_exactly(catalog: SqliteCatalog) -> None:
    for name in ("aluminium_6061_t6", "Aluminium 6061-T6", "EN AW-6061 T6"):
        block = await resolve_material(name)
        assert block["name"] == "aluminium_6061_t6"
        assert block["properties"]["density_kg_m3"]["source_ref"] == DS


@pytest.mark.asyncio
async def test_a_generic_name_is_refused_with_candidates(catalog: SqliteCatalog) -> None:
    with pytest.raises(MaterialResolutionError) as exc:
        await resolve_material("aluminum")
    assert set(exc.value.candidates) >= {"aluminium_6061_t6", "aluminium_7075_t6"}
    assert "library.list_materials" in str(exc.value)
    with pytest.raises(MaterialResolutionError):
        await resolve_material("unobtainium")


@pytest.mark.asyncio
async def test_the_filler_reaches_every_shape(catalog: SqliteCatalog) -> None:
    args: dict[str, Any] = {
        "material": "steel_s235jr",
        "parts": [{"material": "aluminium_6061_t6"}],
        "fea": {"material": {"name": "aluminium_7075_t6"}},
        "candidate_materials": ["steel_s235jr", "aluminium_7075_t6"],
    }
    names = await fill_material_arguments("cadquery.create_assembly", args)
    assert names == ["aluminium_6061_t6", "aluminium_7075_t6", "steel_s235jr"]
    figures = extract(args)
    assert set(figures) == {"steel_s235jr", "aluminium_6061_t6", "aluminium_7075_t6"}
    with using(figures):
        assert resolve_density_kg_m3("aluminium_7075_t6") == 2810


@pytest.mark.asyncio
async def test_an_unresolved_material_fails_the_call(catalog: SqliteCatalog) -> None:
    with pytest.raises(MaterialResolutionError):
        await fill_material_arguments("cadquery.create_parametric", {"material": "pla"})


@pytest.mark.asyncio
async def test_other_tools_are_not_touched(catalog: SqliteCatalog) -> None:
    args = {"material": "anything"}
    assert await fill_material_arguments("library.process_limits", args) == []
    assert "material_properties" not in args


@pytest.mark.asyncio
async def test_the_sidecar_asks_the_gateway_and_gets_the_same_answer(
    catalog: SqliteCatalog,
) -> None:
    app = FastAPI()
    app.include_router(catalog_routes.router)
    gateway = TestClient(app)

    def handler(request: httpx.Request) -> httpx.Response:
        response = gateway.get(str(request.url.raw_path, "ascii"))
        return httpx.Response(response.status_code, json=response.json())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        remote = remote_material_source("http://gw", client=http)
        assert await remote("Aluminium 6061-T6") == await resolve_material("Aluminium 6061-T6")
        with pytest.raises(MaterialResolutionError) as exc:
            await remote("aluminum")
        assert "aluminium_6061_t6" in exc.value.candidates


@pytest.mark.asyncio
async def test_the_bridge_fails_a_call_with_an_unresolved_material(
    catalog: SqliteCatalog,
) -> None:
    from skill_registry.mcp_bridge import McpToolError
    from skill_registry.registry_bridge import RegistryMcpBridge
    from tool_registry.bootstrap import bootstrap_tool_registry

    registry = await bootstrap_tool_registry(adapter_ids=["cadquery"])
    bridge = RegistryMcpBridge(registry, argument_filler=fill_material_arguments)
    with pytest.raises(McpToolError, match="not in the material library"):
        await bridge.invoke(
            "cadquery.create_parametric",
            {
                "shape_type": "box",
                "parameters": {"length": 1, "width": 1, "height": 1},
                "material": "pla",
            },
        )


@pytest.mark.asyncio
async def test_a_cad_call_through_the_server_uses_the_library_density(
    catalog: SqliteCatalog,
) -> None:
    """The figures travel with the call into the adapter (the container path)."""
    import json

    from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
    from tool_registry.mcp_server.server import McpToolServer

    seen: dict[str, Any] = {}

    class Probe(McpToolServer):
        def __init__(self) -> None:
            super().__init__(adapter_id="cadquery", version="0")
            self.register_tool(
                manifest=ToolManifest(
                    tool_id="cadquery.probe",
                    adapter_id="cadquery",
                    name="probe",
                    description="",
                    capability="probe",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    phase=1,
                    resource_limits=ResourceLimits(),
                ),
                handler=self.probe,
            )

        async def probe(self, arguments: dict[str, Any]) -> dict[str, Any]:
            seen["keys"] = sorted(arguments)
            seen["density"] = resolve_density_kg_m3(arguments["material"])
            return {}

    args: dict[str, Any] = {"material": "aluminium_6061_t6"}
    await fill_material_arguments("cadquery.probe", args)
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tool/call",
            "params": {"tool_id": "cadquery.probe", "arguments": args},
        }
    )
    await Probe().handle_request(raw)
    assert seen == {"keys": ["material"], "density": 2700}


@pytest.mark.asyncio
async def test_us_and_uk_spellings_of_aluminium_are_one_material(catalog: SqliteCatalog) -> None:
    block = await resolve_material("Aluminum 6061-T6")
    assert block["name"] == "aluminium_6061_t6"
