"""Stdio round-trip smoke test for ``python -m metaforge.mcp`` (MET-433).

Spawns the standalone MCP entrypoint as a subprocess, drives it over
JSON-RPC on stdin/stdout, and asserts the surface a remote client (Claude
Code, Codex harness, the MET-340 external harness) will actually see.

Two scoping bands:

* ``test_tools_list_lists_core_adapters`` — always runs. No external
  services required. Verifies the cadquery/freecad/calculix surface
  comes up + the stdio loop replies. Catches "the entrypoint script
  is broken" regressions that the unit tests don't see because they
  bypass the subprocess shell.

* ``test_knowledge_search_round_trip`` — opt-in via ``DATABASE_URL`` +
  ``pytest --integration``. Skipped otherwise. Verifies the MET-433
  bootstrap gap stays closed end-to-end: subprocess loads
  LightRAGKnowledgeService from env, ``tools/list`` includes
  ``knowledge.search``, and ``tools/call knowledge.search`` returns a
  hits envelope.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.integration


_READY_LINE = "metaforge-mcp ready"
_LAUNCH_TIMEOUT_S = 60.0
_CALL_TIMEOUT_S = 30.0


def _start_subprocess() -> subprocess.Popen[str]:
    """Spawn the stdio MCP server and wait for the ready line on stderr.

    Returns the live ``Popen`` — caller is responsible for terminating
    it inside a try/finally.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "metaforge.mcp", "--transport", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    deadline = time.monotonic() + _LAUNCH_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.stderr is None:
            break
        line = proc.stderr.readline()
        if not line:
            if proc.poll() is not None:
                stdout = proc.stdout.read() if proc.stdout else ""
                pytest.fail(f"metaforge.mcp exited before ready. stdout={stdout!r}")
            continue
        if _READY_LINE in line:
            return proc
    proc.kill()
    proc.wait(timeout=5)
    pytest.fail(f"metaforge.mcp did not emit '{_READY_LINE}' within {_LAUNCH_TIMEOUT_S}s")


def _call(proc: subprocess.Popen[str], method: str, params: dict | None = None) -> dict:
    """Send a JSON-RPC request on stdin, read one response on stdout."""
    if proc.stdin is None or proc.stdout is None:
        pytest.fail("subprocess pipes missing")
    request = {
        "jsonrpc": "2.0",
        "id": f"smoke-{method}",
        "method": method,
        "params": params or {},
    }
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()
    deadline = time.monotonic() + _CALL_TIMEOUT_S
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                pytest.fail(f"subprocess died waiting for {method} response")
            continue
        return dict(json.loads(line))
    pytest.fail(f"timed out waiting for {method} response after {_CALL_TIMEOUT_S}s")


def test_tools_list_lists_core_adapters() -> None:
    """The stdio entrypoint replies to ``tools/list``.

    Doesn't require Postgres — knowledge tools may or may not be
    present depending on DATABASE_URL. The cadquery/freecad/calculix/
    twin/project surface must always be there.
    """
    proc = _start_subprocess()
    try:
        response = _call(proc, "tools/list")
        assert "result" in response, f"unexpected response: {response}"
        tools = response["result"]["tools"]
        names = {t["name"] for t in tools}

        # The 22-tool baseline (MET-426) — one canonical tool per
        # always-present adapter so a missing adapter shows up fast.
        required = {
            "cadquery.create_parametric",
            "freecad.create_parametric",
            "calculix.run_fea",
            "twin.get_node",
            "constraint.validate",
        }
        missing = required - names
        assert not missing, (
            f"core adapter tools missing from stdio surface: {missing}; present: {sorted(names)}"
        )
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="knowledge.* requires DATABASE_URL → pgvector",
)
def test_knowledge_search_round_trip() -> None:
    """End-to-end stdio round-trip for the MET-433 bootstrap gap.

    With DATABASE_URL set, the entrypoint must construct
    LightRAGKnowledgeService and surface ``knowledge.search``.
    Returns the hits envelope (possibly empty when no chunks ingested)
    rather than the MET-385 not-configured envelope.
    """
    proc = _start_subprocess()
    try:
        list_response = _call(proc, "tools/list")
        names = {t["name"] for t in list_response["result"]["tools"]}
        assert "knowledge.search" in names, (
            f"knowledge.search missing — bootstrap gap re-opened. Present: {sorted(names)}"
        )

        call_response = _call(
            proc,
            "tools/call",
            {"name": "knowledge.search", "arguments": {"query": "smoke", "top_k": 1}},
        )
        # MCP shape: result.content is a list[{type, text}].
        content = call_response["result"]["content"]
        assert content and content[0]["type"] == "text"
        body = json.loads(content[0]["text"])
        # The hits key proves it reached the handler (even when empty).
        assert "hits" in body, f"unexpected knowledge.search body: {body}"
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
