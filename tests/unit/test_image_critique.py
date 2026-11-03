"""A vision model checks each concept image against the selected assembly (FORGE-772).

After ``twin.render_concept_image`` makes a picture, a vision model compares
it with the concept decision's assembly and the result is stored on the
render as ``metadata.critique``. It is advisory: it never fails the image
call, never makes the image evidence, and reaches the concept gate as a note.
Every test uses a fake image provider and a fake vision model.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from api_gateway.twin import image_critique
from api_gateway.twin.claim_recorder import is_illustrative, make_claim_recorder
from api_gateway.twin.image_critique import (
    ImageCritiqueError,
    make_concept_image_critic,
    parse_critique,
    stored_image_bytes,
)
from api_gateway.twin.sketch_reading import SketchVisionUnavailableError, VisionReply
from orchestrator.design_flow.context import CONCEPT_PHASE_ID, concept_image_check_note
from tests.unit.test_concept_image import BRIEF, Harness, _chair
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, WorkProductType
from twin_core.models.work_product import WorkProduct

PROJECT = "3f0b8f5c-5a7e-4a4c-9d7a-0d6d3b0a1c11"

ASSEMBLY = {
    "parts": [
        {"name": "Seat", "source": "made", "material": "birch ply"},
        {"name": "Leg", "source": "made", "quantity": 4},
        {"name": "Backrest", "source": "made"},
        {"name": "Dowel", "source": "standard", "quantity": 8},
    ],
    "connections": [
        {"parts": ["Seat", "Leg"], "joint": "8 mm dowel", "fasteners": "Dowel"},
        {"parts": ["Seat", "Backrest"], "joint": "glued slot"},
    ],
}
OPTIONS = [
    {"name": "Concept A", "summary": "four-leg frame", "selected": True},
    {"name": "Concept B", "summary": "sled base", "selected": False},
]


def _reply(**change: Any) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "parts_seen": [
            {"name": "Seat", "present": True, "note": "top"},
            {"name": "Leg", "present": True, "note": "four turned legs"},
            {"name": "Backrest", "present": True, "note": ""},
            {"name": "Dowel", "present": False, "note": "hidden inside the joint"},
        ],
        "joints": [
            {"between": ["Seat", "Leg"], "matches": True, "note": "legs under the seat"},
            {"between": ["Seat", "Backrest"], "matches": True, "note": ""},
        ],
        "extras": [],
        "verdict": "consistent",
        "summary": "The image shows the four-leg chair as decided.",
    }
    reply.update(change)
    return reply


class FakeVision:
    """A vision model that answers with a fixed reply, or raises."""

    def __init__(self, reply: Any = None, *, error: Exception | None = None) -> None:
        self.reply = reply if reply is not None else _reply()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, image: bytes, mime: str, prompt: str) -> VisionReply:
        self.calls.append({"image": image, "mime": mime, "prompt": prompt})
        if self.error is not None:
            raise self.error
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return VisionReply(text=text, provider="fake-vlm", model="fake-vision-1")


class CritiqueHarness(Harness):
    def __init__(self, monkeypatch: pytest.MonkeyPatch, vision: FakeVision | None = None) -> None:
        self.vision = vision or FakeVision()
        self.decisions: list[str] = []
        super().__init__(monkeypatch, vision_call=self.vision)
        self.verdicts: list[str] = []
        monkeypatch.setattr(image_critique, "_record", self.verdicts.append)

        def read(wp: Any) -> tuple[bytes, str]:
            filename, content, _mime = self.image_blobs[str(wp.id)]
            return content, filename

        self.server = TwinServer(
            twin=self.twin,
            design_sketch_recorder=self.server._design_sketch_recorder,
            concept_image_renderer=self.server._concept_image_renderer,
            concept_image_critic=make_concept_image_critic(
                self.twin, None, vision_call=self.vision, blob_reader=read
            ),
        )

    async def decision(
        self, assembly: Any = None, options: Any = None, *, name: str = "Concept decision"
    ) -> str:
        metadata: dict[str, Any] = {"assembly": assembly or ASSEMBLY}
        if options is not None:
            metadata["concept_options"] = options
        now = datetime.now(UTC) + timedelta(seconds=len(self.decisions))
        wp = await self.twin.create_work_product(
            WorkProduct(
                id=uuid4(),
                name=name,
                type=WorkProductType.DESIGN_DECISION,
                domain="mechanical",
                file_path="",
                content_hash="",
                format="md",
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by="test",
                project_id=UUID(PROJECT),
            )
        )
        self.decisions.append(str(wp.id))
        return str(wp.id)

    async def image(self, **args: Any) -> dict[str, Any]:
        result: dict[str, Any] = await self.server.render_concept_image(
            {"layout": _chair(), "visual_brief": BRIEF, "project_id": PROJECT, **args}
        )
        return result

    async def stored(self, node_id: str) -> dict[str, Any]:
        wp = await self.twin.get_work_product(UUID(node_id))
        assert wp is not None
        critique: dict[str, Any] = wp.metadata["critique"]
        return critique


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch) -> CritiqueHarness:
    return CritiqueHarness(monkeypatch)


def _harness(monkeypatch: pytest.MonkeyPatch, vision: FakeVision) -> CritiqueHarness:
    return CritiqueHarness(monkeypatch, vision)


# -- the schema ----------------------------------------------------------------


def test_a_reply_parses_even_inside_a_code_fence() -> None:
    critique = parse_critique("```json\n" + json.dumps(_reply()) + "\n```")
    assert critique.verdict == "consistent"
    assert [p.name for p in critique.parts_seen][:2] == ["Seat", "Leg"]
    assert critique.joints[0].between == ["Seat", "Leg"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "empty"),
        ("I think it looks fine.", "no JSON object"),
        ("{not json}", "not valid JSON"),
        (json.dumps(_reply(verdict="looks great")), "verdict"),
        (json.dumps(_reply(joints=[{"between": ["Seat"], "matches": True}])), "between"),
        (json.dumps({k: v for k, v in _reply().items() if k != "summary"}), "summary"),
    ],
)
def test_a_bad_reply_is_a_clean_error(text: str, message: str) -> None:
    with pytest.raises(ImageCritiqueError, match=message):
        parse_critique(text)


# -- checked when the image is made --------------------------------------------


async def test_a_consistent_image_is_stored_with_the_model_that_judged(
    h: CritiqueHarness,
) -> None:
    decision = await h.decision(options=OPTIONS)
    out = await h.image()

    critique = await h.stored(out["node_id"])
    assert critique["verdict"] == "consistent"
    assert critique["issues"] == []
    assert critique["judged_by"] == {"provider": "fake-vlm", "model": "fake-vision-1"}
    assert critique["assembly_source"]["kind"] == "decision"
    assert critique["assembly_source"]["decision_id"] == decision
    assert critique["authority"] == "advisory"
    assert out["critique"] == critique
    assert h.verdicts == ["consistent"]

    # The model saw the generated image and the assembly it must match.
    call = h.vision.calls[0]
    assert call["mime"] == "image/png" and call["image"][:4] == b"\x89PNG"
    assert "Seat (made, birch ply)" in call["prompt"]
    assert "Seat to Leg: 8 mm dowel (Dowel)" in call["prompt"]
    assert "Concept A: four-leg frame" in call["prompt"]


async def test_a_missing_made_part_is_inconsistent(monkeypatch: pytest.MonkeyPatch) -> None:
    parts = _reply()["parts_seen"]
    parts[2] = {"name": "Backrest", "present": False, "note": "no back drawn"}
    h = _harness(
        monkeypatch,
        FakeVision(
            _reply(
                parts_seen=parts,
                joints=[
                    {"between": ["Seat", "Leg"], "matches": False, "note": "screwed brackets"},
                    {"between": ["Seat", "Backrest"], "matches": True},
                ],
                extras=["a footrest bar"],
                verdict="inconsistent",
                summary="No backrest; brackets instead of dowels.",
            )
        ),
    )
    await h.decision()
    out = await h.image()

    critique = await h.stored(out["node_id"])
    assert critique["verdict"] == "inconsistent"
    assert critique["issues"] == [
        "Backrest not visible",
        "Seat to Leg joint differs (screwed brackets)",
        "extra: a footrest bar",
    ]
    assert h.verdicts == ["inconsistent"]


async def test_a_consistent_verdict_is_held_to_its_own_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parts = _reply()["parts_seen"]
    parts[1] = {"name": "Leg", "present": False}
    h = _harness(monkeypatch, FakeVision(_reply(parts_seen=parts)))
    await h.decision()
    critique = await h.stored((await h.image())["node_id"])
    assert critique["verdict"] == "inconsistent"
    assert critique["issues"] == ["Leg not visible"]


async def test_a_made_part_the_model_skipped_is_not_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parts = [p for p in _reply()["parts_seen"] if p["name"] != "Backrest"]
    h = _harness(monkeypatch, FakeVision(_reply(parts_seen=parts)))
    await h.decision()
    critique = await h.stored((await h.image())["node_id"])
    assert critique["verdict"] == "unclear"
    assert critique["issues"] == ["Backrest not assessed"]


async def test_an_unclear_image_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _harness(
        monkeypatch,
        FakeVision(_reply(verdict="unclear", summary="The joints are out of frame.")),
    )
    await h.decision()
    critique = await h.stored((await h.image())["node_id"])
    assert critique["verdict"] == "unclear"
    assert critique["summary"] == "The joints are out of frame."
    assert h.verdicts == ["unclear"]


async def test_bad_json_is_unchecked_and_the_image_is_still_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = _harness(monkeypatch, FakeVision("It is a lovely chair."))
    await h.decision()
    out = await h.image()

    assert out["node_id"] and out["images"]
    critique = await h.stored(out["node_id"])
    assert critique["verdict"] == "unchecked"
    assert "no JSON object" in critique["reason"]
    assert critique["judged_by"]["model"] == "fake-vision-1"
    assert h.outcomes == ["generated"]
    assert h.verdicts == ["unchecked"]


@pytest.mark.parametrize(
    "error",
    [
        SketchVisionUnavailableError("no vision-capable model provider is configured"),
        RuntimeError("connection reset"),
        TimeoutError(),
    ],
)
async def test_a_failing_vision_model_never_fails_the_image(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    h = _harness(monkeypatch, FakeVision(error=error))
    await h.decision()
    out = await h.image()

    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    assert wp is not None and wp.type == WorkProductType.RENDER
    critique = wp.metadata["critique"]
    assert critique["verdict"] == "unchecked"
    assert critique["reason"].startswith("no vision model checked the image")
    assert h.outcomes == ["generated"]


async def test_without_a_decision_the_image_is_checked_against_its_own_layout(
    h: CritiqueHarness,
) -> None:
    """FORGE-776: images are made before the concept decision is recorded."""
    out = await h.image()
    critique = await h.stored(out["node_id"])
    assert critique["assembly_source"]["kind"] == "sketch_layout"
    assert critique["verdict"] != "unchecked"
    assert len(h.vision.calls) == 1


async def test_without_any_assembly_nothing_is_sent_to_a_vision_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_gateway.twin.concept_image import make_concept_image_recorder
    from tests.unit.test_concept_image import FakeGenerator
    from twin_core.api import InMemoryTwinAPI

    vision = FakeVision()
    gen = FakeGenerator()
    render = make_concept_image_recorder(
        InMemoryTwinAPI.create(),
        generator=gen,
        blob_writer=lambda *a, **k: "key",
        vision_call=vision,
    )
    out = await render(
        visual_brief=BRIEF,
        concept_name="Concept A",
        part_names=["Seat"],
        layout_faces=[],
        conditioned_on={"layout_source": "inline_layout"},
    )
    assert out["critique"]["verdict"] == "unchecked"
    assert "no concept assembly on record" in out["critique"]["reason"]
    assert vision.calls == []


async def test_an_image_of_a_rejected_concept_is_judged_by_its_own_layout(
    h: CritiqueHarness,
) -> None:
    await h.decision(options=OPTIONS)
    layout = {**_chair(), "concept_name": "Concept B: sled base"}
    out = await h.server.render_concept_image(
        {"layout": layout, "visual_brief": BRIEF, "project_id": PROJECT}
    )
    critique = await h.stored(out["node_id"])
    # FORGE-776: it is judged by its own layout instead.
    assert critique["assembly_source"]["kind"] == "sketch_layout"
    assert len(h.vision.calls) == 1


async def test_an_inline_assembly_checks_any_concept(h: CritiqueHarness) -> None:
    await h.decision(options=OPTIONS)
    layout = {**_chair(), "concept_name": "Concept B: sled base"}
    out = await h.server.render_concept_image(
        {"layout": layout, "visual_brief": BRIEF, "project_id": PROJECT, "assembly": ASSEMBLY}
    )
    critique = await h.stored(out["node_id"])
    assert critique["verdict"] == "consistent"
    assert critique["assembly_source"] == {"kind": "inline"}


async def test_decision_ref_picks_the_decision(h: CritiqueHarness) -> None:
    older = await h.decision(name="First concept decision")
    await h.decision(
        {"parts": [{"name": "Stool top", "source": "made"}]}, name="Revised concept decision"
    )
    out = await h.image(decision_ref="First concept decision")
    critique = await h.stored(out["node_id"])
    assert critique["assembly_source"]["decision_id"] == older
    # By default the newest decision with an assembly is the one used.
    out = await h.image()
    critique = await h.stored(out["node_id"])
    assert critique["assembly_source"]["decision_name"] == "Revised concept decision"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"assembly": {"parts": []}}, "bad assembly"),
        ({"decision_ref": "No such decision"}, "not a design decision"),
        ({"decision_ref": "x", "assembly": ASSEMBLY}, "not both"),
        ({"decision_ref": "  "}, "non-empty"),
    ],
)
async def test_a_bad_explicit_assembly_is_refused_before_any_paid_call(
    h: CritiqueHarness, args: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        await h.image(**args)
    assert h.generator.calls == []
    assert await h.renders() == []


# -- checking an image already in the twin -------------------------------------


async def test_the_standalone_tool_checks_a_stored_image(h: CritiqueHarness) -> None:
    out = await h.image()
    # Made before any decision, it was checked against its own layout.
    assert (await h.stored(out["node_id"]))["assembly_source"]["kind"] == "sketch_layout"
    decision = await h.decision(options=OPTIONS)

    result = await h.server.critique_concept_image({"node_id": out["node_id"]})

    assert result["node_id"] == out["node_id"]
    assert result["concept_name"] == "Concept A: four-leg frame"
    assert result["critique"]["verdict"] == "consistent"
    assert result["critique"]["assembly_source"]["decision_id"] == decision
    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    assert wp is not None
    assert wp.metadata["critique"] == result["critique"]
    assert wp.metadata["authority"] == "illustrative"
    assert h.vision.calls[0]["mime"] == "image/png"


async def test_the_standalone_tool_takes_a_decision_ref(h: CritiqueHarness) -> None:
    out = await h.image()
    decision = await h.decision()
    result = await h.server.critique_concept_image(
        {"node_id": out["node_id"], "decision_ref": decision}
    )
    assert result["critique"]["assembly_source"]["decision_id"] == decision


async def test_the_standalone_tool_refuses_what_is_not_a_concept_image(
    h: CritiqueHarness,
) -> None:
    decision = await h.decision()
    with pytest.raises(ValueError, match="not a concept image"):
        await h.server.critique_concept_image({"node_id": decision})
    with pytest.raises(ValueError, match="not a node id"):
        await h.server.critique_concept_image({"node_id": "nope"})
    with pytest.raises(ValueError, match="node_id"):
        await h.server.critique_concept_image({})


def test_the_tool_is_registered_only_with_a_critic() -> None:
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()
    assert "twin.critique_concept_image" not in TwinServer(twin=twin).tool_ids
    critic = make_concept_image_critic(twin, None, vision_call=FakeVision())
    assert (
        "twin.critique_concept_image" in TwinServer(twin=twin, concept_image_critic=critic).tool_ids
    )


def test_an_svg_wrapped_image_is_unwrapped_for_the_model() -> None:
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,'
        b'iVBORw0KGgo=" x="0"/></svg>'
    )
    data, mime = stored_image_bytes(svg, "svg")
    assert mime == "image/png" and data[:4] == b"\x89PNG"


# -- never evidence ------------------------------------------------------------


async def test_a_consistent_critique_never_makes_the_image_evidence(
    h: CritiqueHarness,
) -> None:
    await h.decision()
    out = await h.image()
    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    assert wp is not None and wp.metadata["critique"]["verdict"] == "consistent"
    assert is_illustrative(wp)
    await h.twin.create_constraint(
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
        await make_claim_recorder(h.twin)(
            requirement_ref="seat_height_max", artefact_ref=out["node_id"], project_id=PROJECT
        )


# -- the concept gate ----------------------------------------------------------

CHECKS = [
    {"concept": "C", "verdict": "consistent", "issues": [], "reason": ""},
    {
        "concept": "A",
        "verdict": "inconsistent",
        "issues": ["Wheel Hook Left not visible"],
        "reason": "",
    },
    {"concept": "B", "verdict": "unchecked", "issues": [], "reason": "no vision model"},
]


def test_the_gate_note_names_each_concept() -> None:
    note = concept_image_check_note(CHECKS)
    assert note is not None
    assert note.startswith(
        "concept image check: C consistent; A inconsistent: Wheel Hook Left not visible; "
        "B unchecked (no vision model)"
    )
    assert "never evidence" in note
    assert concept_image_check_note([]) is None
    assert concept_image_check_note(None) is None


def test_the_gate_note_caps_the_issues() -> None:
    issues = [f"Part {i} not visible" for i in range(5)]
    note = concept_image_check_note([{"concept": "A", "verdict": "inconsistent", "issues": issues}])
    assert note is not None and "Part 2 not visible and 2 more" in note


class _Evaluator:
    def __init__(self, checks: list[dict[str, Any]]) -> None:
        self._checks = checks

    async def present_types(self, _project: str | None, _since: float) -> set[str]:
        return {"design_decision", "design_sketch"}

    async def concept_image_checks(self, _project: str | None) -> list[dict[str, Any]]:
        return self._checks


class _BrokenEvaluator(_Evaluator):
    async def concept_image_checks(self, _project: str | None) -> list[dict[str, Any]]:
        raise RuntimeError("twin down")


class _Constraints:
    async def check(self, _project: str | None) -> Any:
        return SimpleNamespace(passed=True, checked=True, violations=[], undeclared_items=[])


def _temporal(evaluator: Any) -> Any:
    from api_gateway.runs.routes import _GateCheckers

    checkers = _GateCheckers(evaluator, _Constraints(), None)
    return asyncio.run(
        checkers.evaluate(
            {"id": CONCEPT_PHASE_ID, "required_deliverables": ["design_decision"]}, PROJECT
        )
    )


def _in_process(evaluator: Any) -> Any:
    from orchestrator.design_flow.executor import DesignFlowExecutor, FlowContext, GateCoordinator
    from orchestrator.design_flow.spec import Phase
    from orchestrator.harness.runs import InMemoryRunStore

    executor = DesignFlowExecutor(
        store=InMemoryRunStore(),
        brain=None,
        coordinator=GateCoordinator(),
        gate_evaluator=evaluator,
    )
    phase = Phase(
        id=CONCEPT_PHASE_ID, title="t", objective="o", required_deliverables=("design_decision",)
    )
    return asyncio.run(
        executor._readiness(phase, FlowContext(goal="g", project_id=PROJECT), since_ts=0.0)
    )


class TestTheConceptGate:
    def test_the_check_is_a_note_in_both_engines_and_never_blocks(self) -> None:
        check = _temporal(_Evaluator(CHECKS))
        assert check.ready and check.missing == []
        assert "A inconsistent: Wheel Hook Left not visible" in check.reason
        report = _in_process(_Evaluator(CHECKS))
        assert report.ready and report.missing == []
        assert any("A inconsistent: Wheel Hook Left not visible" in n for n in report.notes)

    def test_no_images_add_no_note(self) -> None:
        assert "concept image check" not in _temporal(_Evaluator([])).reason
        assert _in_process(_Evaluator([])).notes == []

    def test_an_unreadable_check_costs_the_note_not_the_gate(self) -> None:
        check = _temporal(_BrokenEvaluator([]))
        assert check.ready and "concept image check" not in check.reason
        report = _in_process(_BrokenEvaluator([]))
        assert report.ready and report.notes == []


async def test_the_evaluator_reads_each_concepts_newest_image(h: CritiqueHarness) -> None:
    from api_gateway.runs.gate_eval import ProjectGateEvaluator

    first = await h.image()
    await h.decision()
    second = await h.image()
    other = await h.server.render_concept_image(
        {
            "layout": {**_chair(), "concept_name": "Concept B: sled base"},
            "visual_brief": BRIEF,
            "project_id": PROJECT,
        }
    )
    ids = [first["node_id"], second["node_id"], other["node_id"]]
    nodes = [await h.twin.get_work_product(UUID(i)) for i in ids]
    for i, node in enumerate(nodes):
        assert node is not None
        await h.twin.update_work_product(
            node.id, {"updated_at": datetime.now(UTC) + timedelta(seconds=i)}
        )
    refreshed = [await h.twin.get_work_product(UUID(i)) for i in ids]

    class _Backend:
        async def get_project(self, _pid: str) -> Any:
            return SimpleNamespace(work_products=refreshed)

    checks = await ProjectGateEvaluator(_Backend(), twin=h.twin).concept_image_checks(PROJECT)  # type: ignore[arg-type]
    assert [c["concept"] for c in checks] == [
        "Concept A: four-leg frame",
        "Concept B: sled base",
    ]
    assert checks[0]["verdict"] == "consistent"
    assert checks[1]["verdict"] == "consistent"
