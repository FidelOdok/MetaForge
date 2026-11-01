"""Named digital-thread questions (FORGE-357).

`twin.thread_for` could already answer both of these — but only if the caller
knew which way to walk. Its own docstring says a requirement "sees nothing
with outgoing-only traversal", and outgoing is the default. So a model asking
"what verifies REQ-3?" calls it with the defaults, gets an empty subgraph, and
reports that nothing verifies the requirement.

The traversal was wrong and the answer looked complete. That is the bug these
two tools exist to remove: the direction is a property of the question, not
something the asker should have to know.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from tool_registry.tools.twin.adapter import TwinServer


class _RecordingTwin:
    """Records how the subgraph was asked for, and returns what it is told to."""

    def __init__(self, nodes: list[Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._nodes = nodes or []

    async def get_subgraph(self, node_id, *, depth, edge_types, direction):  # noqa: ANN001
        self.calls.append(
            {"node_id": node_id, "depth": depth, "edge_types": edge_types, "direction": direction}
        )

        class _Subgraph:
            nodes = self._nodes
            edges: list[Any] = []

        return _Subgraph()


@pytest.mark.asyncio
class TestTheWalkIsRight:
    async def test_what_verifies_walks_incoming(self) -> None:
        # The whole point. Outgoing returns nothing for a requirement, and
        # nothing reads as "unverified".
        twin = _RecordingTwin()
        server = TwinServer(twin=twin)
        await server.what_verifies({"node_id": str(uuid4())})
        assert twin.calls[0]["direction"] == "incoming"

    async def test_where_used_walks_incoming(self) -> None:
        # Containment points parent-to-child, so the part is the target.
        twin = _RecordingTwin()
        server = TwinServer(twin=twin)
        await server.where_used({"node_id": str(uuid4())})
        assert twin.calls[0]["direction"] == "incoming"

    async def test_each_question_restricts_to_its_own_edges(self) -> None:
        # Otherwise "what verifies this" also returns everything that merely
        # contains it, and the answer stops meaning anything.
        twin = _RecordingTwin()
        server = TwinServer(twin=twin)
        await server.what_verifies({"node_id": str(uuid4())})
        await server.where_used({"node_id": str(uuid4())})
        verifies, used = twin.calls[0]["edge_types"], twin.calls[1]["edge_types"]
        assert "satisfies" in verifies and "satisfies" not in used
        assert "contains" in used and "contains" not in verifies


@pytest.mark.asyncio
class TestAnEmptyAnswerSaysSo:
    async def test_nothing_found_is_explicit(self) -> None:
        twin = _RecordingTwin()
        result = await TwinServer(twin=twin).what_verifies({"node_id": str(uuid4())})
        assert result["verified"] is False
        assert result["related_count"] == 0

    async def test_the_note_says_how_it_was_asked(self) -> None:
        # So an empty answer is legible as "nothing recorded" rather than as
        # a tool that did not work — the distinction a reviewer needs.
        result = await TwinServer(twin=_RecordingTwin()).what_verifies({"node_id": str(uuid4())})
        assert "incoming" in result["note"]
        assert "not that the question could not be asked" in result["note"]

    async def test_a_found_relation_flips_the_answer(self) -> None:
        class _Node:
            id = uuid4()
            node_type = "evidence"

            def model_dump(self, *a, **k):  # noqa: ANN002, ANN003
                return {"id": str(self.id), "node_type": self.node_type}

        twin = _RecordingTwin(nodes=[_Node()])
        result = await TwinServer(twin=twin).what_verifies({"node_id": str(uuid4())})
        assert result["verified"] is True
        assert result["related_count"] == 1
        assert "note" not in result


@pytest.mark.asyncio
class TestArguments:
    async def test_a_missing_node_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="node_id is required"):
            await TwinServer(twin=_RecordingTwin()).what_verifies({})

    async def test_a_non_uuid_is_refused(self) -> None:
        with pytest.raises(ValueError, match="valid UUID"):
            await TwinServer(twin=_RecordingTwin()).where_used({"node_id": "REQ-3"})

    async def test_depth_is_bounded(self) -> None:
        with pytest.raises(ValueError, match="between 1 and 5"):
            await TwinServer(twin=_RecordingTwin()).what_verifies(
                {"node_id": str(uuid4()), "depth": 9}
            )
