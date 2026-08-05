"""Unit tests for MET-566: ContextAssembler wiring into chat turns + the
layered system prompt. Network-free — fake knowledge service, fake invoke."""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.chat.context_adapter import (
    assemble_chat_context,
    context_token_budget,
    init_context_assembler,
    render_context_block,
)
from api_gateway.chat.harness_backend import (
    _apply_turn_context,
    build_system_prompt,
    compute_context_stats,
    run_chat_turn,
)
from digital_twin.knowledge.service import SearchHit
from orchestrator.harness.providers import ProviderSpec, load_provider_config
from orchestrator.harness.runtime import HarnessRuntime
from orchestrator.harness.tools import ToolRegistry

CONFIG = load_provider_config(
    {"roles": {"generator": [{"provider": "anthropic", "model": "claude-opus-4-8"}]}}
)


def _runtime(tool_names: list[str] | None = None) -> HarnessRuntime:
    tools = ToolRegistry()

    async def handler(arguments: dict[str, object]) -> object:
        return "ok"

    for name in tool_names or []:
        tools.register_native(
            name, description=name, input_schema={"type": "object"}, handler=handler
        )
    return HarnessRuntime.build(CONFIG, tools=tools)


class _FakeKnowledge:
    """KnowledgeService double returning canned hits."""

    def __init__(self, hits: list[SearchHit] | None = None, fail: bool = False) -> None:
        self._hits = hits or []
        self._fail = fail

    async def search(self, **kwargs: Any) -> list[SearchHit]:
        if self._fail:
            raise RuntimeError("search exploded")
        return self._hits


def _hit(content: str, source: str, score: float = 0.8) -> SearchHit:
    return SearchHit(
        content=content,
        similarity_score=score,
        source_path=source,
        heading=None,
        chunk_index=0,
        total_chunks=1,
    )


# --- context_adapter ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_assemble_returns_none_without_assembler() -> None:
    init_context_assembler(object(), None)
    assert await assemble_chat_context("what alloy?", window=128_000) is None


@pytest.mark.asyncio
async def test_assemble_returns_fragments_with_attribution() -> None:
    init_context_assembler(
        object(), _FakeKnowledge([_hit("The bracket alloy is 7075-T6.", "specs/alloy.md")])
    )
    try:
        resp = await assemble_chat_context("what alloy?", window=128_000)
        assert resp is not None and len(resp.fragments) == 1
        block = render_context_block(resp)
        assert "7075-T6" in block
        assert "[specs/alloy.md]" in block  # source_id attribution survives rendering
    finally:
        init_context_assembler(object(), None)


@pytest.mark.asyncio
async def test_assembly_failure_is_swallowed() -> None:
    init_context_assembler(object(), _FakeKnowledge(fail=True))
    try:
        assert await assemble_chat_context("q", window=128_000) is None
    finally:
        init_context_assembler(object(), None)


@pytest.mark.asyncio
async def test_empty_query_and_empty_hits_return_none() -> None:
    init_context_assembler(object(), _FakeKnowledge([]))
    try:
        assert await assemble_chat_context("   ", window=128_000) is None
        assert await assemble_chat_context("q", window=128_000) is None
    finally:
        init_context_assembler(object(), None)


def test_context_token_budget_scales_and_clamps() -> None:
    assert context_token_budget(200_000) == 6_000  # capped
    assert context_token_budget(10_000) == 1_000  # floored
    assert context_token_budget(80_000) == 4_000  # window // 20


def test_render_surfaces_truncation_and_conflicts() -> None:
    from digital_twin.context.conflicts import Conflict, ConflictSeverity
    from digital_twin.context.models import ContextAssemblyResponse

    resp = ContextAssemblyResponse(
        fragments=[],
        token_count=0,
        truncated=True,
        dropped_source_ids=["a", "b"],
        sources={},
        conflicts=[
            Conflict(
                field="alloy",
                value_a="7075",
                value_b="6061",
                source_a="s1",
                source_b="s2",
                severity=ConflictSeverity.BLOCKING,
                grouping_key="k",
                description="d",
            )
        ],
        has_blocking_conflict=True,
    )
    block = render_context_block(resp)
    assert "WARNING" in block and "conflicting" in block
    assert "2 lower-relevance fragment(s)" in block


# --- layered system prompt ----------------------------------------------------------
def test_layered_prompt_sections() -> None:
    rt = _runtime(["mcp_twin_get_node", "mcp_twin_record_decision", "mcp_freecad_measure"])
    prompt = build_system_prompt(rt, project_brief="Project X, id p-1")
    assert "MetaForge's assistant" in prompt  # identity/rules lead
    assert "Current date:" in prompt
    assert "twin" in prompt and "freecad" in prompt  # families, not 3 raw tool names
    assert "mcp_twin_get_node" not in prompt
    assert "[project context]\nProject X, id p-1" in prompt
    assert "cite its bracketed source id" in prompt  # response guidance last


def test_layered_prompt_without_brief_has_no_project_section() -> None:
    prompt = build_system_prompt(_runtime())
    assert "[project context]" not in prompt


# --- path-dependent placement --------------------------------------------------------
def test_native_path_puts_brief_in_system_not_history() -> None:
    system, history = _apply_turn_context(
        runtime=_runtime(),
        native=True,
        history=[{"role": "user", "content": "hi"}],
        project_brief="Project X",
        context_block=None,
    )
    assert "[project context]\nProject X" in system
    assert history == [{"role": "user", "content": "hi"}]


def test_react_path_keeps_brief_as_leading_history_pair() -> None:
    system, history = _apply_turn_context(
        runtime=_runtime(),
        native=False,
        history=[{"role": "user", "content": "hi"}],
        project_brief="Project X",
        context_block=None,
    )
    assert "[project context]" not in system
    assert history is not None
    assert history[0]["content"].startswith("[project context]")
    assert history[1]["role"] == "assistant"
    assert history[2] == {"role": "user", "content": "hi"}


def test_context_block_is_trailing_pair_on_both_paths() -> None:
    for native in (True, False):
        _, history = _apply_turn_context(
            runtime=_runtime(),
            native=native,
            history=[{"role": "user", "content": "hi"}],
            project_brief=None,
            context_block="- [src] fact",
        )
        assert history is not None
        assert history[-2]["content"].startswith("[retrieved context]")
        assert "- [src] fact" in history[-2]["content"]
        assert history[-1]["role"] == "assistant"


# --- fragments reach the harness request ----------------------------------------------
@pytest.mark.asyncio
async def test_fragments_land_in_native_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MET-566 verification: retrieved fragments must be in the message
    list the provider actually receives, and the brief in its system prompt."""
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "true")
    seen: dict[str, Any] = {}

    async def invoke(spec: ProviderSpec, request: dict) -> dict:
        seen["system"] = request.get("system", "")
        seen["messages"] = request.get("messages", [])
        return {"text": "done", "model": spec.model}

    out = await run_chat_turn(
        "what alloy?",
        invoke=invoke,
        project_brief="Project X, id p-1",
        context_block="- [specs/alloy.md] The bracket alloy is 7075-T6.",
    )
    assert out == "done"
    assert "[project context]\nProject X, id p-1" in seen["system"]
    joined = "\n".join(str(m.get("content")) for m in seen["messages"])
    assert "7075-T6" in joined and "[retrieved context]" in joined


# --- context.stats sections ------------------------------------------------------------
def test_stats_report_brief_and_retrieved_context_components() -> None:
    stats = compute_context_stats(
        runtime=_runtime(),
        system="sys",
        history=[{"role": "user", "content": "hi"}],
        user_content="now?",
        provider="anthropic",
        model="claude-opus-4-8",
        tools_available=0,
        project_brief="Project X brief text",
        context_block="- [src] retrieved fact",
    )
    by_key = {c["key"]: c for c in stats["components"]}
    assert by_key["project_brief"]["tokens"] > 0
    assert by_key["retrieved_context"]["tokens"] > 0
    total = sum(c["tokens"] for c in stats["components"])
    assert stats["used"] == total  # buckets sum to the headline number
