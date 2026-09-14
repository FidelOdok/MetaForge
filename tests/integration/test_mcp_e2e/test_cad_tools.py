"""Phase 3 — cad / sim MCP tools coverage (MET-477).

Four adapters are bootstrap-registered by ``tool_registry``: ``cadquery.*``,
``freecad.*``, ``calculix.*`` and ``kicad.*``. Per-adapter tool counts are
deliberately **not** written down here -- MET-730: the counts that used to be
in this docstring went stale (it claimed a 30-tool total while the assertion
below it demanded 55, and the real number was 68), so the expectations live in
the ``_EXPECTED_*_TOOLS`` sets and are checked by set difference instead.

Full happy-path execution requires the real backends (cadquery library,
FreeCAD headless, ``ccx`` solver). They are not installed in the CI
container, so this file focuses on what's reproducible *without* the
backends:

* inventory: every documented tool appears in ``tools/list``
* adapter-level validation: missing required args + invalid enums
  surface as clean ``McpRpcError`` envelopes — the adapter rejects
  before forwarding to the backend, so this works without the binaries

KiCad joined the unified MCP bootstrap registry in MET-478; the docstring
here previously still described it as absent.
"""

from __future__ import annotations

import pytest

from ._helpers import McpRpcError, call_tool, rpc

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Inventory — also doubles as G2 regression guard for cadquery.
# ---------------------------------------------------------------------------


_EXPECTED_CADQUERY_TOOLS = {
    "cadquery.create_parametric",
    "cadquery.boolean_operation",
    "cadquery.get_properties",
    "cadquery.export_geometry",
    "cadquery.execute_script",
    "cadquery.create_assembly",
    "cadquery.generate_enclosure",
}
_EXPECTED_FREECAD_TOOLS = {
    "freecad.export_geometry",
    "freecad.generate_mesh",
    "freecad.boolean_operation",
    "freecad.get_properties",
    "freecad.create_parametric",
}
_EXPECTED_CALCULIX_TOOLS = {
    "calculix.run_fea",
    "calculix.run_thermal",
    "calculix.validate_mesh",
    "calculix.extract_results",
}


@pytest.mark.parametrize("adapter_id", ["cadquery", "freecad", "calculix"])
async def test_cad_adapter_registers_tools(mcp_client, adapter_id):
    """Each CAD adapter contributes at least one tool to tools/list.

    Parametrised so a regression in any one adapter shows up
    individually (a single test failure named after the broken
    adapter, not a generic "fewer tools than expected").
    """
    result = await rpc(mcp_client, "tools/list")
    names = [t["name"] for t in result.get("tools", [])]
    matches = [n for n in names if n.startswith(f"{adapter_id}.")]
    assert matches, (
        f"adapter {adapter_id!r} registered no tools — check bootstrap fallback path (G2)"
    )


async def test_cadquery_full_tool_set(mcp_client):
    """G2 regression: all 7 cadquery tools must register in-process."""
    result = await rpc(mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}
    missing = _EXPECTED_CADQUERY_TOOLS - tool_ids
    assert not missing, f"missing cadquery tools (G2 regression?): {missing}"


async def test_freecad_full_tool_set(mcp_client):
    result = await rpc(mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}
    missing = _EXPECTED_FREECAD_TOOLS - tool_ids
    assert not missing, f"missing freecad tools: {missing}"


async def test_calculix_full_tool_set(mcp_client):
    result = await rpc(mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}
    missing = _EXPECTED_CALCULIX_TOOLS - tool_ids
    assert not missing, f"missing calculix tools: {missing}"


async def test_cadquery_register_floor(mcp_client):
    """The cadquery adapter ships 7 tools; floor at 5 to keep some room.

    G2 specifically blocked the cadquery surface in the MET-477 smoke,
    so this test gets a tighter floor than the other CAD adapters.
    """
    result = await rpc(mcp_client, "tools/list")
    cadquery_tools = [t for t in result.get("tools", []) if t["name"].startswith("cadquery.")]
    assert len(cadquery_tools) >= 5, (
        f"cadquery exposes only {len(cadquery_tools)} tools — expected at least 5"
    )


_EXPECTED_KICAD_TOOLS = {
    "kicad.run_erc",
    "kicad.run_drc",
    "kicad.export_bom",
    "kicad.export_gerber",
    "kicad.export_netlist",
    "kicad.get_pin_mapping",
}


async def test_kicad_tools_register_in_unified_bootstrap(mcp_client):
    """KiCad now registers in the unified MCP bootstrap (MET-478 blocker fix).

    Pre-MET-478: KiCad shipped as a separate stdio entrypoint and was
    absent from ``tools/list``, forcing the EE vertical scenario to
    skip steps 3-6. Post-MET-478: ``KicadServer`` is registered via
    ``tool_registry.bootstrap._ADAPTER_REGISTRY``, all 6 kicad.* tools
    surface, and the EE vertical scenario in
    ``test_vertical_electronics.py`` executes them end-to-end. The
    handlers themselves still need the KiCad CLI binary in PATH at
    runtime to actually succeed; without it each call surfaces as
    ``-32001 TOOL_EXECUTION_ERROR`` (which the EE scenario's
    ``_attempt()`` helper treats as acceptable in CI).
    """
    result = await rpc(mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}
    missing = _EXPECTED_KICAD_TOOLS - tool_ids
    assert not missing, f"missing kicad tools post-MET-478: {missing}"


_CAD_SIM_ADAPTERS = {"cadquery", "freecad", "calculix", "kicad"}


async def test_every_documented_cad_sim_tool_is_exposed(mcp_client):
    """The union of the per-adapter expectations, in one assertion.

    MET-730: this replaced a hardcoded ``len(cad_ids) == 55``. That literal
    had been hand-edited at least three times (most recently "bump
    cross-adapter tool-count assertions for MET-629") and its own docstring
    still claimed the total was 30, which is how much a magic number tells
    you. It also failed for a whole week unnoticed, because CI never ran this
    suite.

    A count cannot distinguish "a tool was removed" from "a tool was added",
    which are opposite problems. Set difference says which, and needs no edit
    when the catalog grows.
    """
    result = await rpc(mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}

    expected = (
        _EXPECTED_CADQUERY_TOOLS
        | _EXPECTED_FREECAD_TOOLS
        | _EXPECTED_CALCULIX_TOOLS
        | _EXPECTED_KICAD_TOOLS
    )
    missing = expected - tool_ids
    assert not missing, f"documented CAD/sim tools absent from tools/list: {sorted(missing)}"


async def test_no_tool_id_is_registered_twice(mcp_client):
    """Two adapters claiming one id is a wiring bug -- the second silently
    shadows the first at dispatch.

    Being straight about this one's reach: in the default in-process mode it
    cannot fail, because ``UnifiedMcpServer.__init__`` raises on a collision
    and the fixture would blow up before the test body runs. It earns its
    place in the conftest's **live mode** (``METAFORGE_MCP_URL``), where
    ``tools/list`` is whatever a running server actually serves, and as a
    guard on that constructor check ever being relaxed.
    """
    result = await rpc(mcp_client, "tools/list")
    names = [t.get("name") for t in result.get("tools", [])]

    assert names, "tools/list returned nothing; this test would pass vacuously"
    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, f"tool ids registered more than once: {sorted(duplicates)}"


async def test_cad_sim_tool_ids_are_all_well_formed(mcp_client):
    """Every CAD/sim tool is ``<known adapter>.<tool>``.

    Catches a stray or misnamespaced registration, which is the other thing
    an exact count would have caught -- without breaking on every legitimate
    addition.
    """
    result = await rpc(mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", []) if t.get("name")}

    cad_ids = {tid for tid in tool_ids if tid.split(".", 1)[0] in _CAD_SIM_ADAPTERS}
    assert cad_ids, "no CAD/sim tools registered at all"

    malformed = {tid for tid in cad_ids if "." not in tid or not tid.split(".", 1)[1]}
    assert not malformed, f"malformed CAD/sim tool ids: {sorted(malformed)}"


# ---------------------------------------------------------------------------
# Adapter-level validation — rejects before forwarding to the backend.
# Full happy-path execution requires Docker + real cadquery / FreeCAD /
# CalculiX. Those tests live in Phase 5 vertical scenarios under live mode.
# ---------------------------------------------------------------------------


# CadQuery -------------------------------------------------------------------


async def test_cadquery_create_parametric_requires_shape_type(mcp_client):
    with pytest.raises(McpRpcError):
        await call_tool(mcp_client, "cadquery.create_parametric", {})


async def test_cadquery_create_parametric_rejects_unsupported_shape(mcp_client):
    with pytest.raises(McpRpcError):
        await call_tool(
            mcp_client,
            "cadquery.create_parametric",
            {
                "shape_type": "not-a-real-shape",
                "parameters": {"length": 10},
                "output_path": "/tmp/x.step",
            },
        )


async def test_cadquery_boolean_operation_rejects_unknown_op(mcp_client):
    with pytest.raises(McpRpcError):
        await call_tool(
            mcp_client,
            "cadquery.boolean_operation",
            {
                "input_file_a": "/tmp/a.step",
                "input_file_b": "/tmp/b.step",
                "operation": "merge",  # not one of union/subtract/intersect
                "output_path": "/tmp/c.step",
            },
        )


async def test_cadquery_get_properties_requires_input_file(mcp_client):
    with pytest.raises(McpRpcError):
        await call_tool(mcp_client, "cadquery.get_properties", {})


# FreeCAD --------------------------------------------------------------------


async def test_freecad_create_parametric_rejects_unsupported_shape(mcp_client):
    with pytest.raises(McpRpcError):
        await call_tool(
            mcp_client,
            "freecad.create_parametric",
            {
                "shape_type": "not-a-real-shape",
                "parameters": {"length": 10},
                "output_path": "/tmp/x.step",
            },
        )


async def test_freecad_export_geometry_rejects_unsupported_format(mcp_client):
    with pytest.raises(McpRpcError):
        await call_tool(
            mcp_client,
            "freecad.export_geometry",
            {
                "input_file": "/tmp/in.step",
                "output_format": "gif",  # not one of step/stl/obj/brep
                "output_path": "/tmp/out.gif",
            },
        )


# CalculiX -------------------------------------------------------------------


async def test_calculix_run_fea_requires_mesh_file(mcp_client):
    with pytest.raises(McpRpcError):
        await call_tool(
            mcp_client,
            "calculix.run_fea",
            {"load_case": "lc1"},
        )


async def test_calculix_run_fea_rejects_unsupported_analysis_type(mcp_client):
    with pytest.raises(McpRpcError):
        await call_tool(
            mcp_client,
            "calculix.run_fea",
            {
                "mesh_file": "/tmp/mesh.inp",
                "load_case": "lc1",
                "analysis_type": "transient_thermal",  # not static_stress/modal
            },
        )


async def test_calculix_validate_mesh_requires_mesh_file(mcp_client):
    with pytest.raises(McpRpcError):
        await call_tool(mcp_client, "calculix.validate_mesh", {})
