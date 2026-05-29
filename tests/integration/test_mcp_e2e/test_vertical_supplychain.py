"""Phase 5 — Supply-chain-vertical readiness scenario (MET-477).

Per the loop spec, the supply-chain-agent sequence is:

1. ``project.create`` — new hardware project
2. ``digikey.search`` — distributor-side part lookup
3. ``digikey.get_pricing`` — quantity-tier pricing for the chosen MPN
4. ``memory.retrieve_similar_experience`` — lessons from prior runs

Live mode (real DIGIKEY_CLIENT_ID/SECRET in env + METAFORGE_MCP_URL)
exercises the real OAuth + sandbox HTTP path against the deployed
server. CI default mode runs the same sequence against a fake
``DistributorAdapter`` patched into the unified bootstrap (same
pattern ``test_supplier_tools.py`` uses) so the sequence shape is
verifiable without real credentials.

A second test asserts the live-mode skip semantics: when no creds are
present, the ``digikey.*`` tools must be absent from ``tools/list`` —
the readiness reporter can roll the supply-chain vertical up as
NOT READY with that precise blocker.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from ._helpers import call_tool, rpc

pytestmark = pytest.mark.asyncio


_HAS_DIGIKEY_CREDS = bool(
    os.environ.get("DIGIKEY_CLIENT_ID") and os.environ.get("DIGIKEY_CLIENT_SECRET")
)


# ---------------------------------------------------------------------------
# Fixture: project + memory + digikey (patched-fake) wired in-process
# ---------------------------------------------------------------------------


@pytest.fixture
async def supplychain_vertical_client() -> AsyncIterator[httpx.AsyncClient]:
    """MCP client wired for the supply-chain vertical scenario.

    Patches ``DigiKeyAdapter`` with the same ``_FakeDistributorAdapter``
    used in ``test_supplier_tools.py`` so the four ``digikey.*`` MCP
    tools register without real OAuth, and supplies fake env creds so
    the bootstrap factory builds the patched adapter.
    """
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
    from .test_supplier_tools import _FakeDistributorAdapter

    twin = InMemoryTwinAPI.create()
    memory_client = MemoryClient(
        store=InMemoryExperienceStore(),
        embeddings=create_embedding_service("local"),
    )

    fake_env = {
        "DIGIKEY_CLIENT_ID": "fake-id",
        "DIGIKEY_CLIENT_SECRET": "fake-secret",
    }
    with (
        patch.dict(os.environ, fake_env, clear=False),
        patch(
            "tool_registry.tools.digikey.adapter.DigiKeyAdapter",
            return_value=_FakeDistributorAdapter("DigiKey"),
        ),
    ):
        server = await build_unified_server(
            knowledge_service=_FakeKnowledgeService(),
            twin=twin,
            constraint_engine=None,
            project_backend=InMemoryProjectBackend.create(),
            memory_client=memory_client,
            memory_insight_store=InMemoryInsightStore(),
        )
        app = build_http_app(server, enable_sse=False, api_key=None)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
            yield client


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


async def test_supplychain_vertical_shape(
    supplychain_vertical_client: httpx.AsyncClient,
) -> None:
    """End-to-end supply-chain agent sequence.

    Asserts every step executes cleanly. Memory has no pre-populated
    experiences in this fixture, so ``retrieve_similar_experience``
    returns an empty hit list — that's fine: the goal is to prove the
    sequence shape and that the agent can call all four tools in
    order.
    """
    client = supplychain_vertical_client
    outcomes: dict[str, dict[str, Any]] = {}

    # 1) project.create
    create_envelope = await call_tool(
        client,
        "project.create",
        {
            "name": "Supply-Chain Vertical Smoke",
            "description": "Phase 5 SC scenario — distributor + memory recall",
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

    # 2) digikey.search — distributor lookup
    search_envelope = await call_tool(
        client,
        "digikey.search",
        {"query": "BME280 environmental sensor", "limit": 5},
    )
    assert search_envelope["status"] == "success", search_envelope
    payload = search_envelope["data"]
    assert payload["count"] >= 1
    chosen_mpn = payload["results"][0]["mpn"]
    outcomes["digikey.search"] = {
        "status": "success",
        "executed": True,
        "chosen_mpn": chosen_mpn,
        "result_count": payload["count"],
    }

    # 3) digikey.get_pricing — quantity tiers for the chosen MPN
    pricing_envelope = await call_tool(
        client,
        "digikey.get_pricing",
        {"mpn": chosen_mpn},
    )
    assert pricing_envelope["status"] == "success", pricing_envelope
    pricing_payload = pricing_envelope["data"]
    assert pricing_payload["count"] >= 1
    # Quantity tiers sorted ascending — unit price should be non-rising.
    breaks = pricing_payload["breaks"]
    quantities = [b["quantity"] for b in breaks]
    assert quantities == sorted(quantities), f"pricing breaks not sorted by quantity: {quantities}"
    outcomes["digikey.get_pricing"] = {
        "status": "success",
        "executed": True,
        "break_count": pricing_payload["count"],
        "first_unit_price": breaks[0]["unit_price"],
    }

    # 4) memory.retrieve_similar_experience — empty store → empty hits,
    # but the call itself must succeed (G1/G3 regression coverage).
    memory_envelope = await call_tool(
        client,
        "memory.retrieve_similar_experience",
        {"goal": f"supply chain risk for {chosen_mpn}", "limit": 3},
    )
    assert memory_envelope["status"] == "success", memory_envelope
    hits = memory_envelope["data"]["hits"]
    assert isinstance(hits, list)
    outcomes["memory.retrieve_similar_experience"] = {
        "status": "success",
        "executed": True,
        "hit_count": len(hits),
    }

    # Whole sequence executed — no tolerance band here because in
    # this fixture every backend is in-memory and patched.
    assert {step for step, info in outcomes.items() if info["executed"]} == {
        "project.create",
        "digikey.search",
        "digikey.get_pricing",
        "memory.retrieve_similar_experience",
    }, f"supply-chain sequence didn't fully execute: {outcomes}"


# ---------------------------------------------------------------------------
# Readiness signal: distributor tools absent without creds.
# ---------------------------------------------------------------------------


async def test_supplychain_vertical_skips_when_no_digikey_creds(mcp_client) -> None:
    """Reporter signal: without real creds, the SC vertical can't go.

    The Phase 7 readiness reporter keys off this assertion to roll the
    supply-chain vertical up as NOT READY with the precise blocker
    ('DIGIKEY_CLIENT_ID + DIGIKEY_CLIENT_SECRET not set'). The
    ``mcp_client`` fixture uses the default in-process build with no
    distributor env vars set, so digikey.* must be absent. Live mode
    skips this — the deployed server may have real creds.
    """
    if os.environ.get("METAFORGE_MCP_URL"):
        pytest.skip("readiness signal is for CI default bootstrap")

    result = await rpc(mcp_client, "tools/list")
    tool_ids = {t.get("name") for t in result.get("tools", [])}

    if not _HAS_DIGIKEY_CREDS:
        digikey = {tid for tid in tool_ids if tid and tid.startswith("digikey.")}
        assert digikey == set(), (
            f"digikey tools registered without DIGIKEY_CLIENT_ID/SECRET: {digikey}"
        )
