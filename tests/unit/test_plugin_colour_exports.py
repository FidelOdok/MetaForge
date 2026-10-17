"""Plugin geometry always carries part colours (FORGE-637).

The viewer shows only the colours written in a STEP, and the table a plugin
designed arrived grey. Now an external MCP client's ``twin.commit_geometry``
is refused while any part is uncoloured, and the commit (and
``freecad.export_model``) can write colours given as a library appearance's
``#RRGGBB``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest

from mcp_core.context import McpCallContext, with_context
from mcp_core.guardrails import REQUIRE_PART_COLOURS_ARG, Caller, strip_reserved_arguments
from metaforge.mcp.server import UnifiedMcpServer
from tool_registry.mcp_server.handlers import ToolManifest
from tool_registry.mcp_server.server import McpToolServer
from tool_registry.tools.freecad.materials_appearance import (
    apply_step_colours,
    read_step_colours,
    resolve_rgb,
    step_part_names,
    uncoloured_products,
)
from tool_registry.tools.twin.adapter import TwinServer

#: Two named parts, each with one solid, no colour (the shape of the table's
#: plugin-made STEP).
STEP = b"""ISO-10303-21;
HEADER;
ENDSEC;
DATA;
#1 = APPLICATION_CONTEXT('x');
#2 = PRODUCT('tabletop','tabletop','',(#3));
#3 = PRODUCT_CONTEXT('',#1,'mechanical');
#4 = PRODUCT_DEFINITION_FORMATION('','',#2);
#5 = PRODUCT_DEFINITION('design','',#4,#6);
#6 = PRODUCT_DEFINITION_CONTEXT('part definition',#1,'design');
#7 = PRODUCT_DEFINITION_SHAPE('','',#5);
#8 = SHAPE_DEFINITION_REPRESENTATION(#7,#9);
#9 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#10,#11),#30);
#10 = AXIS2_PLACEMENT_3D('',#40,#41,#42);
#11 = MANIFOLD_SOLID_BREP('',#12);
#12 = CLOSED_SHELL('',());
#20 = PRODUCT('leg_front_left','leg_front_left','',(#3));
#21 = PRODUCT_DEFINITION_FORMATION('','',#20);
#22 = PRODUCT_DEFINITION('design','',#21,#6);
#23 = PRODUCT_DEFINITION_SHAPE('','',#22);
#24 = SHAPE_DEFINITION_REPRESENTATION(#23,#25);
#25 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#10,#26),#30);
#26 = MANIFOLD_SOLID_BREP('',#12);
#30 = GEOMETRIC_REPRESENTATION_CONTEXT(3);
ENDSEC;
END-ISO-10303-21;
"""


# ── the STEP helpers ──────────────────────────────────────────────────────


def test_an_appearance_hex_is_a_colour() -> None:
    assert resolve_rgb("#0A0A0D") == pytest.approx((10 / 255, 10 / 255, 13 / 255))
    assert resolve_rgb("d8bc8e") == pytest.approx((216 / 255, 188 / 255, 142 / 255))
    with pytest.raises(ValueError, match="#RRGGBB"):
        resolve_rgb("black")


def test_uncoloured_parts_are_named_until_each_is_coloured() -> None:
    assert step_part_names(STEP) == ["leg_front_left", "tabletop"]
    assert uncoloured_products(STEP) == ["leg_front_left", "tabletop"]
    half = apply_step_colours(STEP, None, {"tabletop": resolve_rgb("#D8BC8E")})
    assert uncoloured_products(half) == ["leg_front_left"]
    whole = apply_step_colours(half, None, {"leg_front_left": resolve_rgb("#0A0A0D")})
    assert uncoloured_products(whole) == []
    assert uncoloured_products(b"not a step") == ["(whole model)"]


# ── twin.commit_geometry ──────────────────────────────────────────────────


class _Twin:
    pass


def _server() -> tuple[TwinServer, list[dict[str, Any]]]:
    stored: list[dict[str, Any]] = []

    async def recorder(**kwargs: Any) -> dict[str, Any]:
        stored.append(kwargs)
        return {"node_id": "n1"}

    return TwinServer(twin=_Twin(), geometry_recorder=recorder), stored


def _args(**extra: Any) -> dict[str, Any]:
    return {"step_base64": base64.b64encode(STEP).decode(), "name": "table_assembly", **extra}


async def test_a_plugin_commit_of_an_uncoloured_model_is_refused() -> None:
    srv, stored = _server()
    with pytest.raises(ValueError, match="these do not: leg_front_left, tabletop"):
        await srv.commit_geometry(_args(**{REQUIRE_PART_COLOURS_ARG: True}))
    assert stored == []


async def test_colours_given_at_commit_are_written_into_the_stored_step() -> None:
    srv, stored = _server()
    await srv.commit_geometry(
        _args(
            part_colours={"tabletop": "#D8BC8E", "leg_front_left": "#0A0A0D"},
            **{REQUIRE_PART_COLOURS_ARG: True},
        )
    )
    written = base64.b64decode(stored[0]["step_base64"])
    assert uncoloured_products(written) == []
    colours = read_step_colours(written)
    # The STEP carries six decimals.
    assert colours["leg_front_left"] == pytest.approx(resolve_rgb("#0A0A0D"), abs=1e-5)
    assert colours["tabletop"] == pytest.approx(resolve_rgb("#D8BC8E"), abs=1e-5)


async def test_one_colour_for_the_whole_model_also_satisfies_the_rule() -> None:
    srv, stored = _server()
    await srv.commit_geometry(_args(color="#808080", **{REQUIRE_PART_COLOURS_ARG: True}))
    assert uncoloured_products(base64.b64decode(stored[0]["step_base64"])) == []


async def test_a_part_name_the_step_lacks_is_refused_not_skipped() -> None:
    srv, stored = _server()
    with pytest.raises(ValueError, match="names leg_back_left, which this STEP does not have"):
        await srv.commit_geometry(_args(part_colours={"leg_back_left": "#0A0A0D"}))
    with pytest.raises(ValueError, match="#RRGGBB"):
        await srv.commit_geometry(_args(part_colours={"tabletop": "birch"}))
    assert stored == []


async def test_without_the_flag_an_uncoloured_commit_is_unchanged() -> None:
    """The design-flow worker and in-app chat are not held to it (yet)."""
    srv, stored = _server()
    await srv.commit_geometry(_args())
    assert base64.b64decode(stored[0]["step_base64"]) == STEP


# ── the server sets the flag, and only the server ─────────────────────────


class _EchoTwin(McpToolServer):
    def __init__(self) -> None:
        super().__init__(adapter_id="twin", version="0")
        self.seen: list[dict[str, Any]] = []
        self.register_tool(
            ToolManifest(
                tool_id="twin.commit_geometry",
                adapter_id="twin",
                name="twin.commit_geometry",
                description="stub",
                capability="test",
            ),
            self._commit,
        )

    async def _commit(self, args: dict[str, Any]) -> dict[str, Any]:
        self.seen.append(dict(args))
        return {"node_id": "n1"}


def _call(server: UnifiedMcpServer, arguments: dict[str, Any]) -> None:
    asyncio.run(
        server._tool_call(
            {"tool_id": "twin.commit_geometry", "arguments": arguments, "_call_id": "c1"}
        )
    )


def _local_server(twin: _EchoTwin) -> UnifiedMcpServer:
    """A plugin on the engineer's own machine (stdio), whose writes run unheld."""
    server = UnifiedMcpServer([twin])
    server.declare_caller(Caller.LOCAL)
    return server


def test_an_external_client_is_always_held_to_part_colours() -> None:
    twin = _EchoTwin()
    server = _local_server(twin)
    _call(server, {"name": "t", REQUIRE_PART_COLOURS_ARG: False})
    assert twin.seen[-1][REQUIRE_PART_COLOURS_ARG] is True


def test_the_design_flow_service_caller_is_not_flagged() -> None:
    twin = _EchoTwin()
    server = _local_server(twin)
    ctx = McpCallContext(service_verified=True)
    with with_context(ctx):
        _call(server, {"name": "t", REQUIRE_PART_COLOURS_ARG: True})
    assert REQUIRE_PART_COLOURS_ARG not in twin.seen[-1]


def test_a_client_cannot_set_or_clear_the_flag() -> None:
    assert strip_reserved_arguments({"a": 1, REQUIRE_PART_COLOURS_ARG: True}) == {"a": 1}
    assert json.dumps(strip_reserved_arguments({REQUIRE_PART_COLOURS_ARG: False})) == "{}"
