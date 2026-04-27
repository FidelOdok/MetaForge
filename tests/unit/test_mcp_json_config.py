"""Schema test for the repo-root ``.mcp.json`` (MET-339).

Locks in the contract Claude Code expects so a careless edit can't
break the launcher. Doesn't validate every Claude-Code-specific field
— just the ones our entrypoint depends on.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_JSON = REPO_ROOT / ".mcp.json"


def _load() -> dict:
    return json.loads(MCP_JSON.read_text(encoding="utf-8"))


def test_mcp_json_parses_as_object() -> None:
    payload = _load()
    assert isinstance(payload, dict)
    assert "mcpServers" in payload


def test_metaforge_server_entry_present() -> None:
    payload = _load()
    servers = payload["mcpServers"]
    assert "metaforge" in servers, sorted(servers)


def test_metaforge_entry_targets_stdio_module_runner() -> None:
    entry = _load()["mcpServers"]["metaforge"]
    assert entry["command"] == "python"
    assert entry["args"][:2] == ["-m", "metaforge.mcp"]
    assert "--transport" in entry["args"]
    transport_idx = entry["args"].index("--transport")
    assert entry["args"][transport_idx + 1] == "stdio"


def test_env_block_is_string_map() -> None:
    entry = _load()["mcpServers"]["metaforge"]
    env = entry.get("env", {})
    assert isinstance(env, dict)
    for key, value in env.items():
        assert isinstance(key, str)
        assert isinstance(value, str), f"{key} must be string, got {type(value).__name__}"
