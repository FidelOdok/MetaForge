#!/usr/bin/env python3
"""Check that a plain install of the project can start its local MCP server.

The claude-code-local plugin launches ``metaforge-mcp`` as a subprocess, and its
README tells people to run a plain ``pipx install``. A dependency that only the
dev or gateway extras bring in therefore breaks it, and the symptom is a server
that shows CONNECTION_CLOSED with nothing to say why. The unit tests cannot see
that: they run in an environment that already has every extra.

Run it in an environment built with ``pip install .`` and nothing else. It
starts the server exactly as the plugin does, sends the three messages any MCP
client sends first, and checks the answers.

    python scripts/ci/check_mcp_starts.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

TIMEOUT_SECONDS = 120
MIN_TOOLS = 100  # the server registers a few hundred; far fewer means adapters failed to load
REQUIRED_TOOLS = ("twin.imagine_get_studio", "twin.imagine_put_studio", "twin.imagine_validate")

MESSAGES = (
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "check-mcp-starts", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
)


def fail(message: str, stderr: str = "") -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    if stderr.strip():
        print("--- server stderr (last 25 lines) ---", file=sys.stderr)
        print("\n".join(stderr.strip().splitlines()[-25:]), file=sys.stderr)
    return 1


def main() -> int:
    exe = shutil.which("metaforge-mcp")
    if exe is None:
        return fail("metaforge-mcp is not on PATH: was the project installed?")
    env = {**os.environ, "METAFORGE_OTEL_EXPORT": "false"}
    proc = subprocess.Popen(
        [exe, "--transport", "stdio", "--profile", "all"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    payload = "".join(json.dumps(m) + "\n" for m in MESSAGES)
    try:
        # Closing stdin after the three messages makes the server answer and exit cleanly.
        stdout, stderr = proc.communicate(payload, timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, stderr = proc.communicate()
        return fail(f"the server did not answer within {TIMEOUT_SECONDS}s", stderr)

    replies: dict[int, dict] = {}
    for line in stdout.splitlines():
        try:
            reply = json.loads(line)
        except ValueError:
            continue
        if isinstance(reply, dict) and isinstance(reply.get("id"), int):
            replies[reply["id"]] = reply

    if 1 not in replies:
        return fail(f"no answer to initialize (the server exited with {proc.returncode})", stderr)
    name = replies[1].get("result", {}).get("serverInfo", {}).get("name")
    if name != "metaforge-mcp":
        return fail(f"initialize answered with an unexpected server name: {name!r}", stderr)
    if 2 not in replies:
        return fail("no answer to tools/list", stderr)
    tools = [t.get("name") for t in replies[2].get("result", {}).get("tools", [])]
    if len(tools) < MIN_TOOLS:
        return fail(f"only {len(tools)} tools registered, expected at least {MIN_TOOLS}", stderr)
    missing = [t for t in REQUIRED_TOOLS if t not in tools]
    if missing:
        return fail(f"expected tools are missing: {', '.join(missing)}", stderr)

    print(f"OK: metaforge-mcp started from a plain install and offers {len(tools)} tools.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
