"""Unit tests for the Project MCP adapter (MET-427)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from api_gateway.projects.backend import InMemoryProjectBackend
from api_gateway.projects.schemas import ProjectResponse
from tool_registry.tools.project.adapter import ProjectServer


@pytest.fixture
def backend() -> InMemoryProjectBackend:
    return InMemoryProjectBackend.create()


@pytest.fixture
def server(backend: InMemoryProjectBackend) -> ProjectServer:
    return ProjectServer(backend=backend)


async def _call(server: ProjectServer, name: str, args: dict) -> dict:
    """Invoke a tool via the legacy ``tool/call`` dialect.

    The per-adapter ``McpToolServer`` only speaks legacy; the spec
    ``tools/call`` translator lives on ``UnifiedMcpServer``. For the
    adapter unit test we go direct.
    """
    raw = await server.handle_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tool/call",
                "params": {"tool_id": name, "arguments": args},
            }
        )
    )
    response = json.loads(raw)
    assert "error" not in response, response
    # Legacy ``tool/call`` envelope: result = {tool_id, status, data, duration_ms}.
    result = response["result"]
    if isinstance(result, dict) and result.get("status") == "success":
        return result["data"]
    return result


class TestRegistration:
    def test_three_tools_registered(self, server: ProjectServer) -> None:
        assert set(server.tool_ids) == {
            "project.create",
            "project.list",
            "project.get",
        }


class TestCreate:
    async def test_creates_with_uuid_and_timestamp(self, server: ProjectServer) -> None:
        result = await _call(
            server,
            "project.create",
            {"name": "demo-flight-controller", "description": "Test FC"},
        )
        assert result["name"] == "demo-flight-controller"
        assert result["description"] == "Test FC"
        assert result["status"] == "draft"
        # UUID round-trips
        from uuid import UUID

        UUID(result["id"])
        # created_at is recent and ISO-format
        created = datetime.fromisoformat(result["created_at"])
        assert (datetime.now(UTC) - created).total_seconds() < 5

    async def test_create_requires_name(self, server: ProjectServer) -> None:
        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tool/call",
                    "params": {"tool_id": "project.create", "arguments": {}},
                }
            )
        )
        response = json.loads(raw)
        # Empty args → handler raises ValueError("'name' is required")
        assert "error" in response, response


class TestList:
    async def test_list_empty(self, server: ProjectServer) -> None:
        result = await _call(server, "project.list", {})
        assert result == {"projects": [], "total": 0}

    async def test_list_after_create(self, server: ProjectServer) -> None:
        await _call(server, "project.create", {"name": "alpha"})
        await _call(server, "project.create", {"name": "beta"})
        result = await _call(server, "project.list", {})
        assert result["total"] == 2
        names = {p["name"] for p in result["projects"]}
        assert names == {"alpha", "beta"}


class TestGet:
    async def test_get_by_id_round_trips(self, server: ProjectServer) -> None:
        created = await _call(server, "project.create", {"name": "demo"})
        fetched = await _call(server, "project.get", {"id": created["id"]})
        assert fetched["id"] == created["id"]
        assert fetched["name"] == "demo"

    async def test_get_by_name(self, server: ProjectServer) -> None:
        created = await _call(server, "project.create", {"name": "by-name"})
        fetched = await _call(server, "project.get", {"name": "by-name"})
        assert fetched["id"] == created["id"]

    async def test_get_missing_returns_null(self, server: ProjectServer) -> None:
        result = await _call(server, "project.get", {"id": str(uuid4())})
        assert result is None or result == {}  # tools/call may wrap null as {}


class TestLateBinding:
    async def test_unbound_backend_raises(self) -> None:
        server = ProjectServer(backend=None)
        with pytest.raises(RuntimeError, match="set_backend"):
            _ = server.backend

    async def test_set_backend_binds_late(self) -> None:
        server = ProjectServer(backend=None)
        backend = InMemoryProjectBackend.create()
        server.set_backend(backend)
        assert server.backend is backend


class TestProjectIdScoping:
    """MET-441: ``current_context().project_id`` scopes list/get."""

    async def test_list_unscoped_when_no_ctx_project_id(self, server: ProjectServer) -> None:
        """Default ctx has no project_id → list returns everything."""
        await _call(server, "project.create", {"name": "alpha"})
        await _call(server, "project.create", {"name": "beta"})
        result = await _call(server, "project.list", {})
        assert result["total"] == 2
        names = {p["name"] for p in result["projects"]}
        assert names == {"alpha", "beta"}

    async def test_list_scopes_to_ctx_project(self, server: ProjectServer) -> None:
        """MET-441: when ctx.project_id is set, list returns only that project."""
        from uuid import UUID

        from mcp_core.context import McpCallContext, with_context

        a = await _call(server, "project.create", {"name": "alpha"})
        await _call(server, "project.create", {"name": "beta"})

        ctx = McpCallContext(project_id=UUID(a["id"]))
        with with_context(ctx):
            result = await _call(server, "project.list", {})

        assert result["total"] == 1
        assert result["projects"][0]["name"] == "alpha"

    async def test_get_returns_none_when_ctx_project_mismatch(
        self, server: ProjectServer
    ) -> None:
        """MET-441: project.get respects ctx.project_id boundary."""
        import json
        from uuid import UUID

        from mcp_core.context import McpCallContext, with_context

        a = await _call(server, "project.create", {"name": "alpha"})
        b = await _call(server, "project.create", {"name": "beta"})

        ctx = McpCallContext(project_id=UUID(a["id"]))
        with with_context(ctx):
            # Use raw call so we can see the unwrapped data=None envelope.
            raw = await server.handle_request(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "tool/call",
                        "params": {"tool_id": "project.get", "arguments": {"id": b["id"]}},
                    }
                )
            )
            response = json.loads(raw)
            assert "error" not in response, response
            assert response["result"]["data"] is None

        # Same lookup without ctx scoping returns the project.
        result = await _call(server, "project.get", {"id": b["id"]})
        assert result is not None
        assert result["name"] == "beta"


class TestProtocolDuckTyping:
    """A duck-typed backend without inheriting ProjectBackend still works.

    Confirms the structural ``ProjectBackendLike`` protocol does what the
    layer-rule rationale claims.
    """

    async def test_duck_typed_backend_works(self) -> None:
        from datetime import UTC, datetime

        class _DuckBackend:
            def __init__(self) -> None:
                self._store: dict[str, ProjectResponse] = {}

            async def list_projects(self) -> list[ProjectResponse]:
                return list(self._store.values())

            async def get_project(self, project_id: str) -> ProjectResponse | None:
                return self._store.get(project_id)

            async def create_project(
                self, *, name: str, description: str, status: str
            ) -> ProjectResponse:
                now = datetime.now(UTC).isoformat()
                pid = str(uuid4())
                p = ProjectResponse(
                    id=pid,
                    name=name,
                    description=description,
                    status=status,
                    agent_count=0,
                    created_at=now,
                    last_updated=now,
                )
                self._store[pid] = p
                return p

        server = ProjectServer(backend=_DuckBackend())
        result = await _call(server, "project.create", {"name": "duck"})
        assert result["name"] == "duck"
        listed = await _call(server, "project.list", {})
        assert listed["total"] == 1
