"""Unit tests for harness-backed chat (MET-548, surface A). Network-free."""

from __future__ import annotations

import pytest

from api_gateway.chat.backend import InMemoryChatBackend
from api_gateway.chat.harness_backend import (
    _build_context,
    _flag_if_unfounded_completion_claim,
    chat_harness_enabled,
    make_set_project_scope_tool,
    provider_config_from_env,
    run_chat_turn,
)
from api_gateway.projects.schemas import ProjectResponse
from orchestrator.harness.providers import CredentialStore, ProviderSpec
from orchestrator.harness.providers.auth_store import AuthStore, Selection
from orchestrator.harness.react import ReActStep, ToolCall


def test_flag_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_CHAT_HARNESS", raising=False)
    assert chat_harness_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "on", "YES"])
def test_flag_on(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("METAFORGE_CHAT_HARNESS", val)
    assert chat_harness_enabled() is True


def test_provider_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("METAFORGE_LLM_PROVIDER", "METAFORGE_LLM_MODEL", "METAFORGE_LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    cfg = provider_config_from_env()
    specs = cfg.slots.candidates("generator")
    assert specs[0] == ProviderSpec(
        name="anthropic",
        model="claude-opus-4-8",
        api_key_env="METAFORGE_LLM_API_KEY",
        max_context_tokens=200_000,  # MET-655 remainder: now populated for every candidate
    )


def test_provider_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("METAFORGE_LLM_MODEL", "meta-llama/llama-4")
    monkeypatch.setenv("METAFORGE_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    spec = provider_config_from_env().slots.candidates("generator")[0]
    assert spec.name == "openrouter" and spec.base_url == "https://openrouter.ai/api/v1"


def test_a_stored_selection_predating_validation_is_ignored_not_resent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """FORGE-93 re-test: PUT /v1/harness/selection now rejects a new invalid
    openai-codex + slashed-model pairing, but a pairing stored *before* that
    validation existed stayed durable -- every default-provider call kept
    400ing then silently falling back to OpenRouter forever. The stored
    selection must be re-validated on read, not just on write."""
    monkeypatch.setenv("METAFORGE_HARNESS_AUTH_PATH", str(tmp_path / "harness-auth.json"))
    for k in ("METAFORGE_LLM_PROVIDER", "METAFORGE_LLM_MODEL", "METAFORGE_LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    # Bypass set_selection's own (now-fixed) validation -- write the invalid
    # pairing directly, exactly as it would already sit in an old store.
    store = AuthStore()
    store._selection = Selection(provider="openai-codex", model="openai/gpt-4o")
    store._save()

    cfg = provider_config_from_env()
    spec = cfg.slots.candidates("generator")[0]
    assert spec.model != "openai/gpt-4o"


def test_a_stored_selection_with_a_valid_model_is_still_honored(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("METAFORGE_HARNESS_AUTH_PATH", str(tmp_path / "harness-auth.json"))
    for k in ("METAFORGE_LLM_PROVIDER", "METAFORGE_LLM_MODEL", "METAFORGE_LLM_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    AuthStore().set_selection("openai-codex", "gpt-5.5")

    cfg = provider_config_from_env()
    spec = cfg.slots.candidates("generator")[0]
    assert spec.model == "gpt-5.5"


# --- FORGE-98: post-turn grounding guard ------------------------------------
# Live repro: a turn made ZERO tool calls (no tool events in the gateway log)
# yet the final reply claimed "Assembled the available parts... Added
# revolute joints J1 to J6... Part Count: 6 components." Nothing existed.


def _step_with_successful_tool_call() -> ReActStep:
    return ReActStep(
        thought="calling a tool",
        tool_call=ToolCall(name="twin.commit_geometry", arguments={}),
        observation={"ok": True},
        error=None,
    )


def _step_with_failed_tool_call() -> ReActStep:
    return ReActStep(
        thought="calling a tool",
        tool_call=ToolCall(name="twin.commit_geometry", arguments={}),
        observation=None,
        error="boom",
    )


def _step_with_no_tool_call() -> ReActStep:
    return ReActStep(thought="just reasoning", tool_call=None)


class TestFlagUnfoundedCompletionClaim:
    def test_flags_a_completion_claim_with_zero_tool_calls(self) -> None:
        answer = "Assembled the available parts and added revolute joints J1 to J6."
        flagged = _flag_if_unfounded_completion_claim(answer, [])
        assert flagged.startswith("⚠")
        assert answer in flagged

    def test_flags_when_every_tool_call_this_turn_failed(self) -> None:
        answer = "Created the bracket and committed it to the twin."
        flagged = _flag_if_unfounded_completion_claim(answer, [_step_with_failed_tool_call()])
        assert flagged.startswith("⚠")

    def test_does_not_flag_when_a_tool_call_succeeded(self) -> None:
        answer = "Created the bracket and committed it to the twin."
        flagged = _flag_if_unfounded_completion_claim(answer, [_step_with_successful_tool_call()])
        assert flagged == answer

    def test_does_not_flag_plain_conversational_replies(self) -> None:
        answer = "The safety factor formula is yield strength divided by max stress."
        flagged = _flag_if_unfounded_completion_claim(answer, [_step_with_no_tool_call()])
        assert flagged == answer

    def test_empty_answer_is_never_flagged(self) -> None:
        assert _flag_if_unfounded_completion_claim("", []) == ""

    def test_a_reasoning_only_step_does_not_count_as_a_tool_call(self) -> None:
        answer = "Generated the enclosure geometry."
        flagged = _flag_if_unfounded_completion_claim(answer, [_step_with_no_tool_call()])
        assert flagged.startswith("⚠")


@pytest.mark.asyncio
async def test_run_chat_turn_flags_a_zero_tool_call_completion_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("METAFORGE_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")  # ReAct JSON-final path

    async def fake_invoke(spec: ProviderSpec, request: object) -> dict:
        return {
            "text": (
                '{"thought": "done", "final": '
                '"Assembled the gripper and committed it to the twin."}'
            ),
            "model": spec.model,
        }

    out = await run_chat_turn("assemble the gripper", invoke=fake_invoke)
    assert out.startswith("⚠")
    assert "Assembled the gripper" in out


@pytest.mark.asyncio
async def test_run_chat_turn_returns_final(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")  # asserts ReAct JSON-final extraction

    async def fake_invoke(spec: ProviderSpec, request: object) -> dict:
        return {
            "text": '{"thought": "easy", "final": "hello from the harness"}',
            "model": spec.model,
        }

    out = await run_chat_turn("say hi", invoke=fake_invoke)
    assert out == "hello from the harness"


@pytest.mark.asyncio
async def test_run_chat_turn_exhaustion_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METAFORGE_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")  # asserts ReAct exhaustion path

    async def never_final(spec: ProviderSpec, request: object) -> dict:
        # Always proposes a (nonexistent) tool, never finalizes -> exhaust.
        return {"text": '{"tool": "noop", "arguments": {}}', "model": spec.model}

    out = await run_chat_turn("loop forever", invoke=never_final, max_steps=2)
    # Production-harness audit follow-up: a step-cap exhaustion now returns a
    # real trajectory summary (what was attempted) instead of a bare
    # "couldn't converge" non-answer that threw away the full trace.
    assert "ran out of turns" in out
    assert "noop" in out


@pytest.mark.asyncio
async def test_run_chat_turn_rotates_stored_credentials(monkeypatch, tmp_path):
    from orchestrator.harness.providers import Credential, CredentialStore
    from orchestrator.harness.providers.pipeline import ProviderError

    monkeypatch.setenv("METAFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("METAFORGE_LLM_MODEL", "x")
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")  # fake emits ReAct JSON-final
    store = CredentialStore(tmp_path / "c.json")
    store.add(Credential(provider="openrouter", name="a", api_key_env="KEY_A"))
    store.add(Credential(provider="openrouter", name="b", api_key_env="KEY_B"))

    async def invoke(spec: ProviderSpec, request: object) -> dict:
        if spec.api_key_env == "KEY_A":
            raise ProviderError("revoked", status_code=401)
        return {"text": '{"final": "ok via B"}', "model": spec.model}

    out = await run_chat_turn("hi", invoke=invoke, credentials=store)
    assert out == "ok via B"
    assert [c.name for c in store.healthy("openrouter")] == ["b"]  # A blacklisted


@pytest.mark.asyncio
async def test_run_chat_turn_react_path_receives_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MET-565 regression: run_chat_turn accepted ``history`` but the ReAct
    branch never passed it to ModelPolicy, so non-native-tools providers got
    zero conversation context (and zero project brief) every turn."""
    monkeypatch.delenv("METAFORGE_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "false")

    seen: dict[str, object] = {}

    async def fake_invoke(spec: ProviderSpec, request: object) -> dict:
        seen["content"] = request["messages"][-1]["content"]  # type: ignore[index]
        return {"text": '{"thought": "recall", "final": "40mm"}', "model": spec.model}

    history = [
        {"role": "user", "content": "the bracket is 40mm wide"},
        {"role": "assistant", "content": "Understood."},
    ]
    out = await run_chat_turn("how wide?", invoke=fake_invoke, history=history)
    assert out == "40mm"
    assert "the bracket is 40mm wide" in str(seen["content"])


# ---------------------------------------------------------------------------
# chat.set_project_scope — the agent's side of MET-580
# ---------------------------------------------------------------------------


class _FakeProjectBackend:
    def __init__(self, projects: list[ProjectResponse]) -> None:
        self._projects = projects

    async def list_projects(self) -> list[ProjectResponse]:
        return list(self._projects)

    async def get_project(self, project_id: str) -> ProjectResponse | None:
        return next((p for p in self._projects if p.id == project_id), None)


def _project(id_: str, name: str) -> ProjectResponse:
    return ProjectResponse(
        id=id_,
        name=name,
        description="",
        status="active",
        work_products=[],
        last_updated="2026-07-01T00:00:00Z",
        created_at="2026-07-01T00:00:00Z",
    )


@pytest.fixture(autouse=True)
def _wire_project_backend_for_scope_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.projects.routes as projects_routes

    monkeypatch.setattr(
        projects_routes,
        "_backend",
        _FakeProjectBackend([_project("p-123", "Pan-Tilt Gimbal")]),
    )


@pytest.mark.asyncio
async def test_scope_tool_switches_project() -> None:
    backend = InMemoryChatBackend.create()
    channel = await backend.channel_for_scope("assistant")
    thread = await backend.create_thread(
        channel_id=channel.id, scope_kind="assistant", scope_entity_id="e1", title="t"
    )

    tool = make_set_project_scope_tool(thread.id, backend)
    result = await tool.handler({"project": "gimbal"})

    assert result["scope_kind"] == "project"
    assert result["project_id"] == "p-123"
    assert result["project_name"] == "Pan-Tilt Gimbal"
    updated = await backend.get_thread(thread.id)
    assert updated.scope_kind == "project" and updated.scope_entity_id == "p-123"


@pytest.mark.asyncio
async def test_scope_tool_refuses_ambiguous_project(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_gateway.projects.routes as projects_routes

    monkeypatch.setattr(
        projects_routes,
        "_backend",
        _FakeProjectBackend([_project("p-1", "Monitor Build"), _project("p-2", "Monitor Test")]),
    )
    backend = InMemoryChatBackend.create()
    channel = await backend.channel_for_scope("assistant")
    thread = await backend.create_thread(
        channel_id=channel.id, scope_kind="assistant", scope_entity_id="e1", title="t"
    )
    tool = make_set_project_scope_tool(thread.id, backend)

    with pytest.raises(ValueError, match="matches 2 projects"):
        await tool.handler({"project": "monitor"})

    # Refused, so the thread must be untouched — the model never guesses.
    unchanged = await backend.get_thread(thread.id)
    assert unchanged.scope_kind == "assistant"


@pytest.mark.asyncio
async def test_scope_tool_detaches_to_assistant_scope() -> None:
    backend = InMemoryChatBackend.create()
    proj_channel = await backend.channel_for_scope("project")
    thread = await backend.create_thread(
        channel_id=proj_channel.id, scope_kind="project", scope_entity_id="p-123", title="t"
    )
    tool = make_set_project_scope_tool(thread.id, backend)

    result = await tool.handler({"project": "none"})

    assert result["scope_kind"] == "assistant"
    assert result["project_id"] is None
    updated = await backend.get_thread(thread.id)
    assert updated.scope_kind == "assistant"


@pytest.mark.asyncio
async def test_scope_tool_requires_project_argument() -> None:
    backend = InMemoryChatBackend.create()
    tool = make_set_project_scope_tool("t1", backend)
    with pytest.raises(ValueError, match="'project' is required"):
        await tool.handler({})


@pytest.mark.asyncio
async def test_build_context_registers_scope_tool_only_when_backend_given() -> None:
    store = CredentialStore()
    backend = InMemoryChatBackend.create()

    with_backend = await _build_context("t1", store, None, chat_backend=backend)
    tool = with_backend.runtime.tools.get("chat.set_project_scope")
    assert tool.origin == "native"

    without_backend = await _build_context("t1", store, None)
    from orchestrator.harness.tools import ToolNotFoundError

    with pytest.raises(ToolNotFoundError):
        without_backend.runtime.tools.get("chat.set_project_scope")
