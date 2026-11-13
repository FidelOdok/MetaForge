"""The sidecar's DFM reads the gateway's machine library (the lamp, 2026-11-11).

The sidecar binds ``library.*`` to the gateway over HTTP, but the DFM context
looks a machine up in the process library, which in the sidecar was an empty
in-memory store. A printer registered and selected in the manufacturing
context was then "not selected", and every exact DFM run there skipped the
build-volume check. ``GatewayMachineView`` is the read-only, synchronous view
the sidecar installs as its process library.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from api_gateway.library import routes as library_routes
from api_gateway.twin.dfm_findings import _machine_context
from metaforge.mcp.remote_library import GatewayMachineView

MODEL = {
    "id": "mm_cr10v3",
    "layer": "model",
    "kind": "fdm_printer",
    "make": "Creality",
    "model": "CR-10 V3",
    "processes": ["fdm_printing"],
    "capabilities": {
        "build_volume_mm": {
            "value": [300.0, 300.0, 400.0],
            "unit": "mm",
            "source_kind": "manufacturer_spec",
            "source_ref": "https://store.creality.com/blogs/all/cr-series-3d-printers-parameter-comparison",
            "needs_review": False,
        }
    },
    "version": 1,
    "updated_at": 1.0,
}
INSTANCE = {
    "id": "mi_home",
    "layer": "instance",
    "name": "My CR-10 V3 (home)",
    "kind": "fdm_printer",
    "model_id": "mm_cr10v3",
    "processes": ["fdm_printing"],
    "route": "in_house",
    "location": "home",
    "status": "available",
    "capabilities": {},
    "connection": {},
    "version": 1,
    "updated_at": 2.0,
}


def _gateway(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    rows = {MODEL["id"]: MODEL, INSTANCE["id"]: INSTANCE}
    if path == "/v1/library/machines":
        return httpx.Response(200, json={"machines": [MODEL, INSTANCE]})
    mid = path.rsplit("/", 1)[-1]
    if path.startswith("/v1/library/machines/") and mid in rows:
        return httpx.Response(200, json={"machine": rows[mid], "history": []})
    return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture
def installed() -> Iterator[None]:
    client = httpx.Client(transport=httpx.MockTransport(_gateway))
    library_routes.init_machine_library(
        GatewayMachineView("http://gateway.test", client=client)  # type: ignore[arg-type]
    )
    yield
    library_routes.init_machine_library(None)


def _volume(ctx: Any) -> Any:
    caps = getattr(ctx, "capabilities", None) or {}
    cap = caps.get("build_volume_mm") if isinstance(caps, dict) else None
    return getattr(cap, "value", None) if cap is not None else None


def test_without_the_view_the_sidecar_finds_no_machine() -> None:
    """The negative control: the default in-memory library is empty."""
    library_routes.init_machine_library(None)
    assert _machine_context({"machine_id": "mi_home", "machine": "My CR-10 V3 (home)"}) is None


def test_a_selected_instance_resolves_with_its_models_build_volume(installed: None) -> None:
    ctx = _machine_context({"machine_id": "mi_home", "machine": "My CR-10 V3 (home)"})
    assert ctx is not None
    assert list(_volume(ctx)) == [300.0, 300.0, 400.0]


def test_a_machine_named_without_an_id_resolves_by_name(installed: None) -> None:
    ctx = _machine_context({"machine": "My CR-10 V3 (home)"})
    assert ctx is not None and list(_volume(ctx)) == [300.0, 300.0, 400.0]


def test_an_unknown_machine_is_none_not_an_error(installed: None) -> None:
    assert _machine_context({"machine_id": "mi_nope"}) is None


def test_the_sidecar_installs_the_view_only_from_main() -> None:
    """``_bootstrap`` (what tests build) must not install it process-wide; ``main`` does."""
    from metaforge.mcp import __main__ as sidecar

    library_routes.init_machine_library(None)
    assert sidecar._install_gateway_machine_view({}) is False
    assert not isinstance(library_routes.get_machine_library(), GatewayMachineView)
    try:
        assert sidecar._install_gateway_machine_view({"METAFORGE_GATEWAY_URL": "http://gw"})
        assert isinstance(library_routes.get_machine_library(), GatewayMachineView)
    finally:
        library_routes.init_machine_library(None)
    source = open(sidecar.__file__).read()
    build = source[source.index("def _build_machine_library") :]
    build = build[: build.index("\ndef ", 1)]
    assert "GatewayMachineView" not in build
