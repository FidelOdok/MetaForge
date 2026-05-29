"""Phase 5 — Electronics-vertical readiness scenario (MET-477).

Per the loop spec, the EE-agent tool sequence is:

1. ``project.create`` — new hardware project
2. ``knowledge.populate_bom`` — EE-relevant component query
3. ``kicad.run_erc``      ┐
4. ``kicad.run_drc``      │ — KiCad validation suite
5. ``kicad.export_bom``   │
6. ``kicad.export_gerber``┘
7. ``constraint.validate`` — gate the assembled WP list

KiCad bootstrap gap
-------------------
KiCad has an adapter under ``tool_registry/tools/kicad/`` but is **not
wired into the unified MCP bootstrap** (`tool_registry.bootstrap`
registers only ``cadquery / freecad / calculix``). KiCad ships as a
separate stdio entrypoint (``tool_registry/tools/kicad/entrypoint.py``).

That means in CI (and on the current fidel-dev deploy) ``kicad.*`` tools
return `-32601 METHOD_NOT_FOUND`. The EE scenario therefore:

* asserts steps 1/2/7 (`project.create`, `knowledge.populate_bom`,
  `constraint.validate`) succeed at the dispatcher level;
* records each `kicad.*` step's outcome as "skipped — KiCad adapter not
  in unified bootstrap" so the Phase 7 readiness reporter can roll up
  the EE vertical as **NOT READY** with a precise blocker reason;
* still proves the *non-KiCad* part of the wire-up is intact.

When KiCad is added to ``tool_registry.bootstrap._ADAPTER_REGISTRY`` (or
its own MCP entrypoint is consolidated into the unified server), the
KiCad steps will start surfacing real outcomes and this test will
upgrade automatically.
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
    """Call ``tool``; capture success or any kind of error envelope.

    For the EE vertical, ``kicad.*`` legitimately returns
    ``-32601 METHOD_NOT_FOUND`` in CI because KiCad isn't in the
    unified MCP bootstrap. That outcome is recorded as ``"skipped"``
    so the readiness reporter can roll the EE vertical up as NOT
    READY with that specific blocker.

    All non-KiCad steps treat ``-32600`` / ``-32601`` as wire-up
    failures (the same contract as the mechanical vertical) — but
    KiCad is the documented exception.
    """
    is_kicad = tool.startswith("kicad.")
    try:
        envelope = await call_tool(client, tool, args)
        return {
            "tool": tool,
            "status": envelope.get("status"),
            "executed": True,
            "data": envelope.get("data"),
        }
    except McpRpcError as exc:
        if exc.code == -32601 and is_kicad:
            return {
                "tool": tool,
                "status": "skipped",
                "executed": False,
                "skip_reason": "kicad adapter not in unified MCP bootstrap",
                "error_code": exc.code,
            }
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

    # In CI default mode all KiCad steps should be skipped with the
    # documented reason; in live mode they may be executed.
    if not os.environ.get("METAFORGE_MCP_URL"):
        for step in kicad_steps:
            assert outcomes[step]["status"] == "skipped", (
                f"{step}: expected skipped (KiCad not in bootstrap), got {outcomes[step]}"
            )
            assert outcomes[step]["skip_reason"] == ("kicad adapter not in unified MCP bootstrap")


async def test_electronics_vertical_reports_kicad_gap_in_inventory(
    electronics_vertical_client: httpx.AsyncClient,
) -> None:
    """Phase 7 readiness rollup needs a stable signal for the KiCad gap.

    The inventory check from ``test_cad_tools.py`` already asserts
    KiCad is absent from ``tools/list`` in the unified MCP bootstrap.
    This test mirrors that assertion locally so the EE vertical's
    READY/NOT READY signal in REPORT.md keys off a self-contained
    check and doesn't depend on cross-file ordering.
    """
    if os.environ.get("METAFORGE_MCP_URL"):
        pytest.skip("inventory gap check is for the CI default bootstrap")

    from ._helpers import rpc

    result = await rpc(electronics_vertical_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}
    kicad_present = {tid for tid in tool_ids if tid and tid.startswith("kicad.")}
    assert kicad_present == set(), (
        f"EE vertical expected KiCad absent from unified MCP bootstrap; "
        f"found {kicad_present}. Update vertical-readiness gating."
    )
