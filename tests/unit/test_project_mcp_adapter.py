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
    def test_five_tools_registered(self, server: ProjectServer) -> None:
        assert set(server.tool_ids) == {
            "project.create",
            "project.list",
            "project.get",
            "project.update",
            "project.delete",
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

    async def test_duplicate_name_is_rejected(self, server: ProjectServer) -> None:
        await _call(server, "project.create", {"name": "Quadruped Robot"})
        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tool/call",
                    "params": {
                        "tool_id": "project.create",
                        "arguments": {"name": "quadruped robot"},
                    },
                }
            )
        )
        response = json.loads(raw)
        assert "error" in response, response


class TestList:
    async def test_list_empty(self, server: ProjectServer) -> None:
        result = await _call(server, "project.list", {})
        assert result == {
            "projects": [],
            "total": 0,
            "offset": 0,
            "limit": 20,
            "has_more": False,
            "next_offset": None,
        }

    async def test_list_after_create(self, server: ProjectServer) -> None:
        await _call(server, "project.create", {"name": "alpha"})
        await _call(server, "project.create", {"name": "beta"})
        result = await _call(server, "project.list", {})
        assert result["total"] == 2
        assert result["has_more"] is False
        assert result["next_offset"] is None
        names = {p["name"] for p in result["projects"]}
        assert names == {"alpha", "beta"}

    async def test_list_returns_summary_shape_not_full_work_products(
        self, server: ProjectServer
    ) -> None:
        """MET-589: list entries drop the full work_products array (the

        thing that blew list pages past the harness's truncation cap) in
        favor of a count; project.get still has the full detail.
        """
        created = await _call(server, "project.create", {"name": "solo"})
        result = await _call(server, "project.list", {})
        entry = result["projects"][0]
        assert "work_products" not in entry
        assert entry["work_product_count"] == 0
        fetched = await _call(server, "project.get", {"id": created["id"]})
        assert "work_products" in fetched

    async def test_list_paginates_with_limit_and_offset(self, server: ProjectServer) -> None:
        for i in range(5):
            await _call(server, "project.create", {"name": f"project-{i}"})

        page1 = await _call(server, "project.list", {"limit": 2, "offset": 0})
        assert len(page1["projects"]) == 2
        assert page1["total"] == 5
        assert page1["offset"] == 0
        assert page1["limit"] == 2
        assert page1["has_more"] is True
        assert page1["next_offset"] == 2

        page2 = await _call(server, "project.list", {"limit": 2, "offset": page1["next_offset"]})
        assert len(page2["projects"]) == 2
        assert page2["has_more"] is True
        assert page2["next_offset"] == 4

        page3 = await _call(server, "project.list", {"limit": 2, "offset": page2["next_offset"]})
        assert len(page3["projects"]) == 1
        assert page3["has_more"] is False
        assert page3["next_offset"] is None

        # Every project appears exactly once across pages, none dropped.
        seen_ids = {p["id"] for page in (page1, page2, page3) for p in page["projects"]}
        assert len(seen_ids) == 5

    async def test_list_limit_is_capped_at_100(self, server: ProjectServer) -> None:
        result = await _call(server, "project.list", {"limit": 5000})
        assert result["limit"] == 100

    async def test_list_accepts_numeric_strings_for_limit_and_offset(
        self, server: ProjectServer
    ) -> None:
        """Regression (MET-589): some MCP call paths deliver numeric args as

        strings (e.g. "2") rather than JSON numbers. A strict
        ``isinstance(x, int)`` check rejected those with a false "must be an
        integer" error even though the value was perfectly valid — caught
        live on fidel-dev, where every explicit limit/offset call failed.
        """
        for i in range(3):
            await _call(server, "project.create", {"name": f"str-arg-{i}"})

        result = await _call(server, "project.list", {"limit": "2", "offset": "0"})
        assert len(result["projects"]) == 2
        assert result["limit"] == 2
        assert result["offset"] == 0
        assert result["has_more"] is True

    async def test_list_rejects_invalid_limit(self, server: ProjectServer) -> None:
        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tool/call",
                    "params": {"tool_id": "project.list", "arguments": {"limit": 0}},
                }
            )
        )
        response = json.loads(raw)
        assert "error" in response, response

    async def test_list_rejects_non_numeric_limit(self, server: ProjectServer) -> None:
        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tool/call",
                    "params": {"tool_id": "project.list", "arguments": {"limit": "not-a-number"}},
                }
            )
        )
        response = json.loads(raw)
        assert "error" in response, response

    async def test_list_rejects_boolean_limit(self, server: ProjectServer) -> None:
        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tool/call",
                    "params": {"tool_id": "project.list", "arguments": {"limit": True}},
                }
            )
        )
        response = json.loads(raw)
        assert "error" in response, response

    async def test_list_self_caps_page_size_regardless_of_requested_limit(
        self, server: ProjectServer
    ) -> None:
        """MET-589 regression: a huge `limit` used to just return every

        project in one oversized payload, which the harness's own char-budget
        truncation would then silently chop — even though the tool itself
        reported `has_more=False`, making the caller believe it saw
        everything. The page must now self-cap on serialized size, and
        `has_more`/`next_offset` must reflect the *actual* returned count.
        """
        big_description = "x" * 3000
        for i in range(10):
            await _call(
                server,
                "project.create",
                {"name": f"big-{i}", "description": big_description},
            )

        result = await _call(server, "project.list", {"limit": 100, "offset": 0})
        assert result["total"] == 10
        returned = len(result["projects"])
        assert returned < 10  # self-cap kicked in despite limit=100 covering all 10
        assert result["has_more"] is True
        assert result["next_offset"] == returned

        # Walk every remaining page; every project must surface exactly once.
        seen_ids = {p["id"] for p in result["projects"]}
        next_offset = result["next_offset"]
        while next_offset is not None:
            page = await _call(server, "project.list", {"limit": 100, "offset": next_offset})
            assert len(page["projects"]) >= 1  # forward progress guaranteed
            seen_ids.update(p["id"] for p in page["projects"])
            next_offset = page["next_offset"]
        assert len(seen_ids) == 10


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


class TestUpdate:
    async def test_renames_project(self, server: ProjectServer) -> None:
        created = await _call(server, "project.create", {"name": "old-name"})
        updated = await _call(server, "project.update", {"id": created["id"], "name": "new-name"})
        assert updated["name"] == "new-name"
        assert updated["id"] == created["id"]

    async def test_updates_description_and_status_independently(
        self, server: ProjectServer
    ) -> None:
        created = await _call(server, "project.create", {"name": "p"})
        updated = await _call(server, "project.update", {"id": created["id"], "status": "active"})
        assert updated["status"] == "active"
        assert updated["name"] == "p"  # untouched

    async def test_requires_at_least_one_field(self, server: ProjectServer) -> None:
        created = await _call(server, "project.create", {"name": "p2"})
        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tool/call",
                    "params": {"tool_id": "project.update", "arguments": {"id": created["id"]}},
                }
            )
        )
        response = json.loads(raw)
        assert "error" in response, response

    async def test_missing_project_raises(self, server: ProjectServer) -> None:
        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tool/call",
                    "params": {
                        "tool_id": "project.update",
                        "arguments": {"id": str(uuid4()), "name": "x"},
                    },
                }
            )
        )
        response = json.loads(raw)
        assert "error" in response, response

    async def test_rename_to_existing_name_conflicts(self, server: ProjectServer) -> None:
        await _call(server, "project.create", {"name": "taken"})
        other = await _call(server, "project.create", {"name": "renamable"})
        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tool/call",
                    "params": {
                        "tool_id": "project.update",
                        "arguments": {"id": other["id"], "name": "TAKEN"},
                    },
                }
            )
        )
        response = json.loads(raw)
        assert "error" in response, response


class TestDelete:
    async def test_deletes_project(self, server: ProjectServer) -> None:
        created = await _call(server, "project.create", {"name": "to-delete"})
        result = await _call(server, "project.delete", {"id": created["id"]})
        assert result == {"id": created["id"], "deleted": True}

        listed = await _call(server, "project.list", {})
        assert listed["total"] == 0

    async def test_delete_missing_raises(self, server: ProjectServer) -> None:
        raw = await server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tool/call",
                    "params": {"tool_id": "project.delete", "arguments": {"id": str(uuid4())}},
                }
            )
        )
        response = json.loads(raw)
        assert "error" in response, response


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

    async def test_get_returns_none_when_ctx_project_mismatch(self, server: ProjectServer) -> None:
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
