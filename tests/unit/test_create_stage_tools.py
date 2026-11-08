"""Stage-scoped tools on Create design turns (FORGE-825).

A Create design turn used to carry every tool id the gateway knew, and the
harness then cut the array to the provider's 128 by name, silently dropping
reads such as twin.get_manufacturing_context and twin.job_status. Each turn
now names its stage and the gateway resolves that stage's set.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from api_gateway.chat.harness_backend import run_chat_turn_streaming
from api_gateway.chat.schemas import SendMessageRequest
from mcp_core.profiles import (
    CREATE_STAGE_TOOLS,
    CREATE_STAGES,
    MAX_TOOLS,
    UnknownProfileError,
    create_stage_tools,
)
from orchestrator.harness.providers import CredentialStore, ProviderSpec
from skill_registry.mcp_bridge import InMemoryMcpBridge
from tests.unit.test_mcp_tool_profiles import REPO, declared_tool_ids

#: Reads a stage must never lose: the live run's cap dropped these.
_ALWAYS = (
    "twin.get_node",
    "twin.job_status",
    "twin.get_manufacturing_context",
    "twin.imagine_get_studio",
)

#: Decisions that belong to the person: in no stage set.
_PERSON_ONLY = (
    "twin.attempt_promotion",
    "twin.approve_design_loop",
    "twin.approve_engineering_change",
    "twin.approve_engineering_entity",
    "approvals.decide",
    "vendor.place_order",
)


def test_the_stages_are_the_create_views() -> None:
    assert CREATE_STAGES == ("imagine", "develop", "check", "make")
    assert set(CREATE_STAGE_TOOLS) == set(CREATE_STAGES)


@pytest.mark.parametrize("stage", CREATE_STAGES)
def test_every_id_in_a_stage_exists_in_the_registry(stage: str) -> None:
    missing = sorted(create_stage_tools(stage) - declared_tool_ids())
    assert not missing, f"{stage} names tools no adapter registers: {missing}"


@pytest.mark.parametrize("stage", CREATE_STAGES)
def test_a_stage_set_is_well_under_the_cap(stage: str) -> None:
    assert 20 <= len(create_stage_tools(stage)) <= MAX_TOOLS


@pytest.mark.parametrize("stage", CREATE_STAGES)
def test_a_stage_keeps_the_reads_that_matter(stage: str) -> None:
    tools = create_stage_tools(stage)
    for tool in _ALWAYS:
        assert tool in tools
    assert not set(_PERSON_ONLY) & tools


def test_each_stage_carries_its_own_work() -> None:
    assert {"twin.imagine_put_studio", "twin.generate_concept_mesh"} <= CREATE_STAGE_TOOLS[
        "imagine"
    ]
    assert {"twin.design_cad_loop", "cadquery.execute_script", "kicad.generate_schematic"} <= (
        CREATE_STAGE_TOOLS["develop"]
    )
    assert {"twin.get_dfm_report", "calculix.run_fea", "spice.run_simulation"} <= (
        CREATE_STAGE_TOOLS["check"]
    )
    assert {"twin.get_release_checklist", "twin.create_release_package"} <= (
        CREATE_STAGE_TOOLS["make"]
    )
    # The studio is written in Imagine only.
    for stage in ("develop", "check", "make"):
        assert "twin.imagine_put_studio" not in CREATE_STAGE_TOOLS[stage]


def test_the_imagine_playbook_only_names_tools_its_stages_carry() -> None:
    """The chat follows imagine_studio in Imagine, then Develop, Check and Make after handover."""
    text = (REPO / "mcp_core/plugin_skills/imagine_studio/SKILL.md").read_text(encoding="utf-8")
    named = set(re.findall(r"\b((?:twin|kicad|cadquery|firmware|calculix)\.[a-z0-9_]+)", text))
    # FORGE-827: a delegated run carries on through Check and Make too.
    reachable = frozenset().union(*(CREATE_STAGE_TOOLS[s] for s in CREATE_STAGES))
    assert named and not named - reachable


def test_an_unknown_stage_is_refused_rather_than_widened() -> None:
    with pytest.raises(UnknownProfileError, match="imagine"):
        create_stage_tools("ideate")


def test_the_message_body_accepts_only_known_stages() -> None:
    body = SendMessageRequest(content="hi", actor_id="u", actor_kind="user", create_stage="develop")
    assert body.create_stage == "develop"
    with pytest.raises(ValueError):
        SendMessageRequest(content="hi", actor_id="u", actor_kind="user", create_stage="ideate")


def _bridge() -> InMemoryMcpBridge:
    """Every declared tool, as the sandbox gateway lists them (171 there)."""
    bridge = InMemoryMcpBridge()
    for tid in sorted(declared_tool_ids()):
        bridge.register_tool(tid, capability="x", input_schema={"type": "object"})
    return bridge


async def _sent_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, **kwargs: Any
) -> tuple[set[str], int]:
    monkeypatch.setenv("METAFORGE_NATIVE_TOOLS", "1")
    seen: dict[str, Any] = {}

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        seen["tools"] = request.get("tools", [])
        return {"model": spec.model, "text": "done", "tool_calls": []}

    async def on_delta(_: str) -> None:
        return None

    await run_chat_turn_streaming(
        "Start the Imagine studio for this product.",
        on_delta=on_delta,
        invoke=invoke,
        max_steps=2,
        credentials=CredentialStore(tmp_path / "c.json"),
        mcp_bridge=_bridge(),
        ui_blocks=[],
        **kwargs,
    )
    names = {t["function"]["name"] for t in seen["tools"]}
    mcp = {n.removeprefix("mcp_").replace("_", ".", 1) for n in names if n.startswith("mcp_")}
    return mcp, len(json.dumps(seen["tools"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", CREATE_STAGES)
async def test_a_design_turn_sends_its_stage_set(
    stage: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    sent, _ = await _sent_tools(
        monkeypatch,
        tmp_path,
        create_stage=stage,
        # A stale client still sending ids: the stage wins.
        enabled_tools=["web.search"],
    )
    assert sent == set(create_stage_tools(stage))


@pytest.mark.asyncio
async def test_a_stage_turn_is_far_smaller_than_every_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    every, every_bytes = await _sent_tools(monkeypatch, tmp_path)
    imagine, imagine_bytes = await _sent_tools(monkeypatch, tmp_path, create_stage="imagine")
    assert len(every) > 100
    assert len(imagine) <= MAX_TOOLS
    assert imagine_bytes * 3 < every_bytes
