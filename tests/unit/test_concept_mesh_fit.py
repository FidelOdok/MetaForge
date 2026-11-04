"""A reference mesh scaled to its concept's envelope.

When a concept mesh is made from images drawn from a block layout, the GLB is
scaled uniformly so its box best fits the layout's envelope: one factor, the
one minimising the summed squared log ratio of the three axes, applied as a
parent node's ``scale`` in the glTF JSON. The buffers are never touched. What
no uniform factor can fix is recorded as ``proportion_error_pct``. Nothing
here calls Tripo: the provider runs on ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import math
import struct
from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.glb_bounds import (
    SCALE_NODE_NAME,
    envelope_fit,
    glb_bounds,
    gltf_json,
    scale_glb,
)
from tests.unit.test_concept_mesh import (  # noqa: F401 - _outcomes is an autouse fixture
    FakeTripo,
    Harness,
    _outcomes,
)

ENVELOPE = [490.0, 395.0, 290.0]
_BIN = 0x004E4942
BIN_PAYLOAD = bytes(range(48))


def _glb(doc: dict[str, Any], payload: bytes = BIN_PAYLOAD) -> bytes:
    """A GLB with a JSON chunk and a BIN chunk the scaling must not touch."""
    raw = json.dumps(doc).encode()
    raw += b" " * (-len(raw) % 4)
    chunks = struct.pack("<II", len(raw), 0x4E4F534A) + raw
    if payload:
        chunks += struct.pack("<II", len(payload), _BIN) + payload
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def _box(nodes: list[dict[str, Any]], scenes: list[list[int]] | None = None) -> bytes:
    """A unit cube mesh (mesh 0) under the given node graph."""
    return _glb(
        {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": s} for s in (scenes or [[0]])],
            "nodes": nodes,
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"min": [-0.5, -0.5, -0.5], "max": [0.5, 0.5, 0.5]}],
            "buffers": [{"byteLength": len(BIN_PAYLOAD)}],
        }
    )


def _bin_chunk(glb: bytes) -> bytes:
    (json_len,) = struct.unpack_from("<I", glb, 12)
    return glb[20 + json_len :]


#: Twice the envelope, in model units: width on glTF x, depth on z, height on y.
DOUBLE_GLB = _box([{"mesh": 0, "scale": [0.98, 0.58, 0.79]}])


# -- the fit -----------------------------------------------------------------------


def test_a_model_twice_the_envelope_is_halved_exactly() -> None:
    fit = envelope_fit(glb_bounds(DOUBLE_GLB).size, (490, 395, 290))
    assert fit["scale_factor"] == pytest.approx(0.5)
    assert fit["axis_mapping"] == "x=width, z=depth"
    assert fit["proportion_error_pct"] == {"width": 0.0, "depth": 0.0, "height": 0.0}


def test_the_horizontal_axes_pair_whichever_way_fits() -> None:
    # Width along glTF z, depth along x.
    fit = envelope_fit((0.79, 0.58, 0.98), (490, 395, 290))
    assert fit["axis_mapping"] == "z=width, x=depth"
    assert fit["scale_factor"] == pytest.approx(0.5)


def test_a_shape_that_does_not_match_keeps_a_proportion_error() -> None:
    # A cube against a 490 x 395 x 290 envelope: no uniform factor fits it.
    fit = envelope_fit((1.0, 1.0, 1.0), (490, 395, 290))
    geomean = (0.49 * 0.395 * 0.29) ** (1 / 3)
    assert fit["scale_factor"] == pytest.approx(geomean)
    expected = {
        axis: round((geomean - want) / want * 100, 1)
        for axis, want in (("width", 0.49), ("depth", 0.395), ("height", 0.29))
    }
    assert fit["proportion_error_pct"] == expected
    assert expected["width"] < 0 < expected["height"]
    # The factor is the least-squares one: nudging it either way does worse.
    logs = [math.log(w) for w in (0.49, 0.395, 0.29)]

    def cost(s: float) -> float:
        return sum((math.log(s) - v) ** 2 for v in logs)

    assert cost(geomean) < cost(geomean * 1.01)
    assert cost(geomean) < cost(geomean * 0.99)


def test_a_flat_model_cannot_be_fitted() -> None:
    with pytest.raises(ValueError, match="no extent"):
        envelope_fit((1.0, 0.0, 1.0), (490, 395, 290))


# -- the scaled file ---------------------------------------------------------------


def test_scaling_adds_a_parent_node_and_keeps_the_buffers() -> None:
    scaled = scale_glb(DOUBLE_GLB, 0.5)
    assert glb_bounds(scaled).size == pytest.approx((0.49, 0.29, 0.395))
    doc = gltf_json(scaled)
    assert doc["nodes"][0] == {"mesh": 0, "scale": [0.98, 0.58, 0.79]}
    assert doc["nodes"][1] == {
        "name": SCALE_NODE_NAME,
        "scale": [0.5, 0.5, 0.5],
        "children": [0],
    }
    assert doc["scenes"][0]["nodes"] == [1]
    assert _bin_chunk(scaled)[8:] == BIN_PAYLOAD
    (total,) = struct.unpack_from("<I", scaled, 8)
    assert total == len(scaled) and len(scaled) % 4 == 0


def test_a_nested_node_transform_is_preserved() -> None:
    half_turn = math.sqrt(0.5)
    nodes = [
        {"children": [1], "translation": [1.0, 0.0, 0.0], "rotation": [0, half_turn, 0, half_turn]},
        {"mesh": 0, "scale": [2.0, 1.0, 1.0], "translation": [0.0, 0.5, 0.0]},
    ]
    glb = _box(nodes)
    before = glb_bounds(glb)
    scaled = scale_glb(glb, 0.25)
    after = glb_bounds(scaled)
    doc = gltf_json(scaled)
    # Both original nodes are exactly as they were; only the new parent scales.
    assert doc["nodes"][:2] == nodes
    assert after.min == pytest.approx(tuple(v * 0.25 for v in before.min))
    assert after.max == pytest.approx(tuple(v * 0.25 for v in before.max))


def test_scenes_without_one_shared_root_list_scale_each_root_in_place() -> None:
    matrix = [1.0, 0, 0, 0, 0, 2.0, 0, 0, 0, 0, 1.0, 0, 3.0, 0, 0, 1.0]
    glb = _box([{"mesh": 0, "matrix": matrix}, {"mesh": 0}], scenes=[[0], [1]])
    before = glb_bounds(glb)
    scaled = scale_glb(glb, 2.0)
    doc = gltf_json(scaled)
    assert len(doc["nodes"]) == 2  # no parent added
    assert doc["nodes"][1] == {"mesh": 0, "translation": [0.0, 0.0, 0.0], "scale": [2.0, 2.0, 2.0]}
    after = glb_bounds(scaled)
    assert after.size == pytest.approx(tuple(v * 2 for v in before.size))


def test_a_bad_factor_is_refused() -> None:
    from api_gateway.twin.glb_bounds import GlbBoundsError

    with pytest.raises(GlbBoundsError):
        scale_glb(DOUBLE_GLB, 0.0)


# -- through the tool --------------------------------------------------------------


async def _views(h: Harness) -> dict[str, str]:
    return {v: await h.concept_image(view=v, overall_mm=ENVELOPE) for v in ("front", "back")}


async def test_with_an_envelope_the_stored_model_is_fitted_and_auto_size_is_off() -> None:
    h = Harness(FakeTripo(glb=DOUBLE_GLB))
    out = await h.server.generate_concept_mesh({"view_images": await _views(h)})
    sent = json.loads(h.fake.requests[2].content)
    assert sent["auto_size"] is False
    assert out["fit_to_envelope"] is True
    assert out["scale_factor"] == pytest.approx(0.5)
    assert out["bbox_m_before"] == [0.98, 0.58, 0.79]
    assert out["bbox_m"] == [0.49, 0.29, 0.395]
    assert out["proportion_error_pct"] == {"width": 0.0, "depth": 0.0, "height": 0.0}
    stored = h.blobs[out["node_id"]][1]
    assert glb_bounds(stored).size == pytest.approx((0.49, 0.29, 0.395))
    assert h.files[out["provider_glb_object_key"]] == DOUBLE_GLB
    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    assert wp.metadata["scale_factor"] == out["scale_factor"]
    assert wp.metadata["minio_object_key"] == out["minio_object_key"]


async def test_auto_size_can_still_be_asked_for_with_an_envelope() -> None:
    h = Harness(FakeTripo(glb=DOUBLE_GLB))
    out = await h.server.generate_concept_mesh({"view_images": await _views(h), "auto_size": True})
    assert json.loads(h.fake.requests[2].content)["auto_size"] is True
    assert out["bbox_units_before"] == "m"
    assert out["scale_factor"] == pytest.approx(0.5)


async def test_without_an_envelope_nothing_is_scaled_and_auto_size_is_on() -> None:
    h = Harness(FakeTripo(glb=DOUBLE_GLB, statuses=["success"]))
    out = await h.server.generate_concept_mesh({"prompt": "a microwave"})
    assert json.loads(h.fake.requests[0].content)["auto_size"] is True
    assert "scale_factor" not in out and "envelope_mm" not in out
    assert "provider_glb_object_key" not in out
    assert h.blobs[out["node_id"]][1] == DOUBLE_GLB
    assert len(h.files) == 1


async def test_fit_to_envelope_false_stores_the_provider_file_and_compares_it() -> None:
    h = Harness(FakeTripo(glb=DOUBLE_GLB))
    out = await h.server.generate_concept_mesh(
        {"view_images": await _views(h), "fit_to_envelope": False}
    )
    assert json.loads(h.fake.requests[2].content)["auto_size"] is False
    assert out["fit_to_envelope"] is False
    assert "scale_factor" not in out and "provider_glb_object_key" not in out
    assert h.blobs[out["node_id"]][1] == DOUBLE_GLB
    assert out["envelope_mm"] == ENVELOPE
    assert out["deviation_basis"] == "proportional"
    assert out["deviation_pct"] == {"width": 0.0, "depth": 0.0, "height": 0.0}


async def test_a_bad_fit_to_envelope_is_refused_before_any_call() -> None:
    h = Harness(FakeTripo(glb=DOUBLE_GLB))
    with pytest.raises(ValueError, match="fit_to_envelope"):
        await h.server.generate_concept_mesh({"prompt": "a microwave", "fit_to_envelope": "yes"})
    assert h.fake.requests == []
