"""Sketch understanding: a vision model proposes, a person confirms (FORGE-762).

A person uploads a hand sketch. A vision model reads it into parts,
connections, rough proportions and open questions. None of that is a record
of truth: the image is illustrative, every proportion is an assumption, and
the reading is only concept input once a person has confirmed it.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from api_gateway.twin.claim_recorder import is_illustrative, make_claim_recorder
from api_gateway.twin.sketch_reading import (
    SketchReadingError,
    SketchVisionUnavailableError,
    VisionReply,
    concept_inputs_block,
    confirmed_sketch_readings,
    make_default_vision_call,
    make_sketch_interpreter,
    make_sketch_reading_decider,
    parse_reading,
)
from mcp_core.guardrails import (
    APPROVED_BY_ARG,
    ApproverArgumentRejectedError,
    Caller,
    HumanAuthorityRequiredError,
    decide,
    requires_human_authority,
)
from orchestrator.harness.providers import ProviderSpec, RetryPolicy, RoleModelSlots
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, WorkProductType

PROJECT = str(uuid4())
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

READING: dict[str, Any] = {
    "summary": "A small stool: a round seat on three splayed legs.",
    "parts": [
        {"name": "seat", "description": "round top"},
        {"name": "leg", "description": "splayed leg", "count": 3},
    ],
    "connections": [{"from_part": "leg", "to_part": "seat", "joint_type": "dowelled"}],
    "proportions": [
        {"subject": "seat diameter", "value": 300, "unit": "mm", "tag": "assumption"},
        {"subject": "leg length", "value": 1.5, "unit": "ratio", "relative_to": "seat"},
    ],
    "open_questions": ["Is there a stretcher between the legs?"],
}


def _fake_vlm(payload: Any) -> Any:
    calls: list[tuple[bytes, str, str]] = []

    async def call(image: bytes, mime: str, prompt: str) -> VisionReply:
        calls.append((image, mime, prompt))
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return VisionReply(text=text, provider="fake", model="fake-vlm")

    call.calls = calls  # type: ignore[attr-defined]
    return call


@pytest.fixture(autouse=True)
def _no_object_store(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    def fake_store(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
        return f"work-products/{node_id}/{filename}"

    monkeypatch.setattr(blobs, "store_work_product_blob", fake_store)


async def _interpret(twin: InMemoryTwinAPI, payload: Any = READING, **kw: Any) -> dict[str, Any]:
    interpret = make_sketch_interpreter(twin, vision_call=_fake_vlm(payload))
    result: dict[str, Any] = await interpret(
        name="Stool sketch",
        image_bytes=PNG,
        mime_type="image/png",
        note="a stool for the kitchen",
        project_id=PROJECT,
        **kw,
    )
    return result


# ── The proposal ──────────────────────────────────────────────────────────


async def test_a_valid_reading_is_stored_as_a_proposal_on_an_illustrative_image() -> None:
    twin = InMemoryTwinAPI.create()
    out = await _interpret(twin)

    assert out["status"] == "proposed"
    assert out["authority"] == "illustrative"
    assert [p["name"] for p in out["reading"]["parts"]] == ["seat", "leg"]
    assert out["read_by"] == {"provider": "fake", "model": "fake-vlm"}

    wp = await twin.get_work_product(UUID(out["node_id"]))
    assert wp.type == WorkProductType.IMAGE
    assert wp.metadata["authority"] == "illustrative"
    assert wp.metadata["mime_type"] == "image/png"
    assert is_illustrative(wp)
    stored = wp.metadata["sketch_reading"]
    assert stored["status"] == "proposed"
    assert stored["decided_by"] is None
    assert stored["note"] == "a stool for the kitchen"


async def test_the_note_reaches_the_model_with_the_image() -> None:
    vlm = _fake_vlm(READING)
    interpret = make_sketch_interpreter(InMemoryTwinAPI.create(), vision_call=vlm)
    await interpret(image_bytes=PNG, mime_type="image/png", note="three legs")
    image, mime, prompt = vlm.calls[0]
    assert image == PNG and mime == "image/png"
    assert "three legs" in prompt
    assert "assumption" in prompt


async def test_proportions_are_assumptions_even_when_the_model_says_verified() -> None:
    claimed = json.loads(json.dumps(READING))
    claimed["proportions"][0]["tag"] = "verified"
    claimed["proportions"][1]["tag"] = "measured"
    out = await _interpret(InMemoryTwinAPI.create(), claimed)
    assert {p["tag"] for p in out["reading"]["proportions"]} == {"assumption"}
    assert parse_reading(json.dumps(claimed)).proportions[0].tag == "assumption"


@pytest.mark.parametrize(
    "reply",
    [
        "I see a stool.",
        "{not json at all",
        "",
        json.dumps({"parts": []}),  # no summary
        json.dumps({**READING, "parts": "seat"}),
        json.dumps(
            {**READING, "connections": [{"from_part": "leg", "to_part": "backrest"}]}
        ),  # a joint to a part it never listed
    ],
)
async def test_bad_model_output_is_refused_cleanly_and_nothing_is_stored(reply: str) -> None:
    twin = InMemoryTwinAPI.create()
    with pytest.raises(SketchReadingError):
        await _interpret(twin, reply)
    assert await twin.list_work_products(work_product_type=WorkProductType.IMAGE) == []


async def test_json_in_a_code_fence_is_read() -> None:
    reading = parse_reading("```json\n" + json.dumps(READING) + "\n```")
    assert reading.summary.startswith("A small stool")


async def test_an_svg_or_unknown_type_is_refused_before_the_model_is_asked() -> None:
    vlm = _fake_vlm(READING)
    interpret = make_sketch_interpreter(InMemoryTwinAPI.create(), vision_call=vlm)
    with pytest.raises(ValueError, match="unsupported image type"):
        await interpret(image_bytes=b"<svg/>", mime_type="image/svg+xml")
    assert vlm.calls == []


async def test_an_uploaded_image_work_product_can_be_read(tmp_path: Any) -> None:
    from twin_core.models.work_product import WorkProduct

    path = tmp_path / "stool.png"
    path.write_bytes(PNG)
    twin = InMemoryTwinAPI.create()
    wp = await twin.create_work_product(
        WorkProduct(
            name="stool.png",
            type=WorkProductType.IMAGE,
            domain="mechanical",
            file_path=str(path),
            content_hash="h",
            format="png",
            created_by="upload",
            project_id=UUID(PROJECT),
        )
    )
    vlm = _fake_vlm(READING)
    out = await make_sketch_interpreter(twin, vision_call=vlm)(work_product_id=str(wp.id))
    assert out["node_id"] == str(wp.id)
    assert vlm.calls[0][0] == PNG
    again = await twin.get_work_product(wp.id)
    assert again.metadata["authority"] == "illustrative"
    assert again.metadata["sketch_reading"]["status"] == "proposed"


# ── Confirm and reject ────────────────────────────────────────────────────


async def test_a_person_confirms_and_the_reading_becomes_concept_input() -> None:
    twin = InMemoryTwinAPI.create()
    out = await _interpret(twin)
    assert await confirmed_sketch_readings(twin, PROJECT) == []

    decided = await make_sketch_reading_decider(twin)(
        out["node_id"], decision="confirm", decided_by="user:ada", reason="matches my idea"
    )
    assert decided["status"] == "confirmed"
    assert decided["decided_by"] == "user:ada"

    wp = await twin.get_work_product(UUID(out["node_id"]))
    assert wp.metadata["sketch_reading"]["status"] == "confirmed"
    assert wp.metadata["sketch_reading"]["decided_by"] == "user:ada"
    assert wp.metadata["authority"] == "illustrative"  # confirming does not verify

    offered = await confirmed_sketch_readings(twin, PROJECT)
    assert [r["node_id"] for r in offered] == [out["node_id"]]
    block = concept_inputs_block(offered)
    assert "confirmed by a person" in block
    assert "assumption: seat diameter 300 mm" in block
    assert "open: Is there a stretcher" in block


async def test_a_rejected_or_unconfirmed_reading_is_never_offered() -> None:
    twin = InMemoryTwinAPI.create()
    rejected = await _interpret(twin)
    await _interpret(twin)  # left proposed
    await make_sketch_reading_decider(twin)(
        rejected["node_id"], decision="reject", decided_by="user:ada"
    )
    wp = await twin.get_work_product(UUID(rejected["node_id"]))
    assert wp.metadata["sketch_reading"]["status"] == "rejected"
    assert await confirmed_sketch_readings(twin, PROJECT) == []
    assert concept_inputs_block([]) == ""


async def test_a_decided_reading_cannot_be_decided_again() -> None:
    twin = InMemoryTwinAPI.create()
    out = await _interpret(twin)
    decide_reading = make_sketch_reading_decider(twin)
    await decide_reading(out["node_id"], decision="confirm", decided_by="user:ada")
    with pytest.raises(ValueError, match="already confirmed"):
        await decide_reading(out["node_id"], decision="reject", decided_by="user:bob")


async def test_a_decision_needs_a_person_and_a_known_verdict() -> None:
    twin = InMemoryTwinAPI.create()
    out = await _interpret(twin)
    decide_reading = make_sketch_reading_decider(twin)
    with pytest.raises(ValueError, match="person"):
        await decide_reading(out["node_id"], decision="confirm", decided_by=" ")
    with pytest.raises(ValueError, match="confirm' or 'reject"):
        await decide_reading(out["node_id"], decision="verify", decided_by="user:ada")


# ── Never evidence ────────────────────────────────────────────────────────


async def test_the_stored_image_cannot_back_a_claim_even_once_confirmed() -> None:
    twin = InMemoryTwinAPI.create()
    out = await _interpret(twin)
    await make_sketch_reading_decider(twin)(
        out["node_id"], decision="confirm", decided_by="user:ada"
    )
    requirement = await twin.create_constraint(
        Constraint(
            name="seat_height_max",
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mechanical",
            source="test",
            project_id=UUID(PROJECT),
        )
    )
    with pytest.raises(ValueError, match="illustrative"):
        await make_claim_recorder(twin)(
            requirement_ref=requirement.name, artefact_ref=out["node_id"], project_id=PROJECT
        )


# ── The confirm tool is a person's ────────────────────────────────────────


def test_confirming_is_a_human_authority_tool_held_even_locally() -> None:
    assert requires_human_authority("twin.confirm_sketch_reading")
    assert decide(
        "twin.confirm_sketch_reading", caller=Caller.LOCAL, exempt_local_writes=True
    ).requires_approval
    assert decide("twin.confirm_sketch_reading", caller=Caller.SERVICE).refused
    assert not requires_human_authority("twin.interpret_sketch")


def _server(twin: InMemoryTwinAPI) -> Any:
    from tool_registry.tools.twin.adapter import TwinServer

    return TwinServer(
        twin=twin,
        sketch_interpreter=make_sketch_interpreter(twin, vision_call=_fake_vlm(READING)),
        sketch_reading_decider=make_sketch_reading_decider(twin),
    )


async def test_the_tools_register_and_the_model_cannot_name_the_decider() -> None:
    twin = InMemoryTwinAPI.create()
    server = _server(twin)
    assert {"twin.interpret_sketch", "twin.confirm_sketch_reading"} <= set(server.tool_ids)

    out = await server.interpret_sketch(
        {
            "image_base64": base64.b64encode(PNG).decode(),
            "mime_type": "image/png",
            "project_id": PROJECT,
        }
    )
    assert out["status"] == "proposed"

    with pytest.raises(ApproverArgumentRejectedError):
        await server.confirm_sketch_reading(
            {"node_id": out["node_id"], "decision": "confirm", "decided_by": "the model"}
        )
    with pytest.raises(HumanAuthorityRequiredError):
        await server.confirm_sketch_reading({"node_id": out["node_id"], "decision": "confirm"})

    done = await server.confirm_sketch_reading(
        {"node_id": out["node_id"], "decision": "confirm", APPROVED_BY_ARG: "user:ada"}
    )
    assert done == {**done, "status": "confirmed", "decided_by": "user:ada"}


async def test_the_interpret_tool_wants_exactly_one_image_source() -> None:
    server = _server(InMemoryTwinAPI.create())
    with pytest.raises(ValueError, match="exactly one"):
        await server.interpret_sketch({})
    with pytest.raises(ValueError, match="valid base64"):
        await server.interpret_sketch({"image_base64": "***", "mime_type": "image/png"})


# ── The model call itself ─────────────────────────────────────────────────


class _Config:
    def __init__(self, specs: list[ProviderSpec]) -> None:
        self.slots = RoleModelSlots(slots={"generator": specs})
        self.retry = RetryPolicy(api_max_retries=0)


async def test_no_vision_capable_provider_fails_with_a_clear_message() -> None:
    call = make_default_vision_call(
        provider_config=_Config([ProviderSpec(name="gemini", model="gemini-2.5-pro")])
    )
    with pytest.raises(SketchVisionUnavailableError, match="no vision-capable model provider"):
        await call(PNG, "image/png", "read it")


async def test_an_unconfigured_provider_chain_fails_cleanly() -> None:
    class _Broken:
        @property
        def slots(self) -> Any:
            raise KeyError("no provider configured for role 'generator'")

    call = make_default_vision_call(provider_config=_Broken())
    with pytest.raises(SketchVisionUnavailableError, match="no model provider is configured"):
        await call(PNG, "image/png", "read it")


async def test_a_silent_provider_times_out_rather_than_hanging() -> None:
    async def slow(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        await asyncio.sleep(10)
        return {"text": "{}"}

    call = make_default_vision_call(
        invoke=slow,
        provider_config=_Config([ProviderSpec(name="anthropic", model="claude-x")]),
        timeout_s=0.05,
    )
    with pytest.raises(SketchVisionUnavailableError, match="within"):
        await call(PNG, "image/png", "read it")


async def test_a_failing_provider_is_reported_not_raised_raw() -> None:
    from orchestrator.harness.providers import ProviderError

    async def broken(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        raise ProviderError("missing API key in env 'ANTHROPIC_API_KEY'")

    call = make_default_vision_call(
        invoke=broken,
        provider_config=_Config([ProviderSpec(name="anthropic", model="claude-x")]),
    )
    with pytest.raises(SketchVisionUnavailableError, match="missing API key"):
        await call(PNG, "image/png", "read it")


@pytest.mark.parametrize(
    ("family", "block_type"), [("anthropic", "image"), ("openrouter", "image_url")]
)
async def test_the_image_is_sent_in_the_providers_own_block(family: str, block_type: str) -> None:
    sent: list[Any] = []

    async def invoke(spec: ProviderSpec, request: Any) -> dict[str, Any]:
        sent.append(request)
        return {"text": json.dumps(READING)}

    call = make_default_vision_call(
        invoke=invoke,
        provider_config=_Config(
            [ProviderSpec(name="gemini", model="g"), ProviderSpec(name=family, model="m")]
        ),
    )
    reply = await call(PNG, "image/png", "read it")
    assert isinstance(reply, VisionReply)
    assert (reply.provider, reply.model) == (family, "m")
    content = sent[0]["messages"][0]["content"]
    assert content[0]["type"] == block_type
    assert content[1] == {"type": "text", "text": "read it"}


# ── The concept phase sees only confirmed readings ────────────────────────


async def test_the_concept_phase_prompt_carries_only_confirmed_readings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api_gateway.twin.routes as twin_routes
    from api_gateway.runs import flow_brain
    from orchestrator.design_flow.executor import FlowContext
    from orchestrator.design_flow.templates import load_templates

    twin = InMemoryTwinAPI.create()
    monkeypatch.setattr(twin_routes, "get_twin", lambda: twin)
    phases = {p.id: p for p in load_templates()["mech_v1"].definition.phases}
    concept, design = phases["concept_selection"], phases["design"]
    ctx = FlowContext(goal="a stool", project_id=PROJECT)

    out = await _interpret(twin)
    assert await flow_brain._phase_inputs(concept, ctx) == ""

    await make_sketch_reading_decider(twin)(
        out["node_id"], decision="confirm", decided_by="user:ada"
    )
    block = await flow_brain._phase_inputs(concept, ctx)
    assert "A small stool" in block
    assert await flow_brain._phase_inputs(design, ctx) == ""

    prompt = flow_brain.ReActPhaseBrain(mcp_bridge=None)._prompt("a stool", concept, ctx, block)
    assert "confirmed by a person" in prompt
