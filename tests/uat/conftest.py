"""Shared fixtures for the Level-11 UAT suite.

Every UAT test imports through here so the per-layer files stay short
and the heavy lifting (subprocess spawn, knowledge-service lifecycle,
Postgres connection) is centralised.

Most UAT tests are also ``@pytest.mark.integration`` because they hit
real backends. Run the full suite with::

    pytest tests/uat/ --uat --integration -v --tb=short
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANNER_ROOT = Path("/mnt/c/Users/odokf/Documents/MetaForge-Planner")


# ---------------------------------------------------------------------------
# Backend connection strings — overridable via env
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://metaforge:metaforge@localhost:5432/metaforge",
    ).replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="session")
def neo4j_uri() -> str:
    return os.environ.get("NEO4J_URI", "bolt://localhost:7687")


# ---------------------------------------------------------------------------
# Per-test LightRAG knowledge service (real backend)
# ---------------------------------------------------------------------------


@pytest.fixture
async def knowledge_service(tmp_path: Path, postgres_dsn: str) -> AsyncIterator[object]:
    """Boot a real LightRAG-backed KnowledgeService isolated to this test.

    Each test gets a fresh ``namespace_prefix`` so concurrent UAT tests
    don't share data. Skips when the optional ``lightrag-hku`` extra
    isn't installed (environment-not-ready ≠ acceptance gap).
    """
    pytest.importorskip("lightrag", reason="install via `pip install -e '.[knowledge]'`")
    pytest.importorskip("asyncpg", reason="install via `pip install -e '.[knowledge]'`")

    from digital_twin.knowledge import create_knowledge_service

    suffix = uuid.uuid4().hex[:8]
    try:
        svc = create_knowledge_service(
            "lightrag",
            working_dir=str(tmp_path / f"lightrag-uat-{suffix}"),
            postgres_dsn=postgres_dsn,
            namespace_prefix=f"lightrag_uat_{suffix}",
        )
    except RuntimeError as exc:
        pytest.skip(f"KnowledgeService unavailable: {exc}")
    await svc.initialize()  # type: ignore[attr-defined]
    try:
        yield svc
    finally:
        await svc.close()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Spawn the standalone MCP server (MET-337) — used by C2 + golden flow
# ---------------------------------------------------------------------------


async def spawn_metaforge_mcp(
    *,
    adapters: str = "cadquery,calculix",
    api_key: str | None = None,
    client_key: str | None = None,
) -> tuple[object, object]:
    """Spawn ``python -m metaforge.mcp --transport stdio`` and connect.

    Returns ``(McpClient, StdioTransport)``. Caller owns teardown.
    Async — must be ``await``ed from within the test's event loop so
    the transport's reader/writer pipes share the loop they'll be used
    on.
    """
    from mcp_core.client import McpClient
    from mcp_core.schemas import ToolManifest
    from mcp_core.transports import StdioTransport

    env = dict(os.environ)
    env["METAFORGE_ADAPTERS"] = adapters
    if api_key is not None:
        env["METAFORGE_MCP_API_KEY"] = api_key
    if client_key is not None:
        env["METAFORGE_MCP_CLIENT_KEY"] = client_key

    transport = StdioTransport(
        command=[sys.executable, "-m", "metaforge.mcp", "--transport", "stdio"],
        env=env,
        ready_signal="metaforge-mcp ready",
        ready_timeout=30.0,
    )
    await transport.connect()
    client = McpClient()
    await client.connect("metaforge", transport)
    # Run tool/list to populate the manifest cache.
    import json as _json

    raw = await transport.send('{"jsonrpc":"2.0","id":"discover","method":"tool/list","params":{}}')
    payload = _json.loads(raw)
    for tool in payload.get("result", {}).get("tools", []):
        client.register_manifest(
            ToolManifest(
                tool_id=tool["tool_id"],
                adapter_id=tool.get("adapter_id", "metaforge"),
                name=tool["name"],
                description=tool.get("description", ""),
                capability=tool.get("capability", ""),
                input_schema=tool.get("input_schema", {}),
                output_schema=tool.get("output_schema", {}),
                phase=tool.get("phase", 1),
            )
        )
    return client, transport


# ---------------------------------------------------------------------------
# Common assertion helpers — keep test bodies declarative
# ---------------------------------------------------------------------------


def assert_validates(met_id: str, criterion: str, condition: bool, detail: str = "") -> None:
    """Annotate a UAT assertion with the Linear ticket it validates.

    The detail string lands in the failure message verbatim — keep it
    concise; the UAT report harvest captures it.
    """
    if not condition:
        msg = f"[{met_id}] {criterion}"
        if detail:
            msg += f" — {detail}"
        raise AssertionError(msg)
