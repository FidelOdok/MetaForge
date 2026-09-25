"""Unit tests for the Twin MCP tool adapter (MET-382)."""

from __future__ import annotations

import base64
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
        self.subgraph_edge_types_calls: list[list[str] | None] = []
        self.subgraph_direction_calls: list[str] = []
        self.cypher_calls: list[tuple[str, dict[str, Any]]] = []
        self.evaluate_calls: list[str] = []
        self.evaluate_project_id_calls: list[UUID | None] = []
        # Configurable returns.
        self.subgraph_return: SubGraph | None = None
        self.cypher_rows: list[dict[str, Any]] = []
        self.evaluate_return: ConstraintEvaluationResult = ConstraintEvaluationResult(
            passed=True, evaluated_count=0
        )

    async def get_subgraph(
        self,
        root_id: UUID,
        depth: int = 2,
        edge_types=None,
        direction: str = "outgoing",
    ) -> SubGraph:
        self.subgraph_calls.append((root_id, depth))
        self.subgraph_edge_types_calls.append(edge_types)
        self.subgraph_direction_calls.append(direction)
        return self.subgraph_return or SubGraph(nodes=[], edges=[], root_id=root_id, depth=depth)

    async def query_cypher(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self.cypher_calls.append((query, params or {}))
        return list(self.cypher_rows)

    async def evaluate_constraints(
        self, branch: str = "main", project_id: UUID | None = None
    ) -> ConstraintEvaluationResult:
        self.evaluate_calls.append(branch)
        self.evaluate_project_id_calls.append(project_id)
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

    async def test_no_edge_types_means_no_filter(self) -> None:
        """FORGE-47: omitting edge_types keeps thread_for's pre-existing
        unfiltered behavior exactly -- None reaches get_subgraph, not []."""
        twin = _FakeTwin()
        node_id = uuid4()
        twin.subgraph_return = SubGraph(nodes=[], edges=[], root_id=node_id, depth=3)
        srv = TwinServer(twin=twin)
        await srv.handle_request(_request("twin.thread_for", {"node_id": str(node_id)}))
        assert twin.subgraph_edge_types_calls == [None]

    async def test_edge_types_passed_through_as_plain_strings(self) -> None:
        """No EdgeType import needed at this layer -- StrEnum equality means
        the real graph engines filter correctly against plain strings."""
        twin = _FakeTwin()
        node_id = uuid4()
        twin.subgraph_return = SubGraph(nodes=[], edges=[], root_id=node_id, depth=3)
        srv = TwinServer(twin=twin)
        await srv.handle_request(
            _request(
                "twin.thread_for",
                {"node_id": str(node_id), "edge_types": ["implements", "derives_from"]},
            )
        )
        assert twin.subgraph_edge_types_calls == [["implements", "derives_from"]]

    async def test_edge_types_must_be_a_list_of_strings(self) -> None:
        srv = TwinServer(twin=_FakeTwin())
        raw = await srv.handle_request(
            _request("twin.thread_for", {"node_id": str(uuid4()), "edge_types": "implements"})
        )
        assert "error" in json.loads(raw)

    async def test_edge_types_rejects_non_string_items(self) -> None:
        srv = TwinServer(twin=_FakeTwin())
        raw = await srv.handle_request(
            _request("twin.thread_for", {"node_id": str(uuid4()), "edge_types": [1, 2]})
        )
        assert "error" in json.loads(raw)

    async def test_default_direction_is_outgoing(self) -> None:
        """FORGE-72: omitting direction preserves the pre-fix behavior."""
        twin = _FakeTwin()
        node_id = uuid4()
        twin.subgraph_return = SubGraph(nodes=[], edges=[], root_id=node_id, depth=3)
        srv = TwinServer(twin=twin)
        await srv.handle_request(_request("twin.thread_for", {"node_id": str(node_id)}))
        assert twin.subgraph_direction_calls == ["outgoing"]

    @pytest.mark.parametrize("direction", ["outgoing", "incoming", "both"])
    async def test_direction_passed_through(self, direction: str) -> None:
        twin = _FakeTwin()
        node_id = uuid4()
        twin.subgraph_return = SubGraph(nodes=[], edges=[], root_id=node_id, depth=3)
        srv = TwinServer(twin=twin)
        await srv.handle_request(
            _request("twin.thread_for", {"node_id": str(node_id), "direction": direction})
        )
        assert twin.subgraph_direction_calls == [direction]

    async def test_invalid_direction_rejected(self) -> None:
        srv = TwinServer(twin=_FakeTwin())
        raw = await srv.handle_request(
            _request("twin.thread_for", {"node_id": str(uuid4()), "direction": "sideways"})
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

    async def test_cypher_injects_project_id_when_ctx_scoped(self) -> None:
        """MET-441: when ctx.project_id is set, the generated Cypher
        binds project_id alongside the user's property.
        """
        from uuid import uuid4

        from mcp_core.context import McpCallContext, with_context

        twin = _FakeTwin()
        srv = TwinServer(twin=twin)
        proj = uuid4()

        with with_context(McpCallContext(project_id=proj)):
            await srv.handle_request(
                _request(
                    "twin.find_by_property",
                    {"node_type": "BOMItem", "property": "mpn", "value": "STM32"},
                )
            )

        cypher, params = twin.cypher_calls[0]
        assert "project_id: $project_id" in cypher
        assert params["project_id"] == str(proj)
        assert params["value"] == "STM32"

    async def test_cypher_unscoped_when_ctx_has_no_project_id(self) -> None:
        """Default ctx (no project_id) → no project_id filter injected."""
        twin = _FakeTwin()
        srv = TwinServer(twin=twin)
        await srv.handle_request(
            _request(
                "twin.find_by_property",
                {"node_type": "BOMItem", "property": "mpn", "value": "STM32"},
            )
        )
        cypher, params = twin.cypher_calls[0]
        assert "project_id" not in cypher
        assert "project_id" not in params


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

    async def test_unscoped_when_ctx_has_no_project_id(self) -> None:
        """FORGE-74: default ctx (no project_id) -> no scoping applied,
        same admin-path behavior as before this fix."""
        twin = _FakeTwin()
        srv = TwinServer(twin=twin)
        await srv.handle_request(_request("twin.constraint_violations", {}))
        assert twin.evaluate_project_id_calls == [None]

    async def test_scoped_to_ctx_project_id(self) -> None:
        """FORGE-74: when ctx.project_id is set, it's forwarded to
        evaluate_constraints so one project's violations can't be reported
        against another's (the bug this fix closes)."""
        from mcp_core.context import McpCallContext, with_context

        twin = _FakeTwin()
        srv = TwinServer(twin=twin)
        proj = uuid4()

        with with_context(McpCallContext(project_id=proj)):
            await srv.handle_request(_request("twin.constraint_violations", {}))

        assert twin.evaluate_project_id_calls == [proj]

    async def test_explicit_project_id_argument_scopes(self) -> None:
        """FORGE-75: an explicit project_id argument works with no ambient
        context at all -- the actually-reachable path from a real chat turn,
        since the project brief tells the agent to pass this directly."""
        twin = _FakeTwin()
        srv = TwinServer(twin=twin)
        proj = uuid4()

        await srv.handle_request(_request("twin.constraint_violations", {"project_id": str(proj)}))

        assert twin.evaluate_project_id_calls == [proj]

    async def test_explicit_project_id_argument_wins_over_ctx(self) -> None:
        """FORGE-75: an explicit argument takes precedence over whatever the
        ambient context (if any) is set to."""
        from mcp_core.context import McpCallContext, with_context

        twin = _FakeTwin()
        srv = TwinServer(twin=twin)
        ctx_proj = uuid4()
        explicit_proj = uuid4()

        with with_context(McpCallContext(project_id=ctx_proj)):
            await srv.handle_request(
                _request("twin.constraint_violations", {"project_id": str(explicit_proj)})
            )

        assert twin.evaluate_project_id_calls == [explicit_proj]

    async def test_invalid_project_id_argument_rejected(self) -> None:
        srv = TwinServer(twin=_FakeTwin())
        raw = await srv.handle_request(
            _request("twin.constraint_violations", {"project_id": "not-a-uuid"})
        )
        assert "error" in json.loads(raw)


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
# record_engineering_entity (FORGE-47)
# ---------------------------------------------------------------------------


class TestRecordEngineeringEntity:
    async def test_tool_not_exposed_without_a_recorder(self) -> None:
        srv = TwinServer(twin=_FakeTwin())
        assert "twin.record_engineering_entity" not in set(srv.tool_ids)

    async def test_tool_exposed_when_recorder_given(self) -> None:
        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {"node_id": "n1", "entity_type": kwargs["entity_type"], "parent_ids": []}

        srv = TwinServer(twin=_FakeTwin(), engineering_entity_recorder=recorder)
        assert "twin.record_engineering_entity" in set(srv.tool_ids)

    async def test_calls_recorder_with_defaults(self) -> None:
        calls: dict[str, Any] = {}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            calls.update(kwargs)
            return {"node_id": "n1", "entity_type": "intent", "parent_ids": []}

        srv = TwinServer(twin=_FakeTwin(), engineering_entity_recorder=recorder)
        raw = await srv.handle_request(
            _request(
                "twin.record_engineering_entity",
                {"entity_type": "intent", "statement": "Build a desktop quadruped."},
            )
        )
        data = json.loads(raw)["result"]["data"]
        assert data["node_id"] == "n1"
        assert calls["entity_type"] == "intent"
        assert calls["statement"] == "Build a desktop quadruped."
        assert calls["title"] is None
        assert calls["parent_refs"] is None
        assert calls["relation"] == "derives_from"

    async def test_passes_through_title_extra_parent_refs_and_relation(self) -> None:
        calls: dict[str, Any] = {}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            calls.update(kwargs)
            return {"node_id": "n2", "entity_type": "stakeholder_need", "parent_ids": ["p1"]}

        srv = TwinServer(twin=_FakeTwin(), engineering_entity_recorder=recorder)
        await srv.handle_request(
            _request(
                "twin.record_engineering_entity",
                {
                    "entity_type": "stakeholder_need",
                    "statement": "The operator needs it to be safe.",
                    "title": "Operator safety need",
                    "extra": {"stakeholder": "STK-OPERATOR"},
                    "parent_refs": ["Desktop quadruped intent"],
                    "relation": "motivates",
                    "project_id": "11111111-1111-4111-8111-111111111111",
                    "session_id": "sess-1",
                },
            )
        )
        assert calls["title"] == "Operator safety need"
        assert calls["extra"] == {"stakeholder": "STK-OPERATOR"}
        assert calls["parent_refs"] == ["Desktop quadruped intent"]
        assert calls["relation"] == "motivates"
        assert calls["project_id"] == "11111111-1111-4111-8111-111111111111"
        assert calls["session_id"] == "sess-1"

    async def test_missing_entity_type_rejected(self) -> None:
        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {"node_id": "n1", "entity_type": "intent", "parent_ids": []}

        srv = TwinServer(twin=_FakeTwin(), engineering_entity_recorder=recorder)
        raw = await srv.handle_request(
            _request("twin.record_engineering_entity", {"statement": "x"})
        )
        assert "error" in json.loads(raw)

    async def test_missing_statement_rejected(self) -> None:
        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {"node_id": "n1", "entity_type": "intent", "parent_ids": []}

        srv = TwinServer(twin=_FakeTwin(), engineering_entity_recorder=recorder)
        raw = await srv.handle_request(
            _request("twin.record_engineering_entity", {"entity_type": "intent"})
        )
        assert "error" in json.loads(raw)


# ---------------------------------------------------------------------------
# approve_engineering_entity (FORGE-73, waiver/release model)
# ---------------------------------------------------------------------------


class TestApproveEngineeringEntity:
    async def test_tool_not_exposed_without_an_approver(self) -> None:
        srv = TwinServer(twin=_FakeTwin())
        assert "twin.approve_engineering_entity" not in set(srv.tool_ids)

    async def test_tool_exposed_when_approver_given(self) -> None:
        async def approver(**kwargs: Any) -> dict[str, Any]:
            return {
                "node_id": "n1",
                "entity_type": "waiver",
                "authority": "approved",
                "revision": 2,
            }

        srv = TwinServer(twin=_FakeTwin(), engineering_entity_approver=approver)
        assert "twin.approve_engineering_entity" in set(srv.tool_ids)

    async def test_calls_approver_with_defaults(self) -> None:
        calls: dict[str, Any] = {}

        async def approver(**kwargs: Any) -> dict[str, Any]:
            calls.update(kwargs)
            return {
                "node_id": "n1",
                "entity_type": "waiver",
                "authority": "approved",
                "revision": 2,
            }

        srv = TwinServer(twin=_FakeTwin(), engineering_entity_approver=approver)
        raw = await srv.handle_request(
            _request("twin.approve_engineering_entity", {"entity_id": "n1"})
        )
        data = json.loads(raw)["result"]["data"]
        assert data["authority"] == "approved"
        assert calls["entity_id"] == "n1"
        assert calls["target_state"] == "approved"
        assert calls["approved_by"] is None
        assert calls["expected_revision"] is None

    async def test_passes_through_target_state_approved_by_and_revision(self) -> None:
        calls: dict[str, Any] = {}

        async def approver(**kwargs: Any) -> dict[str, Any]:
            calls.update(kwargs)
            return {
                "node_id": "n1",
                "entity_type": "waiver",
                "authority": "reviewed",
                "revision": 2,
            }

        srv = TwinServer(twin=_FakeTwin(), engineering_entity_approver=approver)
        await srv.handle_request(
            _request(
                "twin.approve_engineering_entity",
                {
                    "entity_id": "n1",
                    "target_state": "reviewed",
                    "approved_by": "safety-lead",
                    "expected_revision": 1,
                },
            )
        )
        assert calls["target_state"] == "reviewed"
        assert calls["approved_by"] == "safety-lead"
        assert calls["expected_revision"] == 1

    async def test_missing_entity_id_rejected(self) -> None:
        async def approver(**kwargs: Any) -> dict[str, Any]:
            return {
                "node_id": "n1",
                "entity_type": "waiver",
                "authority": "approved",
                "revision": 2,
            }

        srv = TwinServer(twin=_FakeTwin(), engineering_entity_approver=approver)
        raw = await srv.handle_request(_request("twin.approve_engineering_entity", {}))
        assert "error" in json.loads(raw)


# ---------------------------------------------------------------------------
# twin.commit_geometry -- file_path commit-by-reference (FORGE-224)
# ---------------------------------------------------------------------------


class TestCommitGeometryFilePath:
    """A stateless tool (freecad.create_parametric, cadquery.create_parametric/
    execute_script/generate_enclosure, ...) has no session_id/obj_id -- its
    result is just a 'cad_file' path on the shared adapter workspace. Before
    this, a model calling twin.commit_geometry directly (not through a skill)
    had no way to reference that output and had to hand-copy a base64 blob it
    was never actually given."""

    async def test_relative_file_path_is_resolved_against_the_workspace_root(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
        (tmp_path / "output").mkdir()
        (tmp_path / "output" / "bracket_None.step").write_bytes(b"ISO-10303-21;")

        received: dict[str, Any] = {}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            received.update(kwargs)
            return {"node_id": "node-1", "model_url": "https://twin.local/models/node-1"}

        srv = TwinServer(twin=_FakeTwin(), geometry_recorder=recorder)
        resp = json.loads(
            await srv.handle_request(
                _request(
                    "twin.commit_geometry",
                    {"file_path": "output/bracket_None.step", "name": "Bracket"},
                )
            )
        )

        assert "error" not in resp, resp
        assert received["step_base64"] == base64.b64encode(b"ISO-10303-21;").decode("ascii")
        assert resp["result"]["data"]["node_id"] == "node-1"

    async def test_absolute_file_path_is_read_as_given(self, tmp_path, monkeypatch) -> None:
        step_file = tmp_path / "box.step"
        step_file.write_bytes(b"ISO-10303-21;HEADER;")
        received: dict[str, Any] = {}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            received.update(kwargs)
            return {"node_id": "node-2"}

        srv = TwinServer(twin=_FakeTwin(), geometry_recorder=recorder)
        await srv.handle_request(
            _request("twin.commit_geometry", {"file_path": str(step_file), "name": "Box"})
        )

        assert received["step_base64"] == base64.b64encode(b"ISO-10303-21;HEADER;").decode("ascii")

    async def test_step_base64_wins_over_file_path_when_both_given(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
        (tmp_path / "part.step").write_bytes(b"ON-DISK-BYTES")
        received: dict[str, Any] = {}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            received.update(kwargs)
            return {"node_id": "node-3"}

        srv = TwinServer(twin=_FakeTwin(), geometry_recorder=recorder)
        explicit = base64.b64encode(b"EXPLICIT-BYTES").decode("ascii")
        await srv.handle_request(
            _request(
                "twin.commit_geometry",
                {"file_path": "part.step", "name": "Part", "step_base64": explicit},
            )
        )

        assert received["step_base64"] == explicit

    async def test_a_missing_file_is_a_clear_error_not_a_silent_empty_commit(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            return {"node_id": "node-4"}

        srv = TwinServer(twin=_FakeTwin(), geometry_recorder=recorder)
        resp = json.loads(
            await srv.handle_request(
                _request(
                    "twin.commit_geometry",
                    {"file_path": "does/not/exist.step", "name": "Ghost"},
                )
            )
        )

        # The framework flattens a handler ValueError to a generic
        # client-facing message (the real detail is logged server-side) --
        # same convention every other error case in this file asserts on.
        assert "error" in resp


# ---------------------------------------------------------------------------
# twin.commit_geometry -- flatten 'properties' onto top-level metadata (FORGE-100)
# ---------------------------------------------------------------------------


class TestCommitGeometryPropertiesFlattening:
    """Re-test 2026-09-25: a node committed by session_id+obj_id still had no
    measured keys -- 'properties' was always accepted but only ever nested
    under metadata.geometry_features.properties, never the top-level keys a
    constraint expression (wp.metadata.get('mass_kg', 0)) actually reads."""

    async def test_canonical_keys_in_properties_are_also_passed_as_extra_metadata(
        self,
    ) -> None:
        received: dict[str, Any] = {}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            received.update(kwargs)
            return {"node_id": "node-1"}

        srv = TwinServer(twin=_FakeTwin(), geometry_recorder=recorder)
        await srv.handle_request(
            _request(
                "twin.commit_geometry",
                {
                    "session_id": "s1",
                    "obj_id": "assembly_4",
                    "name": "Upper Arm Link",
                    "step_base64": base64.b64encode(b"ISO-10303-21;").decode("ascii"),
                    "properties": {
                        "volume_mm3": 1800.0,
                        "surface_area_mm2": 900.0,
                        "mass_kg": 4.86,
                        "bounding_box": {"min_x": 0.0, "max_x": 30.0},
                        "some_other_measurement": "kept in geometry_features only",
                    },
                },
            )
        )

        # The pre-existing nested structure is untouched (back-compat).
        assert received["properties"]["some_other_measurement"] == (
            "kept in geometry_features only"
        )
        # The canonical keys are ALSO flattened for the constraint engine.
        assert received["extra_metadata"] == {
            "volume_mm3": 1800.0,
            "surface_area_mm2": 900.0,
            "mass_kg": 4.86,
            "bbox_mm": {"min_x": 0.0, "max_x": 30.0},
        }

    async def test_no_extra_metadata_key_when_properties_has_no_canonical_measurements(
        self,
    ) -> None:
        received: dict[str, Any] = {}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            received.update(kwargs)
            return {"node_id": "node-1"}

        srv = TwinServer(twin=_FakeTwin(), geometry_recorder=recorder)
        await srv.handle_request(
            _request(
                "twin.commit_geometry",
                {
                    "session_id": "s1",
                    "obj_id": "assembly_4",
                    "name": "Upper Arm Link",
                    "step_base64": base64.b64encode(b"ISO-10303-21;").decode("ascii"),
                    "properties": {"note": "no measured keys here"},
                },
            )
        )

        assert "extra_metadata" not in received

    async def test_no_properties_at_all_omits_extra_metadata(self) -> None:
        received: dict[str, Any] = {}

        async def recorder(**kwargs: Any) -> dict[str, Any]:
            received.update(kwargs)
            return {"node_id": "node-1"}

        srv = TwinServer(twin=_FakeTwin(), geometry_recorder=recorder)
        await srv.handle_request(
            _request(
                "twin.commit_geometry",
                {
                    "session_id": "s1",
                    "obj_id": "assembly_4",
                    "name": "Upper Arm Link",
                    "step_base64": base64.b64encode(b"ISO-10303-21;").decode("ascii"),
                },
            )
        )

        assert "extra_metadata" not in received


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
