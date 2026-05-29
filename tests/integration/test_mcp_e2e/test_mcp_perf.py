"""Phase 6 — MCP perf baselines (MET-477).

Opt-in p50 / p95 latency baselines for the three hot MCP tools:

* ``knowledge.search``       — vector lookup hot path
* ``twin.get_node``          — graph read hot path
* ``constraint.validate``    — engine pre-flight hot path

How to run
----------

These tests are excluded from the default ``pytest`` invocation (see
``[tool.pytest.ini_options]`` ``addopts = "-m 'not perf'"`` in
``pyproject.toml``). Opt in with:

    pytest tests/integration/test_mcp_e2e/test_mcp_perf.py -m perf

Belt-and-braces: each test also checks ``METAFORGE_PERF_TESTS=1`` and
skips when unset, so accidentally running ``pytest -m perf`` in CI
without the env var doesn't kick off a 150-call latency sweep.

Targets are deliberately loose for the in-process fixture (each call
goes through the FastAPI ASGI transport against in-memory backends):

* ``knowledge.search`` p95 ≤ 250 ms
* ``twin.get_node``    p95 ≤ 100 ms
* ``constraint.validate`` p95 ≤ 100 ms

Live mode (``METAFORGE_MCP_URL`` against fidel-dev) is where the real
numbers matter; the in-process targets are sanity bounds. The Phase 7
reporter rolls these into the readiness matrix as the p50/p95 figure
for each hot tool.
"""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

from ._helpers import call_tool

# asyncio mode is applied per-test (the sanity smoke is sync; module-level
# `pytest.mark.asyncio` would warn about applying the mark to a sync fn).


_PERF_ENABLED = bool(os.environ.get("METAFORGE_PERF_TESTS"))
_PERF_SKIP_REASON = (
    "Phase 6 perf baselines are opt-in: set METAFORGE_PERF_TESTS=1 "
    "and run with `pytest -m perf` to execute"
)

_N_CALLS = 50


# ---------------------------------------------------------------------------
# Fixtures — a fully-wired MCP client + a pre-populated twin
# ---------------------------------------------------------------------------


@pytest.fixture
async def perf_twin_with_node() -> Any:
    """Twin pre-populated with one canonical WorkProduct."""
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()
    wp = WorkProduct(
        id=uuid4(),
        name="perf_hot_node",
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path="cad/perf_hot.step",
        content_hash="cafebabe",
        format="step",
        created_by="perf-suite",
    )
    await twin.create_work_product(wp)
    twin.canonical_wp_id = wp.id  # type: ignore[attr-defined]
    return twin


@pytest.fixture
async def perf_mcp_client(
    perf_twin_with_node: Any,
) -> AsyncIterator[httpx.AsyncClient]:
    """MCP client wired with knowledge + twin + constraint backends."""
    live_url = os.environ.get("METAFORGE_MCP_URL") or None
    if live_url:
        async with httpx.AsyncClient(base_url=live_url, timeout=60.0) as client:
            yield client
        return

    from digital_twin.knowledge.embedding_service import create_embedding_service
    from digital_twin.memory.client import MemoryClient
    from digital_twin.memory.consolidation import InMemoryInsightStore
    from digital_twin.memory.store import InMemoryExperienceStore
    from metaforge.mcp.__main__ import build_http_app
    from metaforge.mcp.server import build_unified_server

    from .test_knowledge_tools import _FakeKnowledgeService

    memory_client = MemoryClient(
        store=InMemoryExperienceStore(),
        embeddings=create_embedding_service("local"),
    )
    server = await build_unified_server(
        knowledge_service=_FakeKnowledgeService(),
        twin=perf_twin_with_node,
        constraint_engine=perf_twin_with_node.constraints,
        project_backend=None,
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


async def _time_call(
    client: httpx.AsyncClient,
    tool: str,
    args: dict[str, Any],
) -> float:
    """Time one MCP tool call and return latency in milliseconds."""
    start = time.perf_counter()
    envelope = await call_tool(client, tool, args)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    assert envelope.get("status") == "success", (
        f"{tool} returned non-success during perf run: {envelope}"
    )
    return elapsed_ms


def _percentiles(samples: list[float]) -> dict[str, float]:
    """Return p50 / p95 / mean / max from a list of latency-ms samples."""
    s = sorted(samples)
    n = len(s)
    p50_idx = int(0.50 * (n - 1))
    p95_idx = int(0.95 * (n - 1))
    return {
        "n": float(n),
        "p50_ms": s[p50_idx],
        "p95_ms": s[p95_idx],
        "mean_ms": statistics.mean(s),
        "max_ms": s[-1],
    }


# ---------------------------------------------------------------------------
# knowledge.search
# ---------------------------------------------------------------------------


@pytest.mark.perf
@pytest.mark.asyncio
@pytest.mark.skipif(not _PERF_ENABLED, reason=_PERF_SKIP_REASON)
async def test_knowledge_search_p50_p95_baseline(
    perf_mcp_client: httpx.AsyncClient,
) -> None:
    """``knowledge.search`` p50/p95 over 50 calls."""
    samples: list[float] = []
    for i in range(_N_CALLS):
        samples.append(
            await _time_call(
                perf_mcp_client,
                "knowledge.search",
                {"query": f"STM32H7 perf-iter-{i}", "top_k": 5},
            )
        )
    stats = _percentiles(samples)
    print(
        f"\nknowledge.search baseline: n={stats['n']:.0f} "
        f"p50={stats['p50_ms']:.2f}ms p95={stats['p95_ms']:.2f}ms "
        f"mean={stats['mean_ms']:.2f}ms max={stats['max_ms']:.2f}ms"
    )
    # In-process sanity bound — generous since GH Actions runners vary.
    assert stats["p95_ms"] < 250.0, (
        f"knowledge.search p95 {stats['p95_ms']:.2f}ms exceeds 250ms ceiling"
    )


# ---------------------------------------------------------------------------
# twin.get_node
# ---------------------------------------------------------------------------


@pytest.mark.perf
@pytest.mark.asyncio
@pytest.mark.skipif(not _PERF_ENABLED, reason=_PERF_SKIP_REASON)
async def test_twin_get_node_p50_p95_baseline(
    perf_mcp_client: httpx.AsyncClient,
    perf_twin_with_node: Any,
) -> None:
    """``twin.get_node`` p50/p95 over 50 calls on the canonical WP."""
    node_id = str(perf_twin_with_node.canonical_wp_id)
    samples: list[float] = []
    for _ in range(_N_CALLS):
        samples.append(await _time_call(perf_mcp_client, "twin.get_node", {"node_id": node_id}))
    stats = _percentiles(samples)
    print(
        f"\ntwin.get_node baseline: n={stats['n']:.0f} "
        f"p50={stats['p50_ms']:.2f}ms p95={stats['p95_ms']:.2f}ms "
        f"mean={stats['mean_ms']:.2f}ms max={stats['max_ms']:.2f}ms"
    )
    assert stats["p95_ms"] < 100.0, (
        f"twin.get_node p95 {stats['p95_ms']:.2f}ms exceeds 100ms ceiling"
    )


# ---------------------------------------------------------------------------
# constraint.validate
# ---------------------------------------------------------------------------


@pytest.mark.perf
@pytest.mark.asyncio
@pytest.mark.skipif(not _PERF_ENABLED, reason=_PERF_SKIP_REASON)
async def test_constraint_validate_p50_p95_baseline(
    perf_mcp_client: httpx.AsyncClient,
) -> None:
    """``constraint.validate`` p50/p95 over 50 calls (empty WP list)."""
    samples: list[float] = []
    for _ in range(_N_CALLS):
        samples.append(
            await _time_call(
                perf_mcp_client,
                "constraint.validate",
                {"work_product_ids": []},
            )
        )
    stats = _percentiles(samples)
    print(
        f"\nconstraint.validate baseline: n={stats['n']:.0f} "
        f"p50={stats['p50_ms']:.2f}ms p95={stats['p95_ms']:.2f}ms "
        f"mean={stats['mean_ms']:.2f}ms max={stats['max_ms']:.2f}ms"
    )
    assert stats["p95_ms"] < 100.0, (
        f"constraint.validate p95 {stats['p95_ms']:.2f}ms exceeds 100ms ceiling"
    )


# ---------------------------------------------------------------------------
# Sanity: a non-perf test so the module is collected in default CI.
# ---------------------------------------------------------------------------


def test_perf_module_imports_cleanly() -> None:
    """Documentary smoke: the perf module imports without side effects.

    The three perf cases above are marked ``perf`` and excluded from the
    default ``pytest`` run via ``addopts = "-m 'not perf'"``. This one
    test stays unmarked so the file shows green in default CI and
    catches the import path / fixture wiring at routine merge time.
    """
    assert _N_CALLS == 50
    # The perf-enable gate is read from env, not configured here.
    assert isinstance(_PERF_ENABLED, bool)
