"""A guardrail that refuses everything is not a guardrail (FORGE-407).

Two classifications were wrong in the same direction, and the direction
matters: both failed *closed*, which is safer than failing open and still
made the plugin unusable.

* `session.start` was held, so an external harness could not open a session
  — and session capture exists precisely so external harnesses attribute
  their work (FORGE-366). The feature refused the only callers it was for.
* `twin.query_cypher` was classified per tool, so on any deployment running
  `--allow-twin-mutations` — which is every deployment that can build a
  digital thread — a plain `MATCH ... RETURN` was refused as "may overwrite
  or remove data".

Both were found by running the real plugin against a real gateway, not by
any test here. What these add is the assertion that would have caught them.
"""

from __future__ import annotations

import pytest

from mcp_core.annotations import annotations_for
from mcp_core.cypher import detect_mutations, is_read_only_cypher
from mcp_core.guardrails import BOOKKEEPING, Caller, decide


class TestSessionBookkeepingIsNotHeld:
    @pytest.mark.parametrize("tool_id", sorted(BOOKKEEPING))
    def test_a_remote_caller_can_open_and_record_a_session(self, tool_id: str) -> None:
        assert not decide(tool_id, caller=Caller.UNTRUSTED).requires_approval
        assert not decide(tool_id, caller=Caller.REMOTE).requires_approval

    @pytest.mark.parametrize("tool_id", sorted(BOOKKEEPING))
    def test_but_the_annotation_still_says_it_writes(self, tool_id: str) -> None:
        """The gate knows the difference between "writes" and "writes
        something worth holding". The annotation must not pretend it is a
        read -- a client shown `readOnlyHint: true` for a tool that writes
        has been lied to, and that is a worse bug than the one being fixed.
        """
        assert annotations_for(tool_id)["readOnlyHint"] is False

    def test_the_exemption_does_not_leak_to_design_state(self) -> None:
        # The control. If "bookkeeping" grew to cover twin writes, this file
        # would be certifying the hole it was written to close.
        for tool_id in ("twin.commit_geometry", "twin.record_decision", "project.delete"):
            assert tool_id not in BOOKKEEPING
            assert decide(tool_id, caller=Caller.UNTRUSTED).requires_approval

    def test_the_reason_says_why_it_is_not_held(self) -> None:
        reason = decide("session.start", caller=Caller.UNTRUSTED).reason
        assert "not design state" in reason


class TestCypherIsJudgedPerCall:
    def _held(self, query: str | None, *, mutations_enabled: bool = True) -> bool:
        return decide(
            "twin.query_cypher",
            caller=Caller.UNTRUSTED,
            twin_mutations_enabled=mutations_enabled,
            arguments=None if query is None else {"cypher": query},
        ).requires_approval

    def test_a_read_runs_even_with_twin_mutations_enabled(self) -> None:
        """The bug. `--allow-twin-mutations` made the whole tool destructive,
        so the deployment that can build a digital thread was the one that
        could not read it."""
        assert not self._held("MATCH (n:WorkProduct) RETURN n LIMIT 10")

    def test_a_write_is_still_held(self) -> None:
        assert self._held("CREATE (n:WorkProduct {name: 'x'})")

    @pytest.mark.parametrize(
        "query",
        [
            "MATCH (n) SET n.x = 1",
            "MATCH (n) DETACH DELETE n",
            "MERGE (n:X)",
            "MATCH (n) REMOVE n.label",
            "LOAD CSV FROM 'f' AS row CREATE (:X)",
        ],
    )
    def test_every_mutating_form_is_held(self, query: str) -> None:
        assert self._held(query)

    def test_a_property_name_containing_a_keyword_is_still_a_read(self) -> None:
        # `RETURN n.created_at` must not match CREATE. Getting this wrong
        # would re-break reads for a large fraction of real queries.
        assert not self._held("MATCH (n) RETURN n.created_at, n.deleted_flag")

    def test_no_arguments_means_held(self) -> None:
        """Absent evidence is not evidence of a read. Erring the other way
        lets an unapproved write through on a call the gate could not see."""
        assert self._held(None)

    @pytest.mark.parametrize("value", ["", "   "])
    def test_an_empty_query_is_held(self, value: str) -> None:
        assert self._held(value)

    def test_the_annotation_stays_cautious_while_the_gate_is_accurate(self) -> None:
        """They disagree on purpose. The MCP spec has no per-call hint, so
        the static annotation has to describe the tool's worst case; the gate
        sees the actual query. Asserting the divergence keeps somebody from
        "fixing" the annotation to match and re-breaking reads.
        """
        annotations = annotations_for("twin.query_cypher", twin_mutations_enabled=True)
        assert annotations["readOnlyHint"] is False
        assert not self._held("MATCH (n) RETURN n")

    def test_with_mutations_disabled_the_tool_is_read_only_anyway(self) -> None:
        assert not self._held("MATCH (n) RETURN n", mutations_enabled=False)


class TestTheDetectorMovedRatherThanForked:
    def test_both_layers_use_one_implementation(self) -> None:
        """`mcp_core` cannot import `tool_registry`, so the gate needed this
        one layer down. Copying it would have left two regexes, and the one
        that disagreed would be the one that waved a write through."""
        from tool_registry.tools.twin.queries import detect_mutations as from_registry

        assert from_registry is detect_mutations

    def test_the_helper_is_asymmetric_on_purpose(self) -> None:
        assert is_read_only_cypher("MATCH (n) RETURN n") is True
        for junk in (None, 42, "", "   ", b"MATCH"):
            assert is_read_only_cypher(junk) is False
