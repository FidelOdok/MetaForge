"""Context budget (FORGE-479): result handles, brief cap, phase-scoped tools.

The last test is the before/after measurement the story asks for: one scripted
design phase against a 121-tool connection, run with the old behaviour (every
tool, every result inline) and the new one, with prompt tokens read back from
the FORGE-476 accounting store rather than counted separately.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from api_gateway.chat.harness_backend import run_chat_turn
from api_gateway.projects.brief import (
    BRIEF_CHAR_LIMIT_ENV,
    build_project_brief,
    cap_brief,
)
from mcp_core.profiles import (
    DISCIPLINE_PROFILES,
    MAX_TOOLS,
    MIN_TOOLS,
    PHASE_MCP_BUDGET,
    PROFILES,
    phase_overflow,
    tools_for_disciplines,
)
from orchestrator.design_flow.spec import FLOWS
from orchestrator.harness.providers import CredentialStore, ProviderSpec
from orchestrator.harness.providers.usage import (
    UsageStore,
    configure_usage_store,
    usage_scope,
)
from orchestrator.harness.result_handles import (
    INLINE_LIMIT_ENV,
    READER_TOOL_NAME,
    ResultStore,
    inline_limit,
    summarize,
)
from skill_registry.mcp_bridge import InMemoryMcpBridge


class TestResultStore:
    def test_small_results_are_not_handled(self) -> None:
        assert ResultStore().offer("t", {"ok": True}) is None

    def test_large_results_become_summary_plus_handle(self) -> None:
        store = ResultStore()
        rows = [{"id": i, "payload": "x" * 100} for i in range(500)]
        text = store.offer("twin.query_cypher", {"rows": rows, "total": 500})
        assert text is not None and len(text) < inline_limit() // 2
        body = json.loads(text)
        assert body["result_handle"] == "result-1"
        assert body["summary"]["total"] == 500
        assert body["summary"]["rows"]["length"] == 500
        assert READER_TOOL_NAME in body["note"]

    def test_reader_pages_through_and_reads_one_key(self) -> None:
        store = ResultStore()
        store.offer("t", {"rows": ["a" * 100] * 400, "total": 400})
        first = store.read("result-1", offset=0, limit=1000)
        assert len(first["content"]) == 1000
        assert first["next_offset"] == 1000
        assert store.read("result-1", key="total")["content"] == "400"
        last = store.read("result-1", offset=first["total_chars"] - 5)
        assert last["next_offset"] is None

    def test_reader_window_is_capped_at_the_inline_limit(self) -> None:
        store = ResultStore()
        store.offer("t", {"blob": "z" * 50_000})
        got = store.read("result-1", key="blob", limit=10**9)
        assert len(got["content"]) == inline_limit()

    def test_unknown_and_evicted_handles_say_so(self) -> None:
        store = ResultStore(max_entries=1)
        store.offer("t", {"b": "q" * 20_000})
        store.offer("t", {"b": "q" * 20_000})
        with pytest.raises(KeyError, match="evicted"):
            store.get("result-1")
        with pytest.raises(KeyError, match="unknown"):
            store.get("result-99")

    def test_the_reader_is_never_itself_handled(self) -> None:
        assert ResultStore().offer(READER_TOOL_NAME, {"content": "x" * 50_000}) is None

    def test_limit_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(INLINE_LIMIT_ENV, "100")
        assert ResultStore().offer("t", {"a": "b" * 200}) is not None
        monkeypatch.setenv(INLINE_LIMIT_ENV, "junk")
        assert inline_limit() == 8_000

    def test_summary_bounds_wide_objects(self) -> None:
        out = summarize({f"k{i}": i for i in range(100)})
        assert len(out) <= 25


class TestBriefCap:
    def test_short_brief_is_untouched(self) -> None:
        assert cap_brief("short", "p1") == "short"

    def test_long_brief_is_capped_with_a_link_and_keeps_directives(self) -> None:
        body = "\n".join(f"- part {i} - cad_model (status draft)" for i in range(2000))
        text = body + '\nAny CAD model you generate in this project is NOT saved: project_id="p1"'
        out = cap_brief(text, "p1", limit=3000)
        assert len(out) <= 3000
        assert "metaforge://twin/brief/p1" in out
        assert 'project_id="p1"' in out
        assert out.startswith("- part 0 ")

    @pytest.mark.asyncio
    async def test_build_project_brief_caps_but_full_does_not(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(BRIEF_CHAR_LIMIT_ENV, "1500")
        wps = [
            SimpleNamespace(
                id=f"w{i}", name=f"Part {i}", type="cad_model", status="draft", updated_at=i
            )
            for i in range(30)
        ]
        project = SimpleNamespace(
            id="p1", name="P", status="active", description="d", work_products=wps
        )

        async def excerpt(_: str) -> str | None:
            return None

        capped = await build_project_brief(project, doc_excerpt=excerpt)
        full = await build_project_brief(project, doc_excerpt=excerpt, full=True)
        assert len(capped) <= 1500 < len(full)
        assert capped.index("Part 29") < capped.index("metaforge://twin/brief/p1")
        assert "Part 29 " in capped  # newest first survives the cap


class TestPhaseTools:
    def test_every_template_phase_fits_the_budget_without_dropping_anything(self) -> None:
        for flow in FLOWS.values():
            for phase in flow.phases:
                tools = tools_for_disciplines(phase.disciplines)
                assert len(tools) <= PHASE_MCP_BUDGET, (flow.id, phase.id, len(tools))
                assert phase_overflow(phase.disciplines) == [], (flow.id, phase.id)

    def test_mechanical_product_profile_covers_the_shelf_run_gaps(self) -> None:
        p = PROFILES["mechanical_product"]
        assert MIN_TOOLS <= len(p) <= MAX_TOOLS
        for needed in (
            "freecad.open_session",
            "freecad.create_sketch",
            "freecad.pad_sketch",
            "freecad.pocket_sketch",
            "freecad.add_part_to_assembly",
            "freecad.export_model",
            "freecad.close_session",
            "twin.commit_geometry",
            "twin.record_component_selection",
            "project.open",
        ):
            assert needed in p
        # Promotion is a human authority; no agent profile carries it.
        assert not any("promotion" in t for prof in PROFILES.values() for t in prof)

    def test_a_disciplineless_phase_is_not_every_tool(self) -> None:
        tools = tools_for_disciplines(())
        assert "twin.record_engineering_entity" in tools
        assert "twin.record_decision" in tools
        assert not any(t.startswith(("freecad.", "calculix.", "kicad.")) for t in tools)

    def test_discipline_profiles_name_real_profiles(self) -> None:
        assert set(DISCIPLINE_PROFILES.values()) <= set(PROFILES)


# ---------------------------------------------------------------- measurement


def _realistic_schema(tool_id: str) -> dict[str, Any]:
    props = {
        f"param_{i}": {"type": "string", "description": f"{tool_id} parameter {i}, in SI units"}
        for i in range(8)
    }
    return {"type": "object", "properties": props, "required": ["param_0"]}


def _bridge_with_121_tools() -> InMemoryMcpBridge:
    bridge = InMemoryMcpBridge()
    ids: set[str] = set().union(*PROFILES.values())
    filler = 0
    while len(ids) < 121:
        ids.add(f"filler{filler % 7}.tool_{filler}")
        filler += 1
    for tid in sorted(ids):
        bridge.register_tool(tid, capability="x", input_schema=_realistic_schema(tid))
    big = {
        "rows": [{"node": i, "props": {"name": f"n{i}", "note": "x" * 120}} for i in range(400)],
        "total": 400,
    }
    bridge.register_tool_response("twin.query_cypher", big)
    bridge.register_tool_response("twin.find_by_property", dict(big))
    return bridge


async def _scripted_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, *, new: bool
) -> tuple[dict[str, Any], int]:
    """One 3-call phase: two large reads, then an answer. -> (accounting, tools)."""
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "1")
    if not new:
        monkeypatch.setenv(INLINE_LIMIT_ENV, "10000000")
    bridge = _bridge_with_121_tools()
    seen: dict[str, Any] = {"tools": 0, "tool_msgs": []}
    step = {"n": 0}

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        step["n"] += 1
        prompt = json.dumps(request.get("tools", [])) + json.dumps(request["messages"])
        prompt += str(request.get("system", ""))
        seen["tools"] = max(seen["tools"], len(request.get("tools", [])))
        seen["tool_msgs"] += [m["content"] for m in request["messages"] if m["role"] == "tool"]
        usage = {"input_tokens": len(prompt) // 4, "output_tokens": 20}
        if step["n"] <= 2:
            tool = ("twin_query_cypher", "twin_find_by_property")[step["n"] - 1]
            call = {"id": f"c{step['n']}", "name": tool, "arguments": {"param_0": "x"}}
            return {"model": spec.model, "text": "", "tool_calls": [call], "usage": usage}
        return {"model": spec.model, "text": "done", "tool_calls": [], "usage": usage}

    allow = tools_for_disciplines(()) if new else None
    with usage_scope(run_id="r-" + ("new" if new else "old"), phase="requirements"):
        await run_chat_turn(
            "Record the requirements.",
            invoke=invoke,
            max_steps=6,
            credentials=CredentialStore(tmp_path / "c.json"),
            mcp_bridge=bridge,
            tool_allowlist=allow,
        )
    return seen, step["n"]


@pytest.fixture()
def usage_store() -> Iterator[UsageStore]:
    s = UsageStore(":memory:")
    configure_usage_store(s)
    yield s
    configure_usage_store(None)


@pytest.mark.asyncio
async def test_phase_prompt_tokens_before_and_after(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, usage_store: UsageStore
) -> None:
    old_seen, _ = await _scripted_phase(monkeypatch, tmp_path, new=False)
    monkeypatch.delenv(INLINE_LIMIT_ENV, raising=False)
    new_seen, _ = await _scripted_phase(monkeypatch, tmp_path, new=True)

    before = usage_store.run_totals("r-old")
    after = usage_store.run_totals("r-new")
    assert before is not None and after is not None
    b, a = before["prompt_tokens"], after["prompt_tokens"]
    print(
        f"FORGE-479 scripted phase: tools {old_seen['tools']} -> {new_seen['tools']}, "
        f"prompt tokens {b} -> {a} ({100 * (b - a) / b:.0f}% fewer), calls {after['calls']}"
    )

    assert new_seen["tools"] <= MAX_TOOLS
    assert old_seen["tools"] > 100
    assert a < b * 0.25, (b, a)
    # No tool result over the configured size reached the model inline.
    assert all(len(m) <= inline_limit() for m in new_seen["tool_msgs"])
    assert any("result_handle" in m for m in new_seen["tool_msgs"])
    assert any(len(m) > inline_limit() for m in old_seen["tool_msgs"])
