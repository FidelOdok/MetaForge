"""The sidecar serves a project's brief and requirements from the store its tools use.

The brief resources were built on the gateway's ``get_project_backend()``, which is never
set up in the MCP server's process: an empty in-memory store. Every
``metaforge://twin/brief/<id>`` and ``/requirements/<id>`` read then said "no project",
locally and on staging, while ``project.get`` found the same project. The plugin's
status, use and new commands read those resources.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from api_gateway.projects import backend as backend_module
from api_gateway.projects.backend import InMemoryProjectBackend
from metaforge.mcp.__main__ import _bootstrap, _parse_args

_SERVICE_ENV = (
    "NEO4J_URI",
    "METAFORGE_NEO4J_URI",
    "METAFORGE_GRAPH_BACKEND",
    "DATABASE_URL",
    "OPEN_ROUTER_API_KEY",
    "METAFORGE_ADAPTERS",
    "METAFORGE_DASHBOARD_URL",
    "METAFORGE_GATEWAY_URL",
)


async def _read(server: Any, uri: str) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": uri}})
    )
    result: dict[str, Any] = json.loads(raw)
    return result


async def test_the_brief_resource_finds_a_project_the_project_tools_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in _SERVICE_ENV:
        monkeypatch.delenv(key, raising=False)
    store = InMemoryProjectBackend.create()
    project = await store.create_project(
        name="Local connector test", description="Throwaway project.", status="draft"
    )

    async def _same_store() -> InMemoryProjectBackend:
        return store

    monkeypatch.setattr(backend_module, "create_project_backend", _same_store)
    server, twin, *_ = await _bootstrap(_parse_args(["--transport", "http"]))
    try:
        pid = str(getattr(project, "id", None) or project["id"])
        brief = await _read(server, f"metaforge://twin/brief/{pid}")
        assert "error" not in brief, brief
        text = brief["result"]["contents"][0]["text"]
        assert "Local connector test" in text
        requirements = await _read(server, f"metaforge://twin/requirements/{pid}")
        assert "error" not in requirements, requirements
    finally:
        await twin.aclose()
