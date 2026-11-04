"""Multiview 3D reference models from a concept's own views (FORGE-779).

The concept's straight-on views (front, left, back, right) are each rendered
as a concept image conditioned on that orthographic view of the block layout,
then sent together to Tripo's multiview-to-model endpoint. Every mode takes
the generation options, and the GLB's bounding box is read back and compared
with the layout's envelope. Everything runs against ``httpx.MockTransport``
or a fake image model; nothing here calls a paid API.
"""

from __future__ import annotations

import io
import json
import math
import struct
from typing import Any
from uuid import UUID, uuid4

import pytest

from api_gateway.twin.concept_image import build_prompt, rasterise_layout
from api_gateway.twin.glb_bounds import GlbBoundsError, envelope_deviation, glb_bounds
from api_gateway.twin.mesh_generation import DEFAULT_TRIPO_MODEL, MeshGenerationError
from tests.unit.test_concept_image import BRIEF, _chair
from tests.unit.test_concept_image import Harness as ImageHarness
from tests.unit.test_concept_mesh import (  # noqa: F401 - _outcomes is an autouse fixture
    GLB,
    PROJECT,
    Clock,
    FakeTripo,
    Harness,
    Outcomes,
    _generator,
    _outcomes,
    _png,
)
from tool_registry.tools.twin.concept_sketch import (
    IMAGE_VIEWS,
    ORTHO_IMAGE_VIEWS,
    ortho_faces,
    parse_layout,
)
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct

ENVELOPE = [490.0, 395.0, 290.0]
DEFAULT_OPTIONS = {
    "auto_size": True,
    "face_limit": 100_000,
    "texture": True,
    "pbr": True,
    "smart_low_poly": False,
    "generate_parts": False,
}


# -- the orthographic views of a layout ------------------------------------------


def _probe() -> Any:
    """A body, a knob at its front right top, and a dial facing the front."""
    return parse_layout(
        {
            "concept_name": "Probe",
            "parts": [
                {
                    "name": "Body",
                    "size_mm": [100, 50, 30],
                    "position_mm": [0, 0, 0],
                    "provenance": "intended",
                },
                {
                    "name": "Knob",
                    "size_mm": [20, 10, 10],
                    "position_mm": [80, 0, 30],
                    "provenance": "intended",
                },
                {
                    "name": "Dial",
                    "shape": "cylinder",
                    "axis": "y",
                    "size_mm": [10, 4, 10],
                    "position_mm": [10, -4, 10],
                    "provenance": "intended",
                },
            ],
        }
    )


def _centre_x(faces: list[Any], part: str) -> float:
    pts = [p for name, _, outline in faces if name == part for p in outline]
    return sum(x for x, _ in pts) / len(pts)


def _height(faces: list[Any], part: str) -> float:
    ys = [y for name, _, outline in faces if name == part for _, y in outline]
    return max(ys) - min(ys)


def test_the_image_views_are_isometric_and_four_sides() -> None:
    assert IMAGE_VIEWS == ("isometric", "front", "left", "back", "right")
    assert set(ORTHO_IMAGE_VIEWS) == {"front", "left", "back", "right"}


@pytest.mark.parametrize(
    ("view", "knob_side"),
    [
        # Front: x to the right, so the knob (high x) is right of centre.
        ("front", "right"),
        # Back: x mirrored, so the knob is on the left.
        ("back", "left"),
        # Left: looking along +x, the front (y = 0) is on the right.
        ("left", "right"),
        # Right: looking along -x, the front is on the left.
        ("right", "left"),
    ],
)
def test_each_view_is_seen_from_its_own_side(view: str, knob_side: str) -> None:
    faces = ortho_faces(_probe(), view, 1000, 1000)
    knob, body = _centre_x(faces, "Knob"), _centre_x(faces, "Body")
    assert (knob > body) == (knob_side == "right")
    for _, _, outline in faces:
        for x, y in outline:
            assert 0 <= x <= 1000 and 0 <= y <= 1000


def test_up_is_up_in_every_view() -> None:
    for view in ORTHO_IMAGE_VIEWS:
        faces = ortho_faces(_probe(), view, 800, 800)
        knob_y = [y for n, _, o in faces if n == "Knob" for _, y in o]
        body_y = [y for n, _, o in faces if n == "Body" for _, y in o]
        # The knob sits on top of the body: smaller page y.
        assert max(knob_y) == pytest.approx(min(body_y))


def test_the_views_share_one_scale() -> None:
    heights = {v: _height(ortho_faces(_probe(), v, 1024, 1024), "Body") for v in ORTHO_IMAGE_VIEWS}
    assert len({round(h, 6) for h in heights.values()}) == 1
    front = ortho_faces(_probe(), "front", 1024, 1024)
    xs = [x for n, _, o in front if n == "Body" for x, _ in o]
    # 100 mm wide against 30 mm tall: proportions kept.
    assert (max(xs) - min(xs)) / heights["front"] == pytest.approx(100 / 30)


def test_a_cylinder_end_on_is_an_ellipse_and_side_on_a_rectangle() -> None:
    front = {n: (cls, o) for n, cls, o in ortho_faces(_probe(), "front", 600, 600)}
    left = {n: (cls, o) for n, cls, o in ortho_faces(_probe(), "left", 600, 600)}
    assert front["Dial"][0] == "cap" and len(front["Dial"][1]) > 8
    assert left["Dial"][0] == "face-front" and len(left["Dial"][1]) == 4
    assert front["Body"][0] == "face-front" and len(front["Body"][1]) == 4


def test_the_nearest_part_is_drawn_last() -> None:
    # From the front the dial (y < 0) is nearest; from the back it is farthest.
    assert [n for n, _, _ in ortho_faces(_probe(), "front", 500, 500)][-1] == "Dial"
    assert [n for n, _, _ in ortho_faces(_probe(), "back", 500, 500)][0] == "Dial"


def test_an_unknown_view_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown orthographic view"):
        ortho_faces(_probe(), "top", 500, 500)


def test_an_orthographic_view_rasterises_to_a_png() -> None:
    from PIL import Image

    png = rasterise_layout(ortho_faces(_probe(), "front", 1024, 1024), "1024x1024")
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(png)).convert("RGB")
    assert img.size == (1024, 1024)
    assert img.getpixel((512, 512)) != (255, 255, 255)  # the body is drawn
    assert img.getpixel((5, 5)) == (255, 255, 255)


# -- the view prompt ------------------------------------------------------------


def test_the_prompt_names_the_view() -> None:
    prompt = build_prompt(BRIEF, "Concept A", ["Seat", "Leg"], "front")
    assert "a straight-on orthographic FRONT view of the product, no perspective" in prompt
    assert "isometric" not in prompt
    assert "Parts, as laid out: Seat, Leg" in prompt
    assert "orthographic LEFT view" in build_prompt(BRIEF, "Concept A", ["Seat"], "left")
    with pytest.raises(ValueError, match="unknown view"):
        build_prompt(BRIEF, "Concept A", ["Seat"], "top")


def test_the_isometric_prompt_is_unchanged() -> None:
    prompt = build_prompt(BRIEF, "Concept A", ["Seat"])
    assert prompt == build_prompt(BRIEF, "Concept A", ["Seat"], "isometric")
    assert "seen in isometric view: keep its overall proportions" in prompt
    assert "orthographic" not in prompt


async def test_a_front_view_image_records_its_view(monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    h = ImageHarness(monkeypatch)
    sketch = await h.server.render_concept_sketch(_chair())
    out = await h.server.render_concept_image(
        {"layout_node_id": sketch["node_id"], "visual_brief": BRIEF, "view": "front"}
    )
    assert out["view"] == "front"
    assert out["conditioned_on"]["view"] == "front"
    assert out["conditioned_on"]["image_conditioning"] == "layout_png"
    assert out["conditioned_on"]["layout_overall_mm"] == [420, 400, 850]
    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    assert wp.metadata["conditioned_on"]["view"] == "front"
    assert "orthographic FRONT view" in wp.metadata["prompt"]
    # The label banner and the critique are as for any concept image.
    assert "critique" in out and out["images"][0]["label"]
    call = h.generator.calls[0]
    assert "orthographic FRONT view" in call["prompt"]
    # The conditioning is the front view: the chair is taller than it is wide.
    img = Image.open(io.BytesIO(call["conditioning"])).convert("L")
    bbox = img.point(lambda v: 255 if v < 250 else 0).getbbox()
    assert bbox is not None and (bbox[3] - bbox[1]) > (bbox[2] - bbox[0])


async def test_a_bad_view_is_refused_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ImageHarness(monkeypatch)
    with pytest.raises(ValueError, match="'view' must be one of"):
        await h.server.render_concept_image(
            {"layout": _chair(), "visual_brief": BRIEF, "view": "top"}
        )
    assert h.generator.calls == []


def test_the_image_tool_offers_the_views(monkeypatch: pytest.MonkeyPatch) -> None:
    h = ImageHarness(monkeypatch)
    props = h.server._tools["twin.render_concept_image"].manifest.input_schema["properties"]
    assert props["view"]["enum"] == list(IMAGE_VIEWS)


# -- the multiview mesh ----------------------------------------------------------


def _glb_doc(doc: dict[str, Any]) -> bytes:
    """A GLB holding only a JSON chunk: enough for its bounds to be read."""
    raw = json.dumps(doc).encode()
    raw += b" " * (-len(raw) % 4)
    header = b"glTF" + struct.pack("<II", 2, 12 + 8 + len(raw))
    return header + struct.pack("<II", len(raw), 0x4E4F534A) + raw


def _box_glb(**node: Any) -> bytes:
    """A unit cube mesh under one node carrying ``node``'s transform."""
    return _glb_doc(
        {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0, **node}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"min": [-0.5, -0.5, -0.5], "max": [0.5, 0.5, 0.5]}],
        }
    )


#: 490 wide, 290 tall and 450 deep (glTF z): about 14% deeper than 395.
DEEP_GLB = _box_glb(scale=[0.49, 0.29, 0.45], translation=[3.0, 0.145, -1.0])


async def _views(h: Harness, *names: str, **kw: Any) -> dict[str, str]:
    return {v: await h.concept_image(view=v, overall_mm=ENVELOPE, **kw) for v in names}


async def test_a_multiview_model_uploads_each_view_and_sends_view_keys(
    _outcomes: Outcomes,  # noqa: F811
) -> None:
    from PIL import Image

    h = Harness(FakeTripo(glb=DEEP_GLB))
    views = await _views(h, "right", "front", "left")
    out = await h.server.generate_concept_mesh({"view_images": views})
    assert h.fake.paths()[:4] == [
        "POST /v3/files",
        "POST /v3/files",
        "POST /v3/files",
        "POST /v3/generation/multiview-to-model",
    ]
    # Uploaded in Tripo's view order, each named for its view, banner cropped.
    for upload, view in zip(h.fake.requests[:3], ("front", "left", "right"), strict=True):
        assert f'filename="{view}.png"'.encode() in upload.content
        start = upload.content.index(b"\x89PNG")
        assert Image.open(io.BytesIO(upload.content[start:])).size == (512, 512 - 28)
    body = json.loads(h.fake.requests[3].content)
    assert body == {
        "inputs": [
            {"front": "file_abc123"},
            {"left": "file_abc123_2"},
            {"right": "file_abc123_3"},
        ],
        "model": DEFAULT_TRIPO_MODEL,
        **DEFAULT_OPTIONS,
    }
    assert out["mode"] == "multiview_to_model"
    assert out["view_image_node_ids"] == views
    assert _outcomes == ["generated"] and _outcomes.modes == ["multiview"]


async def test_a_multiview_model_records_its_views_edges_and_size() -> None:
    h = Harness(FakeTripo(glb=DEEP_GLB))
    views = await _views(h, "front", "back")
    out = await h.server.generate_concept_mesh({"view_images": views})
    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    meta = wp.metadata
    assert wp.type == WorkProductType.RENDER and wp.format == "glb"
    assert meta["authority"] == "illustrative"
    assert meta["mode"] == "multiview_to_model"
    assert meta["view_image_node_ids"] == views
    assert meta["source_image_node_id"] == views["front"]
    assert meta["label_band_removed"] is True
    assert meta["label_band_removed_views"] == {"front": True, "back": True}
    assert meta["generation_options"] == DEFAULT_OPTIONS
    assert str(wp.project_id) == PROJECT
    edges = await h.twin.get_edges(wp.id, direction="outgoing")
    targets = {str(e.target_id) for e in edges if e.edge_type == EdgeType.GENERATED_FROM}
    assert targets == set(views.values())
    # The size check: read from the GLB, against the layout's envelope.
    assert meta["bbox_m"] == [0.49, 0.29, 0.45]
    assert meta["bbox_units"] == "m"
    assert meta["bbox_approximate"] is False
    assert meta["envelope_mm"] == ENVELOPE
    assert meta["axis_mapping"] == "x=width, z=depth"
    assert meta["deviation_basis"] == "absolute"
    assert meta["deviation_pct"] == {"width": 0.0, "depth": 13.9, "height": 0.0}
    assert out["deviation_pct"] == meta["deviation_pct"]
    # Never rescaled: the stored file is the one Tripo returned.
    assert h.blobs[out["node_id"]][1] == DEEP_GLB


def _args(views: dict[str, str], iso: str, case: str) -> dict[str, Any]:
    f, left, back = views["front"], views["left"], views["back"]
    return {
        "no_front": {"view_images": {"left": left, "back": back}},
        "one_view": {"view_images": {"front": f}},
        "unknown_view": {"view_images": {"front": f, "top": left}},
        "same_image": {"view_images": {"front": f, "left": f}},
        "isometric_as_left": {"view_images": {"front": f, "left": iso}},
        "back_as_left": {"view_images": {"front": f, "left": back}},
        "not_a_render": {"view_images": {"front": f, "left": str(uuid4())}},
        "blank_ref": {"view_images": {"front": f, "left": " "}},
        "not_an_object": {"view_images": ["front"]},
        "empty": {"view_images": {}},
        "with_image": {"view_images": views, "image_node_id": iso},
        "with_prompt": {"view_images": views, "prompt": "x"},
        "parts_textured": {"image_node_id": iso, "generate_parts": True, "texture": True},
        "parts_low_poly": {"view_images": views, "generate_parts": True, "smart_low_poly": True},
        "zero_faces": {"image_node_id": iso, "face_limit": 0},
        "text_faces": {"image_node_id": iso, "face_limit": "many"},
        "bool_faces": {"image_node_id": iso, "face_limit": True},
        "too_many_faces": {"image_node_id": iso, "face_limit": 2_000_001},
        "low_poly_faces": {"image_node_id": iso, "smart_low_poly": True, "face_limit": 50_000},
        "auto_size_text": {"prompt": "x", "auto_size": "yes"},
        "texture_int": {"prompt": "x", "texture": 1},
    }[case]


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("no_front", "needs a front view"),
        ("one_view", "at least 2"),
        ("unknown_view", "unknown view top"),
        ("same_image", "its own image"),
        ("isometric_as_left", "is a isometric image"),
        ("back_as_left", "is a back image"),
        ("not_a_render", "not a concept image"),
        ("blank_ref", "non-empty"),
        ("not_an_object", "must be an object"),
        ("empty", "must be an object"),
        ("with_image", "exactly one of"),
        ("with_prompt", "exactly one of"),
        ("parts_textured", "untextured"),
        ("parts_low_poly", "cannot be combined"),
        ("zero_faces", "positive integer"),
        ("text_faces", "positive integer"),
        ("bool_faces", "positive integer"),
        ("too_many_faces", "above"),
        ("low_poly_faces", "500 to 20000"),
        ("auto_size_text", "true or false"),
        ("texture_int", "true or false"),
    ],
)
async def test_bad_multiview_or_option_input_is_refused_before_any_http_call(
    case: str,
    message: str,
    _outcomes: Outcomes,  # noqa: F811
) -> None:
    h = Harness()
    views = await _views(h, "front", "left", "back")
    iso = await h.concept_image()
    with pytest.raises(ValueError, match=message):
        await h.server.generate_concept_mesh(_args(views, iso, case))
    assert h.fake.requests == []
    assert await h.meshes() == []
    assert _outcomes == []


async def test_views_drawn_from_different_layouts_are_refused() -> None:
    h = Harness()
    front = await h.concept_image(view="front", layout_hash="layout-1")
    left = await h.concept_image(view="left", layout_hash="layout-2")
    with pytest.raises(ValueError, match="different layouts"):
        await h.server.generate_concept_mesh({"view_images": {"front": front, "left": left}})
    assert h.fake.requests == []


async def test_generate_parts_sends_an_untextured_request_and_says_so() -> None:
    h = Harness()
    out = await h.server.generate_concept_mesh(
        {"view_images": await _views(h, "front", "left"), "generate_parts": True}
    )
    body = json.loads(h.fake.requests[2].content)
    assert body["generate_parts"] is True
    assert body["texture"] is False and body["pbr"] is False
    assert "texture and pbr were sent false" in out["generation_options_note"]
    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    assert wp.metadata["generation_options_note"] == out["generation_options_note"]
    # texture false with generate_parts is what it needs, so it is accepted too.
    again = Harness()
    await again.server.generate_concept_mesh(
        {"prompt": "a rack", "generate_parts": True, "texture": False}
    )
    assert json.loads(again.fake.requests[0].content)["texture"] is False


async def test_options_are_passed_through_on_the_single_image_path() -> None:
    h = Harness()
    out = await h.server.generate_concept_mesh(
        {
            "image_node_id": await h.concept_image(),
            "auto_size": False,
            "face_limit": 5000,
            "texture": False,
            "smart_low_poly": True,
        }
    )
    body = json.loads(h.fake.requests[1].content)
    assert body == {
        "input": "file_abc123",
        "model": DEFAULT_TRIPO_MODEL,
        "auto_size": False,
        "face_limit": 5000,
        "texture": False,
        "pbr": False,
        "smart_low_poly": True,
        "generate_parts": False,
    }
    assert out["generation_options"] == {k: body[k] for k in DEFAULT_OPTIONS}
    assert "generation_options_note" not in out


async def test_smart_low_poly_defaults_to_its_own_face_limit() -> None:
    h = Harness()
    await h.server.generate_concept_mesh({"prompt": "a rack", "smart_low_poly": True})
    assert json.loads(h.fake.requests[0].content)["face_limit"] == 20_000


async def test_the_single_image_path_keeps_its_shape_with_default_options(
    _outcomes: Outcomes,  # noqa: F811
) -> None:
    h = Harness()
    image_id = await h.concept_image()
    out = await h.server.generate_concept_mesh({"image_node_id": image_id})
    assert h.fake.paths()[:2] == ["POST /v3/files", "POST /v3/generation/image-to-model"]
    body = json.loads(h.fake.requests[1].content)
    assert body == {"input": "file_abc123", "model": DEFAULT_TRIPO_MODEL, **DEFAULT_OPTIONS}
    assert out["mode"] == "image_to_model"
    assert out["source_image_node_id"] == image_id
    assert "view_image_node_ids" not in out
    # The placeholder GLB has no JSON chunk: said so, and the mesh is still stored.
    assert "could not be read" in out["bbox_note"]
    assert "deviation_pct" not in out
    assert _outcomes.modes == ["image"]


async def test_text_to_model_sends_the_options_and_counts_its_mode(
    _outcomes: Outcomes,  # noqa: F811
) -> None:
    h = Harness(FakeTripo(statuses=["success"]))
    out = await h.server.generate_concept_mesh({"prompt": "a bike rack", "face_limit": 20_000})
    body = json.loads(h.fake.requests[0].content)
    assert body["prompt"] == "a bike rack" and body["face_limit"] == 20_000
    assert body["auto_size"] is True
    assert out["mode"] == "text_to_model"
    assert "envelope_mm" not in out
    assert _outcomes.modes == ["text"]


async def test_a_single_image_finds_its_envelope_on_its_sketch() -> None:
    h = Harness(FakeTripo(glb=DEEP_GLB))
    sketch_id = uuid4()
    await h.twin.create_work_product(
        WorkProduct(
            id=sketch_id,
            name="Concept sketch: Concept A",
            type=WorkProductType.DESIGN_SKETCH,
            domain="mechanical",
            file_path="",
            content_hash="sketch-hash",
            format="svg",
            metadata={
                "concept_layout": {
                    "concept_name": "Concept A",
                    "parts": [
                        {
                            "name": "Body",
                            "size_mm": [490, 395, 250],
                            "position_mm": [0, 0, 0],
                            "provenance": "intended",
                        },
                        {
                            "name": "Lid",
                            "size_mm": [200, 100, 40],
                            "position_mm": [10, 10, 250],
                            "provenance": "intended",
                        },
                    ],
                }
            },
            created_by="twin.render_concept_sketch",
        )
    )
    image_id = await h.concept_image(layout_node_id=str(sketch_id))
    out = await h.server.generate_concept_mesh({"image_node_id": image_id})
    assert out["envelope_mm"] == ENVELOPE
    assert out["deviation_pct"]["depth"] == 13.9


async def test_the_provider_drops_options_its_endpoint_does_not_take() -> None:
    fake = FakeTripo(statuses=["success"])
    mesh = await _generator(fake, Clock())(
        _png(), "image/png", None, options={"face_limit": 9000, "not_a_tripo_option": 1}
    )
    body = json.loads(fake.requests[1].content)
    assert body == {"input": "file_abc123", "model": DEFAULT_TRIPO_MODEL, "face_limit": 9000}
    assert mesh.options == {"face_limit": 9000}


async def test_the_provider_refuses_a_multiview_without_a_front() -> None:
    fake = FakeTripo()
    with pytest.raises(MeshGenerationError, match="front view"):
        await _generator(fake, Clock())(
            None, "", None, views={"left": (_png(), "image/png"), "back": (_png(), "image/png")}
        )
    assert fake.requests == []


# -- reading a GLB's size --------------------------------------------------------


def test_bounds_follow_the_node_transforms() -> None:
    bounds = glb_bounds(DEEP_GLB)
    assert bounds.min == pytest.approx((2.755, 0.0, -1.225))
    assert bounds.size == pytest.approx((0.49, 0.29, 0.45))
    assert bounds.approximate is False and bounds.method == "node_transforms"


def test_bounds_through_a_matrix_and_a_parent() -> None:
    doc = {
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {"children": [1], "matrix": [2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 1, 1, 1, 1]},
            {"mesh": 0, "scale": [1, 2, 3]},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"min": [0, 0, 0], "max": [1, 1, 1]}],
    }
    bounds = glb_bounds(_glb_doc(doc))
    assert bounds.min == pytest.approx((1, 1, 1))
    assert bounds.size == pytest.approx((2, 4, 6))
    assert bounds.approximate is False


def test_a_rotated_node_is_approximate() -> None:
    s = math.sin(math.pi / 4)
    bounds = glb_bounds(_box_glb(scale=[1, 2, 3], rotation=[0, s, 0, s]))
    # A quarter turn about y swaps x and z.
    assert bounds.size == pytest.approx((3, 2, 1))
    assert bounds.approximate is True


def test_no_scene_graph_falls_back_to_the_accessors() -> None:
    doc = {
        "meshes": [
            {"primitives": [{"attributes": {"POSITION": 0}}, {"attributes": {"POSITION": 1}}]}
        ],
        "accessors": [
            {"min": [0, 0, 0], "max": [1, 1, 1]},
            {"min": [-1, 0, 0], "max": [0, 2, 0.5]},
        ],
    }
    bounds = glb_bounds(_glb_doc(doc))
    assert bounds.size == pytest.approx((2, 2, 1))
    assert bounds.approximate is True and bounds.method == "accessor_union"


@pytest.mark.parametrize("data", [b"not a glb at all", GLB, _glb_doc({"meshes": []})])
def test_an_unreadable_glb_is_a_clear_error(data: bytes) -> None:
    with pytest.raises(GlbBoundsError):
        glb_bounds(data)


def test_deviation_without_auto_size_is_of_proportions() -> None:
    # Normalised units, largest axis 1: the shape is still 14% too deep.
    dev = envelope_deviation((1.0, 0.592, 0.919), (490, 395, 290), scaled_to_metres=False)
    assert dev["basis"] == "proportional"
    assert dev["axis_mapping"] == "x=width, z=depth"
    assert dev["deviation_pct"]["depth"] == pytest.approx(14.0, abs=0.1)
    assert abs(dev["deviation_pct"]["width"]) < 0.1


def test_deviation_pairs_the_horizontal_axes_the_way_that_fits() -> None:
    # Facing along x: glTF x is the depth.
    dev = envelope_deviation((0.395, 0.29, 0.49), (490, 395, 290), scaled_to_metres=True)
    assert dev["axis_mapping"] == "z=width, x=depth"
    assert dev["deviation_pct"] == {"width": 0.0, "depth": 0.0, "height": 0.0}
