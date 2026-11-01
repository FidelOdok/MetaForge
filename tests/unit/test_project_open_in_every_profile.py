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
