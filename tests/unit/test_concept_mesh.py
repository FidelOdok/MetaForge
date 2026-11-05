"""The AI 3D reference mesh tool and its Tripo provider (FORGE-778).

A concept image (or a prompt) becomes a GLB through Tripo's image-to-3D API.
The mesh is illustrative: a ``render`` in GLB, linked to its image, never a
``cad_model`` and never evidence. Every test runs against
``httpx.MockTransport``; nothing here calls Tripo.
"""

from __future__ import annotations

import io
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from api_gateway.twin import concept_mesh
from api_gateway.twin.claim_recorder import (
    ILLUSTRATIVE_TYPES,
    is_illustrative,
    make_claim_recorder,
)
from api_gateway.twin.concept_mesh import LABEL, make_concept_mesh_recorder
from api_gateway.twin.mesh_generation import (
    DEFAULT_TIMEOUT_S,
    DEFAULT_TRIPO_MODEL,
    MeshGenerationError,
    MeshGenerationTimeoutError,
    MeshGenerationUnavailableError,
    TripoMeshGenerator,
    resolve_mesh_generator,
)
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.constraint import Constraint
from twin_core.models.enums import ConstraintSeverity, EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct

KEY = "tsk_test-secret-value-123456"
BASE = "https://openapi.tripo3d.ai/v3"
CDN = "https://cdn.tripo3d.ai/output/model_pbr.glb"
GLB = b"glTF\x02\x00\x00\x00" + b"\x00" * 64
PROJECT = "3f0b8f5c-5a7e-4a4c-9d7a-0d6d3b0a1c11"


def _png(size: tuple[int, int] = (512, 512)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (250, 240, 220)).save(buf, format="PNG")
    return buf.getvalue()


class FakeTripo:
    """A Tripo v3 API on ``httpx.MockTransport``, recording every request."""

    def __init__(
        self,
        *,
        statuses: list[str] | None = None,
        upload_status: int = 200,
        upload_body: dict[str, Any] | None = None,
        task_extra: dict[str, Any] | None = None,
        glb: bytes = GLB,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.statuses = list(statuses or ["queued", "running", "success"])
        self.upload_status = upload_status
        self.upload_body = upload_body
        self.task_extra = task_extra or {}
        self.glb = glb
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == f"{BASE}/files" and request.method == "POST":
            uploads = sum(1 for r in self.requests if r.url.path.endswith("/files"))
            token = "file_abc123" if uploads == 1 else f"file_abc123_{uploads}"
            body = self.upload_body or {"code": 0, "data": {"file_token": token}}
            return httpx.Response(self.upload_status, json=body)
        if url.startswith(f"{BASE}/generation/") and request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "task-1"}})
        if url == f"{BASE}/tasks/task-1" and request.method == "GET":
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            data: dict[str, Any] = {"task_id": "task-1", "status": status, **self.task_extra}
            if status == "success":
                data["output"] = {"model_url": CDN, "rendered_image_url": "https://cdn/x.png"}
            return httpx.Response(200, json={"code": 0, "data": data})
        if url == CDN:
            return httpx.Response(200, content=self.glb)
        return httpx.Response(404, json={"code": 404, "message": "no route"})

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]


class Clock:
    """A clock that only moves when the injected sleep is called."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _generator(fake: FakeTripo, clock: Clock, timeout_s: float = 600.0) -> TripoMeshGenerator:
    return TripoMeshGenerator(
        api_key=KEY,
        timeout_s=timeout_s,
        transport=fake.transport,
        sleep=clock.sleep,
        clock=clock,
    )


class Harness:
    def __init__(self, fake: FakeTripo | None = None, **kw: Any) -> None:
        self.fake = fake or FakeTripo()
        self.clock = Clock()
        self.twin = InMemoryTwinAPI.create()
        self.blobs: dict[str, tuple[str, bytes, str]] = {}
        #: Every blob written, by object key (a node may hold more than one).
        self.files: dict[str, bytes] = {}
        self.image_bytes: dict[str, bytes] = {}
        self.outcomes: list[str] = []
        self.generator = kw.pop("generator", _generator(self.fake, self.clock))

        def read(wp: Any) -> tuple[bytes, str]:
            return self.image_bytes[str(wp.id)], "concept.png"

        def write(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
            self.blobs[node_id] = (filename, content, content_type)
            key = f"work-products/{node_id}/{filename}"
            self.files[key] = content
            return key

        self.server = TwinServer(
            twin=self.twin,
            concept_mesh_generator=make_concept_mesh_recorder(
                self.twin,
                None,
                generator=self.generator,
                blob_reader=read,
                blob_writer=write,
                **kw,
            ),
        )

    async def concept_image(
        self,
        *,
        generated_by: str = "twin.render_concept_image",
        wp_type: Any = None,
        view: str | None = None,
        layout_hash: str = "layout-1",
        overall_mm: list[float] | None = None,
        layout_node_id: str | None = None,
    ) -> str:
        wp_id = uuid4()
        metadata: dict[str, Any] = {
            "generated_by": generated_by,
            "concept_name": "Concept A",
            "authority": "illustrative",
        }
        if view is not None or overall_mm is not None or layout_node_id is not None:
            cond: dict[str, Any] = {"layout_content_hash": layout_hash}
            if view is not None:
                cond["view"] = view
            if overall_mm is not None:
                cond["layout_overall_mm"] = overall_mm
            if layout_node_id is not None:
                cond["layout_node_id"] = layout_node_id
            metadata["conditioned_on"] = cond
        await self.twin.create_work_product(
            WorkProduct(
                id=wp_id,
                name="Concept image: Concept A",
                type=wp_type or WorkProductType.RENDER,
                domain="mechanical",
                file_path="",
                content_hash="abc123",
                format="png",
                metadata=metadata,
                created_by=generated_by,
                project_id=PROJECT,
            )
        )
        self.image_bytes[str(wp_id)] = _png()
        return str(wp_id)

    async def meshes(self) -> list[Any]:
        renders = await self.twin.list_work_products(work_product_type=WorkProductType.RENDER)
        made_here = "twin.generate_concept_mesh"
        return [wp for wp in renders if (wp.metadata or {}).get("generated_by") == made_here]


class Outcomes(list[str]):
    """The outcomes counted, in order; ``modes`` holds each one's mode label."""

    def __init__(self) -> None:
        super().__init__()
        self.modes: list[str] = []


@pytest.fixture(autouse=True)
def _outcomes(monkeypatch: pytest.MonkeyPatch) -> Outcomes:
    seen = Outcomes()

    def record(outcome: str, mode: str = "image") -> None:
        seen.append(outcome)
        seen.modes.append(mode)

    monkeypatch.setattr(concept_mesh, "_record", record)
    return seen


# -- the provider ------------------------------------------------------------


async def test_image_to_model_uploads_creates_polls_and_downloads() -> None:
    fake, clock = FakeTripo(), Clock()
    mesh = await _generator(fake, clock)(_png(), "image/png", None)
    assert fake.paths() == [
        "POST /v3/files",
        "POST /v3/generation/image-to-model",
        "GET /v3/tasks/task-1",
        "GET /v3/tasks/task-1",
        "GET /v3/tasks/task-1",
        "GET /output/model_pbr.glb",
    ]
    upload, create = fake.requests[0], fake.requests[1]
    assert b'name="file"' in upload.content and b"\x89PNG" in upload.content
    assert json.loads(create.content) == {"input": "file_abc123", "model": DEFAULT_TRIPO_MODEL}
    for api_call in fake.requests[:-1]:
        assert api_call.headers["Authorization"] == f"Bearer {KEY}"
    # The CDN download never carries the key.
    assert "Authorization" not in fake.requests[-1].headers
    assert clock.sleeps == [5.0, 5.0]
    assert mesh.glb_bytes == GLB
    assert (mesh.provider, mesh.task_id, mesh.mode) == ("tripo", "task-1", "image_to_model")


async def test_text_to_model_skips_the_upload() -> None:
    fake, clock = FakeTripo(statuses=["success"]), Clock()
    mesh = await _generator(fake, clock)(None, "", "a wooden bike rack")
    assert fake.paths()[:2] == ["POST /v3/generation/text-to-model", "GET /v3/tasks/task-1"]
    assert json.loads(fake.requests[0].content) == {
        "prompt": "a wooden bike rack",
        "model": DEFAULT_TRIPO_MODEL,
    }
    assert mesh.mode == "text_to_model"


@pytest.mark.parametrize("status", ["failed", "cancelled", "banned", "expired"])
async def test_a_task_that_ends_badly_names_its_status(status: str) -> None:
    fake = FakeTripo(statuses=["running", status], task_extra={"error_message": "bad input"})
    with pytest.raises(MeshGenerationError, match=f"task task-1 ended {status}: bad input"):
        await _generator(fake, Clock())(_png(), "image/png", None)
    assert not any(r.url.host == "cdn.tripo3d.ai" for r in fake.requests)


async def test_a_task_still_running_at_the_deadline_times_out() -> None:
    fake, clock = FakeTripo(statuses=["running"]), Clock()
    with pytest.raises(MeshGenerationTimeoutError, match="still running after 30s"):
        await _generator(fake, clock, timeout_s=30)(_png(), "image/png", None)
    # Bounded by the injected clock: no real waiting, no endless polling.
    assert sum(clock.sleeps) <= 30
    assert len(clock.sleeps) == 6


async def test_an_unknown_status_keeps_polling_until_success() -> None:
    fake = FakeTripo(statuses=["unknown", "success"])
    mesh = await _generator(fake, Clock())(_png(), "image/png", None)
    assert mesh.glb_bytes == GLB


async def test_a_rejected_key_is_reported_without_the_key() -> None:
    fake = FakeTripo(upload_status=401, upload_body={"code": 1002, "message": f"bad {KEY}"})
    with pytest.raises(MeshGenerationError) as err:
        await _generator(fake, Clock())(_png(), "image/png", None)
    assert "API key was not accepted" in str(err.value)
    assert KEY not in str(err.value)


async def test_a_refusal_message_is_redacted() -> None:
    fake = FakeTripo(
        upload_status=403,
        upload_body={"code": 2010, "message": f"no credit for {KEY} (key-abcdef123)"},
    )
    with pytest.raises(MeshGenerationError, match="HTTP 403") as err:
        await _generator(fake, Clock())(_png(), "image/png", None)
    text = str(err.value)
    assert "no credit" in text and KEY not in text and "key-abcdef123" not in text


async def test_a_download_that_is_not_a_glb_is_refused() -> None:
    fake = FakeTripo(statuses=["success"], glb=b"<html>expired</html>")
    with pytest.raises(MeshGenerationError, match="not a GLB"):
        await _generator(fake, Clock())(_png(), "image/png", None)


# -- env selection -------------------------------------------------------------


def test_a_key_alone_does_not_turn_it_on() -> None:
    with pytest.raises(MeshGenerationUnavailableError, match="METAFORGE_MESH_PROVIDER=tripo"):
        resolve_mesh_generator({"TRIPO_API_KEY": KEY})


def test_tripo_without_a_key_is_unavailable() -> None:
    with pytest.raises(MeshGenerationUnavailableError, match="TRIPO_API_KEY is not set"):
        resolve_mesh_generator({"METAFORGE_MESH_PROVIDER": "tripo"})


def test_an_unknown_provider_is_unavailable() -> None:
    with pytest.raises(MeshGenerationUnavailableError, match="not a known mesh provider"):
        resolve_mesh_generator({"METAFORGE_MESH_PROVIDER": "meshy", "TRIPO_API_KEY": KEY})


def test_tripo_reads_its_settings() -> None:
    gen = resolve_mesh_generator(
        {
            "METAFORGE_MESH_PROVIDER": "Tripo",
            "TRIPO_API_KEY": KEY,
            "METAFORGE_MESH_TIMEOUT_S": "120",
            "METAFORGE_MESH_MODEL_VERSION": "v3.0-20250812",
        }
    )
    assert gen.model_version == "v3.0-20250812"
    assert gen._timeout_s == 120.0
    defaults = resolve_mesh_generator({"METAFORGE_MESH_PROVIDER": "tripo", "TRIPO_API_KEY": KEY})
    assert defaults.model_version == DEFAULT_TRIPO_MODEL
    assert defaults._timeout_s == DEFAULT_TIMEOUT_S
    assert KEY not in repr(vars(defaults).get("model_version"))


# -- the tool ------------------------------------------------------------------


async def test_the_mesh_is_an_illustrative_glb_render_linked_to_its_image(
    _outcomes: list[str],
) -> None:
    h = Harness()
    image_id = await h.concept_image()
    out = await h.server.generate_concept_mesh({"image_node_id": image_id})
    assert out["format"] == "glb"
    assert out["provider"] == "tripo"
    assert out["task_id"] == "task-1"
    assert out["size_bytes"] == len(GLB)
    assert out["source_image_node_id"] == image_id
    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    assert wp.type == WorkProductType.RENDER
    assert wp.type != WorkProductType.CAD_MODEL
    assert wp.format == "glb"
    meta = wp.metadata
    assert meta["authority"] == "illustrative"
    assert meta["illustrative_label"] == LABEL == "AI 3D reference model, illustrative only"
    assert meta["provider"] == "tripo"
    assert meta["task_id"] == "task-1"
    assert meta["model_version"] == DEFAULT_TRIPO_MODEL
    assert meta["source_image_node_id"] == image_id
    assert meta["label_band_removed"] is True
    assert str(wp.project_id) == PROJECT
    filename, content, mime = h.blobs[out["node_id"]]
    assert filename.endswith(".glb") and content == GLB and mime == "model/gltf-binary"
    edges = await h.twin.get_edges(wp.id, direction="outgoing")
    assert any(
        e.edge_type == EdgeType.GENERATED_FROM and str(e.target_id) == image_id for e in edges
    )
    assert _outcomes == ["generated"]


async def test_the_label_band_is_cropped_before_upload() -> None:
    from PIL import Image

    h = Harness()
    image_id = await h.concept_image()
    await h.server.generate_concept_mesh({"image_node_id": image_id})
    upload = h.fake.requests[0].content
    start = upload.index(b"\x89PNG")
    sent = Image.open(io.BytesIO(upload[start:]))
    assert sent.size == (512, 512 - max(28, 512 // 18))


async def test_the_mesh_cannot_back_a_claim() -> None:
    assert "render" in ILLUSTRATIVE_TYPES
    h = Harness()
    out = await h.server.generate_concept_mesh({"image_node_id": await h.concept_image()})
    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    assert is_illustrative(wp)
    await h.twin.create_constraint(
        Constraint(
            name="rack_width_max",
            expression="True",
            severity=ConstraintSeverity.ERROR,
            domain="mechanical",
            source="test",
            project_id=UUID(PROJECT),
        )
    )
    with pytest.raises(ValueError, match="illustrative"):
        await make_claim_recorder(h.twin)(
            requirement_ref="rack_width_max", artefact_ref=out["node_id"], project_id=PROJECT
        )


async def test_text_to_model_through_the_tool() -> None:
    h = Harness(FakeTripo(statuses=["success"]))
    out = await h.server.generate_concept_mesh({"prompt": "a wall-mounted bike rack"})
    assert out["source_image_node_id"] is None
    wp = await h.twin.get_work_product(UUID(out["node_id"]))
    assert wp.metadata["prompt"] == "a wall-mounted bike rack"
    assert wp.metadata["mode"] == "text_to_model"


@pytest.mark.parametrize(
    ("make_args", "message"),
    [
        (lambda img: {}, "exactly one of"),
        (lambda img: {"image_node_id": img, "prompt": "x"}, "exactly one of"),
        (lambda img: {"prompt": "  "}, "non-empty"),
        (lambda img: {"prompt": "x" * 1025}, "longer than 1024"),
        (lambda img: {"image_node_id": "not-a-uuid"}, "not a node id"),
        (lambda img: {"image_node_id": str(uuid4())}, "not a concept image"),
        (lambda img: {"image_node_id": img, "name": " "}, "non-empty"),
    ],
)
async def test_bad_input_is_refused_before_any_http_call(
    make_args: Any, message: str, _outcomes: list[str]
) -> None:
    h = Harness()
    image_id = await h.concept_image()
    with pytest.raises(ValueError, match=message):
        await h.server.generate_concept_mesh(make_args(image_id))
    assert h.fake.requests == []
    assert await h.meshes() == []
    assert _outcomes == []


async def test_a_render_that_is_not_a_concept_image_is_refused() -> None:
    h = Harness()
    studio = await h.concept_image(generated_by="twin.render_work_product")
    sketch = await h.concept_image(wp_type=WorkProductType.DESIGN_SKETCH)
    for node in (studio, sketch):
        with pytest.raises(ValueError, match="not a concept image"):
            await h.server.generate_concept_mesh({"image_node_id": node})
    assert h.fake.requests == []


async def test_no_provider_is_a_clear_error_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, _outcomes: list[str]
) -> None:
    monkeypatch.delenv("METAFORGE_MESH_PROVIDER", raising=False)
    monkeypatch.setenv("TRIPO_API_KEY", KEY)
    h = Harness(generator=None)
    with pytest.raises(MeshGenerationUnavailableError, match="METAFORGE_MESH_PROVIDER"):
        await h.server.generate_concept_mesh({"image_node_id": await h.concept_image()})
    assert await h.meshes() == []
    assert _outcomes == ["unavailable"]


async def test_a_failed_task_writes_nothing(_outcomes: list[str]) -> None:
    h = Harness(FakeTripo(statuses=["failed"]))
    with pytest.raises(MeshGenerationError, match="ended failed"):
        await h.server.generate_concept_mesh({"image_node_id": await h.concept_image()})
    assert await h.meshes() == []
    assert h.blobs == {}
    assert _outcomes == ["failed"]


async def test_a_timeout_is_counted_and_writes_nothing(_outcomes: list[str]) -> None:
    fake, clock = FakeTripo(statuses=["queued"]), Clock()
    h = Harness(fake, generator=_generator(fake, clock, timeout_s=20))
    with pytest.raises(MeshGenerationTimeoutError):
        await h.server.generate_concept_mesh({"image_node_id": await h.concept_image()})
    assert await h.meshes() == []
    assert _outcomes == ["timeout"]


async def test_the_tool_is_registered_with_its_schema() -> None:
    h = Harness()
    manifest = h.server._tools["twin.generate_concept_mesh"].manifest
    props = manifest.input_schema["properties"]
    assert set(props) == {
        "image_node_id",
        "view_images",
        "prompt",
        "name",
        "project_id",
        "domain",
        "auto_size",
        "face_limit",
        "texture",
        "generate_parts",
        "smart_low_poly",
        "fit_to_envelope",
        # The manufacturing context: speculative mode and the component scope.
        "speculative",
        "component",
        "wait",
        # FORGE-788: the job's target, for supersede and stale results.
        "target_key",
        "base_revision",
    }
    assert props["view_images"]["required"] == ["front"]
    assert set(props["view_images"]["properties"]) == {"front", "left", "back", "right"}
    assert {"node_id", "format", "provider", "task_id", "size_bytes", "source_image_node_id"} <= (
        set(manifest.output_schema["properties"])
    )


def test_the_tool_is_annotated_and_deep_linked() -> None:
    from mcp_core.annotations import annotations_for
    from mcp_core.deeplinks import _ROUTES

    hints = annotations_for("twin.generate_concept_mesh")
    assert hints.get("openWorldHint") is True
    assert hints.get("destructiveHint") is False
    assert "twin.generate_concept_mesh" in _ROUTES
