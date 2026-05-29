"""Phase 5 — Mechanical-vertical readiness scenario (MET-477).

Sequence (one project, four tool calls, one constraint pass):

1. ``project.create`` — opens a new hardware project
2. ``knowledge.populate_bom`` — design-intent query against the KB
3. ``cadquery.create_parametric`` — generate a bracket / enclosure
4. ``calculix.validate_mesh`` — mesh-quality check before solve
5. ``constraint.validate`` — gate the assembled WP list

Backend availability gating
---------------------------
The CAD/sim adapters call out to real binaries (`cadquery` Python lib,
`ccx` solver). Neither is installed in CI, so steps 3 and 4 normally
surface as ``-32001 TOOL_EXECUTION_ERROR`` — the adapter validates
input and forwards, the backend import / shell-out fails, the handler
raises, and the dispatcher returns a clean error envelope. **That's a
valid pass** for this scenario: the goal is to prove the *sequence
shape* — each tool is reachable, no dispatch / wiring breaks happen,
context flows project → knowledge → cad → sim → constraint.

Live mode (``METAFORGE_MCP_URL`` pointing at a deployed MCP with real
CAD/sim binaries) is where the scenario should produce success envelopes
at every step. A future Linear ticket will wire a containerized CAD/sim
runner into CI; until then the scenario stays "shape-only" in CI and
"full" in live mode.

Each step result lands in a ``vertical_outcomes`` dict so a follow-up
Phase 7 reporter can roll it up into the readiness matrix.
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
async def mechanical_vertical_client() -> AsyncIterator[httpx.AsyncClient]:
    """MCP client wired for the mechanical vertical scenario."""
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

    # Reuse the fake KnowledgeService pattern from
    # ``test_knowledge_tools.py`` so the BOM populator has real-looking
    # search hits + extract_properties responses to chew on.
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
    """Call ``tool``; capture either success envelope or -32001 cleanly.

    For Phase 5 in CI we treat ``-32001 TOOL_EXECUTION_ERROR`` as an
    acceptable outcome (CAD/sim binaries are absent). What we refuse is
    a dispatcher-level error (``-32600``, ``-32601``) — that would mean
    the wire-up itself is broken.
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
        # Dispatch errors are real failures; tool-execution errors are
        # backend-missing-in-CI and expected.
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


async def test_mechanical_vertical_shape(
    mechanical_vertical_client: httpx.AsyncClient,
) -> None:
    """End-to-end mechanical-agent tool sequence.

    Asserts every step is *reachable* through the dispatcher and either
    succeeds (live mode) or fails at the backend (in-process mode
    without CAD/sim binaries). A dispatch-level failure on any step
    fails the test — that's the gap we'd file.
    """
    client = mechanical_vertical_client
    outcomes: dict[str, dict[str, Any]] = {}

    # 1) project.create — must succeed (the backend is fully in-memory).
    create_envelope = await call_tool(
        client,
        "project.create",
        {
            "name": "Mechanical Vertical Smoke",
            "description": "Phase 5 mech scenario — bracket + FEA",
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

    # 2) knowledge.populate_bom — design intent for an enclosure sensor.
    bom_envelope = await call_tool(
        client,
        "knowledge.populate_bom",
        {
            "search_query": "small thermal management bracket for embedded MCU",
            "constraints": [
                {"property": "supply_voltage", "op": "<=", "value": 3.6},
            ],
            "top_k": 3,
        },
    )
    assert bom_envelope["status"] == "success", bom_envelope
    outcomes["knowledge.populate_bom"] = {
        "status": "success",
        "executed": True,
        "suggestion_count": len(bom_envelope["data"].get("suggestions", [])),
    }

    # 3) cadquery.create_parametric — bracket. Backend may be absent in
    # CI — accept either success or -32001.
    outcomes["cadquery.create_parametric"] = await _attempt(
        client,
        "cadquery.create_parametric",
        {
            "shape_type": "bracket",
            "parameters": {"length_mm": 60, "width_mm": 30, "thickness_mm": 4},
            "material": "Aluminum 6061-T6",
            "output_path": "/tmp/mech_bracket.step",
        },
    )

    # 4) calculix.validate_mesh — mesh pre-flight. Backend likely
    # absent; same tolerance as step 3.
    outcomes["calculix.validate_mesh"] = await _attempt(
        client,
        "calculix.validate_mesh",
        {"mesh_file": "/tmp/mech_bracket.inp", "max_aspect_ratio": 10.0},
    )

    # 5) constraint.validate — gate evaluation. Empty WP list → vacuous
    # pass; with the freshly-created project's WPs we'd also expect a
    # pass since no constraints exist in this twin yet.
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

    # The sequence-shape contract: every step is reachable + every
    # outcome is either success or a tool-execution error (never a
    # dispatch error — that would have raised in _attempt).
    reachable = {
        step
        for step, info in outcomes.items()
        if info["status"]
        in {
            "success",
            "tool_execution_error",
        }
    }
    assert reachable == {
        "project.create",
        "knowledge.populate_bom",
        "cadquery.create_parametric",
        "calculix.validate_mesh",
        "constraint.validate",
    }, f"sequence broke at: {set(outcomes) - reachable}"

    # The three non-CAD/sim steps must actually have executed; the two
    # CAD/sim steps are allowed to no-op-fail when their binaries are
    # absent in this environment.
    executed = {step for step, info in outcomes.items() if info["executed"]}
    assert {
        "project.create",
        "knowledge.populate_bom",
        "constraint.validate",
    } <= executed, f"core sequence didn't execute: {outcomes}"
