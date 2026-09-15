"""Chat harness can drive MCP tools via the bridge (MET-548). Network-free."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_gateway.chat.harness_backend import _build_context, mcp_tools_from_bridge, run_chat_turn
from api_gateway.chat.tool_approvals import get_approval_store, reset_approval_store
from orchestrator.harness.providers import CredentialStore, ProviderSpec
from orchestrator.harness.runs import ApprovalDecision
from orchestrator.harness.tools import ApprovalDeniedError
from skill_registry.mcp_bridge import InMemoryMcpBridge


@pytest.mark.asyncio
async def test_mcp_tools_from_bridge_builds_defs() -> None:
    bridge = InMemoryMcpBridge()
    bridge.register_tool("calculix.run_fea", capability="fea", name="Run FEA")
    defs = await mcp_tools_from_bridge(bridge)
    assert len(defs) == 1
    server, td = defs[0]
    assert server == "calculix"  # split from tool_id
    assert td.name == "run_fea"
    assert "calculix.run_fea" in td.description


@pytest.mark.asyncio
async def test_mcp_tools_from_bridge_tags_the_starter_approval_tier() -> None:
    """Production-harness audit follow-up: a conservative starter set of
    persistent-write tool ids require approval; everything else is auto-allow."""
    bridge = InMemoryMcpBridge()
    bridge.register_tool("twin.commit_geometry", capability="twin_write")
    bridge.register_tool("twin.record_decision", capability="twin_write")
    bridge.register_tool("project.create", capability="project_write")
    bridge.register_tool("project.update", capability="project_write")
    bridge.register_tool("project.delete", capability="project_write")
    bridge.register_tool("project.get", capability="project_read")
    bridge.register_tool("freecad.pad_sketch", capability="cad_author")
    defs = {td.name: td for _server, td in await mcp_tools_from_bridge(bridge)}

    for gated in ("commit_geometry", "record_decision"):
        assert defs[gated].requires_approval is True
    for gated in ("create", "update", "delete"):
        assert defs[gated].requires_approval is True
    assert defs["get"].requires_approval is False
    assert defs["pad_sketch"].requires_approval is False


@pytest.mark.asyncio
async def test_mcp_tools_from_bridge_surfaces_input_schema() -> None:
    """The tool's real parameter schema reaches the model (MET-548 fix).

    Previously every tool advertised a bare ``{"type": "object"}``, so the
    model never learned which arguments were required and calls to tools with
    required params (session.start, freecad.*, calculix.*) failed.
    """
    schema = {
        "type": "object",
        "properties": {
            "agent_code": {"type": "string", "minLength": 1},
            "task_type": {"type": "string", "minLength": 1},
        },
        "required": ["agent_code", "task_type"],
    }
    bridge = InMemoryMcpBridge()
    bridge.register_tool(
        "session.start", capability="session_capture", name="Start", input_schema=schema
    )
    defs = await mcp_tools_from_bridge(bridge)
    _, td = defs[0]
    assert td.input_schema == schema
    assert td.input_schema["required"] == ["agent_code", "task_type"]


@pytest.mark.asyncio
async def test_mcp_tools_from_bridge_falls_back_when_no_schema() -> None:
    """Tools without a usable object schema keep the permissive fallback."""
    bridge = InMemoryMcpBridge()
    bridge.register_tool("twin.get_node", capability="twin_inspect", name="Get Node")
    defs = await mcp_tools_from_bridge(bridge)
    _, td = defs[0]
    assert td.input_schema == {"type": "object"}


@pytest.mark.asyncio
async def test_mcp_tools_from_bridge_excludes_chat_visible_false() -> None:
    """MET-747 follow-up: registering a 129th tool 400'd every OpenAI-family
    chat turn platform-wide (their hard 128-tool-array cap) -- there was no
    smaller default set to fall back on. Tools meant to be invoked only from
    inside a skill's handler (via context.mcp.invoke, a direct-by-id call
    unrelated to this list) opt out via chat_visible=False so the chat tool
    array can stay under that cap as the registry keeps growing."""
    bridge = InMemoryMcpBridge()
    bridge.register_tool("twin.get_node", capability="twin_inspect")
    bridge.register_tool(
        "twin.commit_hazard_analysis", capability="twin_hazard_analysis", chat_visible=False
    )
    defs = await mcp_tools_from_bridge(bridge)
    names = {td.name for _server, td in defs}
    assert names == {"get_node"}


@pytest.mark.asyncio
async def test_mcp_tools_from_bridge_chat_visible_false_wins_over_explicit_enabled() -> None:
    """A structural chat_visible=False opt-out wins even if a caller's
    explicit ``enabled`` selection names the tool id -- it's a stronger
    restriction than the default-all-tools set, not just a display default."""
    bridge = InMemoryMcpBridge()
    bridge.register_tool(
        "twin.commit_hazard_analysis", capability="twin_hazard_analysis", chat_visible=False
    )
    defs = await mcp_tools_from_bridge(bridge, enabled={"twin.commit_hazard_analysis"})
    assert defs == []


class TestDomainScoping:
    """MET-747 follow-up: ``domains`` trims the MCP tool schema list a turn
    starts with -- the direct lever for staying under OpenAI's 128-tool-array
    cap for domain-aware callers (design-flow phases). Exercised against the
    real ``domain_agents`` skill corpus on disk (no fixture skills), since
    that's exactly what production wiring reads."""

    @pytest.mark.asyncio
    async def test_domains_none_registers_everything_unchanged(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("twin.get_node", capability="twin_inspect")
        bridge.register_tool("kicad.run_drc", capability="eda_drc")
        defs = await mcp_tools_from_bridge(bridge, domains=None)
        assert {td.name for _s, td in defs} == {"get_node", "run_drc"}

    @pytest.mark.asyncio
    async def test_domains_keeps_core_adapters_always_visible(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("twin.get_node", capability="twin_inspect")
        bridge.register_tool("project.get", capability="project_read")
        bridge.register_tool("knowledge.search", capability="knowledge")
        defs = await mcp_tools_from_bridge(bridge, domains=("mechanical",))
        assert {td.name for _s, td in defs} == {"get_node", "get", "search"}

    @pytest.mark.asyncio
    async def test_domains_admits_the_discipline_own_tools(self) -> None:
        """freecad.pad_sketch is declared by a real mechanical skill's
        ``tools_required`` -- a mechanical-scoped turn must see it."""
        bridge = InMemoryMcpBridge()
        bridge.register_tool("freecad.pad_sketch", capability="cad_author")
        defs = await mcp_tools_from_bridge(bridge, domains=("mechanical",))
        assert {td.name for _s, td in defs} == {"pad_sketch"}

    @pytest.mark.asyncio
    async def test_domains_excludes_other_disciplines_tools(self) -> None:
        """kicad.run_drc is declared only by an electronics skill -- a
        mechanical-scoped turn must NOT see it (this is the cap-avoidance
        payoff: fewer irrelevant schemas sent to the model)."""
        bridge = InMemoryMcpBridge()
        bridge.register_tool("freecad.pad_sketch", capability="cad_author")
        bridge.register_tool("kicad.run_drc", capability="eda_drc")
        defs = await mcp_tools_from_bridge(bridge, domains=("mechanical",))
        assert {td.name for _s, td in defs} == {"pad_sketch"}

    @pytest.mark.asyncio
    async def test_domains_union_across_multiple_disciplines(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("freecad.pad_sketch", capability="cad_author")
        bridge.register_tool("kicad.run_drc", capability="eda_drc")
        defs = await mcp_tools_from_bridge(bridge, domains=("mechanical", "electronics"))
        assert {td.name for _s, td in defs} == {"pad_sketch", "run_drc"}

    @pytest.mark.asyncio
    async def test_empty_domains_tuple_is_treated_as_no_scoping(self) -> None:
        """A phase with no declared disciplines (``Phase.disciplines`` default
        ``()``) must not accidentally scope down to core-only -- it keeps the
        pre-MET-747 behavior of registering everything available."""
        bridge = InMemoryMcpBridge()
        bridge.register_tool("kicad.run_drc", capability="eda_drc")
        defs = await mcp_tools_from_bridge(bridge, domains=())
        assert {td.name for _s, td in defs} == {"run_drc"}


class TestSearchToolsMetaTool:
    """MET-747 follow-up: ``search_tools`` is the mid-turn escape hatch for a
    domain-scoped turn that genuinely needs an out-of-scope tool."""

    @pytest.mark.asyncio
    async def test_registers_a_matching_out_of_scope_tool(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("twin.get_node", capability="twin_inspect")
        bridge.register_tool("kicad.run_drc", capability="eda_drc")
        ctx = await _build_context("thread-1", CredentialStore(), bridge, domains=("mechanical",))
        before = {t.name for t in ctx.runtime.tools.all_tools()}
        assert "mcp_kicad_run_drc" not in before

        result = await ctx.runtime.call_tool("search_tools", {"query": "kicad"})
        assert result["registered"] == ["mcp_kicad_run_drc"]

        after = {t.name for t in ctx.runtime.tools.all_tools()}
        assert "mcp_kicad_run_drc" in after

    @pytest.mark.asyncio
    async def test_calling_the_newly_registered_tool_works(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("kicad.run_drc", capability="eda_drc")
        bridge.register_tool_response("kicad.run_drc", {"violations": 0})
        ctx = await _build_context("thread-1", CredentialStore(), bridge, domains=("mechanical",))
        await ctx.runtime.call_tool("search_tools", {"query": "kicad"})
        result = await ctx.runtime.call_tool("mcp_kicad_run_drc", {})
        assert result == {"violations": 0}

    @pytest.mark.asyncio
    async def test_no_match_reports_nothing_registered(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("twin.get_node", capability="twin_inspect")
        ctx = await _build_context("thread-1", CredentialStore(), bridge, domains=("mechanical",))
        result = await ctx.runtime.call_tool("search_tools", {"query": "nonexistent_widget"})
        assert result["registered"] == []

    @pytest.mark.asyncio
    async def test_already_registered_tool_is_reported_not_reregistered(self) -> None:
        """Searching for a tool that's already in scope (e.g. a core adapter,
        or one the domain already admitted) must not raise DuplicateToolError."""
        bridge = InMemoryMcpBridge()
        bridge.register_tool("twin.get_node", capability="twin_inspect")
        ctx = await _build_context("thread-1", CredentialStore(), bridge, domains=("mechanical",))
        result = await ctx.runtime.call_tool("search_tools", {"query": "get_node"})
        assert result["registered"] == []
        assert "mcp_twin_get_node" in result["already_available"]

    @pytest.mark.asyncio
    async def test_empty_query_is_rejected(self) -> None:
        bridge = InMemoryMcpBridge()
        ctx = await _build_context("thread-1", CredentialStore(), bridge)
        with pytest.raises(ValueError, match="query"):
            await ctx.runtime.call_tool("search_tools", {"query": ""})

    @pytest.mark.asyncio
    async def test_not_registered_without_an_mcp_bridge(self) -> None:
        ctx = await _build_context("thread-1", CredentialStore(), None)
        names = {t.name for t in ctx.runtime.tools.all_tools()}
        assert "search_tools" not in names


@pytest.mark.asyncio
async def test_chat_harness_invokes_mcp_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This test scripts ReAct-protocol replies — pin the ReAct path
    # explicitly (MET-575: the path now follows the resolved provider,
    # and the all-defaults resolution is anthropic → native).
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")
    bridge = InMemoryMcpBridge()
    bridge.register_tool("twin.query_node", capability="twin", name="Query Node")
    bridge.register_tool_response("twin.query_node", {"node": "N1", "mass_g": 42})

    # Scripted model: first call requests the tool, second returns a final answer.
    calls = {"n": 0}

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            payload = {
                "thought": "look it up",
                "tool": "mcp_twin_query_node",
                "arguments": {"id": "N1"},
            }
            return {"text": json.dumps(payload), "model": spec.model}
        return {"text": '{"thought": "done", "final": "Mass is 42 g"}', "model": spec.model}

    out = await run_chat_turn(
        "What is the mass of N1?",
        invoke=invoke,
        max_steps=3,
        credentials=CredentialStore(tmp_path / "c.json"),
        mcp_bridge=bridge,
    )
    assert out == "Mass is 42 g"
    assert calls["n"] == 2  # tool step + final step — the tool was actually driven


class TestChatHarnessApprovalWiring:
    """Production-harness audit follow-up: `_build_context` shares the SAME
    process-level approval store `get_approval_store()` returns, so a
    separate request (simulated here, the real one is the REST endpoint in
    `tool_approvals.py`) can resolve a tool call this turn paused on."""

    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        reset_approval_store()
        yield
        reset_approval_store()

    @pytest.mark.asyncio
    async def test_requires_approval_tool_resolves_via_the_shared_store(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("twin.commit_geometry", capability="twin_write")
        bridge.register_tool_response("twin.commit_geometry", {"committed": True})

        ctx = await _build_context("thread-1", CredentialStore(), bridge)
        # Speed the poll up for the test — no real wall-clock wait needed to
        # prove the wiring, same seam HarnessRuntime's own tests use.
        approved_ids: list[str] = []

        async def fast_sleep(seconds: float) -> None:
            if not approved_ids:
                run = get_approval_store().list()[0]
                get_approval_store().submit_approval(run.id, ApprovalDecision.APPROVE)
                approved_ids.append(run.id)

        ctx.runtime.approval_sleep = fast_sleep
        result = await ctx.runtime.call_tool("mcp_twin_commit_geometry", {})
        assert result == {"committed": True}
        # The SAME store `_build_context` wired in is the one that resolved it.
        assert get_approval_store().list()[0].status.value == "running"

    @pytest.mark.asyncio
    async def test_requires_approval_tool_denied_via_the_shared_store(self) -> None:
        bridge = InMemoryMcpBridge()
        bridge.register_tool("twin.commit_geometry", capability="twin_write")
        bridge.register_tool_response("twin.commit_geometry", {"committed": True})

        ctx = await _build_context("thread-1", CredentialStore(), bridge)

        async def fast_sleep(seconds: float) -> None:
            run = get_approval_store().list()[0]
            get_approval_store().submit_approval(run.id, ApprovalDecision.REJECT)

        ctx.runtime.approval_sleep = fast_sleep
        with pytest.raises(ApprovalDeniedError, match="rejected"):
            await ctx.runtime.call_tool("mcp_twin_commit_geometry", {})
