"""The doctor must not call a profile a fault (FORGE-420).

With the `core` profile the client sees 21 tools. `health.check` reported
`tool_count: 117` -- the registered catalogue -- and nothing reconciled the
two, so `/metaforge:doctor` concluded:

    "96 tools are not reaching the client ... something between the gateway
     and this client is cutting the list down"

and named cadquery, freecad, calculix and kicad as missing. That was
FORGE-410 working exactly as designed, diagnosed as a breakage. The prompt
made it worse by priming for it: it said a shorter list means an adapter is
down, and said nothing about profiles.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest

from mcp_core.profiles import PROFILES, tools_for_profile
from mcp_core.workflows import prompt_body
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer


class _Adapter(McpToolServer):
    """Registers a couple of real `core` tool ids plus one outside it."""

    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        # `health.check` is in `core` and is also registered by the server's
        # own auto-appended health adapter, which collides.
        core = [t for t in sorted(tools_for_profile("core")) if t != "health.check"][:2]
        for tool_id in [*core, "cadquery.create_parametric"]:
            self.register_tool(
                ToolManifest(
                    tool_id=tool_id,
                    adapter_id="twin",
                    name=tool_id,
                    description="stub",
                    capability="test",
                ),
                self._ok,
            )
        self._core_ids = core

    async def _ok(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {}


def _health(profile: str | None) -> dict[str, Any]:
    server = UnifiedMcpServer([_Adapter()], profile=profile)
    raw = asyncio.run(
        server.handle_request(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "health/check", "params": {}})
        )
    )
    return dict(json.loads(raw)["result"])


class TestTheReportExplainsTheShorterList:
    def test_an_active_profile_is_named(self) -> None:
        assert _health("core")["profile"]["active"] == "core"

    def test_it_says_how_many_tools_this_connection_is_served(self) -> None:
        report = _health("core")
        served = report["profile"]["served_tool_count"]
        assert served < report["tool_count"], "the test fixture must register a non-core tool"
        assert served == len(
            set(tools_for_profile("core")) & set(UnifiedMcpServer([_Adapter()])._tool_index)
        )

    def test_the_detail_says_nothing_is_missing(self) -> None:
        """The sentence that has to replace "96 tools are not reaching the
        client". A number alone would be read the same wrong way."""
        detail = _health("core")["profile"]["detail"]
        assert "not missing" in detail
        assert "nothing is down" in detail

    def test_the_other_profiles_are_named_so_the_advice_is_actionable(self) -> None:
        assert set(_health("core")["profile"]["available"]) == set(PROFILES)

    def test_tool_count_still_means_the_registered_catalogue(self) -> None:
        """Other callers read it, and it is a real fact. The fix is to
        explain it, not to redefine it underneath them."""
        report = _health("core")
        assert report["tool_count"] == len(UnifiedMcpServer([_Adapter()])._tool_index)


class TestNoProfileIsStillAnAnswer:
    def test_it_reports_active_none_rather_than_omitting_the_key(self) -> None:
        """A doctor that cannot tell "no profile" from "this build does not
        say" has to guess, and guessing is what produced the original
        report."""
        profile = _health(None)["profile"]
        assert profile["active"] is None
        assert "every registered tool is served" in profile["detail"]

    def test_served_count_equals_the_catalogue(self) -> None:
        report = _health(None)
        assert report["profile"]["served_tool_count"] == report["tool_count"]


class TestAnUnknownProfileIsAFaultNotASilentPass:
    def test_a_deployment_profile_is_rejected_at_construction(self) -> None:
        """The earliest possible failure, and the right one: a server told to
        serve a profile that does not exist should not start."""
        from mcp_core.profiles import UnknownProfileError

        with pytest.raises(UnknownProfileError):
            UnifiedMcpServer([_Adapter()], profile="nonexistent")

    def test_a_per_connection_profile_is_reported_rather_than_raising(self) -> None:
        """This one cannot be caught at construction -- it arrives as
        `?profile=` on a live connection. A health check that raises tells
        nobody anything, so it reports the fault instead."""
        from mcp_core.context import McpCallContext, with_context

        server = UnifiedMcpServer([_Adapter()])
        ctx = McpCallContext(
            actor_id="user:test",
            correlation_id=UUID("00000000-0000-0000-0000-000000000001"),
            profile="nonexistent",
        )

        async def _check() -> dict[str, Any]:
            with with_context(ctx):
                raw = await server.handle_request(
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "health/check", "params": {}})
                )
            return dict(json.loads(raw))

        response = asyncio.run(_check())
        assert "error" not in response, response
        profile = response["result"]["profile"]
        assert profile["active"] is None
        assert "nonexistent" in profile["error"]


class TestThePromptNoLongerPrimesTheWrongConclusion:
    def test_it_tells_the_agent_to_check_the_profile_first(self) -> None:
        body = prompt_body("doctor")
        assert "profile.active" in body
        assert "before concluding anything is missing" in body

    def test_it_records_what_went_wrong(self) -> None:
        """The prompt said a shorter list means an adapter is down, and said
        nothing about profiles. Naming the actual misdiagnosis is what stops
        it being reintroduced as a simplification."""
        body = prompt_body("doctor")
        assert "96 tools" in body
        assert "working exactly as designed" in body

    def test_the_adapter_down_guidance_survives(self) -> None:
        """Both causes make the list shorter. Replacing one explanation with
        the other would just move the misdiagnosis."""
        body = prompt_body("doctor")
        assert "container is down" in body
        assert "`adapters` array" in body


@pytest.mark.asyncio
class TestItAgreesWithWhatToolsListServes:
    async def test_the_served_count_matches_the_tool_list(self) -> None:
        """The whole bug was two numbers that disagreed with nothing
        reconciling them. They must not drift apart again."""
        server = UnifiedMcpServer([_Adapter()], profile="core")
        raw = await server.handle_request(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        )
        listed = len(json.loads(raw)["result"]["tools"])
        health_raw = await server.handle_request(
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "health/check", "params": {}})
        )
        served = json.loads(health_raw)["result"]["profile"]["served_tool_count"]
        assert served == listed
