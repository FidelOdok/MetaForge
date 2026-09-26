"""Provider tool-array cap (the 130-tool 400 that killed every chat turn).

Live failure this reproduces, from fidel-dev's gateway:

    Invalid 'tools': array too long. Expected an array with maximum length
    128, but got an array with length 130 instead.
    code=array_above_max_length -> all_providers_failed -> harness_chat_failed

130 = 12 native + 118 MCP tools. It is a request-SHAPE limit, so the existing
token budgeting could not see it: every turn failed before the model read a
token, including a bare "hello".
"""

from __future__ import annotations

import pytest

from orchestrator.harness.native_tools import _select_tools, _tool_schemas
from orchestrator.harness.providers.registry import max_tools_for
from orchestrator.harness.runtime import HarnessRuntime
from orchestrator.harness.tools import ToolRegistry

SCHEMA = {"type": "object", "properties": {"x": {"type": "number"}}}


async def _echo(args: dict[str, object]) -> dict[str, object]:
    return {"echo": args}


def _registry(*, natives: int, mcp_per_server: int, servers: int) -> ToolRegistry:
    reg = ToolRegistry()
    for i in range(natives):
        reg.register_native(
            f"native.tool_{i:03d}", description="d", input_schema=SCHEMA, handler=_echo
        )
    for s in range(servers):
        for i in range(mcp_per_server):
            reg.register_mcp(
                f"server{s:02d}",
                f"tool_{i:03d}",
                description="d",
                input_schema=SCHEMA,
                handler=_echo,
            )
    return reg


# ---------------------------------------------------------------------------
# The provider capability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["openai", "openrouter", "deepseek"])
def test_openai_family_providers_are_capped_at_128(provider: str) -> None:
    assert max_tools_for(provider) == 128


def test_anthropic_family_is_uncapped() -> None:
    assert max_tools_for("anthropic") is None


@pytest.mark.parametrize("provider", [None, "", "not-a-real-provider"])
def test_unknown_or_absent_provider_is_uncapped(provider: str | None) -> None:
    """Never invent a cap we can't justify — unknown means unbounded."""
    assert max_tools_for(provider) is None


# ---------------------------------------------------------------------------
# The regression: 130 tools must not go out as 130
# ---------------------------------------------------------------------------


def test_130_tools_are_capped_to_128() -> None:
    reg = _registry(natives=12, mcp_per_server=10, servers=12)  # 12 + 120 = 132
    runtime = HarnessRuntime.build(None, tools=reg)
    schemas = _tool_schemas(runtime, max_tools=128)
    assert len(schemas) == 128


def test_uncapped_call_sends_everything() -> None:
    """max_tools=None keeps the historical behaviour exactly."""
    reg = _registry(natives=12, mcp_per_server=10, servers=12)
    runtime = HarnessRuntime.build(None, tools=reg)
    assert len(_tool_schemas(runtime, max_tools=None)) == 132


def test_under_the_cap_is_untouched() -> None:
    reg = _registry(natives=2, mcp_per_server=3, servers=3)  # 11
    runtime = HarnessRuntime.build(None, tools=reg)
    assert len(_tool_schemas(runtime, max_tools=128)) == 11


def test_schemas_remain_well_formed_after_capping() -> None:
    reg = _registry(natives=12, mcp_per_server=10, servers=12)
    runtime = HarnessRuntime.build(None, tools=reg)
    for schema in _tool_schemas(runtime, max_tools=50):
        assert schema["type"] == "function"
        assert schema["function"]["name"]
        assert schema["function"]["parameters"]["type"] == "object"


# ---------------------------------------------------------------------------
# Selection policy — the part that must not be a naive slice
# ---------------------------------------------------------------------------


def test_native_tools_are_never_dropped() -> None:
    """Natives are curated and session-critical (chat.set_project_scope)."""
    reg = _registry(natives=12, mcp_per_server=20, servers=10)  # 12 + 200
    kept, dropped = _select_tools(reg.all_tools(), 128)
    assert len(kept) == 128
    assert all(not n.startswith("native.") for n in dropped)
    assert sum(1 for s in kept if s.origin == "native") == 12


def test_no_adapter_is_wiped_out_entirely() -> None:
    """A naive tail-slice deletes whole adapters alphabetically — twin.*,
    web.* go first, which is exactly backwards. Every origin must survive."""
    reg = _registry(natives=5, mcp_per_server=30, servers=8)  # 5 + 240
    kept, _ = _select_tools(reg.all_tools(), 128)
    origins = {s.origin for s in kept if s.origin != "native"}
    assert origins == {f"server{i:02d}" for i in range(8)}


def test_late_alphabet_adapter_survives_the_cap() -> None:
    """Direct regression on the ordering trap: `web` sorts last, and a tail
    slice would drop it first."""
    reg = ToolRegistry()
    for i in range(200):
        reg.register_mcp("aaa", f"t{i:03d}", description="d", input_schema=SCHEMA, handler=_echo)
    for name in ("search", "fetch"):
        reg.register_mcp("web", name, description="d", input_schema=SCHEMA, handler=_echo)

    kept, _ = _select_tools(reg.all_tools(), 128)
    assert {s.name for s in kept} & {"mcp_web_search", "mcp_web_fetch"}


def test_kept_and_dropped_partition_the_input() -> None:
    reg = _registry(natives=6, mcp_per_server=15, servers=9)
    specs = reg.all_tools()
    kept, dropped = _select_tools(specs, 100)
    assert len(kept) == 100
    assert len(kept) + len(dropped) == len(specs)
    assert set(dropped).isdisjoint({s.name for s in kept})


def test_natives_alone_exceeding_the_cap_still_truncate() -> None:
    """Pathological, but an over-long array must never be sent regardless."""
    reg = _registry(natives=140, mcp_per_server=0, servers=0)
    kept, dropped = _select_tools(reg.all_tools(), 128)
    assert len(kept) == 128
    assert len(dropped) == 12


# ---------------------------------------------------------------------------
# FORGE-94: a large adapter's own session-lifecycle tool (and anything
# search_tools has pinned) must survive the round-robin regardless of where
# it sorts alphabetically within its adapter's queue.
# ---------------------------------------------------------------------------


def test_adapter_open_session_survives_even_deep_in_a_large_alphabetical_tail() -> None:
    """Direct regression: a large adapter (FreeCAD-shaped) whose tool count
    alone exceeds the whole budget still keeps its own open_session, even
    though alphabetically 'open_session' sorts well into the tools this
    adapter would otherwise lose to smaller adapters getting an equal
    per-round share."""
    reg = ToolRegistry()
    # 'freecad' alone has more tools than the entire cap; every other server
    # combined also exceeds it, so the round-robin must drop deep into
    # freecad's queue -- open_session sorts roughly in the middle of a
    # realistic alphabetical tool list (create_* .. transform_*).
    for i in range(150):
        reg.register_mcp(
            "freecad", f"m{i:03d}_tool", description="d", input_schema=SCHEMA, handler=_echo
        )
    reg.register_mcp("freecad", "open_session", description="d", input_schema=SCHEMA, handler=_echo)
    for s in range(5):
        for i in range(30):
            reg.register_mcp(
                f"server{s}", f"t{i:03d}", description="d", input_schema=SCHEMA, handler=_echo
            )

    kept, dropped = _select_tools(reg.all_tools(), 128)
    assert "mcp_freecad_open_session" in {s.name for s in kept}
    assert "mcp_freecad_open_session" not in dropped


def test_session_critical_verbs_survive_even_deep_in_a_large_alphabetical_tail() -> None:
    """FORGE-94 remainder (re-test 2026-09-25): protecting open_session alone
    wasn't enough -- pad_sketch/pocket_sketch/revolve_sketch/list_joints still
    sorted into the dropped tail of a large FreeCAD-shaped registry, leaving
    an agent that opened a session unable to turn a sketch into a feature or
    inspect the joints it just added."""
    reg = ToolRegistry()
    for i in range(150):
        reg.register_mcp(
            "freecad", f"m{i:03d}_tool", description="d", input_schema=SCHEMA, handler=_echo
        )
    for tool in ("open_session", "pad_sketch", "pocket_sketch", "revolve_sketch", "list_joints"):
        reg.register_mcp("freecad", tool, description="d", input_schema=SCHEMA, handler=_echo)
    for s in range(5):
        for i in range(30):
            reg.register_mcp(
                f"server{s}", f"t{i:03d}", description="d", input_schema=SCHEMA, handler=_echo
            )

    kept, dropped = _select_tools(reg.all_tools(), 128)
    kept_names = {s.name for s in kept}
    for tool in ("open_session", "pad_sketch", "pocket_sketch", "revolve_sketch", "list_joints"):
        name = f"mcp_freecad_{tool}"
        assert name in kept_names, f"{name} was dropped"
        assert name not in dropped


_SESSION_ID_SCHEMA = {
    "type": "object",
    "properties": {"session_id": {"type": "string"}, "x": {"type": "number"}},
    "required": ["session_id"],
}


def test_any_session_id_requiring_tool_survives_without_being_named() -> None:
    """FORGE-94 remainder (re-test 2026-09-26): curating verb names is the
    same whack-a-mole class of fix _open_session-only protection already
    needed replacing once -- it just moves the gap (measure, set_expression,
    linear_pattern, ... still sorted into the dropped tail, and any BRAND
    NEW stateful tool starts out unprotected until someone remembers to name
    it here, e.g. FORGE-231's own import_step). Any tool whose schema
    requires session_id is structurally the same class -- it can only be
    called inside an already-open session -- and must survive without ever
    being named in _SESSION_CRITICAL_VERBS."""
    reg = ToolRegistry()
    for i in range(150):
        reg.register_mcp(
            "freecad", f"m{i:03d}_tool", description="d", input_schema=SCHEMA, handler=_echo
        )
    # None of these are in _SESSION_CRITICAL_VERBS -- they must survive on
    # the schema check alone.
    for tool in ("measure", "set_expression", "linear_pattern", "import_step", "shell_solid"):
        reg.register_mcp(
            "freecad", tool, description="d", input_schema=_SESSION_ID_SCHEMA, handler=_echo
        )
    for s in range(5):
        for i in range(30):
            reg.register_mcp(
                f"server{s}", f"t{i:03d}", description="d", input_schema=SCHEMA, handler=_echo
            )

    kept, dropped = _select_tools(reg.all_tools(), 128)
    kept_names = {s.name for s in kept}
    for tool in ("measure", "set_expression", "linear_pattern", "import_step", "shell_solid"):
        name = f"mcp_freecad_{tool}"
        assert name in kept_names, f"{name} was dropped"
        assert name not in dropped


def test_a_tool_without_session_id_required_is_not_protected_by_the_schema_check() -> None:
    """The schema check must not accidentally protect everything -- a tool
    that merely HAS a session_id property, optional or absent, is unaffected
    and can still be round-robin-dropped like any ordinary tool."""
    reg = ToolRegistry()
    optional_session_id_schema = {
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": [],
    }
    for i in range(150):
        reg.register_mcp(
            "freecad",
            f"m{i:03d}_tool",
            description="d",
            input_schema=optional_session_id_schema,
            handler=_echo,
        )
    for s in range(5):
        for i in range(30):
            reg.register_mcp(
                f"server{s}", f"t{i:03d}", description="d", input_schema=SCHEMA, handler=_echo
            )

    kept, dropped = _select_tools(reg.all_tools(), 128)
    # Some of freecad's 150 tools must have been dropped -- the schema check
    # doesn't blanket-protect an adapter just because session_id is a
    # possible (not required) property.
    assert any(name.startswith("mcp_freecad_") for name in dropped)


def test_pinned_tools_survive_the_round_robin() -> None:
    reg = ToolRegistry()
    for s in range(10):
        for i in range(30):
            reg.register_mcp(
                f"server{s}", f"t{i:03d}", description="d", input_schema=SCHEMA, handler=_echo
            )
    # Pin a tool that would otherwise sort deep into a dropped tail.
    pinned = frozenset({"mcp_server9_t029"})

    kept, dropped = _select_tools(reg.all_tools(), 128, pinned=pinned)
    assert "mcp_server9_t029" in {s.name for s in kept}
    assert "mcp_server9_t029" not in dropped


def test_tool_schemas_passes_the_runtimes_pinned_names_through() -> None:
    reg = ToolRegistry()
    for s in range(10):
        for i in range(30):
            reg.register_mcp(
                f"server{s}", f"t{i:03d}", description="d", input_schema=SCHEMA, handler=_echo
            )
    reg.pin("mcp_server9_t029")
    runtime = HarnessRuntime.build(None, tools=reg)

    schemas = _tool_schemas(runtime, max_tools=128)
    names = {s["function"]["name"] for s in schemas}
    assert "mcp_server9_t029" in names


# ---------------------------------------------------------------------------
# ToolRegistry.pin / pinned_names
# ---------------------------------------------------------------------------


def test_pin_and_pinned_names_roundtrip() -> None:
    reg = ToolRegistry()
    assert reg.pinned_names() == frozenset()
    reg.pin("mcp_freecad_open_session")
    reg.pin("mcp_freecad_pad_sketch")
    assert reg.pinned_names() == frozenset({"mcp_freecad_open_session", "mcp_freecad_pad_sketch"})


def test_pin_is_idempotent() -> None:
    reg = ToolRegistry()
    reg.pin("mcp_x_y")
    reg.pin("mcp_x_y")
    assert reg.pinned_names() == frozenset({"mcp_x_y"})
