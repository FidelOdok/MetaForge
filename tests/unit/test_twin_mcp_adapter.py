"""Unit tests for the Twin MCP tool adapter (MET-382)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from tool_registry.tools.twin.adapter import TwinServer
from tool_registry.tools.twin.queries import (
    detect_mutations,
    serialise_subgraph,
)
from twin_core.constraint_engine.models import (
    ConstraintEvaluationResult,
    ConstraintViolation,
)
from twin_core.models.enums import ConstraintSeverity, EdgeType
from twin_core.models.relationship import EdgeBase, SubGraph
from twin_core.models.work_product import WorkProduct


def _request(tool_id: str, args: dict[str, Any]) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tool/call",
            "params": {"tool_id": tool_id, "arguments": args},
        }
    )


def _wp(name: str, *, wp_id: UUID | None = None) -> WorkProduct:
    """Helper for fixture work-products."""
    return WorkProduct(
        id=wp_id or uuid4(),
        name=name,
        type="documentation",
        domain="test",
        file_path=f"/tmp/{name}.md",
        content_hash="0" * 64,
        format="markdown",
        created_by="test:harness",
    )


class _FakeTwin:
    """Minimal TwinAPI double — records calls and returns canned data."""

    def __init__(self) -> None:
        self.subgraph_calls: list[tuple[UUID, int]] = []
        self.cypher_calls: list[tuple[str, dict[str, Any]]] = []
        self.evaluate_calls: list[str] = []
        # Configurable returns.
        self.subgraph_return: SubGraph | None = None
        self.cypher_rows: list[dict[str, Any]] = []
        self.evaluate_return: ConstraintEvaluationResult = ConstraintEvaluationResult(
            passed=True, evaluated_count=0
        )

    async def get_subgraph(self, root_id: UUID, depth: int = 2, edge_types=None) -> SubGraph:
        self.subgraph_calls.append((root_id, depth))
        return self.subgraph_return or SubGraph(nodes=[], edges=[], root_id=root_id, depth=depth)

    async def query_cypher(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.cypher_calls.append((query, params or {}))
        return list(self.cypher_rows)

    async def evaluate_constraints(self, branch: str = "main") -> ConstraintEvaluationResult:
        self.evaluate_calls.append(branch)
        return self.evaluate_return


# ---------------------------------------------------------------------------
# Mutation detector
# ---------------------------------------------------------------------------


class TestDetectMutations:
    @pytest.mark.parametrize(
        "cypher",
        [
            "MATCH (n) RETURN n",
            "MATCH (a)-[r]->(b) WHERE a.id = $id RETURN a, r, b",
            "RETURN n.created_at AS created",  # word "create" inside a property — not a keyword
        ],
    )
    def test_read_only_returns_empty(self, cypher: str) -> None:
        assert detect_mutations(cypher) == []

    @pytest.mark.parametrize(
        "cypher,expected",
        [
            ("CREATE (n:Foo)", ["CREATE"]),
            ("MATCH (n) DELETE n", ["DELETE"]),
            ("MATCH (n) DETACH DELETE n", ["DETACH", "DELETE"]),
            ("MERGE (n:Foo {id:$id})", ["MERGE"]),
            ("MATCH (n) SET n.x = 1", ["SET"]),
            ("MATCH (n) REMOVE n.x", ["REMOVE"]),
            (
                "create (n) set n.x = 1",
                ["CREATE", "SET"],
            ),  # case-insensitive
        ],
    )
    def test_detects_keywords(self, cypher: str, expected: list[str]) -> None:
        assert detect_mutations(cypher) == expected

    def test_empty_input(self) -> None:
        assert detect_mutations("") == []
        assert detect_mutations(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Adapter registration + tool/list
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_registers_five_tools(self) -> None:
        srv = TwinServer(twin=_FakeTwin())
        assert srv.adapter_id == "twin"
        assert sorted(srv.tool_ids) == [
            "twin.constraint_violations",
            "twin.find_by_property",
            "twin.get_node",
            "twin.query_cypher",
            "twin.thread_for",
        ]


# ---------------------------------------------------------------------------
# get_node
# ---------------------------------------------------------------------------


class TestGetNode:
    async def test_returns_root_and_neighbours_separately(self) -> None:
        twin = _FakeTwin()
        root_id = UUID("11111111-1111-1111-1111-111111111111")
        neighbour_id = UUID("22222222-2222-2222-2222-222222222222")
        root = _wp("Root", wp_id=root_id)
        neighbour = _wp("Neighbour", wp_id=neighbour_id)
        edge = EdgeBase(
            source_id=root_id,
            target_id=neighbour_id,
            edge_type=EdgeType.DEPENDS_ON,
        )
        twin.subgraph_return = SubGraph(
            nodes=[root, neighbour],
            edges=[edge],
            root_id=root_id,
            depth=1,
        )

        srv = TwinServer(twin=twin)
        raw = await srv.handle_request(_request("twin.get_node", {"node_id": str(root_id)}))
        body = json.loads(raw)
        data = body["result"]["data"]

        assert data["node"]["name"] == "Root"
        assert len(data["neighbours"]) == 1
        assert data["neighbours"][0]["name"] == "Neighbour"
        assert len(data["edges"]) == 1

        # Verifies the adapter called get_subgraph with depth=1.
        assert twin.subgraph_calls == [(root_id, 1)]

    async def test_invalid_uuid_raises(self) -> None:
        srv = TwinServer(twin=_FakeTwin())
        raw = await srv.handle_request(_request("twin.get_node", {"node_id": "not-uuid"}))
        body = json.loads(raw)
        assert "error" in body, body
        assert body["error"]["code"] == -32001  # tool execution error

    async def test_missing_node_id_raises(self) -> None:
        srv = TwinServer(twin=_FakeTwin())
        raw = await srv.handle_request(_request("twin.get_node", {}))
        body = json.loads(raw)
        assert "error" in body


# ---------------------------------------------------------------------------
# thread_for
# ---------------------------------------------------------------------------


class TestThreadFor:
    async def test_default_depth_is_three(self) -> None:
        twin = _FakeTwin()
        node_id = uuid4()
        twin.subgraph_return = SubGraph(nodes=[], edges=[], root_id=node_id, depth=3)
        srv = TwinServer(twin=twin)
        await srv.handle_request(_request("twin.thread_for", {"node_id": str(node_id)}))
        assert twin.subgraph_calls == [(node_id, 3)]

    async def test_custom_depth(self) -> None:
        twin = _FakeTwin()
        node_id = uuid4()
        twin.subgraph_return = SubGraph(nodes=[], edges=[], root_id=node_id, depth=5)
        srv = TwinServer(twin=twin)
        raw = await srv.handle_request(
            _request("twin.thread_for", {"node_id": str(node_id), "depth": 5})
        )
        data = json.loads(raw)["result"]["data"]
        assert data["depth"] == 5
        assert twin.subgraph_calls == [(node_id, 5)]

    @pytest.mark.parametrize("depth", [0, 11, -1])
    async def test_depth_out_of_range_raises(self, depth: int) -> None:
        srv = TwinServer(twin=_FakeTwin())
        raw = await srv.handle_request(
            _request("twin.thread_for", {"node_id": str(uuid4()), "depth": depth})
        )
        assert "error" in json.loads(raw)


# ---------------------------------------------------------------------------
# find_by_property
# ---------------------------------------------------------------------------


class TestFindByProperty:
    async def test_basic_lookup(self) -> None:
        twin = _FakeTwin()
        wp = _wp("STM32H7")
        twin.cypher_rows = [{"n": wp}]
        srv = TwinServer(twin=twin)
        raw = await srv.handle_request(
            _request(
                "twin.find_by_property",
                {"node_type": "BOMItem", "property": "mpn", "value": "STM32H723VGT6"},
            )
        )
        data = json.loads(raw)["result"]["data"]
        assert data["count"] == 1
        assert data["nodes"][0]["name"] == "STM32H7"

        # Verify the adapter built the right cypher.
        assert len(twin.cypher_calls) == 1
        cypher, params = twin.cypher_calls[0]
        assert "MATCH (n:`BOMItem` {`mpn`: $value})" in cypher
        assert params == {"value": "STM32H723VGT6", "limit": 25}

    @pytest.mark.parametrize(
        "node_type",
        ["Bad-Label", "1Numeric", "drop table", "Foo Bar", ""],
    )
    async def test_invalid_node_type_label_rejected(self, node_type: str) -> None:
        srv = TwinServer(twin=_FakeTwin())
        raw = await srv.handle_request(
            _request(
                "twin.find_by_property",
                {"node_type": node_type, "property": "mpn", "value": "x"},
            )
        )
        assert "error" in json.loads(raw)

    @pytest.mark.parametrize("limit", [0, 201, -1])
    async def test_limit_out_of_range_rejected(self, limit: int) -> None:
        srv = TwinServer(twin=_FakeTwin())
        raw = await srv.handle_request(
            _request(
                "twin.find_by_property",
                {
                    "node_type": "WorkProduct",
                    "property": "name",
                    "value": "x",
                    "limit": limit,
                },
            )
        )
        assert "error" in json.loads(raw)


# ---------------------------------------------------------------------------
# constraint_violations
# ---------------------------------------------------------------------------


class TestConstraintViolations:
    async def test_severity_separation(self) -> None:
        violation = ConstraintViolation(
            constraint_id=uuid4(),
            constraint_name="material_match",
            severity=ConstraintSeverity.ERROR,
            message="material mismatch",
            work_product_ids=[],
            expression="True",
            evaluated_at=datetime.now(UTC),
        )
        warning = ConstraintViolation(
            constraint_id=uuid4(),
            constraint_name="prefer_metric",
            severity=ConstraintSeverity.WARNING,
            message="metric preferred",
            work_product_ids=[],
            expression="True",
            evaluated_at=datetime.now(UTC),
        )
        twin = _FakeTwin()
        twin.evaluate_return = ConstraintEvaluationResult(
            passed=False,
            violations=[violation],
            warnings=[warning],
            evaluated_count=2,
        )
        srv = TwinServer(twin=twin)
        raw = await srv.handle_request(_request("twin.constraint_violations", {}))
        data = json.loads(raw)["result"]["data"]
        assert data["passed"] is False
        assert len(data["violations"]) == 1
        assert len(data["warnings"]) == 1
        assert data["violations"][0]["severity"] == "error"
        assert data["warnings"][0]["severity"] == "warning"
        assert twin.evaluate_calls == ["main"]

    async def test_custom_branch(self) -> None:
        twin = _FakeTwin()
        srv = TwinServer(twin=twin)
        await srv.handle_request(_request("twin.constraint_violations", {"branch": "feature/x"}))
        assert twin.evaluate_calls == ["feature/x"]


# ---------------------------------------------------------------------------
# query_cypher (audit + mutation gate)
# ---------------------------------------------------------------------------


class TestQueryCypher:
    async def test_read_only_query_runs(self) -> None:
        twin = _FakeTwin()
        twin.cypher_rows = [{"n": "row1"}, {"n": "row2"}]
        srv = TwinServer(twin=twin)
        raw = await srv.handle_request(
            _request("twin.query_cypher", {"cypher": "MATCH (n) RETURN n LIMIT 5"})
        )
        data = json.loads(raw)["result"]["data"]
        assert data["count"] == 2
        assert len(twin.cypher_calls) == 1

    async def test_mutation_rejected_when_disabled(self) -> None:
        twin = _FakeTwin()
        srv = TwinServer(twin=twin, allow_mutations=False)
        raw = await srv.handle_request(
            _request("twin.query_cypher", {"cypher": "MATCH (n) DELETE n"})
        )
        body = json.loads(raw)
        assert "error" in body
        # Twin was never called — mutation gate fires before query.
        assert twin.cypher_calls == []

    async def test_mutation_allowed_when_flag_set(self) -> None:
        twin = _FakeTwin()
        srv = TwinServer(twin=twin, allow_mutations=True)
        raw = await srv.handle_request(
            _request("twin.query_cypher", {"cypher": "CREATE (n:Foo) RETURN n"})
        )
        # Hits the backend.
        assert "result" in json.loads(raw)
        assert len(twin.cypher_calls) == 1

    async def test_empty_cypher_rejected(self) -> None:
        srv = TwinServer(twin=_FakeTwin())
        raw = await srv.handle_request(_request("twin.query_cypher", {"cypher": "  "}))
        assert "error" in json.loads(raw)

    async def test_non_dict_params_rejected(self) -> None:
        srv = TwinServer(twin=_FakeTwin())
        raw = await srv.handle_request(
            _request(
                "twin.query_cypher",
                {"cypher": "RETURN 1", "params": "not-a-dict"},
            )
        )
        assert "error" in json.loads(raw)


# ---------------------------------------------------------------------------
# Subgraph serialisation helper
# ---------------------------------------------------------------------------


class TestSerialiseSubgraph:
    def test_handles_none(self) -> None:
        result = serialise_subgraph(None)
        assert result == {"nodes": [], "edges": [], "root_id": None, "depth": 0}

    def test_handles_pydantic(self) -> None:
        sg = SubGraph(nodes=[], edges=[], root_id=uuid4(), depth=2)
        out = serialise_subgraph(sg)
        assert "nodes" in out
        assert out["depth"] == 2
        # UUID became a string (mode="json").
        assert isinstance(out["root_id"], str)
