"""A reply should be able to point at the view that shows the thing (FORGE-371).

An agent saying "I committed the bracket" is asking the reader to go and
find it: thirteen dashboard pages, some project, some node. The server is
the only party that knows both the id and the route.

Most of these tests are about the two ways a link can lie — a base URL the
server guessed, and a route that does not actually show what the label
says — because a wrong link is worse than none. The agent states it with
the same confidence either way, and the reader only finds out by clicking.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from mcp_core.deeplinks import DeepLinkBuilder, links_for
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer

BASE = "https://forge.example.com"


# ---------------------------------------------------------------------------
# Never invent the base
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base", [None, "", "   "])
def test_no_base_means_no_links(base: str | None) -> None:
    builder = DeepLinkBuilder(base)
    assert builder.configured is False
    assert builder.for_tool("twin.get_node", {"data": {"node_id": "n1"}}) == []


def test_a_trailing_slash_does_not_double_up() -> None:
    builder = DeepLinkBuilder(f"{BASE}/")
    assert builder.build("twin") == f"{BASE}/twin"


def test_health_says_when_links_are_off() -> None:
    """Otherwise a doctor report leaves the reader to conclude the tools
    simply never produce them."""
    server = UnifiedMcpServer([_Adapter()])
    report = asyncio.run(server._health_check())
    assert report["dashboard_links"].startswith("disabled")

    configured = UnifiedMcpServer([_Adapter()], dashboard_url=BASE)
    assert asyncio.run(configured._health_check())["dashboard_links"] == "enabled"


# ---------------------------------------------------------------------------
# Never invent the route
# ---------------------------------------------------------------------------


def test_an_unmapped_tool_gets_no_link() -> None:
    """A plausible-looking page is still a guess."""
    builder = DeepLinkBuilder(BASE)
    assert builder.for_tool("kicad.run_drc", {"data": {"id": "x"}}) == []


def test_a_missing_id_gets_no_link_rather_than_a_bare_page() -> None:
    """Linking /twin with no ?node= points at whatever happens to be
    selected, which is a different object from the one just written."""
    builder = DeepLinkBuilder(BASE)
    assert builder.for_tool("twin.commit_geometry", {"data": {}}) == []


def test_a_part_links_to_the_model_tab_with_the_node_selected() -> None:
    links = DeepLinkBuilder(BASE).for_tool("twin.commit_geometry", {"data": {"node_id": "n-7"}})
    assert links[0].url == f"{BASE}/twin?tab=model&node=n-7"


def test_architecture_links_to_the_structure_tab() -> None:
    links = DeepLinkBuilder(BASE).for_tool(
        "twin.commit_system_architecture", {"data": {"node_id": "n-9"}}
    )
    assert "tab=structure" in links[0].url
    assert "node=n-9" in links[0].url


def test_a_gate_attempt_links_to_the_gate_review() -> None:
    links = DeepLinkBuilder(BASE).for_tool("twin.attempt_promotion", {"data": {"ok": True}})
    assert links[0].url == f"{BASE}/requirements"


def test_a_project_links_by_path_not_query() -> None:
    """/projects/:id is a path parameter; /twin reads ?node=. Getting that
    backwards produces a URL that resolves to the list page."""
    links = DeepLinkBuilder(BASE).for_tool("project.open", {"project": {"id": "p-1"}})
    assert links[0].url == f"{BASE}/projects/p-1"


def test_ids_are_escaped() -> None:
    links = DeepLinkBuilder(BASE).for_tool("twin.get_node", {"data": {"node_id": "a b&c"}})
    assert " " not in links[0].url
    assert "&c" not in links[0].url.split("node=")[1]


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"node_id": "n-1"}},
        {"node_id": "n-1"},
        {"data": {"project": {"node_id": "n-1"}}},
    ],
)
def test_the_id_is_found_wherever_the_adapter_put_it(payload: dict[str, Any]) -> None:
    """Results are enveloped {tool_id, status, data}, but not uniformly."""
    assert DeepLinkBuilder(BASE).for_tool("twin.get_node", payload)


# ---------------------------------------------------------------------------
# It reaches the client
# ---------------------------------------------------------------------------


class _Adapter(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0.1.0")
        self.register_tool(
            ToolManifest(
                tool_id="twin.get_node",
                adapter_id="twin",
                name="twin.get_node",
                description="stub",
                capability="test",
            ),
            self._ok,
        )

    async def _ok(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"node_id": "n-1"}


def _call(server: UnifiedMcpServer) -> dict[str, Any]:
    raw = asyncio.run(
        server.handle_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tools/call",
                    "params": {"name": "twin.get_node", "arguments": {"node_id": "n-1"}},
                }
            )
        )
    )
    return json.loads(raw)["result"]


def test_links_ride_in_meta_beside_the_call_id() -> None:
    """Not inside the text payload: that is the tool's own output, and
    burying a protocol-level field in it makes every adapter's schema
    wrong -- the same reasoning callId already follows."""
    result = _call(UnifiedMcpServer([_Adapter()], dashboard_url=BASE))
    assert result["_meta"]["links"][0]["url"].startswith(f"{BASE}/twin?node=")
    assert result["_meta"]["links"][0]["label"]
    assert "callId" in result["_meta"]


def test_no_links_key_when_there_is_nothing_to_link() -> None:
    """Absent rather than an empty list: an empty list reads as "we looked
    and there is no view for this", which is a different claim."""
    result = _call(UnifiedMcpServer([_Adapter()]))
    assert "links" not in result["_meta"]
    assert "callId" in result["_meta"]


def test_links_for_tolerates_no_builder() -> None:
    assert links_for(None, "twin.get_node", {"data": {"node_id": "n"}}) == []


# ---------------------------------------------------------------------------
# The routes have to be real
# ---------------------------------------------------------------------------


def test_every_mapped_route_exists_in_the_dashboard() -> None:
    """The ratchet. A link is a claim that a page will show the thing, and
    the failure mode of renaming a route is a link that 404s months later
    in someone else's transcript — nothing here would otherwise notice."""
    import re
    from pathlib import Path

    from mcp_core.deeplinks import _ROUTES

    app = Path(__file__).resolve().parents[2] / "dashboard" / "src" / "App.tsx"
    declared = set(re.findall(r"\['([^']+)',\s*\w+Page\]", app.read_text()))
    assert declared, "could not read the dashboard route table"

    for tool_id, (route, id_field, query_key, _label) in _ROUTES.items():
        base = route.split("?")[0]
        expected = f"{base}/:id" if (id_field and not query_key) else base
        assert expected in declared, (
            f"{tool_id} links to /{expected}, which the dashboard has no route for"
        )


def test_the_twin_tab_param_is_honoured_by_the_page() -> None:
    """`?tab=` was local state until FORGE-371, so a link naming a tab
    landed on the page and silently showed the default — which reads as
    the link being wrong rather than unsupported."""
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[2] / "dashboard" / "src" / "pages" / "TwinViewerPage.tsx"
    ).read_text()
    # Applied by an effect, not in the useState initialiser: useSearchParams
    # is declared below the tab state, and reading it there is a
    # temporal-dead-zone crash at render that tsc does not catch.
    assert "parseTwinTab(wanted)" in page
    assert "searchParams.get('tab')" in page
    assert "searchParams.get('node')" in page
