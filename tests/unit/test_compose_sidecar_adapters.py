"""The MCP sidecar's ``--adapters`` list names every adapter compose wires.

The sidecar loads only the adapters named in its ``--adapters`` list. The
cloud adapters (meshy, zoo, tripo, slant3d) had their keys passed to the
mcp-http service but were left out of that list, so on a deployed host the
gateway's chat had ``tripo.*`` and ``zoo.*`` while every MCP client (Claude
Code, Cursor) did not: the sidecar logged ``tripo_mcp_adapter_skipped
reason='not in adapter_ids'``.

Structural assertions on docker-compose.yml; they need no Docker.
"""

from __future__ import annotations

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

# Each key compose hands the sidecar, and the adapter that reads it.
_KEYED_ADAPTERS = {
    "MESHY_API_KEY": "meshy",
    "ZOO_API_TOKEN": "zoo",
    "TRIPO_API_KEY": "tripo",
    "SLANT3D_API_KEY": "slant3d",
}


def _mcp_http_block() -> str:
    text = COMPOSE.read_text(encoding="utf-8")
    match = re.search(r"^  mcp-http:\n(.*?)(?=^  \S)", text, re.MULTILINE | re.DOTALL)
    assert match, "docker-compose.yml has no mcp-http service"
    return match.group(1)


def _sidecar_adapters(block: str) -> set[str]:
    match = re.search(r"--adapters ([\w,]+)", block)
    assert match, "mcp-http's command has no --adapters list"
    return set(match.group(1).split(","))


def test_every_container_adapter_url_is_in_the_list() -> None:
    block = _mcp_http_block()
    wired = {m.lower() for m in re.findall(r"METAFORGE_ADAPTER_(\w+)_URL=", block)}
    assert wired, "mcp-http sets no adapter URLs"
    missing = sorted(wired - _sidecar_adapters(block))
    assert not missing, f"mcp-http reaches {missing} but does not load them"


def test_every_cloud_key_has_its_adapter_in_the_list() -> None:
    block = _mcp_http_block()
    passed = {key for key in _KEYED_ADAPTERS if re.search(rf"- {key}=", block)}
    assert passed == set(_KEYED_ADAPTERS), "mcp-http no longer receives every cloud key"
    missing = sorted({_KEYED_ADAPTERS[k] for k in passed} - _sidecar_adapters(block))
    assert not missing, f"mcp-http receives keys for {missing} but does not load them"
