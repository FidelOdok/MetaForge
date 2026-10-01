"""A profile is a per-connection choice, not a deployment-wide one (FORGE-410).

FORGE-339 built tool profiles of 25–30 tools and wired them to a `--profile`
start-up flag. The deployment plugins actually connect to runs with no
`--profile`, so it served everything — 108 tools at the last count — and C1's
cap never reached a plugin user at all. Claude Code copes by loading tools
lazily; a harness with a hard cap truncates, which is the exact failure
profiles exist to prevent.

A deployment-wide flag could not have fixed it either: one sidecar serves both
the plugin and the dashboard chat, and shrinking it for one shrinks it for the
other. So the choice moved to the connection, carried on the MCP URL because a
plugin manifest has no way to pass a flag.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core.context import McpCallContext, with_context
from mcp_core.guardrails import Caller
from mcp_core.profiles import PROFILES, tools_for_profile


def _server(**kwargs: Any) -> Any:
    from metaforge.mcp.server import UnifiedMcpServer

    return UnifiedMcpServer(adapters=[], caller=Caller.UNTRUSTED, **kwargs)


async def _tools(server: Any, profile: str | None = None) -> dict[str, Any]:
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    if profile is None:
        return json.loads(await server.handle_request(request))
    with with_context(McpCallContext(profile=profile)):
        return json.loads(await server.handle_request(request))


@pytest.mark.asyncio
class TestTheConnectionChooses:
    async def test_a_requested_profile_filters_the_list(self) -> None:
        response = await _tools(_server(), profile="core")
        served = {t["name"] for t in response["result"]["tools"]}
        assert served <= set(tools_for_profile("core"))
        assert response["result"]["_meta"]["profile"]["name"] == "core"

    async def test_no_request_means_no_filtering(self) -> None:
        """The dashboard and the chat harness share this server. A default that
        capped them would trade one truncation for another."""
        response = await _tools(_server())
        assert "profile" not in response["result"].get("_meta", {})

    async def test_the_connection_outranks_the_deployment_flag(self) -> None:
        """Why this is per-connection: a sidecar started `--profile mechanical`
        must still serve `core` to a plugin that asks for it, because the two
        consumers are different connections to the same process."""
        response = await _tools(_server(profile="mechanical"), profile="core")
        assert response["result"]["_meta"]["profile"]["name"] == "core"

    async def test_the_deployment_flag_still_applies_when_nothing_is_asked(self) -> None:
        response = await _tools(_server(profile="mechanical"))
        assert response["result"]["_meta"]["profile"]["name"] == "mechanical"


@pytest.mark.asyncio
class TestAnUnknownProfileFailsLoudly:
    async def test_it_is_an_invalid_params_error_not_a_dropped_connection(self) -> None:
        response = await _tools(_server(), profile="nonsense")
        assert "error" in response
        assert response["error"]["code"] == -32602
        assert response["error"]["data"]["code"] == "unknown_profile"

    async def test_it_names_the_profiles_that_exist(self) -> None:
        response = await _tools(_server(), profile="nonsense")
        assert sorted(PROFILES) == response["error"]["data"]["available"]

    async def test_it_does_not_serve_everything_instead(self) -> None:
        """The failure that would matter. A client that asked for a 30-tool set
        and silently received 108 has been handed the exact problem profiles
        exist to prevent."""
        response = await _tools(_server(), profile="nonsense")
        assert "result" not in response


class TestEveryProfileCarriesTheDiagnostic:
    @pytest.mark.parametrize("name", sorted(PROFILES))
    def test_health_check_is_in_it(self, name: str) -> None:
        """A capped connection on a truncating harness is the one most likely to
        need `/metaforge:doctor` (FORGE-409). A profile that omits the tool
        takes it away from exactly that connection."""
        assert "health.check" in tools_for_profile(name)

    @pytest.mark.parametrize("name", sorted(PROFILES))
    def test_and_the_profile_stays_inside_C1s_cap(self, name: str) -> None:
        # "profiles of 20 to 40 tools" — adding to the shared base is cheap
        # per-profile and expensive across all five, so this is the guard.
        assert 20 <= len(tools_for_profile(name)) <= 40


class TestTheShippedManifestsAskForOne:
    """The bug was not that profiles did not work. It was that nothing asked
    for one, so these assert the packaging, which is where it was missing."""

    def _manifest(self, package: str) -> dict[str, Any]:
        from pathlib import Path

        path = Path("integrations") / package / ".claude-plugin" / "plugin.json"
        return json.loads(path.read_text())

    def test_the_http_plugin_puts_the_profile_on_the_url(self) -> None:
        server = self._manifest("claude-code")["mcpServers"]["metaforge"]
        assert "profile=" in server["url"]

    def test_it_is_user_switchable(self) -> None:
        config = self._manifest("claude-code")["userConfig"]
        assert config["tool_profile"]["default"] == "core"

    def test_the_stdio_plugin_passes_the_flag_instead(self) -> None:
        """stdio has no URL to hang a query on."""
        server = self._manifest("claude-code-local")["mcpServers"]["metaforge"]
        assert server["args"][-2:] == ["--profile", "core"]

    def test_the_codex_package_asks_too(self) -> None:
        from pathlib import Path

        config = json.loads(Path("integrations/codex/.mcp.json").read_text())
        assert "profile=core" in config["mcpServers"]["metaforge"]["url"]

    def test_every_default_names_a_real_profile(self) -> None:
        # A manifest asking for a profile that does not exist would now be a
        # hard error on every tools/list, which is worse than the bug.
        assert "core" in PROFILES
