"""Tool profiles, and the adapters that used to disappear (FORGE-339).

Two properties, both about the same failure: a client being handed a
shorter tool list than it should have, with nothing to say so. A model
reads a missing tool as a capability the system does not have, which is
indistinguishable from it genuinely not having one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from mcp_core.profiles import (
    DEFAULT_PROFILE,
    MAX_TOOLS,
    MIN_TOOLS,
    PROFILES,
    UnknownProfileError,
    profile_names,
    tools_for_profile,
)
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

REPO = Path(__file__).resolve().parents[2]


def declared_tool_ids() -> set[str]:
    ids: set[str] = set()
    for root in ("tool_registry", "metaforge"):
        for path in (REPO / root).rglob("*.py"):
            ids |= set(
                re.findall(
                    r'tool_id="([a-z0-9_.]+)"', path.read_text(encoding="utf-8", errors="replace")
                )
            )
    return ids


def _manifest(tool_id: str, adapter_id: str) -> ToolManifest:
    return ToolManifest(
        tool_id=tool_id,
        adapter_id=adapter_id,
        name=tool_id,
        description=f"stub {tool_id}",
        capability="test",
    )


class _Twin(McpToolServer):
    TOOLS = (
        "twin.get_node",
        "twin.find_by_property",
        "twin.commit_geometry",
        "twin.approve_engineering_change",
    )

    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        for t in self.TOOLS:
            self.register_tool(_manifest(t, "twin"), self._ok)

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {}


class _DownAdapter(McpToolServer):
    """A container that is not running — the normal dev condition, per the
    -32001 the CAD/FEA adapters answer with."""

    def __init__(self) -> None:
        super().__init__(adapter_id="calculix", version="0.1.0")
        self.register_tool(_manifest("calculix.run_fea", "calculix"), self._ok)

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def handle_request(self, raw: str) -> str:
        raise RuntimeError("adapter container is down (-32001)")


class _GarbageAdapter(McpToolServer):
    """Answers, but not with JSON. This was the fully silent path."""

    def __init__(self) -> None:
        super().__init__(adapter_id="kicad", version="0.1.0")
        self.register_tool(_manifest("kicad.run_drc", "kicad"), self._ok)

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def handle_request(self, raw: str) -> str:
        return "<html>502 Bad Gateway</html>"


async def _tools_list(server: UnifiedMcpServer) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    )
    return json.loads(raw)["result"]


class TestProfileDefinitions:
    def test_every_profile_fits_the_bound_the_story_asks_for(self) -> None:
        # Checked here rather than trimmed at runtime: a profile that
        # outgrows its ceiling is a decision about what to drop.
        for name, tools in PROFILES.items():
            assert MIN_TOOLS <= len(tools) <= MAX_TOOLS, (
                f"profile {name!r} has {len(tools)} tools, outside {MIN_TOOLS}-{MAX_TOOLS}"
            )

    def test_the_named_profiles_all_exist(self) -> None:
        assert profile_names() == ["core", "electronics", "mechanical", "robotics", "simulation"]
        assert DEFAULT_PROFILE in PROFILES

    def test_no_profile_names_a_tool_that_does_not_exist(self) -> None:
        # A typo here silently shrinks a profile rather than erroring.
        declared = declared_tool_ids()
        assert len(declared) > 80, "tool-id scan found almost nothing — has it broken?"
        for name, tools in PROFILES.items():
            missing = sorted(tools - declared)
            assert missing == [], f"profile {name!r} names unregistered tools: {missing}"

    def test_an_unknown_profile_names_the_real_ones(self) -> None:
        with pytest.raises(UnknownProfileError) as exc:
            tools_for_profile("mechnical")
        assert "mechanical" in str(exc.value)
        assert "Available:" in str(exc.value)


class TestStartup:
    def test_a_typo_in_the_flag_stops_the_server(self) -> None:
        # The alternative is serving a profile nobody asked for, or
        # everything — both look like working configuration.
        with pytest.raises(UnknownProfileError):
            UnifiedMcpServer(adapters=[_Twin()], profile="mechnical")

    def test_no_profile_serves_everything(self) -> None:
        srv = UnifiedMcpServer(adapters=[_Twin()], profile=None)
        assert srv._profile is None


@pytest.mark.asyncio
class TestServing:
    async def test_a_profile_filters_the_list(self) -> None:
        result = await _tools_list(UnifiedMcpServer(adapters=[_Twin()], profile="mechanical"))
        names = {t["name"] for t in result["tools"]}
        assert "twin.commit_geometry" in names
        # Not in the mechanical profile; present on the adapter.
        assert "twin.approve_engineering_change" not in names

    async def test_the_profile_reports_what_it_could_not_serve(self) -> None:
        # A profile naming tools no loaded adapter registers is a
        # configuration mistake, not a smaller profile.
        result = await _tools_list(UnifiedMcpServer(adapters=[_Twin()], profile="mechanical"))
        missing = result["_meta"]["profile"]["missing"]
        assert "cadquery.create_parametric" in missing
        assert result["_meta"]["profile"]["name"] == "mechanical"

    async def test_a_down_adapter_is_reported_rather_than_dropped(self) -> None:
        result = await _tools_list(UnifiedMcpServer(adapters=[_Twin(), _DownAdapter()]))
        names = {t["name"] for t in result["tools"]}
        # It genuinely cannot be served...
        assert "calculix.run_fea" not in names
        # ...but the client is told, which is the whole difference.
        reported = {a["adapter_id"] for a in result["_meta"]["unavailableAdapters"]}
        assert "calculix" in reported

    async def test_a_malformed_response_is_reported_too(self) -> None:
        # This path had no log at all: the adapter vanished completely.
        result = await _tools_list(UnifiedMcpServer(adapters=[_Twin(), _GarbageAdapter()]))
        reported = {a["adapter_id"] for a in result["_meta"]["unavailableAdapters"]}
        assert "kicad" in reported

    async def test_a_healthy_server_says_nothing_extra(self) -> None:
        # _meta only appears when there is something to report, so its
        # presence is itself the signal.
        result = await _tools_list(UnifiedMcpServer(adapters=[_Twin()]))
        assert "_meta" not in result
        assert len(result["tools"]) == len(_Twin.TOOLS)
