"""Unit tests for the harness central tool registry (MET-547, Phase 2)."""

from __future__ import annotations

import pytest

from orchestrator.harness.tools import (
    NATIVE,
    DuplicateToolError,
    GateBlockedError,
    ToolNotFoundError,
    ToolRegistry,
)

SCHEMA = {"type": "object", "properties": {"x": {"type": "number"}}}


async def _echo(args: dict[str, object]) -> dict[str, object]:
    return {"echo": args}


def test_mcp_name_namespacing() -> None:
    assert ToolRegistry.mcp_name("calculix", "run_fea") == "mcp_calculix_run_fea"
    # Non-alphanumeric runs collapse to single underscores.
    assert ToolRegistry.mcp_name("Digi-Key", "get.price") == "mcp_digi_key_get_price"


@pytest.mark.asyncio
async def test_register_and_invoke_native() -> None:
    reg = ToolRegistry()
    spec = reg.register_native(
        "twin_search", description="search the twin", input_schema=SCHEMA, handler=_echo
    )
    assert spec.origin == NATIVE
    assert reg.get("twin_search").description == "search the twin"
    assert await reg.invoke("twin_search", {"q": 1}) == {"echo": {"q": 1}}


def test_register_mcp_uses_namespaced_name() -> None:
    reg = ToolRegistry()
    spec = reg.register_mcp(
        "calculix", "run_fea", description="run FEA", input_schema=SCHEMA, handler=_echo
    )
    assert spec.name == "mcp_calculix_run_fea"
    assert spec.origin == "calculix"
    assert reg.get("mcp_calculix_run_fea") is spec


def test_duplicate_registration_raises() -> None:
    reg = ToolRegistry()
    reg.register_native("t", description="d", input_schema=SCHEMA, handler=_echo)
    with pytest.raises(DuplicateToolError):
        reg.register_native("t", description="d2", input_schema=SCHEMA, handler=_echo)


def test_get_unknown_raises() -> None:
    with pytest.raises(ToolNotFoundError):
        ToolRegistry().get("nope")


@pytest.mark.asyncio
async def test_invoke_unknown_raises() -> None:
    with pytest.raises(ToolNotFoundError):
        await ToolRegistry().invoke("nope", {})


# ---------------------------------------------------------------------------
# FORGE-236: unknown-tool-name alias resolution + structured error
# ---------------------------------------------------------------------------


def _registry_with_twin_commit_geometry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_mcp("twin", "commit_geometry", description="d", input_schema=SCHEMA, handler=_echo)
    return reg


@pytest.mark.parametrize(
    "requested",
    [
        "twin_commit_geometry",  # dropped the mcp_ prefix entirely
        "twin.commit_geometry",  # dotted tool_id form
        "twin/commit_geometry",  # slashed form
        "mcp_twin_commit_geometry",  # exact -- must still resolve via get()
    ],
)
def test_get_resolves_unprefixed_dotted_and_slashed_aliases(requested: str) -> None:
    reg = _registry_with_twin_commit_geometry()
    assert reg.get(requested).name == "mcp_twin_commit_geometry"


@pytest.mark.asyncio
async def test_invoke_resolves_an_alias_too_not_just_get() -> None:
    """The alias resolution must actually reach a call, not just a lookup --
    live-observed: the model called twin_commit_geometry directly."""
    reg = _registry_with_twin_commit_geometry()
    assert await reg.invoke("twin_commit_geometry", {"x": 1}) == {"echo": {"x": 1}}


def test_get_unknown_raises_structured_payload_not_a_bare_keyerror_string() -> None:
    """FORGE-236: str(KeyError(name)) is just repr(name) -- e.g.
    "'twin_commit_geometry'" -- live-observed making a model conclude a
    healthy backend was down. to_payload() must carry an actionable hint."""
    reg = _registry_with_twin_commit_geometry()
    with pytest.raises(ToolNotFoundError) as exc_info:
        reg.get("freecad_open_session")  # genuinely not registered here
    payload = exc_info.value.to_payload()
    assert payload["status"] == "error"
    assert payload["error"] == "unknown_tool"
    assert payload["tool"] == "freecad_open_session"
    assert "mcp_" in payload["hint"]


def test_get_unknown_but_close_suggests_did_you_mean() -> None:
    reg = _registry_with_twin_commit_geometry()
    with pytest.raises(ToolNotFoundError) as exc_info:
        reg.get("mcp_twin_commit_geometrie")  # one-character typo
    assert "mcp_twin_commit_geometry" in exc_info.value.to_payload()["did_you_mean"]


def test_alias_resolution_never_shadows_a_real_exact_match() -> None:
    """A native tool's own bare name (no mcp_ prefix by design) must resolve
    by exact match first -- aliasing only ever activates once that lookup
    has already failed."""
    reg = ToolRegistry()
    reg.register_native("twin_search", description="d", input_schema=SCHEMA, handler=_echo)
    assert reg.get("twin_search").origin == NATIVE


def test_list_and_filter_by_origin() -> None:
    reg = ToolRegistry()
    reg.register_native("twin_search", description="d", input_schema=SCHEMA, handler=_echo)
    reg.register_mcp("calculix", "run_fea", description="d", input_schema=SCHEMA, handler=_echo)
    reg.register_mcp("kicad", "erc", description="d", input_schema=SCHEMA, handler=_echo)

    assert reg.names() == ["mcp_calculix_run_fea", "mcp_kicad_erc", "twin_search"]
    assert [s.name for s in reg.all_tools(origin=NATIVE)] == ["twin_search"]
    assert [s.name for s in reg.all_tools(origin="calculix")] == ["mcp_calculix_run_fea"]
    assert len(reg.all_tools()) == 3


@pytest.mark.asyncio
async def test_ungated_tool_invokes_without_gate_check() -> None:
    reg = ToolRegistry()
    reg.register_native("t", description="d", input_schema=SCHEMA, handler=_echo)
    assert await reg.invoke("t", {"a": 1}) == {"echo": {"a": 1}}


@pytest.mark.asyncio
async def test_gated_tool_invokes_when_gates_satisfied() -> None:
    reg = ToolRegistry()
    reg.register_native(
        "cut",
        description="destructive",
        input_schema=SCHEMA,
        handler=_echo,
        required_gates=["approval"],
    )
    result = await reg.invoke("cut", {"x": 1}, gate_check=lambda g: True)
    assert result == {"echo": {"x": 1}}


@pytest.mark.asyncio
async def test_gated_tool_blocked_when_gate_unsatisfied() -> None:
    reg = ToolRegistry()
    reg.register_native(
        "cut",
        description="destructive",
        input_schema=SCHEMA,
        handler=_echo,
        required_gates=["approval"],
    )
    with pytest.raises(GateBlockedError) as exc:
        await reg.invoke("cut", {}, gate_check=lambda g: False)
    assert exc.value.gate == "approval"


@pytest.mark.asyncio
async def test_gated_tool_fails_safe_without_evaluator() -> None:
    reg = ToolRegistry()
    reg.register_native(
        "cut",
        description="destructive",
        input_schema=SCHEMA,
        handler=_echo,
        required_gates=["approval"],
    )
    # No gate_check passed -> a gated tool must NOT run (external-client safe).
    with pytest.raises(GateBlockedError):
        await reg.invoke("cut", {})


@pytest.mark.asyncio
async def test_invoke_without_policy_engine_is_a_noop() -> None:
    """FORGE-71: no policy_engine passed -- every caller before this --
    behaves exactly as it always has."""
    reg = ToolRegistry()
    reg.register_native("t", description="d", input_schema=SCHEMA, handler=_echo)
    assert await reg.invoke("t", {"a": 1}) == {"echo": {"a": 1}}


@pytest.mark.asyncio
async def test_invoke_blocked_by_policy_engine() -> None:
    from twin_core.policy import EngineeringPolicyViolation, Policy
    from twin_core.policy.engine import PolicyEngine

    engine = PolicyEngine()
    engine.register(Policy(id="POL-1", action="t", require={"ready": True}))
    reg = ToolRegistry()
    reg.register_native("t", description="d", input_schema=SCHEMA, handler=_echo)
    with pytest.raises(EngineeringPolicyViolation):
        await reg.invoke("t", {}, policy_engine=engine, actor={}, state={})


@pytest.mark.asyncio
async def test_invoke_allowed_by_policy_engine_when_satisfied() -> None:
    from twin_core.policy import Policy
    from twin_core.policy.engine import PolicyEngine

    engine = PolicyEngine()
    engine.register(Policy(id="POL-1", action="t", require={"ready": True}))
    reg = ToolRegistry()
    reg.register_native("t", description="d", input_schema=SCHEMA, handler=_echo)
    result = await reg.invoke("t", {}, policy_engine=engine, actor={}, state={"ready": True})
    assert result == {"echo": {}}


@pytest.mark.asyncio
async def test_invoke_default_allow_with_no_registered_policy() -> None:
    """PolicyEngine's own default-allow -- an action with no matching
    Policy proceeds untouched even when an engine is passed."""
    from twin_core.policy.engine import PolicyEngine

    reg = ToolRegistry()
    reg.register_native("t", description="d", input_schema=SCHEMA, handler=_echo)
    result = await reg.invoke("t", {}, policy_engine=PolicyEngine())
    assert result == {"echo": {}}


def test_visible_filters_by_gate() -> None:
    reg = ToolRegistry()
    reg.register_native("safe", description="d", input_schema=SCHEMA, handler=_echo)
    reg.register_native(
        "cut",
        description="d",
        input_schema=SCHEMA,
        handler=_echo,
        required_gates=["approval"],
    )
    satisfied = {"approval"}
    visible = [s.name for s in reg.visible(lambda g: g in satisfied)]
    assert visible == ["cut", "safe"]

    satisfied.clear()
    visible = [s.name for s in reg.visible(lambda g: g in satisfied)]
    assert visible == ["safe"]  # gated tool hidden until its gate holds
