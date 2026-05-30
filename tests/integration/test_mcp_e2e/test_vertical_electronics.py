"""Phase 5 — Electronics-vertical readiness scenario (MET-477).

Per the loop spec, the EE-agent tool sequence is:

1. ``project.create`` — new hardware project
2. ``knowledge.populate_bom`` — EE-relevant component query
3. ``kicad.run_erc``      ┐
4. ``kicad.run_drc``      │ — KiCad validation suite
5. ``kicad.export_bom``   │
6. ``kicad.export_gerber``┘
7. ``constraint.validate`` — gate the assembled WP list

KiCad bootstrap — MET-478
-------------------------
KiCad is now registered in the unified MCP bootstrap
(``tool_registry.bootstrap._ADAPTER_REGISTRY``), so all six kicad.*
tools surface in ``tools/list``. Pre-MET-478 the scenario skipped
steps 3-6 because those tools returned ``-32601 METHOD_NOT_FOUND``;
post-MET-478 the dispatcher routes them through the adapter and the
underlying handlers run.

In CI the KiCad CLI binary isn't in PATH on the GitHub Actions
runners, so each kicad.* call surfaces as ``-32001
TOOL_EXECUTION_ERROR`` (the adapter validates input, the
``KicadCliNotFoundError`` raises, the dispatcher returns a clean
envelope). The ``_attempt()`` helper treats that as an acceptable CI
outcome — same tolerance band as the mechanical vertical's cadquery /
calculix steps. Live mode (a deploy with the KiCad CLI installed)
flips the kicad.* outcomes to ``success``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from ._helpers import McpRpcError, call_tool

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fully-wired MCP fixture: project + knowledge + twin (+ constraint).
# ---------------------------------------------------------------------------


@pytest.fixture
async def electronics_vertical_client() -> AsyncIterator[httpx.AsyncClient]:
    """MCP client wired for the EE vertical scenario."""
    live_url = os.environ.get("METAFORGE_MCP_URL") or None
    if live_url:
        async with httpx.AsyncClient(base_url=live_url, timeout=60.0) as client:
            yield client
        return

    from api_gateway.projects.backend import InMemoryProjectBackend
    from digital_twin.knowledge.embedding_service import create_embedding_service
    from digital_twin.memory.client import MemoryClient
    from digital_twin.memory.consolidation import InMemoryInsightStore
    from digital_twin.memory.store import InMemoryExperienceStore
    from metaforge.mcp.__main__ import build_http_app
    from metaforge.mcp.server import build_unified_server
    from twin_core.api import InMemoryTwinAPI

    from .test_knowledge_tools import _FakeKnowledgeService

    twin = InMemoryTwinAPI.create()
    memory_client = MemoryClient(
        store=InMemoryExperienceStore(),
        embeddings=create_embedding_service("local"),
    )
    server = await build_unified_server(
        knowledge_service=_FakeKnowledgeService(),
        twin=twin,
        constraint_engine=twin.constraints,
        project_backend=InMemoryProjectBackend.create(),
        memory_client=memory_client,
        memory_insight_store=InMemoryInsightStore(),
    )
    app = build_http_app(server, enable_sse=False, api_key=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
        yield client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _attempt(
    client: httpx.AsyncClient,
    tool: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Call ``tool``; capture success or a ``-32001`` tool error.

    Post-MET-478 KiCad is wired into the unified MCP bootstrap, so
    the EE vertical follows the same contract as the mechanical
    vertical: dispatcher-level errors (``-32600`` / ``-32601``) are
    wire-up failures that fail the test loudly; ``-32001`` means the
    handler ran but its backend (KiCad CLI, cadquery lib, ccx) is
    absent — acceptable in CI, surfaces as ``status="success"`` in
    live mode against a deploy with the binaries installed.
    """
    try:
        envelope = await call_tool(client, tool, args)
        return {
            "tool": tool,
            "status": envelope.get("status"),
            "executed": True,
            "data": envelope.get("data"),
        }
    except McpRpcError as exc:
        if exc.code in (-32600, -32601):
            raise AssertionError(
                f"{tool}: dispatcher-level error {exc.code} (wire-up broken): {exc.message}"
            ) from exc
        assert exc.code == -32001, f"{tool}: unexpected error code {exc.code} ({exc.message})"
        return {
            "tool": tool,
            "status": "tool_execution_error",
            "executed": False,
            "data": exc.data,
        }


# ---------------------------------------------------------------------------
# The scenario
# ---------------------------------------------------------------------------


async def test_electronics_vertical_shape(
    electronics_vertical_client: httpx.AsyncClient,
) -> None:
    """End-to-end EE-agent tool sequence.

    Asserts the non-KiCad steps execute and the KiCad ones either
    execute (live mode with KiCad wired) or skip cleanly with a
    documented reason (CI default).
    """
    client = electronics_vertical_client
    outcomes: dict[str, dict[str, Any]] = {}

    # 1) project.create
    create_envelope = await call_tool(
        client,
        "project.create",
        {
            "name": "Electronics Vertical Smoke",
            "description": "Phase 5 EE scenario — KiCad validation suite",
            "status": "draft",
        },
    )
    assert create_envelope["status"] == "success", create_envelope
    project_id = create_envelope["data"]["id"]
    outcomes["project.create"] = {
        "status": "success",
        "executed": True,
        "project_id": project_id,
    }

    # 2) knowledge.populate_bom — EE intent
    bom_envelope = await call_tool(
        client,
        "knowledge.populate_bom",
        {
            "search_query": "3.3V low-dropout regulator for sensor rail",
            "constraints": [
                {"property": "supply_voltage", "op": "<=", "value": 3.6},
            ],
            "top_k": 5,
        },
    )
    assert bom_envelope["status"] == "success", bom_envelope
    outcomes["knowledge.populate_bom"] = {
        "status": "success",
        "executed": True,
        "suggestion_count": len(bom_envelope["data"].get("suggestions", [])),
    }

    # 3-6) kicad.run_erc / run_drc / export_bom / export_gerber
    # All expected to skip in CI; live mode (post-bootstrap-wire) would
    # execute and return real envelopes.
    outcomes["kicad.run_erc"] = await _attempt(
        client,
        "kicad.run_erc",
        {"schematic_path": "/tmp/example.kicad_sch"},
    )
    outcomes["kicad.run_drc"] = await _attempt(
        client,
        "kicad.run_drc",
        {"pcb_path": "/tmp/example.kicad_pcb"},
    )
    outcomes["kicad.export_bom"] = await _attempt(
        client,
        "kicad.export_bom",
        {"schematic_path": "/tmp/example.kicad_sch", "output_path": "/tmp/bom.csv"},
    )
    outcomes["kicad.export_gerber"] = await _attempt(
        client,
        "kicad.export_gerber",
        {"pcb_path": "/tmp/example.kicad_pcb", "output_dir": "/tmp/gerbers"},
    )

    # 7) constraint.validate — empty list → vacuous pass
    constraint_envelope = await call_tool(
        client,
        "constraint.validate",
        {"work_product_ids": []},
    )
    assert constraint_envelope["status"] == "success", constraint_envelope
    assert constraint_envelope["data"]["passed"] is True
    outcomes["constraint.validate"] = {
        "status": "success",
        "executed": True,
        "passed": True,
    }

    # The non-KiCad steps MUST execute cleanly (no dispatcher errors).
    core_steps = {
        "project.create",
        "knowledge.populate_bom",
        "constraint.validate",
    }
    executed_core = {step for step in core_steps if outcomes[step]["executed"]}
    assert executed_core == core_steps, f"non-KiCad core sequence didn't execute: {outcomes}"

    # Every KiCad step has a recorded outcome (either skipped or
    # executed — never dispatcher-crashed, which would have raised).
    kicad_steps = {
        "kicad.run_erc",
        "kicad.run_drc",
        "kicad.export_bom",
        "kicad.export_gerber",
    }
    assert kicad_steps <= set(outcomes), f"missing KiCad outcomes: {kicad_steps - set(outcomes)}"

    # Post-MET-478: KiCad is wired into the unified MCP bootstrap,
    # so kicad.* tools now route through the dispatcher rather than
    # 404'ing. In CI the underlying handlers still need the KiCad
    # CLI binary (not in PATH on the GitHub Actions runners), so the
    # expected per-step outcome is "tool_execution_error" — the
    # adapter validates input, the CLI shell-out fails, the
    # dispatcher returns a clean -32001 envelope. Live mode against
    # a deploy with the KiCad binary installed flips these to
    # "success".
    if not os.environ.get("METAFORGE_MCP_URL"):
        for step in kicad_steps:
            assert outcomes[step]["status"] in {"success", "tool_execution_error"}, (
                f"{step}: expected success or tool_execution_error post-MET-478, "
                f"got {outcomes[step]}"
            )


async def test_electronics_vertical_kicad_tools_in_inventory(
    electronics_vertical_client: httpx.AsyncClient,
) -> None:
    """Phase 7 readiness signal: KiCad is now wired into the unified MCP.

    Pre-MET-478 this test asserted KiCad's *absence* (the EE-vertical
    blocker). Post-MET-478 the assertion flips: every kicad.* tool
    must appear in ``tools/list`` so the readiness reporter can flip
    the EE vertical from NOT READY → READY. The handlers still need
    the KiCad CLI binary at runtime to actually succeed, but the
    wire-up gap is closed.
    """
    if os.environ.get("METAFORGE_MCP_URL"):
        pytest.skip("inventory readiness check is for the CI default bootstrap")

    from ._helpers import rpc

    result = await rpc(electronics_vertical_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}
    expected = {
        "kicad.run_erc",
        "kicad.run_drc",
        "kicad.export_bom",
        "kicad.export_gerber",
        "kicad.export_netlist",
        "kicad.get_pin_mapping",
    }
    missing = expected - tool_ids
    assert not missing, (
        f"EE vertical expected KiCad tools registered post-MET-478; "
        f"missing {missing} — bootstrap registry regression"
    )
