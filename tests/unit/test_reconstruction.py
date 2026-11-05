"""Parametric reconstruction from an accepted concept (spec 4B, 3D-RC-002, DFM-005).

The deterministic mapping (L1 box, cylinder, parametric sketch profiles to
features), no invented dimensions, the DFM strategy note naming the real
profile rules, the optional model pass (mocked, its suggestions labelled and
its numbers dropped), acceptance into parametric sketches that keep
``required_input``, maturity that moves to Parametric only on acceptance, and
the routes. Against the in-memory twin; nothing calls a paid API.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_gateway.create import routes as create_routes
from api_gateway.create.models import (
    Creation,
    FactCitation,
    ReferenceAssociation,
    ReferenceFact,
    ReferenceRecord,
)
from api_gateway.create.store import SqliteCreationStore
from api_gateway.operations import publisher as ops
from api_gateway.twin import manufacturing_context as mc
from api_gateway.twin import reconstruction as rc
from api_gateway.twin import routes as twin_routes
from api_gateway.twin import structured_mesh
from api_gateway.twin.decision_recorder import make_decision_recorder
from api_gateway.twin.reconstruction_routes import router as reconstruction_router
from twin_core.design_ir.feature_history import (
    PRINCIPAL_PLACEMENT_KEY,
    AcceptanceError,
    ContextFacts,
    FactInput,
    ReconstructionInputs,
    accept_features,
    apply_llm_pass,
    deterministic_proposal,
    principal_placement,
)
from twin_core.design_ir.models import SketchCircle, SketchRectangle
from twin_core.design_ir.sketch_compiler import (
    ClosedProfile,
    FeatureParameter,
    FeaturePlanItem,
    ParametricSketch,
)
from twin_core.dfm.profiles import PROFILES
from twin_core.maturity import compute_maturity
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

PROJECT = "7c3e2f10-4b5a-4c6d-8e7f-0a1b2c3d4e5f"
PID = UUID(PROJECT)

LAYOUT: dict[str, Any] = {
    "concept_name": "Desk speaker",
    "parts": [
        {
            "name": "Enclosure",
            "shape": "box",
            "size_mm": [120, 90, 160],
            "position_mm": [0, 0, 20],
            "provenance": "intended",
        },
        {
            "name": "Base",
            "shape": "cylinder",
            "size_mm": [140, 140, 20],
            "position_mm": [-10, -25, 0],
            "provenance": "assumption",
        },
    ],
    "connections": [{"a": "Base", "b": "Enclosure", "joint": "screw"}],
}
ASSEMBLY: dict[str, Any] = {
    "parts": [{"name": "Enclosure"}, {"name": "Base"}, {"name": "M3 screw", "source": "standard"}],
    "connections": [{"parts": ["Base", "Enclosure"], "joint": "screw"}],
}

#: Every number the inputs carry: the only values a proposal may show.
LAYOUT_NUMBERS = {120.0, 90.0, 160.0, 20.0, 140.0, 0.0, -10.0, -25.0, -180.0, -110.0}


class Blobs:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write(self, node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        key = f"work-products/{node_id}/{filename}"
        self.files[key] = content
        return key


@pytest.fixture
def twin() -> Any:
    from twin_core.api import InMemoryTwinAPI

    return InMemoryTwinAPI.create()


@pytest.fixture(autouse=True)
def publisher() -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


@pytest.fixture(autouse=True)
def blobs() -> Iterator[Blobs]:
    b = Blobs()
    structured_mesh.set_blob_writer(b.write)
    yield b
    structured_mesh.set_blob_writer(None)


@pytest.fixture(autouse=True)
def create_store() -> Iterator[SqliteCreationStore]:
    store = SqliteCreationStore()
    create_routes.init_create_store(store)
    yield store
    create_routes.init_create_store(None)


@pytest.fixture(autouse=True)
def no_llm() -> Iterator[None]:
    rc.set_llm_call(None)
    yield
    rc.set_llm_call(None)


async def _layout(twin: Any, layout: dict[str, Any] = LAYOUT) -> UUID:
    wp = WorkProduct(
        name="Concept sketch: Desk speaker",
        type=WorkProductType.DESIGN_SKETCH,
        domain="mechanical",
        file_path="",
        content_hash=hashlib.sha256(json.dumps(layout).encode()).hexdigest(),
        format="svg",
        metadata={"concept_layout": layout, "source_tool": "twin.render_concept_sketch"},
        created_by="twin.render_concept_sketch",
        project_id=PID,
    )
    await twin.create_work_product(wp)
    return wp.id


async def _concept(twin: Any, *, process: str | None = "FDM") -> None:
    await _layout(twin)
    await make_decision_recorder(twin)(
        title="Concept A", rationale="chosen", assembly=ASSEMBLY, project_id=PROJECT
    )
    if process:
        await mc.write_context(
            twin,
            PROJECT,
            {
                "selected_process": mc.stated(process, source="api"),
                "selected_material": mc.stated("PETG", source="api"),
            },
            source="api",
        )


def _reference(store: SqliteCreationStore, facts: list[ReferenceFact], **kw: Any) -> str:
    creation = store.get(PROJECT)
    rid = kw.pop("work_product_id", "11111111-2222-4333-8444-555555555555")
    rec = ReferenceRecord(work_product_id=rid, facts=facts, **kw)
    creation = Creation.model_validate(
        {
            **creation.model_dump(),
            "brief": {**creation.brief.model_dump(), "reference_details": [rec.model_dump()]},
        }
    )
    store.save(creation, expected_version=None)
    return rid


def _fact(parameter: str, value: float | None, unit: str, basis: str = "stated") -> ReferenceFact:
    return ReferenceFact(
        id=f"fact-{abs(hash(parameter)) % 10**8}",
        parameter=parameter,
        value=value,
        unit=unit,
        basis=basis,  # type: ignore[arg-type]
        uncertainty="low" if basis == "stated" else "high",
        citation=FactCitation(reference_id="ref", region="line 3", excerpt=parameter),
    )


def _inputs(part: dict[str, Any] | None, **kw: Any) -> ReconstructionInputs:
    return ReconstructionInputs(
        component_id=kw.pop("component_id", "CMP-enclosure"),
        component_name=kw.pop("component_name", "Enclosure"),
        part=part,
        layout_ref="DSK-1@1",
        **kw,
    )


def _numbers(proposal: Any) -> list[float]:
    out: list[float] = []
    for f in proposal.features:
        for p in f.parameters.values():
            if isinstance(p.value, int | float):
                out.append(float(p.value))
        if f.placement is not None:
            out.append(float(f.placement.offset))
    return out


# ── the deterministic mapping ────────────────────────────────────────────


def test_a_box_becomes_a_rectangle_and_an_extrude() -> None:
    proposal = deterministic_proposal(
        _inputs(
            LAYOUT["parts"][0], context=ContextFacts(process="FDM", process_authority="user_stated")
        )
    )
    by = {f.id: f for f in proposal.features}
    profile, body = by["profile"], by["body"]
    assert profile.kind == "sketch_profile" and body.kind == "extrude"
    assert body.depends_on == ["profile"]
    [rect] = profile.profile
    assert isinstance(rect, SketchRectangle) and (rect.width, rect.height) == (120, 90)
    assert rect.estimated is True
    assert profile.placement is not None
    assert (profile.placement.plane, profile.placement.offset) == ("XY", 20)
    assert profile.placement.status == "estimated"
    width, depth = profile.parameters["width"], body.parameters["depth"]
    assert (width.value, width.status, width.source.kind) == (120, "estimated", "l1_layout")
    assert (depth.value, depth.status) == (160, "estimated")
    assert depth.source is not None and depth.source.ref == "DSK-1@1"
    assert profile.confidence == 0.7  # the layout says "intended"
    # An enclosure is hollowed out, with its wall thickness left to a person.
    shell = by["shell"]
    assert shell.kind == "shell" and shell.parameters["thickness"].status == "unknown"
    assert [f.kind for f in proposal.features][:3] == ["sketch_profile", "extrude", "shell"]
    assert proposal.summary.startswith("the enclosure:")


def test_a_cylinder_becomes_a_circle_with_a_question_about_revolving() -> None:
    proposal = deterministic_proposal(
        _inputs(LAYOUT["parts"][1], component_id="CMP-base", component_name="Base")
    )
    profile = proposal.feature("profile")
    assert profile is not None
    [circle] = profile.profile
    assert isinstance(circle, SketchCircle) and circle.radius == 70
    # Centre of the block on the XY plane (sketch space has y down).
    assert circle.center == (60, -45)
    assert profile.parameters["diameter"].value == 140
    question = next(a for a in proposal.ambiguities if a.id == "body-op")
    assert {o.id for o in question.options} == {"extrude", "revolve"}
    assert question.default == "extrude"
    assert not any(f.kind == "shell" for f in proposal.features)


def test_a_cylinder_along_x_is_sketched_on_yz() -> None:
    part = {
        "name": "Shade",
        "shape": "cylinder",
        "axis": "x",
        "size_mm": [120, 90, 90],
        "position_mm": [40, 25, 330],
        "provenance": "open",
    }
    proposal = deterministic_proposal(
        _inputs(part, component_id="CMP-shade", component_name="Shade")
    )
    profile = proposal.feature("profile")
    assert profile is not None and profile.placement is not None
    assert (profile.placement.plane, profile.placement.offset) == ("YZ", 40)
    assert proposal.feature("body").parameters["depth"].value == 120  # type: ignore[union-attr]
    assert not any(a.id == "body-op" for a in proposal.ambiguities)
    assert any(a.id == "layout-open" for a in proposal.assumptions)


def test_a_parametric_sketch_profile_is_carried_with_its_statuses() -> None:
    sketch = ParametricSketch(
        unit="mm",
        plane_id="PLN-1@1",
        elements=[SketchCircle(id="c1", center=(0, 0), radius=12)],
        constraints=[],
        profiles=[ClosedProfile(primitive_ids=["c1"])],
        feature_plan=[
            FeaturePlanItem(
                id="feat-extrude-1",
                op="extrude",
                profile_primitive_ids=["c1"],
                parameters={
                    "depth": FeatureParameter(status="provided", value=8, unit="mm"),
                    "material": FeatureParameter(question="What material is it made of?"),
                },
            )
        ],
    )
    proposal = deterministic_proposal(_inputs(None, sketches=[("PSK-KNOB@2", sketch)]))
    by = {f.id: f for f in proposal.features}
    assert set(by) == {"psk1-profile", "psk1-feat-extrude-1"}
    body = by["psk1-feat-extrude-1"]
    assert body.existing_ref == "PSK-KNOB@2" and body.buildable
    assert body.parameters["depth"].status == "confirmed" and body.parameters["depth"].value == 8
    assert body.parameters["material"].status == "unknown"
    assert by["psk1-profile"].profile[0].radius == 12  # type: ignore[union-attr]


# ── no invented dimensions ───────────────────────────────────────────────


def test_numbers_come_only_from_the_layout_or_stated_facts() -> None:
    facts = [
        FactInput(
            "ref-1",
            "specification",
            "Speaker opening diameter",
            52.0,
            "mm",
            "stated",
            "line 4",
            about_component=True,
        ),
        FactInput(
            "ref-1",
            "specification",
            "Enclosure height",
            15.0,
            "cm",
            "stated",
            "line 5",
            about_component=True,
        ),
    ]
    proposal = deterministic_proposal(
        _inputs(LAYOUT["parts"][0], facts=facts, context=ContextFacts(process="FDM"))
    )
    numbers = set(_numbers(proposal))
    assert numbers <= LAYOUT_NUMBERS | {52.0, 150.0}
    depth = proposal.feature("body").parameters["depth"]  # type: ignore[union-attr]
    assert (depth.value, depth.status, depth.source.kind) == (150.0, "confirmed", "reference_fact")  # type: ignore[union-attr]
    opening = next(f for f in proposal.features if f.kind == "opening")
    assert (
        opening.parameters["size"].value == 52.0
        and opening.parameters["size"].status == "confirmed"
    )
    # Where the opening goes is not in any input: it is asked.
    assert any(a.feature_id == opening.id and a.consequential for a in proposal.ambiguities)
    # Every value names its source.
    for f in proposal.features:
        for p in f.parameters.values():
            if p.value is not None:
                assert p.source is not None and p.source.kind in {
                    "l1_layout",
                    "reference_fact",
                    "parametric_sketch",
                    "manufacturing_context",
                    "shape",
                }


def test_an_estimated_fact_stays_estimated_and_nothing_is_read_without_a_unit() -> None:
    facts = [
        FactInput(
            "ref-2", "inspiration", "Enclosure width", 100.0, "mm", "estimated", "whole image"
        ),
        FactInput("ref-2", "inspiration", "Enclosure height", 30.0, "", "estimated", "whole image"),
    ]
    proposal = deterministic_proposal(_inputs(LAYOUT["parts"][0], facts=facts))
    width = proposal.feature("profile").parameters["width"]  # type: ignore[union-attr]
    assert (width.value, width.status) == (100.0, "estimated")
    assert proposal.feature("body").parameters["depth"].value == 160  # type: ignore[union-attr]


def test_without_a_layout_or_sketch_nothing_is_proposed() -> None:
    proposal = deterministic_proposal(_inputs(None))
    assert proposal.features == [] and _numbers(proposal) == []
    assert any(a.id == "nothing-to-map" for a in proposal.assumptions)


# ── the DFM strategy note (DFM-005) ──────────────────────────────────────


def test_the_strategy_note_names_the_real_fdm_rules() -> None:
    facts = [
        FactInput(
            "ref-1",
            "specification",
            "USB port",
            9.0,
            "mm",
            "stated",
            "line 2",
            about_component=True,
        )
    ]
    proposal = deterministic_proposal(
        _inputs(
            LAYOUT["parts"][0],
            facts=facts,
            connections=[{"other": "Base", "joint": "screw"}],
            context=ContextFacts(
                process="FDM", process_authority="user_stated", context_ref="MFC-PROJECT@1"
            ),
        )
    )
    fdm = PROFILES["fdm"]
    note = proposal.dfm_strategy
    assert (note.profile_id, note.profile_version) == ("fdm", fdm.version)
    assert note.context_ref == "MFC-PROJECT@1"
    used = {r.rule_id: r for r in note.rules}
    assert {"fdm.min_wall", "fdm.min_hole", "fdm.min_feature"} <= set(used)
    for rule_id, use in used.items():
        rule = fdm.rule(rule_id)
        assert (use.limit, use.kind, use.source) == (rule.limit, rule.kind.value, rule.source)
        assert rule_id in note.text
    assert "at least 0.8 mm" in used["fdm.min_wall"].applied_as
    # The rule is a bound beside the unknown wall, never its value.
    thickness = proposal.feature("shell").parameters["thickness"]  # type: ignore[union-attr]
    assert thickness.value is None and thickness.minimum == 0.8
    assert thickness.minimum_rule == "fdm.min_wall"
    assert "fdm.min_wall" in proposal.feature("shell").dfm_rules  # type: ignore[union-attr]
    # No machine is selected, so no printable split is claimed.
    assert "fdm.build_volume" not in used
    assert "not checks of this geometry" in note.text


def test_a_printable_split_is_asked_when_the_machine_is_too_small() -> None:
    proposal = deterministic_proposal(
        _inputs(
            LAYOUT["parts"][0],
            context=ContextFacts(process="FDM", build_volume_mm=(100.0, 100.0, 100.0)),
        )
    )
    assert "fdm.build_volume" in {r.rule_id for r in proposal.dfm_strategy.rules}
    assert any(a.id == "printable-split" for a in proposal.ambiguities)


def test_with_no_process_no_rule_shaped_it_and_the_process_is_asked() -> None:
    proposal = deterministic_proposal(_inputs(LAYOUT["parts"][0]))
    assert proposal.dfm_strategy.profile_id is None and proposal.dfm_strategy.rules == []
    assert "No manufacturing rules shaped this strategy" in proposal.dfm_strategy.text
    assert any(a.id == "process" for a in proposal.ambiguities)


# ── the optional model pass ──────────────────────────────────────────────


def test_the_model_pass_names_orders_and_suggests_but_never_supplies_numbers() -> None:
    base = deterministic_proposal(_inputs(LAYOUT["parts"][0], context=ContextFacts(process="FDM")))
    reply = json.dumps(
        {
            "names": {"profile": "Front profile", "body": "Body 160 mm", "shell": "Hollow"},
            "order": ["body", "profile", "shell"],
            "suggestions": [
                {
                    "kind": "rib",
                    "name": "Stiffening ribs",
                    "reason": "tall walls flex",
                    "height": 12,
                },
                {"kind": "boss", "name": "M3 bosses", "reason": "4 screws"},
                {"kind": "fillet", "name": "Edges"},
            ],
        }
    )
    out = apply_llm_pass(base, reply, provider="anthropic", model="m1")
    assert out.llm.outcome == "used" and out.llm.model == "m1"
    assert out.feature("profile").name == "Front profile"  # type: ignore[union-attr]
    # A name with a number is not taken.
    assert out.feature("body").name == "Extrude"  # type: ignore[union-attr]
    # An order that puts a body before its profile is ignored.
    assert [f.id for f in out.features][:2] == ["profile", "body"] and not out.llm.reordered
    suggested = [f for f in out.features if f.suggested_by == "llm"]
    assert {f.kind for f in suggested} == {"rib", "boss"}
    for f in suggested:
        assert f.parameters == {} and f.provenance[0].kind == "llm"
        assert "Suggested by a model" in f.note
        assert not any(ch.isdigit() for ch in f.name)
    assert any(a.id.endswith("-suggested") for a in out.assumptions)
    assert _numbers(out) == _numbers(base)
    assert apply_llm_pass(base, "not json").llm.outcome == "invalid"


async def test_the_mocked_model_pass_is_used_through_the_gateway(twin: Any) -> None:
    await _concept(twin)
    prompts: list[str] = []

    async def fake(prompt: str) -> tuple[str, str | None, str | None]:
        prompts.append(prompt)
        return (
            json.dumps({"suggestions": [{"kind": "rib", "name": "Ribs", "reason": "stiff"}]}),
            "fake",
            "fake-1",
        )

    rc.set_llm_call(fake)
    out = await rc.propose(
        twin,
        PROJECT,
        rc.ReconstructionProposeRequest(component_ids=["CMP-enclosure"], use_llm=True),
    )
    [view] = out.proposals
    assert prompts and "160" not in prompts[0]  # the prompt carries no numbers to echo
    assert view.proposal["llm"]["outcome"] == "used"
    rib = next(f for f in view.proposal["features"] if f["kind"] == "rib")
    assert rib["suggested_by"] == "llm"


async def test_an_unavailable_model_leaves_the_deterministic_proposal(twin: Any) -> None:
    await _concept(twin)

    async def down(prompt: str) -> tuple[str, str | None, str | None]:
        raise rc.LlmUnavailableError("no provider")

    rc.set_llm_call(down)
    out = await rc.propose(
        twin,
        PROJECT,
        rc.ReconstructionProposeRequest(component_ids=["CMP-enclosure"], use_llm=True),
    )
    [view] = out.proposals
    assert view.proposal["llm"]["outcome"] == "unavailable"
    assert any(f["kind"] == "extrude" for f in view.proposal["features"])


# ── storing, linking and reading ─────────────────────────────────────────


async def test_a_proposal_per_component_depends_on_what_it_read(
    twin: Any, create_store: SqliteCreationStore
) -> None:
    await _concept(twin)
    ref_node = WorkProduct(
        name="speaker spec.txt",
        type=WorkProductType.DOCUMENTATION,
        domain="mechanical",
        file_path="",
        content_hash="x" * 64,
        format="txt",
        metadata={},
        created_by="test",
        project_id=PID,
    )
    await twin.create_work_product(ref_node)
    _reference(
        create_store,
        [_fact("Speaker opening diameter", 52, "mm")],
        work_product_id=str(ref_node.id),
        role="specification",
        associations=[ReferenceAssociation(kind="component", id="CMP-enclosure")],
    )
    out = await rc.propose(twin, PROJECT, rc.ReconstructionProposeRequest())
    assert {p.component_id for p in out.proposals} == {"CMP-enclosure", "CMP-base"}
    assert [s.component_id for s in out.skipped] == ["CMP-m3-screw"]
    assert out.summary.startswith("We'd build this as: ")
    enc = next(p for p in out.proposals if p.component_id == "CMP-enclosure")
    assert enc.item_ref == "FHP-ENCLOSURE@1" and enc.status == "proposed"
    inputs = enc.proposal["inputs"]
    assert inputs["reference_ids"] == [str(ref_node.id)]
    assert inputs["context_ref"] == "MFC-PROJECT@1" and inputs["assembly_decision_id"]
    assert inputs["mesh_node_id"] and inputs["layout_node_id"]
    edges = await twin.get_edges(UUID(enc.node_id), direction="outgoing")
    roles = {(e.metadata or {}).get("role") for e in edges}
    assert {
        "concept_assembly",
        "l1_layout",
        "l1_mesh",
        "reference",
        "manufacturing_context",
    } <= roles
    node = await twin.get_work_product(UUID(enc.node_id))
    assert {p["item_ref"] for p in node.metadata["depends_on_items"]} >= {"MFC-PROJECT@1"}

    # The same inputs reuse the head; the view returns the latest per component.
    again = await rc.propose(twin, PROJECT, rc.ReconstructionProposeRequest())
    assert sorted(again.reused) == ["CMP-base", "CMP-enclosure"]
    view = await rc.reconstruction_view(twin, PROJECT)
    assert view.concept_accepted and not view.accepted
    assert {c.component_id: bool(c.proposal) for c in view.components} == {
        "CMP-enclosure": True,
        "CMP-base": True,
        "CMP-m3-screw": False,
    }


async def test_no_accepted_concept_is_refused(twin: Any) -> None:
    with pytest.raises(rc.ReconstructionError) as err:
        await rc.propose(twin, PROJECT, rc.ReconstructionProposeRequest())
    assert err.value.status == 409
    view = await rc.reconstruction_view(twin, PROJECT)
    assert view.concept_accepted is False and view.components == []


# ── acceptance ───────────────────────────────────────────────────────────


async def test_acceptance_makes_a_parametric_sketch_that_keeps_required_input(
    twin: Any, publisher: ops.OperationPublisher
) -> None:
    await _concept(twin)
    await rc.propose(twin, PROJECT, rc.ReconstructionProposeRequest())
    out = await rc.accept_proposal(
        twin,
        PROJECT,
        "FHP-ENCLOSURE",
        rc.ReconstructionAcceptRequest(
            feature_ids=["body", "shell"], edits={"profile": {"width": 118}}, base_revision=1
        ),
    )
    assert set(out.accepted) == {"profile", "body", "shell"} and out.added_dependencies == [
        "profile"
    ]
    [made] = out.sketches
    assert made.item_ref == "PSK-REC-ENCLOSURE@1" and made.approval_id.startswith(
        "parametric_sketch:"
    )
    assert made.required_input == ["depth"] and made.pending_features == ["shell"]
    psk = await twin.get_work_product(UUID(made.node_id))
    meta = psk.metadata
    assert meta["approved"] is False and meta["component_id"] == "CMP-enclosure"
    assert meta["reconstruction_ref"] == "FHP-ENCLOSURE@1"
    [item] = meta["feature_plan"]
    assert item["op"] == "extrude"
    depth = item["parameters"]["depth"]
    assert depth["status"] == "required_input" and depth["value"] is None
    assert "160" in depth["question"] and "estimated" in depth["question"]
    # The material a person stated in the context is provided.
    assert item["parameters"]["material"] == {
        "status": "provided",
        "value": "PETG",
        "unit": None,
        "question": "",
    }
    sketch = ParametricSketch.model_validate(meta["parametric_sketch"])
    rect = sketch.elements[0]
    assert isinstance(rect, SketchRectangle) and rect.width == 118 and rect.height == 90
    assert principal_placement(meta) == {"plane": "XY", "offset": 20.0}
    assert meta[PRINCIPAL_PLACEMENT_KEY]["status"] == "estimated"
    # The proposal's next revision records what was accepted.
    assert (
        out.proposal.item_ref == "FHP-ENCLOSURE@2" and out.proposal.status == "partially_accepted"
    )
    assert out.proposal.proposal["accepted_sketches"] == {"body": "PSK-REC-ENCLOSURE@1"}
    edits = out.proposal.proposal["features"]
    profile = next(f for f in edits if f["id"] == "profile")
    assert profile["parameters"]["width"]["status"] == "confirmed"
    assert profile["parameters"]["width"]["source"]["kind"] == "person"
    # Listed for sign-off through the parametric sketch approval.
    events = [
        e
        for e in publisher.store.events_after(PROJECT, 0)
        if e.operation_id == rc.operation_id_for(PROJECT) and e.type == "review_required"
    ]
    assert events and events[-1].action_refs[0].id == made.approval_id


async def test_a_confirmed_depth_is_provided_and_the_exact_path_has_its_plane(twin: Any) -> None:
    from api_gateway.twin.exact_cad import ExactCadService

    await _concept(twin)
    await rc.propose(twin, PROJECT, rc.ReconstructionProposeRequest())
    out = await rc.accept_proposal(
        twin,
        PROJECT,
        "FHP-ENCLOSURE",
        rc.ReconstructionAcceptRequest(feature_ids=["body"], edits={"body": {"depth": 150}}),
    )
    psk = await twin.get_work_product(UUID(out.sketches[0].node_id))
    plan = psk.metadata["feature_plan"][0]["parameters"]
    assert plan["depth"] == {"status": "provided", "value": 150.0, "unit": "mm", "question": ""}
    psk.metadata["approved"] = True
    service = ExactCadService(twin, bridge=None, recorder=None)
    source = await service.parametric_source(psk)
    sketch = next(e for e in source["design_ir"]["entities"] if e["op"] == "sketch")
    assert (sketch["plane"], sketch["offset"]) == ("XY", 20.0)
    pad = next(e for e in source["design_ir"]["entities"] if e["op"] == "pad")
    assert pad["depth"] == 150.0 and source["plane_ref"] is None


async def test_acceptance_answers_questions_and_refuses_bad_edits(twin: Any) -> None:
    await _concept(twin, process=None)
    await rc.propose(twin, PROJECT, rc.ReconstructionProposeRequest())
    with pytest.raises(rc.ReconstructionError) as err:
        await rc.accept_proposal(
            twin,
            PROJECT,
            "FHP-BASE",
            rc.ReconstructionAcceptRequest(feature_ids=["body"], base_revision=7),
        )
    assert err.value.status == 409
    with pytest.raises(rc.ReconstructionError) as err:
        await rc.accept_proposal(
            twin,
            PROJECT,
            "FHP-BASE",
            rc.ReconstructionAcceptRequest(feature_ids=["body"], edits={"body": {"depth": -1}}),
        )
    assert err.value.status == 422
    out = await rc.accept_proposal(
        twin,
        PROJECT,
        "FHP-BASE",
        rc.ReconstructionAcceptRequest(
            feature_ids=["body"], choices={"body-op": "revolve", "process": "FDM"}
        ),
    )
    psk = await twin.get_work_product(UUID(out.sketches[0].node_id))
    [item] = psk.metadata["feature_plan"]
    assert item["op"] == "revolve" and item["parameters"]["angle"]["status"] == "provided"
    assert item["axis_id"] == "axis"
    assert out.process_written == "FDM"
    stored = await mc.read_context(twin, PROJECT)
    assert stored is not None and stored.record.selected_process.value == "FDM"
    # Already accepted: a second acceptance needs a new proposal.
    with pytest.raises(rc.ReconstructionError):
        await rc.accept_proposal(
            twin, PROJECT, "FHP-BASE", rc.ReconstructionAcceptRequest(feature_ids=["body"])
        )


def test_a_shell_below_the_rule_minimum_is_refused() -> None:
    proposal = deterministic_proposal(
        _inputs(LAYOUT["parts"][0], context=ContextFacts(process="FDM"))
    )
    with pytest.raises(AcceptanceError) as err:
        accept_features(proposal, ["shell"], edits={"shell": {"thickness": 0.5}})
    assert "fdm.min_wall" in str(err.value)
    plan = accept_features(proposal, ["shell"], choices={"shell-hollow": "solid"})
    assert "shell" in plan.rejected and plan.sketches[0].pending == []


async def test_maturity_moves_to_parametric_only_on_acceptance(twin: Any) -> None:
    await _concept(twin)

    async def level() -> str:
        out = await compute_maturity(twin, PID)
        return next(
            c["maturity"] for c in out["components"] if c["component_id"] == "CMP-enclosure"
        )

    assert await level() == "Structured"
    await rc.propose(twin, PROJECT, rc.ReconstructionProposeRequest())
    assert await level() == "Structured"
    await rc.accept_proposal(
        twin, PROJECT, "FHP-ENCLOSURE", rc.ReconstructionAcceptRequest(feature_ids=["body"])
    )
    assert await level() == "Parametric"


# ── routes ───────────────────────────────────────────────────────────────


@pytest.fixture
def client(twin: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(twin_routes, "_twin", twin)
    app = FastAPI()
    app.include_router(reconstruction_router)
    return TestClient(app)


async def test_routes_propose_read_and_accept(twin: Any, client: TestClient) -> None:
    base = f"/v1/twin/projects/{PROJECT}/reconstruction"
    assert client.post(base, json={}).status_code == 409
    empty = client.get(base)
    assert empty.status_code == 200 and empty.json()["concept_accepted"] is False
    await _concept(twin)
    proposed = client.post(base, json={"component_ids": ["CMP-enclosure"]})
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["proposals"][0]["item_key"] == "FHP-ENCLOSURE"
    read = client.get(base).json()
    enc = next(c for c in read["components"] if c["component_id"] == "CMP-enclosure")
    assert enc["proposal"]["open_questions"] >= 1
    accepted = client.post(
        f"{base}/FHP-ENCLOSURE/accept", json={"feature_ids": ["body"], "base_revision": 1}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["sketches"][0]["item_key"] == "PSK-REC-ENCLOSURE"
    assert client.post(f"{base}/FHP-NONE/accept", json={"feature_ids": ["body"]}).status_code == 404
    assert client.post(f"{base}/FHP-ENCLOSURE/accept", json={"feature_ids": []}).status_code == 422
    assert client.get("/v1/twin/projects/not-a-uuid/reconstruction").status_code == 400
