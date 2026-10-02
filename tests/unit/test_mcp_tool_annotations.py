"""MCP tool annotations (FORGE-343).

Annotations are what a harness reads to decide whether a tool call needs a
human. Getting one wrong is not a cosmetic bug: a tool wrongly marked
``readOnlyHint: true`` is one a client may run unattended, and the failure is
silent — the tool runs, the write lands, nobody was asked.

So these tests are mostly about the *shape of being wrong*, not about
individual values: that the default is safe, that the sets cannot drift out
of step with the registry, and that a runtime setting which changes whether a
tool mutates also changes what we claim about it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mcp_core.annotations import (
    ADDITIVE,
    CONDITIONALLY_READ_ONLY,
    DESTRUCTIVE,
    OPEN_WORLD,
    PRODUCING,
    READ_ONLY,
    annotations_for,
    unclassified,
)

REPO = Path(__file__).resolve().parents[2]


def declared_tool_ids() -> set[str]:
    """Every ``tool_id="..."`` the adapters declare.

    Scanned from source rather than from a live registry on purpose: the
    registry needs adapters constructed (some of which want containers), and
    the question here is only "what does this repo claim to expose".
    """
    ids: set[str] = set()
    for root in ("tool_registry", "metaforge"):
        for path in (REPO / root).rglob("*.py"):
            ids |= set(
                re.findall(
                    r'tool_id="([a-z0-9_.]+)"', path.read_text(encoding="utf-8", errors="replace")
                )
            )
    # Ids an adapter assembles rather than writes out. The scan above sees
    # `tool_id="twin.get_node"` and not `tool_id=f"{did}.search"`, so all
    # twelve distributor lookups were invisible to this guard and sat on
    # the destructive default -- a catalog search classified as "may
    # overwrite or remove data". Any future adapter that computes its ids
    # has to export them the same way.
    from tool_registry.tools.distributors.mcp_adapter import distributor_tool_ids

    ids |= distributor_tool_ids()
    # FORGE-492: and ids registered through a loop (`f"freecad.{name}"`), which
    # no source scan can see. The registry is the only honest list of them.
    ids |= set(registered_tool_ids())
    return ids


def registered_tool_ids() -> list[str]:
    """Every tool the adapters register when bootstrapped, as the server sees them."""
    import asyncio

    from tool_registry.bootstrap import bootstrap_tool_registry

    registry = asyncio.run(bootstrap_tool_registry())
    return sorted(m.tool_id for m in registry.list_tools())


class TestSafeByDefault:
    def test_unknown_tool_is_treated_as_a_destructive_write(self) -> None:
        # The property the whole module rests on. An adapter added next year
        # that nobody classifies must be over-guarded, never waved through.
        ann = annotations_for("some.brand_new_tool")
        assert ann["readOnlyHint"] is False
        assert ann["destructiveHint"] is True

    def test_read_only_tools_are_never_destructive(self) -> None:
        wrong = [t for t in READ_ONLY if annotations_for(t)["destructiveHint"]]
        assert wrong == [], f"read-only tools claiming to be destructive: {wrong}"

    def test_destructive_set_is_reported_as_destructive(self) -> None:
        wrong = [t for t in DESTRUCTIVE if not annotations_for(t)["destructiveHint"]]
        assert wrong == [], f"destructive tools not flagged: {wrong}"

    def test_a_tool_is_not_in_two_minds(self) -> None:
        # Overlap would mean the answer depends on evaluation order.
        for a, b in (
            (READ_ONLY, DESTRUCTIVE),
            (READ_ONLY, ADDITIVE),
            (READ_ONLY, PRODUCING),
            (ADDITIVE, DESTRUCTIVE),
            (PRODUCING, DESTRUCTIVE),
            (READ_ONLY, CONDITIONALLY_READ_ONLY),
        ):
            assert not (a & b), f"tool classified twice: {sorted(a & b)}"


class TestRuntimeTruth:
    """A hint that is right only in one deployment is a hint that is wrong."""

    def test_query_cypher_is_read_only_only_while_mutations_are_off(self) -> None:
        off = annotations_for("twin.query_cypher", twin_mutations_enabled=False)
        on = annotations_for("twin.query_cypher", twin_mutations_enabled=True)
        assert off["readOnlyHint"] is True
        # --allow-twin-mutations is on in the dev deployment. Claiming
        # read-only there would tell every client a mutating tool is safe.
        assert on["readOnlyHint"] is False
        assert on["destructiveHint"] is True

    def test_the_flag_moves_nothing_else(self) -> None:
        for tool in ("twin.get_node", "project.delete", "twin.record_decision"):
            assert annotations_for(tool) == annotations_for(tool, twin_mutations_enabled=True)


class TestCoverage:
    """Keeps the sets in step with the adapters as tools are added."""

    def test_the_scan_actually_finds_the_adapters(self) -> None:
        # Without this, a broken regex or a moved directory makes
        # `declared_tool_ids()` return nothing and every coverage assertion
        # below passes by finding no work to do.
        declared = declared_tool_ids()
        assert len(declared) > 80, f"only {len(declared)} tool ids found — has the scan broken?"
        assert "twin.get_node" in declared

    def test_every_declared_tool_is_classified(self) -> None:
        missing = unclassified(sorted(declared_tool_ids()))
        assert missing == [], (
            "these tools have no entry in mcp_core/annotations.py. They behave "
            "safely (destructive default) but nobody has looked at them — add "
            "each to READ_ONLY, ADDITIVE, PRODUCING or DESTRUCTIVE: " + ", ".join(missing)
        )

    def test_no_classification_names_a_tool_that_does_not_exist(self) -> None:
        # Catches a rename or a typo, either of which silently drops a tool
        # back to the default without anyone noticing.
        declared = declared_tool_ids()
        classified = (
            READ_ONLY | CONDITIONALLY_READ_ONLY | ADDITIVE | PRODUCING | DESTRUCTIVE | OPEN_WORLD
        )
        stale = sorted(classified - declared)
        assert stale == [], f"classified but no adapter declares them: {stale}"


class TestRegistryCoverage:
    """FORGE-492: the source scan misses ids an adapter builds at run time.

    `FreecadServer` registers its 39 session tools as ``f"freecad.{name}"``, so
    the regex scan never saw them, they sat on the destructive default, and
    the design-flow service caller was refused every one. This asks the
    registry what is actually registered instead of what the source spells out.
    """

    def test_every_registered_tool_is_classified(self) -> None:
        ids = registered_tool_ids()
        assert len(ids) > 60, f"only {len(ids)} tools registered, did bootstrap break"
        assert "freecad.open_session" in ids
        missing = unclassified(ids)
        assert missing == [], (
            "registered by an adapter but not in mcp_core/annotations.py: " + ", ".join(missing)
        )

    def test_the_session_tools_a_phase_needs_are_not_refused_for_the_service_caller(self) -> None:
        from mcp_core.guardrails import Caller, decide

        for tool in (
            "freecad.open_session",
            "freecad.create_sketch",
            "freecad.pad_sketch",
            "freecad.fillet",
            "freecad.describe_session",
            "freecad.measure",
            "freecad.export_model",
            "freecad.close_session",
        ):
            decision = decide(tool, caller=Caller.SERVICE)
            assert not decision.refused, f"{tool}: {decision.reason}"

    def test_execute_code_stays_destructive(self) -> None:
        # Its sandbox is source-level; Import.export(objs, path) still writes
        # wherever the script says.
        assert "freecad.execute_code" in DESTRUCTIVE
        assert annotations_for("freecad.execute_code")["destructiveHint"] is True

    def test_inspection_tools_are_read_only(self) -> None:
        for tool in (
            "freecad.describe_session",
            "freecad.describe_model",
            "freecad.list_joints",
            "freecad.measure",
        ):
            assert annotations_for(tool)["readOnlyHint"] is True


class TestShape:
    @pytest.mark.parametrize("tool", ["twin.get_node", "project.delete", "web.search"])
    def test_every_hint_is_present_and_boolean(self, tool: str) -> None:
        ann = annotations_for(tool)
        for key in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            assert isinstance(ann[key], bool), f"{tool}.{key} is not a bool"

    def test_title_is_included_only_when_given(self) -> None:
        assert "title" not in annotations_for("twin.get_node")
        assert annotations_for("twin.get_node", title="Get node")["title"] == "Get node"

    def test_only_tools_that_leave_the_twin_are_open_world(self) -> None:
        assert annotations_for("web.search")["openWorldHint"] is True
        assert annotations_for("twin.get_node")["openWorldHint"] is False
