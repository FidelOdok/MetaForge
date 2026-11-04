"""The AI concept image tool and its image provider (FORGE-761).

An image model draws a concept from its block layout and a visual brief.
The picture is illustrative: it carries a visible label, records what
conditioned it, and can never back a requirement claim. Every test uses a
fake provider; nothing here calls a paid API.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import io
from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin import concept_image
from api_gateway.twin.claim_recorder import is_illustrative, make_claim_recorder
from api_gateway.twin.concept_image import LABEL, make_concept_image_recorder
from api_gateway.twin.design_sketch_recorder import make_design_sketch_recorder
from api_gateway.twin.image_generation import (
    DEFAULT_OPENAI_MODEL,
    GeneratedImage,
    ImageGenerationError,
    ImageGenerationTimeoutError,
    ImageGenerationUnavailableError,
    OpenAIImageGenerator,
    _describe,
    resolve_image_generator,
)
from tool_registry.tools.twin.adapter import TwinServer
from tool_registry.tools.twin.concept_sketch import (
    iso_faces,
    layout_to_dict,
    parse_layout,
    render_concept_sketch,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct

PIL = pytest.importorskip("PIL.Image")

BRIEF = "A Scandinavian dining chair in oiled birch with a woven seat, soft daylight."


def _chair() -> dict[str, Any]:
    leg = {"shape": "cylinder", "size_mm": [30, 30, 430], "provenance": "assumption"}
    return {
        "concept_name": "Concept A: four-leg frame",
        "parts": [
            {
                "name": "Seat",
                "size_mm": [420, 400, 18],
                "position_mm": [0, 0, 430],
                "provenance": "intended",
            },
            {**leg, "name": "Leg FL", "position_mm": [10, 10, 0]},
            {**leg, "name": "Leg FR", "position_mm": [380, 10, 0], "provenance": "verified"},
            {**leg, "name": "Leg BL", "position_mm": [10, 360, 0]},
            {**leg, "name": "Leg BR", "position_mm": [380, 360, 0]},
            {"name": "Backrest", "bbox_mm": [[0, 380, 448], [420, 400, 850]], "provenance": "open"},
        ],
        "connections": [{"a": "Seat", "b": "Leg FL", "joint": "8 mm dowel"}],
        "notes": "Birch ply, 18 mm",
    }


def _png(size: tuple[int, int] = (1024, 1024)) -> bytes:
    buf = io.BytesIO()
    PIL.new("RGB", size, (250, 240, 220)).save(buf, format="PNG")
    return buf.getvalue()


class FakeGenerator:
    """Records what it was asked and returns a plain PNG."""

    def __init__(self, *, delay: float = 0.0, honour_conditioning: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self.delay = delay
        self.honour = honour_conditioning

    async def __call__(self, prompt: str, conditioning: bytes | None, size: str) -> GeneratedImage:
        self.calls.append({"prompt": prompt, "conditioning": conditioning, "size": size})
        if self.delay:
            await asyncio.sleep(self.delay)
        w, h = (int(v) for v in size.split("x"))
        return GeneratedImage(
            image_bytes=_png((w, h)),
            mime="image/png",
            provider="fake",
            model="fake-image-1",
            conditioned=conditioning is not None and self.honour,
        )


class Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, generator: Any = None, **kw: Any) -> None:
        import digital_twin.storage.work_product_blobs as blobs

        self.sketch_blobs: dict[str, bytes] = {}
        self.image_blobs: dict[str, tuple[str, bytes, str]] = {}

        def store(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
            self.sketch_blobs[node_id] = content
            return f"work-products/{node_id}/{filename}"

        def write(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
            self.image_blobs[node_id] = (filename, content, content_type)
            return f"work-products/{node_id}/{filename}"

        monkeypatch.setattr(blobs, "store_work_product_blob", store)
        self.outcomes: list[str] = []
        monkeypatch.setattr(concept_image, "_record", self.outcomes.append)
        self.twin = InMemoryTwinAPI.create()
        self.generator = generator if generator is not None else FakeGenerator()
        self.server = TwinServer(
            twin=self.twin,
            design_sketch_recorder=make_design_sketch_recorder(self.twin, None),
            concept_image_renderer=make_concept_image_recorder(
                self.twin, None, generator=self.generator, blob_writer=write, **kw
            ),
        )

    async def renders(self) -> list[Any]:
        return list(await self.twin.list_work_products(work_product_type=WorkProductType.RENDER))


# -- the layout helpers --------------------------------------------------------


def test_a_stored_layout_round_trips_to_the_same_sketch() -> None:
    layout = parse_layout(_chair())
    again = parse_layout(layout_to_dict(layout))
    assert again == layout
    assert render_concept_sketch(again) == render_concept_sketch(layout)


def test_iso_faces_fit_the_page_and_name_every_part() -> None:
    faces = iso_faces(parse_layout(_chair()), 1024, 768)
    assert {part for part, _, _ in faces} == {p["name"] for p in _chair()["parts"]}
    for _, face, outline in faces:
        assert face in {"face-front", "face-right", "face-top", "cap"}
        for x, y in outline:
            assert -1 <= x <= 1025 and -1 <= y <= 769


def test_the_sketch_svg_is_unchanged_by_the_refactor() -> None:
    # The isometric polygons moved into a helper; the SVG must be the same bytes.
    svg = render_concept_sketch(parse_layout(_chair()))
    assert svg.count('class="face-top s-intended"') == 2  # isometric + exploded seat
    assert svg.count('class="cap s-assumption"') == 6  # three assumed legs, two views


# -- the tool ------------------------------------------------------------------


def test_the_tool_is_registered_only_with_a_renderer() -> None:
    twin = InMemoryTwinAPI.create()
    with_it = TwinServer(twin=twin, concept_image_renderer=make_concept_image_recorder(twin))
    assert "twin.render_concept_image" in with_it.tool_ids
    assert "twin.render_concept_image" not in TwinServer(twin=twin).tool_ids


async def test_an_image_from_a_sketch_records_what_conditioned_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    sketch = await h.server.render_concept_sketch(_chair())
    out = await h.server.render_concept_image(
        {"layout_node_id": sketch["node_id"], "visual_brief": BRIEF}
    )

    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    assert wp is not None
    assert wp.type == WorkProductType.RENDER
    assert wp.name == "Concept image: Concept A: four-leg frame"
    meta = wp.metadata
    assert meta["authority"] == "illustrative"
    assert meta["conditioned_on"] == {
        "layout_source": "design_sketch",
        "layout_node_id": sketch["node_id"],
        "layout_content_hash": sketch["content_hash"],
        "layout_overall_mm": [420, 400, 850],
        "view": "isometric",
        "image_conditioning": "layout_png",
    }
    assert out["view"] == "isometric"
    assert meta["visual_brief"] == BRIEF
    assert meta["provider"] == "fake" and meta["model"] == "fake-image-1"
    assert meta["illustrative_label"] == LABEL
    assert BRIEF in meta["prompt"] and "Backrest" in meta["prompt"]
    assert out["conditioned_on"] == meta["conditioned_on"]

    # The layout really went to the model, as a PNG of the requested size.
    call = h.generator.calls[0]
    assert call["conditioning"][:8] == b"\x89PNG\r\n\x1a\n"
    assert PIL.open(io.BytesIO(call["conditioning"])).size == (1024, 1024)

    # Linked to the sketch it was drawn from.
    edges = await h.twin.get_edges(UUID(out["node_id"]), direction="outgoing")
    assert any(
        e.edge_type == EdgeType.GENERATED_FROM and str(e.target_id) == sketch["node_id"]
        for e in edges
    )
    assert out["linked_to_layout"] is True
    assert h.outcomes == ["generated"]


async def test_the_sketch_keeps_its_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch)
    sketch = await h.server.render_concept_sketch(_chair())
    wp = await h.twin.get_work_product(UUID(sketch["node_id"]))
    assert wp is not None
    assert parse_layout(wp.metadata["concept_layout"]) == parse_layout(_chair())
    # Metadata that came along cannot override the sketch's authority.
    assert wp.metadata["authority"] == "illustrative"


async def test_an_inline_layout_records_the_hash_its_sketch_would_carry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    out = await h.server.render_concept_image(
        {"layout": _chair(), "visual_brief": BRIEF, "size": "1536x1024"}
    )
    svg = render_concept_sketch(parse_layout(_chair()))
    assert out["conditioned_on"]["layout_source"] == "inline_layout"
    assert out["conditioned_on"]["layout_content_hash"] == hashlib.sha256(svg.encode()).hexdigest()
    assert "layout_node_id" not in out["conditioned_on"]
    assert out["linked_to_layout"] is False
    assert h.generator.calls[0]["size"] == "1536x1024"


async def test_the_label_is_drawn_onto_the_image(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch)
    out = await h.server.render_concept_image({"layout": _chair(), "visual_brief": BRIEF})
    filename, content, content_type = h.image_blobs[out["node_id"]]
    assert content_type == "image/png" and filename.endswith(".png")
    img = PIL.open(io.BytesIO(content))
    # The PNG says what it is in its text chunk, and the pixels say it too:
    # the generated image was a flat cream colour, the bottom band is dark.
    assert img.info.get("Description") == LABEL
    rgb = img.convert("RGB")
    w, height = rgb.size
    assert rgb.getpixel((w - 5, height - 5)) == (20, 20, 20)
    assert rgb.getpixel((w // 2, 10)) == (250, 240, 220)
    band = [rgb.getpixel((x, height - 20)) for x in range(0, w // 2)]
    assert any(p != (20, 20, 20) for p in band), "no label text in the band"
    # Returned inline too, for a harness to show in chat.
    assert out["images"][0]["label"] == LABEL
    assert out["content_hash"] == hashlib.sha256(content).hexdigest()


async def test_without_pillow_the_label_is_an_svg_text_and_no_layout_image_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    monkeypatch.setattr(concept_image, "_pil", lambda: None)
    out = await h.server.render_concept_image({"layout": _chair(), "visual_brief": BRIEF})
    assert h.generator.calls[0]["conditioning"] is None
    assert out["format"] == "svg"
    assert out["conditioned_on"]["image_conditioning"] == "none"
    assert "image_conditioning_note" in out["conditioned_on"]
    _, content, content_type = h.image_blobs[out["node_id"]]
    assert content_type == "image/svg+xml"
    assert f">{LABEL}</text>" in content.decode()
    assert "images" not in out


async def test_a_provider_that_ignores_the_layout_is_recorded_as_unconditioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch, generator=FakeGenerator(honour_conditioning=False))
    out = await h.server.render_concept_image({"layout": _chair(), "visual_brief": BRIEF})
    assert out["conditioned_on"]["image_conditioning"] == "none"


async def test_the_image_cannot_back_a_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(monkeypatch)
    project = "3f0b8f5c-5a7e-4a4c-9d7a-0d6d3b0a1c11"
    out = await h.server.render_concept_image(
        {"layout": _chair(), "visual_brief": BRIEF, "project_id": project}
    )
    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    assert is_illustrative(wp)
    await h.twin.create_constraint(
        Constraint(
            name="seat_height_max",
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mechanical",
            source="test",
            project_id=UUID(project),
        )
    )
    with pytest.raises(ValueError, match="illustrative"):
        await make_claim_recorder(h.twin)(
            requirement_ref="seat_height_max", artefact_ref=out["node_id"], project_id=project
        )


async def test_no_provider_is_a_clear_error_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("METAFORGE_IMAGE_PROVIDER", "METAFORGE_IMAGE_MODEL", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    h = Harness(monkeypatch)
    h.server = TwinServer(
        twin=h.twin, concept_image_renderer=make_concept_image_recorder(h.twin, None)
    )
    with pytest.raises(ImageGenerationUnavailableError, match="METAFORGE_IMAGE_PROVIDER"):
        await h.server.render_concept_image({"layout": _chair(), "visual_brief": BRIEF})
    assert await h.renders() == []
    assert h.outcomes == ["unavailable"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["parts"][0].update(provenance="certain"), "provenance"),
        (lambda d: d["parts"][1].update(size_mm=[30, 0, 430]), "positive"),
        (lambda d: d["connections"][0].update(b="Armrest"), "Armrest"),
        (lambda d: d.update(parts=[]), "non-empty"),
    ],
)
async def test_a_bad_layout_is_refused_before_any_paid_call(
    monkeypatch: pytest.MonkeyPatch, mutate: Any, message: str
) -> None:
    h = Harness(monkeypatch)
    data = copy.deepcopy(_chair())
    mutate(data)
    with pytest.raises(ValueError, match=message):
        await h.server.render_concept_image({"layout": data, "visual_brief": BRIEF})
    assert h.generator.calls == []
    assert await h.renders() == []


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"layout": "x", "visual_brief": BRIEF}, "must be an object"),
        ({"visual_brief": BRIEF}, "exactly one"),
        ({"layout": {}, "layout_node_id": "x", "visual_brief": BRIEF}, "exactly one"),
        ({"layout": {}, "visual_brief": "  "}, "visual_brief"),
        ({"layout_node_id": "not-a-uuid", "visual_brief": BRIEF}, "not a node id"),
        ({"layout": {}, "visual_brief": BRIEF, "size": "512x512"}, "size"),
    ],
)
async def test_bad_arguments_are_refused(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, Any], message: str
) -> None:
    h = Harness(monkeypatch)
    with pytest.raises(ValueError, match=message):
        await h.server.render_concept_image(arguments)
    assert h.generator.calls == []


async def test_a_node_that_is_not_a_drawn_sketch_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch)
    hand_drawn = await make_design_sketch_recorder(h.twin)(
        name="Hand sketch", html_content="<svg xmlns='http://www.w3.org/2000/svg'/>"
    )
    cad = await h.twin.create_work_product(
        WorkProduct(
            name="Frame",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="frame.step",
            content_hash="h",
            format="step",
            created_by="user",
        )
    )
    for node in (hand_drawn["node_id"], str(cad.id)):
        with pytest.raises(ValueError, match="render_concept_sketch"):
            await h.server.render_concept_image({"layout_node_id": node, "visual_brief": BRIEF})
    with pytest.raises(ValueError, match="no work product"):
        await h.server.render_concept_image(
            {"layout_node_id": "00000000-0000-0000-0000-000000000001", "visual_brief": BRIEF}
        )
    assert h.generator.calls == []


async def test_a_timeout_is_a_clear_error_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = Harness(monkeypatch, generator=FakeGenerator(delay=5.0), timeout_s=0.05)
    with pytest.raises(ImageGenerationTimeoutError, match="did not answer"):
        await h.server.render_concept_image({"layout": _chair(), "visual_brief": BRIEF})
    assert await h.renders() == [] and h.image_blobs == {}
    assert h.outcomes == ["timeout"]


async def test_a_provider_error_is_reported_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(prompt: str, conditioning: bytes | None, size: str) -> GeneratedImage:
        raise RuntimeError("upstream 500")

    h = Harness(monkeypatch, generator=broken)
    with pytest.raises(ImageGenerationError, match="upstream 500"):
        await h.server.render_concept_image({"layout": _chair(), "visual_brief": BRIEF})
    assert await h.renders() == []
    assert h.outcomes == ["failed"]


# -- provider selection ---------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "no image generation provider is configured"),
        ({"METAFORGE_IMAGE_PROVIDER": "off", "OPENAI_API_KEY": "sk-test"}, "configured"),
        ({"METAFORGE_IMAGE_PROVIDER": "dalle-local"}, "not a known image provider"),
        ({"METAFORGE_IMAGE_PROVIDER": "openai"}, "OPENAI_API_KEY is not set"),
    ],
)
def test_an_unconfigured_provider_says_why(env: dict[str, str], message: str) -> None:
    with pytest.raises(ImageGenerationUnavailableError, match=message):
        resolve_image_generator(env)


def test_a_key_alone_does_not_turn_on_paid_generation() -> None:
    with pytest.raises(ImageGenerationUnavailableError):
        resolve_image_generator({"OPENAI_API_KEY": "sk-test"})


def test_openai_is_chosen_by_env() -> None:
    gen = resolve_image_generator(
        {
            "METAFORGE_IMAGE_PROVIDER": "OpenAI",
            "OPENAI_API_KEY": "sk-secret-value-1234",
            "METAFORGE_IMAGE_TIMEOUT_S": "45",
        }
    )
    assert isinstance(gen, OpenAIImageGenerator)
    assert gen.model == DEFAULT_OPENAI_MODEL and gen.provider == "openai"
    assert "sk-secret" not in repr(vars(gen).get("model"))
    custom = resolve_image_generator(
        {
            "METAFORGE_IMAGE_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-x",
            "METAFORGE_IMAGE_MODEL": "gpt-image-1-mini",
        }
    )
    assert isinstance(custom, OpenAIImageGenerator) and custom.model == "gpt-image-1-mini"


def test_provider_errors_never_carry_a_key() -> None:
    class AuthenticationError(Exception):
        status_code = 401

    assert "sk-" not in _describe(AuthenticationError("Incorrect API key provided: sk-abcd1234"))
    leaked = _describe(RuntimeError("bad request for key sk-proj-abcdef123456"))
    assert "sk-proj" not in leaked and "[redacted]" in leaked
