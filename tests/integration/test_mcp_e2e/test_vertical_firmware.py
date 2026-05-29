"""Phase 5 — Firmware-vertical readiness scenario (MET-477).

Per the loop spec, the firmware-agent path is intentionally lighter
than mech / EE: there is no live firmware build, so the scenario is

1. ``project.create`` — new hardware project
2. ``knowledge.search`` — MCU-family lookup against the KB
3. (skill mock) — surrogate for the firmware-build step we don't run

In-process mode wires the project + knowledge + memory backends from
the same patterns as the mech / EE scenarios. The "skill mock" leg is
a small in-test function that consumes the knowledge.search hit and
synthesises a build-plan record (deterministic, no shell-out, no
toolchain dependencies). The plan is the *interface* the firmware
agent will read from later; the test proves that interface can be
populated from a real MCP round-trip.

Outcomes are stashed in a per-step dict for the Phase 7 readiness
reporter, same as the mech / EE scenarios.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from ._helpers import call_tool

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixture: project + knowledge + memory wired in-process
# ---------------------------------------------------------------------------


@pytest.fixture
async def firmware_vertical_client() -> AsyncIterator[httpx.AsyncClient]:
    """MCP client wired for the firmware vertical scenario."""
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
# Skill mock
# ---------------------------------------------------------------------------


def _firmware_build_plan_from_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Surrogate for the firmware-build skill.

    Takes one ``knowledge.search`` hit and turns it into a build-plan
    record the firmware agent would otherwise produce by running the
    toolchain. Deterministic, side-effect-free, no toolchain calls —
    just the interface shape Phase 7 will sample.
    """
    mpn = (hit.get("metadata") or {}).get("mpn") or "UNKNOWN"
    return {
        "mpn": mpn,
        "build_target": f"firmware/{mpn.lower()}/main.elf",
        "toolchain": "arm-none-eabi-gcc",
        "flash_layout": {"flash_kb": 1024, "ram_kb": 320},  # fake but plausible
        "source_path": hit.get("source_path"),
        "build_status": "PLANNED",
    }


# ---------------------------------------------------------------------------
# The scenario
# ---------------------------------------------------------------------------


async def test_firmware_vertical_shape(
    firmware_vertical_client: httpx.AsyncClient,
) -> None:
    """End-to-end firmware-agent tool sequence with a skill mock for the build leg."""
    client = firmware_vertical_client
    outcomes: dict[str, dict[str, Any]] = {}

    # 1) project.create
    create_envelope = await call_tool(
        client,
        "project.create",
        {
            "name": "Firmware Vertical Smoke",
            "description": "Phase 5 firmware scenario — MCU family lookup",
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

    # 2) knowledge.search — MCU family
    search_envelope = await call_tool(
        client,
        "knowledge.search",
        {"query": "STM32H7 ARM Cortex-M7 microcontroller", "top_k": 3},
    )
    assert search_envelope["status"] == "success", search_envelope
    hits = search_envelope["data"].get("hits", [])
    assert hits, "knowledge.search returned no hits for the MCU query"
    outcomes["knowledge.search"] = {
        "status": "success",
        "executed": True,
        "hit_count": len(hits),
        "top_hit_mpn": (hits[0].get("metadata") or {}).get("mpn"),
    }

    # 3) skill mock — synthesise a build plan from the top hit. No
    # MCP round-trip; this is the surrogate the loop spec called out.
    plan = _firmware_build_plan_from_hit(hits[0])
    assert plan["mpn"], "skill mock failed to extract MPN"
    assert plan["build_status"] == "PLANNED"
    assert plan["build_target"].startswith("firmware/")
    outcomes["firmware.build_plan_mock"] = {
        "status": "success",
        "executed": True,
        "build_target": plan["build_target"],
        "toolchain": plan["toolchain"],
    }

    # The firmware sequence is small enough that every step must
    # actually execute — no `_attempt`-tolerated skips here, no
    # backends to gate on.
    assert {step for step, info in outcomes.items() if info["executed"]} == {
        "project.create",
        "knowledge.search",
        "firmware.build_plan_mock",
    }, f"firmware sequence didn't fully execute: {outcomes}"
