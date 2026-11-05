"""Change classification before an authoritative edit (spec section 5).

The classifier: every section 5 example and the edge cases around them, the
rule pass first and the model only when the rules are not sure (mocked
here). The handling: an appearance request updates the appearance record and
nothing else, and the look survives a regenerated mesh (SYNC-007); "metallic"
asks; a material change proposes a manufacturing context update whose
confirmation walks the impact graph; performance and style requests propose
alternatives. The guard: in an appearance turn, a CAD rebuild and a
manufacturing context write are refused, in the tool layer and in the
services. The route and the operation event.

Nothing here calls a model provider.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.chat import routes as chat_routes
from api_gateway.chat.backend import InMemoryChatBackend
from api_gateway.chat.change_turn import classify_turn
from api_gateway.chat.harness_backend import mcp_tools_from_bridge
from api_gateway.chat.models import ChatMessageRecord
from api_gateway.operations import publisher as ops
from api_gateway.twin import appearance as apr
from api_gateway.twin import change_classification as cc
from api_gateway.twin import manufacturing_context as mc
from api_gateway.twin import routes as twin_routes
from api_gateway.twin.change_classification import (
    AppearanceValues,
    ChangeClass,
    ChangeGuardRefusedError,
    classify_change,
    classify_rules,
    turn_classification,
)
from api_gateway.twin.change_classification_routes import router as classify_router
from api_gateway.twin.exact_cad import ExactCadService
from twin_core.api import InMemoryTwinAPI
from twin_core.items import find_item

PROJECT = "5b0e0c4e-8a51-4f7c-9b3e-2d0c6f1a7e11"


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture(autouse=True)
def publisher() -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cc.LLM_ENV, raising=False)


# ── the rule pass: section 5 ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Change the enclosure colour", ChangeClass.APPEARANCE),
        ("Make it metallic", ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL),
        ("Make it wider", ChangeClass.GEOMETRIC),
        ("Move the opening", ChangeClass.GEOMETRIC),
        ("Add ventilation holes", ChangeClass.ENGINEERING_GEOMETRY),
        ("Use aluminium instead of plastic", ChangeClass.MATERIAL_ENGINEERING),
        ("Make it more aerodynamic", ChangeClass.PERFORMANCE_OBJECTIVE),
        ("Make it futuristic", ChangeClass.AMBIGUOUS_STYLE),
    ],
)
def test_every_section_5_example(text: str, expected: ChangeClass) -> None:
    result = classify_rules(text)
    assert result.change_class is expected
    assert result.confidence >= cc.UNCERTAIN_BELOW
    assert result.handling == cc.HANDLING[expected]
    assert result.message


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("make it red", ChangeClass.APPEARANCE),
        ("turn it blue", ChangeClass.APPEARANCE),
        ("matte black finish please", ChangeClass.APPEARANCE),
        ("make it a lighter blue", ChangeClass.APPEARANCE),
        ("paint it #1E88E5", ChangeClass.APPEARANCE),
        ("Can you make it red?", ChangeClass.APPEARANCE),
        ("make it chrome", ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL),
        ("give it a wood look", ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL),
        ("make it gold", ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL),
        ("brushed aluminium finish", ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL),
        ("make it 120 mm wide", ChangeClass.GEOMETRIC),
        ("round the front edges and move the speaker opening up", ChangeClass.GEOMETRIC),
        ("make the walls thicker", ChangeClass.GEOMETRIC),
        ("add four mounting bosses", ChangeClass.ENGINEERING_GEOMETRY),
        ("add some ribs to the base", ChangeClass.ENGINEERING_GEOMETRY),
        ("switch from PLA to PETG", ChangeClass.MATERIAL_ENGINEERING),
        ("use copper for the heat spreader", ChangeClass.MATERIAL_ENGINEERING),
        ("change the material", ChangeClass.MATERIAL_ENGINEERING),
        ("make it stronger", ChangeClass.PERFORMANCE_OBJECTIVE),
        ("make it lighter", ChangeClass.PERFORMANCE_OBJECTIVE),
        ("make it run cooler", ChangeClass.PERFORMANCE_OBJECTIVE),
        ("make it look more premium", ChangeClass.AMBIGUOUS_STYLE),
        ("something more retro", ChangeClass.AMBIGUOUS_STYLE),
        ("What material is the lid?", ChangeClass.NOT_A_CHANGE),
        ("why is the base so heavy?", ChangeClass.NOT_A_CHANGE),
        ("how strong is the bracket?", ChangeClass.NOT_A_CHANGE),
    ],
)
def test_edge_cases(text: str, expected: ChangeClass) -> None:
    assert classify_rules(text).change_class is expected


def test_appearance_names_the_look_and_says_the_model_stays() -> None:
    red = classify_rules("make it red")
    assert red.appearance is not None
    assert red.appearance.colour == "#c62828" and red.appearance.colour_name == "red"
    assert red.message == "Colour change only. Your engineering model stays the same."
    finish = classify_rules("make it matte")
    assert finish.appearance is not None and finish.appearance.finish == "matte"
    assert finish.message.startswith("Finish change only.")
    hexed = classify_rules("paint it #1E88E5")
    assert hexed.appearance is not None and hexed.appearance.colour == "#1e88e5"


def test_metallic_asks_the_look_or_the_material() -> None:
    result = classify_rules("Make it metallic")
    assert result.change_class is ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL
    assert result.handling == "ask_look_or_material"
    assert result.choices == ["The look", "The material"]
    assert "the look" in result.message.lower() and "material" in result.message.lower()
    assert "Ask exactly that" in cc.agent_instruction(result)


def test_the_persons_choice_decides() -> None:
    look = classify_rules("make it metallic\n\nChange kind (chosen by the person): appearance")
    assert look.change_class is ChangeClass.APPEARANCE
    assert look.stage == "person" and look.confidence == 1.0
    assert look.appearance is not None and look.appearance.render_material == "metallic_paint"
    material = classify_rules("make it copper\n\nChange kind (chosen by the person): material")
    assert material.change_class is ChangeClass.MATERIAL_ENGINEERING
    assert material.proposed_context_update == {"selected_material": "Copper"}


def test_the_new_material_is_the_one_proposed() -> None:
    assert classify_rules("Use aluminium instead of plastic").proposed_context_update == {
        "selected_material": "Aluminium"
    }
    assert classify_rules("use plastic instead of aluminium").proposed_context_update == {
        "selected_material": "Plastic"
    }
    assert classify_rules("switch from PLA to PETG").proposed_context_update == {
        "selected_material": "PETG"
    }
    # A material change that names none proposes none: nothing is guessed.
    assert classify_rules("change the material").proposed_context_update is None


def test_target_component_feature_and_dimensions() -> None:
    result = classify_rules("make it red", component="Lid", component_id="n-1")
    assert result.target_component == "Lid" and result.target_component_id == "n-1"
    moved = classify_rules("round the front edges and move the speaker opening up")
    assert moved.target_feature == "speaker opening"
    assert moved.stated_dimensions == []
    assert "no dimensions" in cc.agent_instruction(moved)
    sized = classify_rules("make it 120 mm wide")
    assert sized.stated_dimensions == ["120 mm"]


def test_a_mixed_request_lets_the_engineering_part_decide() -> None:
    result = classify_rules("make it red and wider")
    assert result.change_class is ChangeClass.GEOMETRIC
    assert result.also == [ChangeClass.APPEARANCE]
    assert result.appearance is not None and result.appearance.colour_name == "red"


def test_the_client_context_is_not_classified() -> None:
    sent = (
        "make it red\n\nAttached twin work products (references, not inline image content): "
        '["a"]\nUse only the explicitly selected tools for this request.'
    )
    assert cc.strip_client_suffixes(sent) == "make it red"
    assert classify_rules(sent).change_class is ChangeClass.APPEARANCE


def test_an_unrecognised_request_is_unsure_and_not_called_a_question() -> None:
    result = classify_rules("build the exact CAD for the bracket")
    assert result.change_class is ChangeClass.NOT_A_CHANGE
    assert result.uncertain
    assert result.message == cc.UNRECOGNISED_MESSAGE


# ── the model pass, only when the rules are not sure ────────────────────


async def test_the_model_is_asked_only_when_the_rules_are_unsure() -> None:
    prompts: list[str] = []

    async def llm(prompt: str) -> str:
        prompts.append(prompt)
        return '{"class": "engineering_geometry", "confidence": 0.8, "target_feature": "lid"}'

    sure = await classify_change("make it red", use_llm=True, llm=llm)
    assert sure.stage == "rules" and prompts == []

    unsure = await classify_change("put a little place for the cable", use_llm=True, llm=llm)
    assert len(prompts) == 1 and "put a little place for the cable" in prompts[0]
    assert unsure.change_class is ChangeClass.ENGINEERING_GEOMETRY
    assert unsure.stage == "llm" and unsure.confidence == 0.8
    assert unsure.target_feature == "lid"


async def test_the_model_is_off_unless_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def llm(prompt: str) -> str:
        calls.append(prompt)
        return '{"class": "appearance", "confidence": 0.9}'

    off = await classify_change("put a little place for the cable", llm=llm)
    assert off.stage == "rules" and calls == []
    monkeypatch.setenv(cc.LLM_ENV, "on")
    on = await classify_change("put a little place for the cable", llm=llm)
    assert on.stage == "llm" and len(calls) == 1


@pytest.mark.parametrize("reply", ["not json", '{"class": "teleport", "confidence": 1}'])
async def test_an_unusable_model_answer_keeps_the_rules(reply: str) -> None:
    async def llm(prompt: str) -> str:
        return reply

    result = await classify_change("put a little place for the cable", use_llm=True, llm=llm)
    assert result.stage == "rules" and result.change_class is ChangeClass.NOT_A_CHANGE


async def test_a_failing_model_keeps_the_rules() -> None:
    async def llm(prompt: str) -> str:
        raise RuntimeError("provider down")

    result = await classify_change("put a little place for the cable", use_llm=True, llm=llm)
    assert result.stage == "rules"


# ── appearance: the record, and nothing else ────────────────────────────


async def test_an_appearance_update_touches_only_the_appearance_record(
    twin: InMemoryTwinAPI,
) -> None:
    from twin_core.impact.cross_domain import list_impacts

    stored = await apr.set_appearance(
        twin,
        PROJECT,
        AppearanceValues(colour="#c62828", colour_name="red", finish="matte"),
        component_name="Lid",
        component_id="node-old",
        source="chat",
    )
    assert stored.ref == "APR-PROJECT@1"
    [lid] = stored.record.components
    assert (lid.key, lid.colour, lid.finish) == ("lid", "#c62828", "matte")
    # No CAD, mesh or manufacturing context item, and nothing to walk.
    assert await find_item(twin, "MFC-PROJECT", UUID(PROJECT)) is None
    assert await list_impacts(twin, PROJECT) == []
    wp = await twin.get_work_product(UUID(stored.node_id))
    assert wp is not None and wp.metadata["affects_engineering_model"] is False

    # A second change keeps what it does not name and is the next revision.
    again = await apr.set_appearance(
        twin, PROJECT, AppearanceValues(colour="#1565c0", colour_name="blue"), component_name="lid"
    )
    assert again.ref == "APR-PROJECT@2"
    [lid] = again.record.components
    assert (lid.colour, lid.colour_name, lid.finish, lid.node_id) == (
        "#1565c0",
        "blue",
        "matte",
        "node-old",
    )
    product = await apr.set_appearance(twin, PROJECT, AppearanceValues(finish="gloss"))
    assert product.record.product is not None and product.record.product.finish == "gloss"


async def test_the_look_survives_a_regenerated_mesh(twin: InMemoryTwinAPI) -> None:
    await apr.set_appearance(
        twin,
        PROJECT,
        AppearanceValues(colour="#2e7d32", colour_name="green"),
        component_name="Lamp shade",
        component_id="node-l1",
    )
    await apr.set_appearance(twin, PROJECT, AppearanceValues(colour="#151515"))
    stored = await apr.read_appearance(twin, PROJECT)
    assert stored is not None
    record = stored.record
    # The L1 layout mesh it was set on.
    l1 = apr.resolve(record, name="Lamp shade", node_id="node-l1")
    # A new L3 mesh tessellated from exact CAD: a new node, the same component.
    l3 = apr.resolve(record, name="Lamp shade", node_id="node-l3-after-rebuild")
    # A concept mesh that names the component only by its mesh.
    l0 = apr.resolve(record, mesh_name="lamp_shade")
    assert l1 is not None and l3 is not None and l0 is not None
    assert l1.colour == l3.colour == l0.colour == "#2e7d32"
    # A part with no look of its own shows the product's.
    base = apr.resolve(record, name="Base", node_id="node-b")
    assert base is not None and base.colour == "#151515"


async def test_an_empty_or_bad_look_is_refused(twin: InMemoryTwinAPI) -> None:
    with pytest.raises(apr.AppearanceError):
        await apr.set_appearance(twin, PROJECT, AppearanceValues())
    with pytest.raises(ValueError):
        apr.Appearance(colour="red")


# ── the guard ───────────────────────────────────────────────────────────


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"tool_id": "twin.build_exact_cad", "input_schema": {"type": "object"}},
            {"tool_id": "twin.get_node", "input_schema": {"type": "object"}},
        ]

    async def invoke(self, tool_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(tool_id)
        return {"ok": True}


async def _tools(bridge: _Bridge) -> dict[str, Any]:
    return {tdef.name: tdef for _server, tdef in await mcp_tools_from_bridge(bridge)}  # type: ignore[arg-type]


async def test_an_appearance_turn_cannot_rebuild_cad_or_write_the_context(
    twin: InMemoryTwinAPI,
) -> None:
    bridge = _Bridge()
    tools = await _tools(bridge)
    service = ExactCadService(twin, bridge=None, recorder=None)
    with turn_classification(classify_rules("make it red")):
        with pytest.raises(ChangeGuardRefusedError, match="appearance change"):
            await tools["build_exact_cad"].handler({"item_key": "CAD-LID"})
        with pytest.raises(ChangeGuardRefusedError):
            await service.start_build(project_id=PROJECT, item_key="CAD-LID", session_id="s")
        with pytest.raises(ChangeGuardRefusedError, match="manufacturing context"):
            await mc.write_context(
                twin,
                PROJECT,
                {"selected_material": mc.stated("Aluminium", source="api")},
                source="api",
            )
        # Reading is never held.
        assert await tools["get_node"].handler({"node_id": "x"}) == {"ok": True}
    assert bridge.calls == ["twin.get_node"]
    assert await find_item(twin, "MFC-PROJECT", UUID(PROJECT)) is None

    # Outside the turn the same calls go through.
    await tools["build_exact_cad"].handler({"item_key": "CAD-LID"})
    assert bridge.calls[-1] == "twin.build_exact_cad"
    out = await mc.write_context(
        twin, PROJECT, {"selected_material": mc.stated("Aluminium", source="api")}, source="api"
    )
    assert out.revision_created


async def test_an_unanswered_look_or_material_question_holds_both(twin: InMemoryTwinAPI) -> None:
    with turn_classification(classify_rules("make it metallic")):
        with pytest.raises(ChangeGuardRefusedError, match="look or the material"):
            cc.check_change_guard("cad_rebuild")
        with pytest.raises(ChangeGuardRefusedError):
            cc.check_change_guard("manufacturing_context_write")


@pytest.mark.parametrize("text", ["Make it more aerodynamic", "Make it futuristic"])
async def test_performance_and_style_propose_alternatives_without_a_geometry_write(
    text: str,
) -> None:
    result = classify_rules(text)
    assert result.handling in ("propose_alternatives", "explore_alternatives")
    assert "alternatives" in cc.agent_instruction(result)
    assert "stays as it is" in result.message
    bridge = _Bridge()
    tools = await _tools(bridge)
    with turn_classification(result):
        with pytest.raises(ChangeGuardRefusedError, match="alternatives"):
            await tools["build_exact_cad"].handler({"item_key": "CAD-LID"})
        # A performance goal can still lead to a material proposal: not held.
        cc.check_change_guard("manufacturing_context_write")
    assert bridge.calls == []


async def test_a_geometric_turn_is_not_held() -> None:
    with turn_classification(classify_rules("Make it wider")):
        cc.check_change_guard("cad_rebuild")
        cc.check_change_guard("manufacturing_context_write")


# ── material: propose, the person confirms, impact follows ──────────────


async def test_a_material_change_proposes_a_context_update_that_triggers_impact(
    twin: InMemoryTwinAPI,
) -> None:
    from api_gateway.twin.item_revisions import finish_definition_revision
    from twin_core.impact.cross_domain import list_impacts
    from twin_core.items import plan_revision
    from twin_core.models.enums import WorkProductType
    from twin_core.models.interface import InterfaceType
    from twin_core.models.work_product import WorkProduct

    await mc.write_context(
        twin,
        PROJECT,
        {
            "selected_process": mc.stated("FDM", source="api"),
            "selected_material": mc.stated("PLA", source="api"),
        },
        source="api",
    )
    # A concept generated under that context.
    plan = await plan_revision(
        twin,
        item_type="generated_sketch",
        name="Concept",
        project_id=PROJECT,
        author="human",
        item_key="GSK-CONCEPT",
    )
    assert plan is not None
    wp_id = uuid4()
    now = datetime.now(UTC)
    await twin.create_work_product(
        WorkProduct(
            id=wp_id,
            name="Concept",
            type=WorkProductType.GENERATED_SKETCH,
            domain="concept",
            file_path="",
            content_hash=uuid4().hex,
            format="png",
            metadata={"manufacturing_context_ref": "MFC-PROJECT@1", **plan.stamp()},
            created_at=now,
            updated_at=now,
            created_by="human",
            project_id=UUID(PROJECT),
        )
    )
    await finish_definition_revision(twin, plan, wp_id, name="Concept", result={})

    result = classify_rules("Use aluminium instead of plastic")
    assert result.change_class is ChangeClass.MATERIAL_ENGINEERING
    assert result.handling == "propose_material_update"
    assert result.proposed_context_update == {"selected_material": "Aluminium"}
    # Classifying wrote nothing: the proposal waits for the person.
    stored = await mc.read_context(twin, PROJECT)
    assert stored is not None and stored.record.selected_material.value == "PLA"

    # The person confirms it in Create: a stated value, written in a material turn.
    with turn_classification(result):
        out = await mc.write_context(
            twin,
            PROJECT,
            {
                name: mc.stated(value, source="create")
                for name, value in result.proposed_context_update.items()
            },
            source="create",
        )
    assert out.stored is not None
    assert out.stored.record.selected_material.value == "Aluminium"
    [impact] = await list_impacts(twin, PROJECT)
    assert impact.change.item_key == "MFC-PROJECT"
    assert [e.ref for e in impact.impacted] == ["GSK-CONCEPT"]
    assert impact.impacted[0].edge_type is InterfaceType.MANUFACTURED_BY


# ── the chat turn: event, appearance recorded, agent context ────────────


async def test_a_turn_is_classified_on_its_operation_and_a_named_look_recorded(
    twin: InMemoryTwinAPI, publisher: ops.OperationPublisher
) -> None:
    op = "chat:turn-red-1"
    ops.publish(PROJECT, op, "started", "running", kind="chat")
    turn = await classify_turn(
        PROJECT, op, "make the lid red", twin=twin, component_id="node-9", component_label="Lid"
    )
    assert turn is not None
    assert turn.classification.change_class is ChangeClass.APPEARANCE
    assert turn.applied is not None and "APR-PROJECT@1" in turn.applied
    events = [e for e in publisher.store.events_after(PROJECT, 0) if e.operation_id == op]
    stages = [(e.type.value, e.stage_id) for e in events]
    assert ("milestone", "classified") in stages
    assert ("milestone", "appearance_updated") in stages
    classified = next(e for e in events if e.stage_id == "classified")
    assert classified.message.startswith("Colour change only.")
    assert "appearance, confidence 0.92 (rules); routing: update_appearance" in classified.message
    stored = await apr.read_appearance(twin, PROJECT)
    assert stored is not None and stored.record.components[0].name == "Lid"
    block = turn.agent_block()
    assert "class: appearance" in block and "Do not rebuild CAD" in block
    assert "already done" in block


async def test_a_metallic_turn_records_nothing_and_asks(
    twin: InMemoryTwinAPI, publisher: ops.OperationPublisher
) -> None:
    turn = await classify_turn(PROJECT, "chat:turn-m-1", "make it metallic", twin=twin)
    assert turn is not None and turn.applied is None
    assert await apr.read_appearance(twin, PROJECT) is None
    [event] = [e for e in publisher.store.events_after(PROJECT, 0)]
    assert event.stage_id == "classified" and event.message.startswith("Do you mean the look")


@pytest.fixture
def chat_client(
    twin: InMemoryTwinAPI, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, list[Any]]]:
    chat_routes.init_chat_backend(InMemoryChatBackend.create())
    monkeypatch.setattr(chat_routes, "_twin", twin)
    seen: list[Any] = []

    async def fake_agent(thread: Any, content: str, **kw: Any) -> ChatMessageRecord | None:
        classified = kw.get("classified")
        seen.append(classified)
        # What the agent's tool layer sees during the turn.
        with turn_classification(classified.classification if classified else None):
            try:
                cc.check_change_guard("cad_rebuild")
                seen.append("build allowed")
            except ChangeGuardRefusedError:
                seen.append("build held")
        return None

    monkeypatch.setattr(chat_routes, "_invoke_agent", fake_agent)
    app = FastAPI()
    app.include_router(chat_routes.router)
    yield TestClient(app), seen


def test_a_project_chat_turn_is_classified_before_the_agent(
    chat_client: tuple[TestClient, list[Any]], publisher: ops.OperationPublisher
) -> None:
    client, seen = chat_client
    thread = client.post(
        "/v1/chat/threads", json={"scope_kind": "project", "scope_entity_id": PROJECT}
    ).json()
    resp = client.post(
        f"/v1/chat/threads/{thread['id']}/messages",
        json={
            "content": "make it red\nDiscuss using the supplied context.",
            "actor_id": "u",
            "actor_kind": "user",
            "client_turn_id": "ins-red-1",
        },
    )
    assert resp.status_code == 201, resp.text
    classified, verdict = seen
    assert classified.classification.change_class is ChangeClass.APPEARANCE
    assert verdict == "build held"
    events = [e for e in publisher.store.events_after(PROJECT, 0)]
    order = [e.stage_id or e.type.value for e in events]
    assert order.index("classified") < order.index("completed")
    assert all("make it red" not in e.message for e in events)

    assert resp.json()["classification"]["change_class"] == "appearance"

    # A thread with no project is not classified.
    loose = client.post(
        "/v1/chat/threads", json={"scope_kind": "session", "scope_entity_id": "s-1"}
    ).json()
    seen.clear()
    client.post(
        f"/v1/chat/threads/{loose['id']}/messages",
        json={"content": "make it red", "actor_id": "u", "actor_kind": "user"},
    )
    assert seen == [None, "build allowed"]


def test_metallic_is_answered_with_the_look_or_material_choice(
    chat_client: tuple[TestClient, list[Any]], twin: InMemoryTwinAPI
) -> None:
    client, seen = chat_client
    thread = client.post(
        "/v1/chat/threads", json={"scope_kind": "project", "scope_entity_id": PROJECT}
    ).json()
    url = f"/v1/chat/threads/{thread['id']}/messages"
    resp = client.post(
        url, json={"content": "make it metallic", "actor_id": "u", "actor_kind": "user"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["classification"]["change_class"] == "ambiguous_appearance_or_material"
    # No agent turn: the question is asked first, and nothing is recorded.
    assert seen == []
    messages = client.get(f"/v1/chat/threads/{thread['id']}").json()["messages"]
    question = messages[-1]
    assert question["content"].startswith("Do you mean the look")
    [block] = question["blocks"]
    assert block["type"] == "choice"
    assert [o["label"] for o in block["options"]] == ["The look", "The material"]

    # Answering "The look" (the block's value) is an appearance turn on the request.
    look = block["options"][0]["value"]
    answered = client.post(url, json={"content": look, "actor_id": "u", "actor_kind": "user"})
    body = answered.json()["classification"]
    assert body["change_class"] == "appearance" and body["stage"] == "person"
    assert body["appearance"]["render_material"] == "metallic_paint"
    assert seen[-1] == "build held"
    material = classify_rules(block["options"][1]["value"])
    assert material.change_class is ChangeClass.MATERIAL_ENGINEERING


# ── routes ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(twin: InMemoryTwinAPI, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(twin_routes, "_twin", twin)
    app = FastAPI()
    app.include_router(classify_router)
    return TestClient(app)


def test_classify_route(client: TestClient) -> None:
    base = f"/v1/twin/projects/{PROJECT}/classify-change"
    resp = client.post(base, json={"text": "Make it metallic", "component": "Lid"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["change_class"] == "ambiguous_appearance_or_material"
    assert body["choices"] == ["The look", "The material"]
    assert body["target_component"] == "Lid"
    assert body["handling"] == "ask_look_or_material"
    assert client.post(base, json={"text": ""}).status_code == 422
    bad = client.post("/v1/twin/projects/not-a-uuid/classify-change", json={"text": "x"})
    assert bad.status_code == 400


def test_appearance_routes(client: TestClient) -> None:
    base = f"/v1/twin/projects/{PROJECT}/appearance"
    empty = client.get(base).json()
    assert empty["recorded"] is False and empty["ref"] is None
    put = client.put(base, json={"component": "Lid", "colour": "#C62828", "finish": "matte"})
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["ref"] == "APR-PROJECT@1"
    assert body["record"]["components"][0]["colour"] == "#c62828"
    assert client.put(base, json={"component": "Lid"}).status_code == 422
    assert client.put(base, json={"colour": "red"}).status_code == 422
    assert client.get(base).json()["recorded"] is True
