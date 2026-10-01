"""The brief has to be reachable on the default profile (FORGE-418).

The plugin defaults to `core` (FORGE-410). `core` did not include
`project.open`, which `/metaforge:use` calls to return the project brief --
so on a default install the brief could not be reached at all, and the agent
rebuilt status from `project.get`'s work-product list instead: the long way
round to a worse answer.

`project.open` is the entry point. It resolves a name to an id, returns the
brief inline, and binds the session's project scope, so a profile without it
costs all three.
"""

from __future__ import annotations

import pytest

from mcp_core.profiles import PROFILES, tools_for_profile
from mcp_core.workflows import prompt_body


@pytest.mark.parametrize("profile", sorted(PROFILES))
class TestTheEntryPointIsInEveryProfile:
    def test_project_open_is_served(self, profile: str) -> None:
        assert "project.open" in tools_for_profile(profile)

    def test_so_is_the_rest_of_the_entry_path(self, profile: str) -> None:
        """Opening a project is no use if you cannot find one to open, or
        record anything once you are in it."""
        served = set(tools_for_profile(profile))
        for tool in ("project.list", "project.get", "session.start", "health.check"):
            assert tool in served, f"{profile} is missing {tool}"


class TestTheWorkflowsThatDependOnIt:
    def test_use_calls_project_open(self) -> None:
        """If the prompt names a tool the profile does not serve, the agent
        improvises -- which is exactly what happened."""
        body = prompt_body("use")
        assert "project.open" in body

    def test_every_tool_the_use_prompt_names_is_in_the_default_profile(self) -> None:
        """The generalisable form. A prompt is a promise about what is
        callable; `core` is what a default install gets, so a workflow naming
        a tool outside it is broken before the agent starts.
        """
        body = prompt_body("use")
        served = set(tools_for_profile("core"))
        named = {
            tool
            for tool in (
                "project.open",
                "project.list",
                "project.get",
                "session.start",
                "knowledge.search",
                "twin.get_node",
            )
            if f"`{tool}`" in body
        }
        assert named, "the use prompt names no tools; this test has stopped testing anything"
        assert named <= served, f"named but not served on core: {sorted(named - served)}"


class TestItIsNotJustTheBrief:
    def test_project_open_is_the_documented_way_to_scope_resources(self) -> None:
        """`resources/list` tells a caller with no project in scope to "call
        project.open". On a profile that does not serve it, that hint was
        advice the reader could not take."""
        from metaforge.mcp.server import UnifiedMcpServer

        server = UnifiedMcpServer(adapters=[], profile="core")
        assert "project.open" in tools_for_profile("core")
        del server


class TestTheIntentHarnessOnTheDefaultProfile:
    """FORGE-415 follow-up.

    Wiring `engineering_entity_recorder` into the sidecar made
    `twin.record_engineering_entity` *reachable* — it registers, and an
    unprofiled connection can call it. A default plugin install still could
    not see it, because `core` did not list it: the same gap one layer up,
    and the reason the live check after FORGE-415 showed `served: False` on
    `core` while the unprofiled catalogue showed `served: True`.
    """

    def test_core_serves_the_entry_point(self) -> None:
        assert "twin.record_engineering_entity" in tools_for_profile("core")

    def test_it_is_in_core_and_not_in_base(self) -> None:
        """`core` is what an engineer needs before picking a discipline,
        which is what this is. `_BASE` is a tax every profile pays, and a
        mechanical or electronics session is not where intents get recorded.
        """
        others = [p for p in PROFILES if p != "core"]
        assert others, "this test stops meaning anything with one profile"
        for profile in others:
            assert "twin.record_engineering_entity" not in tools_for_profile(profile), profile

    def test_the_approver_is_deliberately_not_served(self) -> None:
        """Pinned so the omission reads as a decision rather than the next
        thing somebody forgot. Approving a waiver or release_approval is a
        reviewer action, and the dashboard is where the approver is an
        authenticated principal rather than whoever the agent runs as —
        the same reasoning FORGE-393 applies to human-authority tools.
        """
        for profile in PROFILES:
            assert "twin.approve_engineering_entity" not in tools_for_profile(profile), profile

    def test_recording_is_useful_without_the_approver(self) -> None:
        """The pairing that makes record-only coherent: an entity recorded
        here is read back by the G3 feasibility gate automatically, so a
        budget or invariant does its job with nobody approving anything."""
        served = set(tools_for_profile("core"))
        assert {"twin.record_engineering_entity", "twin.record_claim"} <= served
