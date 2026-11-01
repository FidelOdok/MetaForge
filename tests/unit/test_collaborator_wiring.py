"""The factory must not be narrower than the thing it builds (FORGE-415).

`build_unified_server` relisted every collaborator by name -- a second list
beside `bootstrap_tool_registry`'s own -- and it was **38 short**. Each
missing one gates a tool registration, so those tools registered in the
gateway and never in the sidecar. The Engineering Intent & Requirements
Harness, `twin.record_document`, `twin.record_constraint_set` and
`twin.propose_change` were invisible to every external MCP client.

Nothing could see it. Every unit test constructs the adapter directly and
supplies the collaborator itself, so "the factory cannot pass it" is exactly
the gap those tests do not cover.

Fourth instance of one shape, and the reason this file is general rather
than another per-tool test:

- FORGE-406: an approval gate with four unit tests and no production caller.
- FORGE-413: a metrics collector the sidecar never passed, so four metrics
  had zero series and six alert rules could not fire.
- The `bootstrap_tool_registry` signature drift, hit twice (FORGE-405,
  FORGE-298).
- This.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from metaforge.mcp.server import build_unified_server
from tool_registry.bootstrap import bootstrap_tool_registry

_SIDECAR = Path("metaforge/mcp/__main__.py")


def _sidecar_call() -> ast.Call:
    tree = ast.parse(_SIDECAR.read_text())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_unified_server"
    ]
    assert len(calls) == 1, f"expected one build_unified_server call, found {len(calls)}"
    return calls[0]


class TestTheFactoryCanPassAnythingBootstrapAccepts:
    def test_it_forwards_arbitrary_collaborators(self) -> None:
        """The structural fix. A second hand-maintained list drifts; this
        one cannot, because there is no longer a second list."""
        params = inspect.signature(build_unified_server).parameters
        assert any(p.kind is p.VAR_KEYWORD for p in params.values()), (
            "build_unified_server names every collaborator again, so it will "
            "fall behind bootstrap_tool_registry exactly as it did before"
        )

    @pytest.mark.asyncio
    async def test_an_unknown_collaborator_is_refused_not_dropped(self) -> None:
        """Forwarding without validation would trade one silent failure for
        another: a typo'd name would be ignored and the tool would go missing
        one at a time, which is this bug in slow motion."""
        with pytest.raises(TypeError) as err:
            await build_unified_server(adapter_ids=[], no_such_collaborator=object())
        assert "no_such_collaborator" in str(err.value)

    @pytest.mark.asyncio
    async def test_a_real_collaborator_reaches_the_registry(self) -> None:
        """Forwarding that silently discarded the value would pass the test
        above and fix nothing."""
        seen: dict[str, object] = {}
        sentinel = object()

        import metaforge.mcp.server as server_module

        async def _spy(**kwargs: object) -> object:
            seen.update(kwargs)

            class _Registry:
                def list_adapter_servers(self) -> list[object]:
                    return []

            return _Registry()

        original = server_module.bootstrap_tool_registry
        server_module.bootstrap_tool_registry = _spy  # type: ignore[assignment]
        try:
            await build_unified_server(adapter_ids=[], document_recorder=sentinel)
        finally:
            server_module.bootstrap_tool_registry = original  # type: ignore[assignment]
        assert seen["document_recorder"] is sentinel


class TestTheSidecarWiresTheIntentHarness:
    """The specific tools FORGE-415 was filed about."""

    @pytest.mark.parametrize(
        "collaborator",
        [
            "engineering_entity_recorder",
            "engineering_entity_approver",
            "document_recorder",
            "constraint_recorder",
        ],
    )
    def test_each_is_passed(self, collaborator: str) -> None:
        passed = {kw.arg for kw in _sidecar_call().keywords if kw.arg}
        assert collaborator in passed

    def test_the_earlier_two_are_still_passed(self) -> None:
        """FORGE-406 and FORGE-413, pinned here so the general fix does not
        quietly lose what the specific ones added."""
        passed = {kw.arg for kw in _sidecar_call().keywords if kw.arg}
        assert {"approval_gate", "metrics"} <= passed

    def test_proposal_recorder_is_deliberately_absent(self) -> None:
        """It takes the gateway's `ApprovalWorkflow`, which the sidecar has no
        equivalent of -- its approvals go out through the remote gate. Wiring
        it would mean inventing a second approval path, which is the opposite
        of what FORGE-406 was about. Asserted so the omission reads as a
        decision rather than the next thing somebody forgot."""
        passed = {kw.arg for kw in _sidecar_call().keywords if kw.arg}
        assert "proposal_recorder" not in passed
        assert "ApprovalWorkflow" in _SIDECAR.read_text()


class TestTheGapIsMeasuredNotGuessed:
    def test_nothing_bootstrap_accepts_is_unreachable_by_construction(self) -> None:
        """The assertion that would have caught all four instances of this
        pattern. It is about *reachability*, not about any one tool: whatever
        `bootstrap_tool_registry` grows next is passable from the sidecar the
        day it is added.
        """
        accepted = set(inspect.signature(bootstrap_tool_registry).parameters)
        factory = inspect.signature(build_unified_server).parameters
        if any(p.kind is p.VAR_KEYWORD for p in factory.values()):
            return  # everything is reachable
        pytest.fail(f"build_unified_server cannot pass: {sorted(accepted - set(factory))}")
