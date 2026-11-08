"""The MCP sidecar serves the release path the gateway serves.

Found in an autonomous run over MCP HTTP: ``twin.measure_dfm`` and
``twin.evaluate_dfm`` reported "the CAD adapter is not available" for every
CAD item while ``health.check`` showed the cadquery adapter reachable,
because the sidecar built its exact CAD service and never bound it as the
process-wide one ``api_gateway.twin.dfm_exact`` reads. And the release tools
(``twin.run_item_fea``, ``twin.resolve_concept_component``,
``twin.create_baseline``, ``twin.create_release_package``) "aren't on this
server": the sidecar passed none of the collaborators that register them.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from api_gateway.twin import exact_cad
from metaforge.mcp.__main__ import _build_evidence_bindings

RELEASE_TOOLS = {
    "twin.measure_dfm",
    "twin.evaluate_dfm",
    "twin.get_release_checklist",
    "twin.resolve_concept_component",
    "twin.run_item_fea",
    "twin.approve_release",
    "twin.create_baseline",
    "twin.create_release_package",
}


@pytest.fixture
def twin() -> Any:
    from twin_core.api import InMemoryTwinAPI

    return InMemoryTwinAPI.create()


@pytest.fixture(autouse=True)
def _unbound() -> Iterator[None]:
    exact_cad.set_exact_cad_service(None)
    yield
    exact_cad.set_exact_cad_service(None)


def test_the_sidecar_binds_the_cad_service_dfm_reads(twin: Any) -> None:
    from api_gateway.twin import dfm_exact

    bindings, _bridges = _build_evidence_bindings(twin, None)
    assert exact_cad.get_exact_cad_service() is bindings["exact_cad_service"]
    # The service the exact DFM measure and schedule paths use.
    assert dfm_exact._service() is bindings["exact_cad_service"]  # noqa: SLF001


def test_the_sidecar_builds_the_release_collaborators(twin: Any) -> None:
    bindings, _bridges = _build_evidence_bindings(twin, None)
    for name in (
        "dfm_service",
        "release_path_service",
        "baseline_creator",
        "release_package_creator",
    ):
        assert bindings.get(name) is not None, name


async def test_the_sidecar_tool_list_has_the_release_path(twin: Any) -> None:
    from metaforge.mcp.server import build_unified_server

    bindings, _bridges = _build_evidence_bindings(twin, None)
    server = await build_unified_server(
        adapter_ids=[], twin=twin, constraint_engine=twin.constraints, **bindings
    )
    tools = {t.tool_id for t in server.tool_registry.list_tools()}
    missing = RELEASE_TOOLS - tools
    assert not missing, f"the sidecar does not serve {sorted(missing)}"
