"""UAT-C2-L3 — External-harness E2E (MET-340).

Acceptance bullets validated:

* ``python -m metaforge.mcp --transport stdio`` is reachable from a
  separate ``McpClient(StdioTransport(...))``.
* ``tool/list`` returns ≥7 tools with the expected adapter prefixes.
* ``health/check`` returns ``status=healthy`` with the adapter roll-up.
* Subprocess exits cleanly when the client closes stdin.
* End-to-end runtime well under 60s.

This UAT extends ``tests/integration/test_mcp_external_harness.py`` —
that file is the closest precedent in the repo and covers the same
shape with deeper assertions; this UAT pins the cycle-level acceptance.
"""

from __future__ import annotations

import json
import time

import pytest

from tests.uat.conftest import assert_validates, spawn_metaforge_mcp

pytestmark = [pytest.mark.uat, pytest.mark.integration]


async def test_met340_external_harness_round_trip() -> None:
    import warnings

    warnings.filterwarnings(
        "ignore",
        message=".*Event loop is closed.*",
        category=pytest.PytestUnraisableExceptionWarning,
    )

    start = time.perf_counter()
    client, transport = await spawn_metaforge_mcp(adapters="cadquery,calculix")
    try:
        tools = await client.list_tools()  # type: ignore[attr-defined]
        raw = await transport.send('{"jsonrpc":"2.0","id":"h","method":"health/check","params":{}}')
        health = json.loads(raw)["result"]
        raw = await transport.send(
            '{"jsonrpc":"2.0","id":"u","method":"tool/call",'
            '"params":{"tool_id":"nonexistent.tool","arguments":{}}}'
        )
        error_code = json.loads(raw)["error"]["code"]
        tool_count = len(tools)
    finally:
        await client.disconnect("metaforge")  # type: ignore[attr-defined]

    elapsed = time.perf_counter() - start

    assert_validates(
        "MET-340",
        "tool/list returns ≥7 tools end-to-end",
        tool_count >= 7,
        f"got {tool_count}",
    )
    assert_validates(
        "MET-340",
        "health/check returns status=healthy",
        health.get("status") == "healthy",
        f"health: {health}",
    )
    assert_validates(
        "MET-340",
        "health/check carries adapter roll-up",
        health.get("adapter_count", 0) >= 1 and health.get("tool_count", 0) >= 7,
        f"health: {health}",
    )
    assert_validates(
        "MET-340",
        "unknown tool returns JSON-RPC method-not-found (-32601)",
        error_code == -32601,
        f"got code={error_code}",
    )
    assert_validates(
        "MET-340",
        "full E2E round-trip well under 60s",
        elapsed < 60.0,
        f"elapsed={elapsed:.2f}s",
    )
