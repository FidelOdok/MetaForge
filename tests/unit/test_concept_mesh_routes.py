"""Make a 3D concept from the Create stage (the concept mesh route).

``POST /v1/twin/projects/{id}/concept-mesh`` starts ``twin.generate_concept_mesh``
as a background job from the accepted generated concept, else the newest
concept image, else the brief in words. A person's request runs; an agent is
refused; past the hourly mesh cap, or for a tool a policy may never approve,
the route answers 409 ``spend_confirmation_required`` until the person
confirms. Every provider here is a fake: nothing calls Tripo or an image API.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from api_gateway.approvals import policy as approval_policy
from api_gateway.approvals.policy import ApprovalPolicy, PolicyStore
from api_gateway.operations.routes import publish_local
from api_gateway.twin import concept_mesh_routes as mesh_routes
from api_gateway.twin import routes as twin_routes
from api_gateway.twin.concept_mesh import (
    LABEL,
    TOOL,
    make_concept_mesh_recorder,
    source_kind,
)
from api_gateway.twin.image_generation import (
    GeneratedImage,
    ImageRequest,
    ProviderCapabilities,
)
from api_gateway.twin.mesh_generation import GeneratedMesh
from api_gateway.twin.sketch_generation import GenerateSketchRequest, SketchGenerationService
from mcp_core.guardrails import policy_may_approve
from tool_registry.jobs import JobRunner, JobStore
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.work_product import WorkProduct

PROJECT = "21111111-2222-3333-4444-555555555555"
GLB = b"glTF\x02\x00\x00\x00" + b"\x00" * 64


def _png(color: tuple[int, int, int] = (200, 200, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buf, format="PNG")
    return buf.getvalue()


class FakeImages:
    """A concept image provider: returns one fixed picture."""

    provider = "fake"
    model = "fake-image-1"
    capabilities = ProviderCapabilities(mask=False, max_images=4)

    async def generate_request(self, request: ImageRequest) -> GeneratedImage:
        return GeneratedImage(
            image_bytes=_png((120, 160, 200)),
            mime="image/png",
            provider=self.provider,
            model=self.model,
            conditioned=bool(request.images),
            masked=False,
            images_sent=len(request.images),
        )


class FakeMesh:
    """A Tripo stand-in that records each call; never a paid request."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        image: bytes | None,
        mime: str,
        prompt: str | None,
        *,
        views: Any = None,
        options: dict[str, Any] | None = None,
    ) -> GeneratedMesh:
        self.calls.append({"image": image, "mime": mime, "prompt": prompt})
        return GeneratedMesh(
            glb_bytes=GLB,
            provider="tripo",
            model_version="v-test",
            task_id=f"task-{len(self.calls)}",
            mode="image_to_model" if image is not None else "text_to_model",
            options=dict(options or {}),
        )


class World:
    def __init__(self) -> None:
        self.twin = InMemoryTwinAPI.create()
        self.blobs: dict[str, bytes] = {}
        self.mesh = FakeMesh()
        self.runner = JobRunner(JobStore(":memory:"), publisher=publish_local)
        self.brief = ""

    def write(self, node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        key = f"work-products/{node_id}/{filename}"
        self.blobs[key] = content
        return key

    def read(self, wp: Any) -> tuple[bytes, str]:
        return self.blobs[(wp.metadata or {})["minio_object_key"]], str(wp.name)

    def recorder(self) -> Any:
        return make_concept_mesh_recorder(
            self.twin, None, generator=self.mesh, blob_reader=self.read, blob_writer=self.write
        )

    def generations(self) -> SketchGenerationService:
        async def refs(_project: str) -> list[dict[str, Any]]:
            return []

        async def disclose(*_a: Any, **_kw: Any) -> None:
            return None

        return SketchGenerationService(
            self.twin,
            None,
            generator=FakeImages(),
            blob_writer=self.write,
            blob_reader=self.read,
            reference_records=refs,
            timeout_s=5,
            disclosure_recorder=disclose,
        )

    async def accepted_concept(self) -> tuple[str, str]:
        """``(accepted node id, item ref)`` of an accepted generated concept."""
        service = self.generations()
        prepared = await service.prepare(PROJECT, GenerateSketchRequest(intent="a desk lamp"))
        out = await service.start(prepared, wait=True)
        proposal = out["proposals"][0]["node_id"]
        accepted = await service.accept(PROJECT, proposal, expected_baseline=None, actor="tester")
        return accepted["node_id"], accepted["accepted"]

    async def proposal(self) -> str:
        service = self.generations()
        prepared = await service.prepare(PROJECT, GenerateSketchRequest(intent="a stool"))
        out = await service.start(prepared, wait=True)
        return str(out["proposals"][0]["node_id"])

    async def concept_image(self) -> str:
        wp_id = uuid4()
        key = f"renders/{wp_id}/concept.png"
        self.blobs[key] = _png()
        now = datetime.now(UTC)
        await self.twin.create_work_product(
            WorkProduct(
                id=wp_id,
                name="Concept image: Lamp",
                type=WorkProductType.RENDER,
                domain="mechanical",
                file_path="",
                content_hash="b" * 64,
                format="png",
                metadata={
                    "generated_by": "twin.render_concept_image",
                    "concept_name": "Lamp",
                    "authority": "illustrative",
                    "minio_object_key": key,
                },
                created_at=now,
                updated_at=now,
                created_by="twin.render_concept_image",
                project_id=UUID(PROJECT),
            )
        )
        return str(wp_id)

    async def meshes(self) -> list[Any]:
        renders = await self.twin.list_work_products(work_product_type=WorkProductType.RENDER)
        return [wp for wp in renders if (wp.metadata or {}).get("generated_by") == TOOL]


@pytest.fixture
async def world(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    w = World()
    monkeypatch.setattr(twin_routes, "_twin", w.twin)
    monkeypatch.setattr(approval_policy, "_store", PolicyStore())

    async def brief(_project: str) -> str:
        return w.brief

    mesh_routes.set_generator(w.recorder())
    mesh_routes.set_job_runner(w.runner)
    mesh_routes.set_brief_text(brief)
    yield w
    mesh_routes.set_generator(None)
    mesh_routes.set_job_runner(None)
    mesh_routes.set_brief_text(None)


@pytest.fixture
async def client(world: World) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(mesh_routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


URL = f"/v1/twin/projects/{PROJECT}/concept-mesh"


async def _finish(world: World, body: dict[str, Any]) -> dict[str, Any]:
    job = await world.runner.wait(body["job_id"], 5)
    assert job is not None and job.status == "succeeded", job and job.error
    assert job.result is not None
    return job.result


async def test_starts_the_job_from_the_accepted_generated_concept(
    world: World, client: httpx.AsyncClient
) -> None:
    node_id, item_ref = await world.accepted_concept()
    await world.concept_image()  # an image exists too; the accepted concept wins
    started = await client.post(URL, json={})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["operation_id"] == f"job:{body['job_id']}"
    assert body["source"] == {
        "kind": "generated_sketch",
        "node_id": node_id,
        "item_ref": item_ref,
        "name": "Concept",
        "prompt": None,
    }
    assert body["authority"] == "illustrative" and body["label"] == LABEL
    result = await _finish(world, body)
    assert world.mesh.calls[0]["image"] is not None and world.mesh.calls[0]["prompt"] is None

    [mesh] = await world.meshes()
    meta = mesh.metadata
    assert str(mesh.id) == result["node_id"]
    # Provenance: the source, its revision, who asked and on what terms.
    assert meta["source_kind"] == "generated_sketch"
    assert meta["source_image_node_id"] == node_id
    assert meta["source_item_ref"] == item_ref
    assert meta["label_band_removed"] is False
    assert meta["authority"] == "illustrative" and meta["illustrative_label"] == LABEL
    request = meta["request"]
    assert request["surface"] == "create" and request["source_kind"] == "generated_sketch"
    assert request["approved_by"].startswith("person:")
    assert request["policy_mode"] == "off" and request["policy_would_approve"] is False
    assert request["spend_confirmed"] is False
    edges = await world.twin.get_edges(mesh.id, direction="outgoing")
    assert any(
        e.edge_type == EdgeType.GENERATED_FROM and str(e.target_id) == node_id for e in edges
    )


async def test_falls_back_to_the_concept_image_then_the_brief(
    world: World, client: httpx.AsyncClient
) -> None:
    world.brief = ""
    nothing = await client.post(URL, json={})
    assert nothing.status_code == 422
    assert "nothing to make a 3D concept from" in nothing.json()["detail"]

    world.brief = "A desk lamp with a weighted base"
    from_text = await client.post(URL, json={})
    assert from_text.status_code == 200, from_text.text
    assert from_text.json()["source"]["kind"] == "text"
    await _finish(world, from_text.json())
    assert world.mesh.calls[-1]["prompt"] == "A desk lamp with a weighted base"

    image = await world.concept_image()
    from_image = await client.post(URL, json={})
    assert from_image.json()["source"] == {
        "kind": "concept_image",
        "node_id": image,
        "item_ref": None,
        "name": "Concept image: Lamp",
        "prompt": None,
    }
    await _finish(world, from_image.json())
    newest = max(await world.meshes(), key=lambda wp: wp.created_at)
    assert newest.metadata["source_kind"] == "concept_image"


async def test_a_proposal_or_another_kind_of_node_is_not_a_source(
    world: World, client: httpx.AsyncClient
) -> None:
    proposal = await world.proposal()
    wp = await world.twin.get_work_product(UUID(proposal))
    assert source_kind(wp) is None
    refused = await client.post(URL, json={"image_node_id": proposal})
    assert refused.status_code == 422
    missing = await client.post(URL, json={"source": "generated_sketch"})
    assert missing.status_code == 409
    assert world.mesh.calls == []


async def test_an_agent_is_refused(world: World, client: httpx.AsyncClient) -> None:
    world.brief = "a lamp"
    refused = await client.post(URL, json={}, headers={"X-MetaForge-Surface": "agent"})
    assert refused.status_code == 403
    assert "twin.generate_concept_mesh" in refused.json()["detail"]
    assert world.mesh.calls == []


async def test_past_the_hourly_mesh_cap_a_person_confirms_the_spend(
    world: World, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_MESH_PER_HOUR", "1")
    world.brief = "a lamp"
    first = await client.post(URL, json={})
    assert first.status_code == 200
    await _finish(world, first.json())

    world.brief = "a taller lamp"
    held = await client.post(URL, json={})
    assert held.status_code == 409
    detail = held.json()["detail"]
    assert detail["code"] == "spend_confirmation_required"
    assert (
        "cap 1" in detail["message"] and "METAFORGE_AUTO_APPROVE_MESH_PER_HOUR" in detail["message"]
    )
    assert len(world.mesh.calls) == 1

    confirmed = await client.post(URL, json={"confirm_spend": True})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["spend"]["used_last_hour"] == 1
    await _finish(world, confirmed.json())
    newest = max(await world.meshes(), key=lambda wp: wp.created_at)
    assert newest.metadata["request"]["spend_confirmed"] is True


async def test_a_tool_no_policy_may_approve_always_asks(
    world: World, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mcp_core.guardrails as guardrails

    monkeypatch.setattr(guardrails, "never_auto_approved", lambda _tool: True)
    world.brief = "a lamp"
    held = await client.post(URL, json={})
    assert held.status_code == 409
    assert held.json()["detail"]["code"] == "spend_confirmation_required"
    assert (await client.post(URL, json={"confirm_spend": True})).status_code == 200


async def test_the_policy_view_is_recorded(world: World, client: httpx.AsyncClient) -> None:
    await approval_policy.get_policy_store().put(
        ApprovalPolicy(project_id=PROJECT, mode="auto_clean")
    )
    world.brief = "a lamp"
    started = await client.post(URL, json={})
    spend = started.json()["spend"]
    assert spend["policy_would_approve"] is policy_may_approve(TOOL, spend["policy_mode"])


async def test_no_provider_is_refused_before_any_job(
    world: World, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TRIPO_API_KEY", raising=False)
    monkeypatch.delenv("METAFORGE_MESH_PROVIDER", raising=False)
    mesh_routes.set_generator(
        make_concept_mesh_recorder(
            world.twin, None, blob_reader=world.read, blob_writer=world.write
        )
    )
    world.brief = "a lamp"
    refused = await client.post(URL, json={})
    assert refused.status_code == 503


async def test_the_state_names_the_latest_mesh_the_running_job_and_the_next_source(
    world: World, client: httpx.AsyncClient
) -> None:
    empty = (await client.get(URL)).json()
    assert empty["latest"] is None and empty["active_job"] is None
    assert empty["next_source"] is None and empty["spend"]["used_last_hour"] == 0

    node_id, _ref = await world.accepted_concept()
    started = await client.post(URL, json={})
    result = await _finish(world, started.json())
    state = (await client.get(URL)).json()
    assert state["next_source"]["kind"] == "generated_sketch"
    assert state["next_source"]["node_id"] == node_id
    latest = state["latest"]
    assert latest["node_id"] == result["node_id"]
    assert latest["source_kind"] == "generated_sketch"
    assert latest["authority"] == "illustrative" and latest["label"] == LABEL
    assert state["active_job"] is None
    assert state["spend"]["used_last_hour"] == 1
