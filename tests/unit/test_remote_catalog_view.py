"""The sidecar reads the gateway's library catalog (FORGE-903).

In the MCP sidecar the catalog was an empty in-memory store, so the Imagine studio
could not size a part from its library entry and the component thread found no
entry for a part, though the gateway had one: on staging, adopting Develop's
AL8862SP-13 through claude.ai carried no library link. ``GatewayCatalogView`` is the
read-only view the sidecar installs, carrying the caller's library view.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from api_gateway.library import catalog_routes
from metaforge.mcp.remote_library import GatewayCatalogView
from twin_core.library.catalog import CatalogNotFoundError
from twin_core.library.viewer import VIEW_HEADER, library_viewer

SRC = {"value": 105.0, "unit": "C", "source_kind": "datasheet", "source_ref": "https://x"}
MINE = {
    "id": "co_a8e635fe8567",
    "kind": "component",
    "name": "diodes_al8862sp_13",
    "family": "electronic",
    "mpn": "AL8862SP-13",
    "manufacturer": "Diodes Incorporated",
    "specs": {"operating_temp_max_c": SRC},
    "shared": False,
}
SEEN: list[dict[str, str]] = []


def _gateway(request: httpx.Request) -> httpx.Response:
    SEEN.append(dict(request.headers))
    path = request.url.path
    if path == "/v1/library/components":
        return httpx.Response(200, json={"entries": [MINE]})
    if path in (f"/v1/library/components/{MINE['id']}", f"/v1/library/components/{MINE['name']}"):
        return httpx.Response(200, json={"entry": MINE, "history": []})
    return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture
def installed() -> Iterator[None]:
    SEEN.clear()
    client = httpx.Client(transport=httpx.MockTransport(_gateway))
    catalog_routes.init_catalog(GatewayCatalogView("http://gateway.test", client=client))  # type: ignore[arg-type]
    yield
    catalog_routes.init_catalog(None)


def test_reads_go_to_the_gateway_with_the_callers_view(installed: None) -> None:
    catalog = catalog_routes.get_catalog()
    with library_viewer("tenant-a"):
        entry = catalog.get("diodes_al8862sp_13", "component")
    assert entry.as_dict()["mpn"] == "AL8862SP-13"
    assert entry.name == "diodes_al8862sp_13"
    assert SEEN[-1].get(VIEW_HEADER.lower())
    with pytest.raises(CatalogNotFoundError):
        catalog.get("nope", "component")


def test_the_component_thread_finds_an_accounts_own_entry_in_the_sidecar(installed: None) -> None:
    from api_gateway.twin.component_thread import library_components, library_for, library_specs

    entries = library_components()
    assert library_for("AL8862SP-13", entries) == {
        "id": MINE["id"],
        "name": "diodes_al8862sp_13",
        "shared": False,
    }
    assert library_specs(MINE["id"]) == {"operating_temp_max_c": {"value": 105.0, "unit": "degC"}}


def test_the_studio_reads_a_linked_parts_entry_in_the_sidecar(installed: None) -> None:
    from api_gateway.imagine.library_cost import catalog_entry

    entry: Any = catalog_entry("diodes_al8862sp_13")
    assert entry is not None and entry["mpn"] == "AL8862SP-13"
    assert catalog_entry("unknown_part") is None


def test_installed_only_with_a_gateway_url() -> None:
    from metaforge.mcp.__main__ import _install_gateway_catalog_view

    assert _install_gateway_catalog_view({}) is False
    try:
        assert _install_gateway_catalog_view({"METAFORGE_GATEWAY_URL": "http://gw.test"}) is True
        assert isinstance(catalog_routes.get_catalog(), GatewayCatalogView)
    finally:
        catalog_routes.init_catalog(None)
