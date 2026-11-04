"""Freeform concept sketch generation and reference grounding (FORGE-794).

Every provider here is a fake: no test calls a paid image API. Covers the
three modes and what each sends, reference roles and inclusion, facts with
citations (image facts estimated, specification values stated), conflicts
between authoritative references raised for review, region edits and their
preservation check, variants as proposals that never replace the accepted
concept until a person accepts one, supersede and stale results on the job
runner, provenance, the provider adapters' masks and images, the approval and
spend policy, and the routes.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from api_gateway.approvals.tool_write_policy import SPEND_CAPS, hourly_cap
from api_gateway.create import routes as create_routes
from api_gateway.create.models import FactCitation, ReferenceFact, ReferenceRecord
from api_gateway.create.references import (
    detect_conflicts,
    facts_from_reading,
    facts_from_text,
    publish_conflicts,
)
from api_gateway.create.store import SqliteCreationStore
from api_gateway.operations import publisher as ops
from api_gateway.operations.routes import publish_local
from api_gateway.projects import routes as project_routes
from api_gateway.projects.backend import InMemoryProjectBackend
from api_gateway.twin import routes as twin_routes
from api_gateway.twin import sketch_generation_routes as gen_routes
from api_gateway.twin.image_generation import (
    GeneratedImage,
    ImageRequest,
    OpenAIImageGenerator,
    OpenRouterImageGenerator,
    ProviderCapabilities,
    capabilities_of,
    generate_request_with_timeout,
)
from api_gateway.twin.sketch_generation import (
    LABEL,
    STYLE_WORDS,
    TOOL,
    GenerateSketchRequest,
    Region,
    SketchGenerationError,
    SketchGenerationService,
    SpendConfirmationRequiredError,
    make_sketch_generator,
    rasterise_strokes,
)
from api_gateway.twin.sketch_strokes_recorder import SketchSaveRequest, make_sketch_strokes_recorder
from mcp_core.annotations import ADDITIVE, OPEN_WORLD
from mcp_core.guardrails import Caller, decide, never_auto_approved, policy_may_approve
from tool_registry.jobs import JobRunner, JobStore
from twin_core.api import InMemoryTwinAPI
from twin_core.items import find_item
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

PROJECT = "11111111-2222-3333-4444-555555555555"
OTHER_PROJECT = "99999999-2222-3333-4444-555555555555"
SIZE = 1024


# ── helpers ─────────────────────────────────────────────────────────────


def _png(
    color: tuple[int, int, int] = (230, 230, 230),
    *,
    box: tuple[int, int, int, int] | None = None,
    box_color: tuple[int, int, int] = (20, 20, 20),
    size: int = SIZE,
) -> bytes:
    img = Image.new("RGB", (size, size), color)
    if box is not None:
        img.paste(box_color, box)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class FakeGen:
    """A provider that records what it was asked and returns a fixed image."""

    provider = "fake"
    model = "fake-image-1"

    def __init__(
        self,
        *,
        mask: bool = False,
        max_images: int = 4,
        image: bytes | None = None,
        cost: float | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.capabilities = ProviderCapabilities(mask=mask, max_images=max_images)
        self.image = image or _png((120, 160, 200))
        self.cost = cost
        self.gate = gate
        self.calls: list[ImageRequest] = []

    async def generate_request(self, request: ImageRequest) -> GeneratedImage:
        self.calls.append(request)
        if self.gate is not None:
            await self.gate.wait()
        return GeneratedImage(
            image_bytes=self.image,
            mime="image/png",
            provider=self.provider,
            model=self.model,
            conditioned=bool(request.images),
            masked=request.mask is not None,
            images_sent=len(request.images),
            cost_usd=self.cost,
        )


@pytest.fixture
def blobs(monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    stored: dict[str, bytes] = {}

    def fake_store(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
        key = f"work-products/{node_id}/{filename}"
        stored[key] = content
        return key

    def fake_fetch(key: str) -> bytes:
        return stored[key]

    import api_gateway.twin.blob_store as blob_store
    import digital_twin.storage.work_product_blobs as wpb

    monkeypatch.setattr(wpb, "store_work_product_blob", fake_store)
    monkeypatch.setattr(blob_store, "fetch_work_product_blob", fake_fetch)
    return stored


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def publisher() -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


@pytest.fixture
def runner() -> JobRunner:
    return JobRunner(JobStore(":memory:"), publisher=publish_local)


def _service(
    twin: InMemoryTwinAPI,
    blobs: dict[str, bytes],
    gen: Any,
    *,
    records: list[dict[str, Any]] | None = None,
    runner: JobRunner | None = None,
    disclosures: list[dict[str, Any]] | None = None,
) -> SketchGenerationService:
    def write(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        key = f"work-products/{node_id}/{filename}"
        blobs[key] = content
        return key

    def read(wp: Any) -> tuple[bytes, str]:
        return blobs[(wp.metadata or {})["minio_object_key"]], str(wp.name)

    async def refs(_project: str) -> list[dict[str, Any]]:
        return list(records or [])

    async def disclose(project_id: str, sent: list[tuple[str, str]], **kw: Any) -> None:
        if disclosures is not None:
            disclosures.append({"project_id": project_id, "sent": sent, **kw})

    return SketchGenerationService(
        twin,
        None,
        generator=gen,
        blob_writer=write,
        blob_reader=read,
        reference_records=refs,
        job_runner=runner,
        timeout_s=5,
        disclosure_recorder=disclose,
    )


async def _image_wp(
    twin: InMemoryTwinAPI,
    blobs: dict[str, bytes],
    name: str,
    *,
    project: str = PROJECT,
    data: bytes | None = None,
    filename: str = "ref.png",
) -> str:
    wp_id = uuid4()
    key = f"refs/{wp_id}/{filename}"
    blobs[key] = data if data is not None else _png((10, 200, 10))
    now = datetime.now(UTC)
    await twin.create_work_product(
        WorkProduct(
            id=wp_id,
            name=name,
            type=WorkProductType.IMAGE,
            domain="concept",
            file_path="",
            content_hash="a" * 64,
            format=filename.rsplit(".", 1)[-1],
            metadata={"minio_object_key": key, "original_filename": filename},
            created_at=now,
            updated_at=now,
            created_by="human",
            project_id=UUID(project),
        )
    )
    return str(wp_id)


async def _sketch(twin: InMemoryTwinAPI) -> str:
    save = make_sketch_strokes_recorder(twin)
    out = await save(
        PROJECT,
        SketchSaveRequest(
            save_id="save-0001",
            strokes=[{"id": "edge", "points": [[-0.3, 0.0], [0.3, 0.0]], "width": 3}],
        ),
    )
    assert out.item_key
    return out.item_key


async def _generate(service: SketchGenerationService, **fields: Any) -> dict[str, Any]:
    request = GenerateSketchRequest(intent=fields.pop("intent", "a three legged stool"), **fields)
    prepared = await service.prepare(PROJECT, request)
    return await service.start(prepared, wait=True)


# ── modes ───────────────────────────────────────────────────────────────


async def test_text_mode_prompts_intent_style_and_constraints_with_no_image(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    gen = FakeGen()
    out = await _generate(
        _service(twin, blobs, gen),
        style="line_art",
        constraints=["seat 400 mm high"],
        negative=["no armrests"],
    )
    assert out["mode"] == "text"
    assert len(gen.calls) == 1 and gen.calls[0].images == () and gen.calls[0].mask is None
    prompt = gen.calls[0].prompt
    assert "a three legged stool" in prompt
    assert STYLE_WORDS["line_art"] in prompt
    assert "seat 400 mm high" in prompt
    assert "Do not: no armrests" in prompt
    assert "add no dimensions" in prompt
    assert out["target_key"] == "CONCEPT:full" and out["base_revision"] == "none"


async def test_reference_mode_honours_roles_and_leaves_out_inactive_and_evidence(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    mood = await _image_wp(twin, blobs, "Mood board")
    shape = await _image_wp(twin, blobs, "Shape photo")
    evidence = await _image_wp(twin, blobs, "Test photo")
    off = await _image_wp(twin, blobs, "Old idea")
    spec = await _image_wp(twin, blobs, "Spec sheet", filename="spec.txt", data=b"Width: 120 mm\n")
    stated = facts_from_text(
        "Width: 120 mm", reference_id=spec, source_hash="h", role="specification"
    )
    records = [
        {"work_product_id": mood, "role": "inspiration", "active": True},
        {"work_product_id": shape, "role": "geometry", "active": True},
        {"work_product_id": evidence, "role": "verification_evidence", "active": True},
        {"work_product_id": off, "role": "geometry", "active": False},
        {
            "work_product_id": spec,
            "role": "specification",
            "active": True,
            "facts": [f.model_dump() for f in stated],
        },
    ]
    gen = FakeGen()
    disclosures: list[dict[str, Any]] = []
    out = await _generate(
        _service(twin, blobs, gen, records=records, disclosures=disclosures),
        reference_ids=[mood, shape, evidence, off, spec],
    )
    assert out["mode"] == "reference"
    sent = gen.calls[0]
    assert [i.role for i in sent.images] == ["inspiration", "geometry"]
    assert "mood, style and materials only" in sent.prompt
    assert "follow its overall shape and proportions" in sent.prompt
    assert "Width 120 mm" in sent.prompt
    refs = {r["id"]: r for r in out["proposals"][0]["references"]}
    assert off not in refs  # inactive: not part of this request at all
    assert refs[evidence]["excluded"] and not refs[evidence]["image_sent"]
    assert refs[spec]["facts_sent"] == 1 and not refs[spec]["image_sent"]
    # REF-007: which references reached which provider is recorded.
    assert {r for r, _ in disclosures[0]["sent"]} == {mood, shape, spec}
    assert disclosures[0]["provider"] == "fake"


async def test_a_reference_of_another_project_is_refused(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    foreign = await _image_wp(twin, blobs, "Elsewhere", project=OTHER_PROJECT)
    service = _service(twin, blobs, FakeGen())
    with pytest.raises(SketchGenerationError) as err:
        await service.prepare(PROJECT, GenerateSketchRequest(intent="x", reference_ids=[foreign]))
    assert err.value.status_code == 403


async def test_sketch_mode_conditions_on_the_drawn_strokes_and_never_changes_them(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    key = await _sketch(twin)
    before = await find_item(twin, key, PROJECT)
    gen = FakeGen()
    out = await _generate(_service(twin, blobs, gen), sketch_key=key)
    assert out["mode"] == "sketch"
    first = gen.calls[0].images[0]
    assert first.role == "sketch"
    drawn = Image.open(io.BytesIO(first.data)).convert("L")
    assert drawn.getpixel((SIZE // 2, SIZE // 2)) < 50  # the stroke crosses the centre
    assert drawn.getpixel((10, 10)) > 200
    assert "rough hand sketch" in gen.calls[0].prompt
    after = await find_item(twin, key, PROJECT)
    assert after is not None and before is not None
    assert after.head_revision == before.head_revision  # the stroke layer is untouched
    assert out["proposals"][0]["source_sketch"]["item_ref"] == f"{key}@1"
    assert out["target_key"] == f"{key}:full"


async def test_a_sketch_that_moved_on_is_refused(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    key = await _sketch(twin)
    service = _service(twin, blobs, FakeGen())
    with pytest.raises(SketchGenerationError) as err:
        await service.prepare(
            PROJECT, GenerateSketchRequest(intent="x", sketch_key=key, sketch_revision=f"{key}@9")
        )
    assert err.value.status_code == 409


async def test_a_plain_callable_provider_gets_the_first_image_only() -> None:
    seen: list[Any] = []

    async def plain(prompt: str, conditioning: bytes | None, size: str) -> GeneratedImage:
        seen.append(conditioning)
        return GeneratedImage(b"x", "image/png", "plain", "m", conditioned=conditioning is not None)

    caps = capabilities_of(plain)
    assert caps == ProviderCapabilities(mask=False, max_images=1, partial_images=False)
    from api_gateway.twin.image_generation import ConditioningImage

    req = ImageRequest("p", "1024x1024", (ConditioningImage(b"a"), ConditioningImage(b"b")), b"m")
    await generate_request_with_timeout(plain, req, timeout_s=1)
    assert seen == [b"a"]


def test_rasterised_strokes_use_the_sketch_frame() -> None:
    png = rasterise_strokes([{"points": [[-0.5, -0.5], [0.5, 0.5]]}], "1536x1024")
    img = Image.open(io.BytesIO(png)).convert("L")
    assert img.size == (1536, 1024)
    # The shorter side spans one unit, centred: (-0.5,-0.5) is (256, 0).
    assert img.getpixel((258, 3)) < 80 and img.getpixel((1536 // 2, 512)) < 80


# ── references: facts and conflicts ─────────────────────────────────────


def test_image_facts_are_estimated_and_never_measured() -> None:
    reading = {
        "summary": "a stool",
        "parts": [{"name": "leg", "count": 3, "description": "turned leg"}],
        "proportions": [{"subject": "seat height", "value": 450, "unit": "mm", "note": "looks"}],
    }
    facts = facts_from_reading(
        reading, reference_id="img", source_hash="abc", extracted_by="vision/x"
    )
    assert facts and all(f.basis == "estimated" and f.uncertainty == "high" for f in facts)
    height = next(f for f in facts if f.parameter == "seat height")
    assert height.value == 450 and height.citation.reference_id == "img"
    assert height.citation.source_hash == "abc" and height.citation.region == "whole image"


def test_a_specification_value_is_stated_with_a_citation() -> None:
    text = "Product sheet\nMax width: 120 mm\nnotes without a unit: 12\nMass = 2.5 kg\n"
    facts = facts_from_text(text, reference_id="spec", source_hash="h1", role="specification")
    assert [(f.parameter, f.value, f.unit, f.basis) for f in facts] == [
        ("Max width", 120.0, "mm", "stated"),
        ("Mass", 2.5, "kg", "stated"),
    ]
    assert facts[0].citation.region == "line 2" and facts[0].citation.excerpt == "Max width: 120 mm"
    # The same text with a non-authoritative role is only an estimate.
    loose = facts_from_text(text, reference_id="spec", source_hash="h1", role="inspiration")
    assert all(f.basis == "estimated" for f in loose)


def _spec(ref: str, value: float, unit: str, basis: str = "stated") -> ReferenceRecord:
    return ReferenceRecord(
        work_product_id=ref,
        name=ref,
        role="specification",
        facts=[
            ReferenceFact(
                id=f"f-{ref}",
                parameter="Width",
                value=value,
                unit=unit,
                basis=basis,  # type: ignore[arg-type]
                citation=FactCitation(reference_id=ref, region="line 1", source_hash=ref),
            )
        ],
    )


def test_conflicting_specifications_raise_review_required(
    publisher: ops.OperationPublisher,
) -> None:
    records = [_spec("sheet-a", 120, "mm"), _spec("sheet-b", 13, "cm")]
    found = publish_conflicts(PROJECT, records)
    assert len(found) == 1
    events = [e for e in publisher.store.events_after(PROJECT, 0) if e.type == "review_required"]
    assert len(events) == 1
    assert "sheet-a (line 1): 120 mm" in events[0].message
    assert "sheet-b (line 1): 13 cm" in events[0].message
    assert {a.id for a in events[0].artifact_refs} == {"sheet-a", "sheet-b"}
    # The same values in different units agree; estimates never conflict.
    assert detect_conflicts([_spec("a", 120, "mm"), _spec("b", 12, "cm")]) == []
    assert detect_conflicts([_spec("a", 120, "mm"), _spec("b", 99, "mm", "estimated")]) == []


# ── regions, masks and preservation ─────────────────────────────────────

MASK = Region(polygon=[(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)])


async def _accepted_baseline(service: SketchGenerationService) -> str:
    out = await _generate(service)
    accepted = await service.accept(PROJECT, out["node_id"], expected_baseline="none", actor="me")
    return str(accepted["accepted"])


async def test_a_region_edit_is_composited_back_for_a_provider_without_masks(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    base_gen = FakeGen(image=_png((200, 200, 200)))
    head = await _accepted_baseline(_service(twin, blobs, base_gen))
    gen = FakeGen(mask=False, image=_png((0, 0, 0)))
    out = await _generate(
        _service(twin, blobs, gen), mask=MASK, mask_instruction="make the seat round"
    )
    call = gen.calls[0]
    assert call.mask is None and call.images[0].role == "baseline"
    assert "Change only the region from 40% to 60% across" in call.prompt
    proposal = out["proposals"][0]
    pres = proposal["preservation"]
    assert pres["method"] == "composited"
    assert pres["outside_mask_raw"] < 0.1 and pres["outside_mask"] == 1.0 and pres["passed"]
    assert not proposal["review_required"]
    stored = Image.open(io.BytesIO(blobs[next(k for k in blobs if proposal["node_id"] in k)]))
    assert stored.convert("RGB").getpixel((5, 5)) == (200, 200, 200)  # outside: kept
    assert stored.convert("RGB").getpixel((SIZE // 2, SIZE // 2)) == (0, 0, 0)  # inside: new
    assert proposal["base_revision"] == head


async def test_a_masking_provider_that_ignores_the_mask_is_flagged_not_accepted(
    twin: InMemoryTwinAPI,
    blobs: dict[str, bytes],
    publisher: ops.OperationPublisher,
    runner: JobRunner,
) -> None:
    await _accepted_baseline(_service(twin, blobs, FakeGen(image=_png((200, 200, 200)))))
    gen = FakeGen(mask=True, image=_png((0, 0, 0)))
    service = _service(twin, blobs, gen, runner=runner)
    prepared = await service.prepare(
        PROJECT, GenerateSketchRequest(intent="round seat", mask=MASK, mask_instruction="round it")
    )
    reply = await service.start(prepared)
    job = await runner.wait(reply["job_id"], 5)
    await runner.flush_events()
    assert job is not None and job.status == "succeeded"
    mask_png = Image.open(io.BytesIO(gen.calls[0].mask or b""))
    assert mask_png.mode == "RGBA"
    assert mask_png.getpixel((SIZE // 2, SIZE // 2))[3] == 0  # paint here
    assert mask_png.getpixel((5, 5))[3] == 255  # keep this
    proposal = job.result["proposals"][0]  # type: ignore[index]
    assert proposal["preservation"]["method"] == "provider_mask"
    assert not proposal["preservation"]["passed"] and proposal["review_required"]
    assert proposal["generation_status"] == "proposed"
    events = publisher.store.events_after(PROJECT, 0)
    review = [e for e in events if e.type == "review_required"]
    assert review and "outside the region changed" in review[0].message


async def test_a_changed_locked_region_is_flagged_with_its_name(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    await _accepted_baseline(_service(twin, blobs, FakeGen(image=_png((200, 200, 200)))))
    locked = Region(
        polygon=[(0.2, 0.2), (0.3, 0.2), (0.3, 0.3), (0.2, 0.3)], label="mounting holes"
    )
    out = await _generate(
        _service(twin, blobs, FakeGen(image=_png((0, 0, 0)))),
        mask=Region(polygon=[(0.0, 0.0), (0.4, 0.0), (0.4, 0.4), (0.0, 0.4)]),
        mask_instruction="new seat",
        locked_regions=[locked],
        protected_features=["mounting holes", "proportions"],
    )
    pres = out["proposals"][0]["preservation"]
    assert pres["locked_regions"][0]["label"] == "mounting holes"
    assert pres["locked_regions"][0]["score"] < 0.1 and not pres["passed"]
    assert {p["name"]: p["checked_by_pixels"] for p in pres["protected_features"]} == {
        "mounting holes": True,
        "proportions": False,
    }
    assert out["proposals"][0]["review_required"]


# ── variants, accept, baseline ──────────────────────────────────────────


def test_n_variants_defaults_to_one() -> None:
    assert GenerateSketchRequest(intent="x").n_variants == 1
    with pytest.raises(ValueError):
        GenerateSketchRequest(intent="x", n_variants=5)


async def test_variants_are_proposals_until_a_person_accepts_one(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes]
) -> None:
    gen = FakeGen()
    service = _service(twin, blobs, gen)
    out = await _generate(service, n_variants=3)
    assert len(gen.calls) == 3 and len(out["proposals"]) == 3
    assert len({p["generation_id"] for p in out["proposals"]}) == 1
    overview = await service.overview(PROJECT)
    assert overview["lines"] == []  # nothing accepted: no baseline moved
    first = await service.accept(
        PROJECT, out["proposals"][0]["node_id"], expected_baseline="none", actor="me"
    )
    assert first["accepted"].endswith("@1")
    # A sibling of the accepted variant may replace it (a switch, not a race).
    second = await service.accept(
        PROJECT, out["proposals"][1]["node_id"], expected_baseline=first["accepted"], actor="me"
    )
    assert second["accepted"].endswith("@2") and second["previous_baseline"] == first["accepted"]
    later = await _generate(service)
    overview = await service.overview(PROJECT)
    line = overview["lines"][0]
    assert line["head_ref"] == second["accepted"]  # the new proposal did not move it
    assert [h["item_ref"] for h in line["history"]] == [second["accepted"], first["accepted"]]
    assert later["proposals"][0]["generation_status"] == "proposed"
    # A stale view of the baseline is refused.
    with pytest.raises(SketchGenerationError) as err:
        await service.accept(
            PROJECT, later["node_id"], expected_baseline=first["accepted"], actor="me"
        )
    assert err.value.status_code == 409


async def test_reject_branch_and_compare(twin: InMemoryTwinAPI, blobs: dict[str, bytes]) -> None:
    service = _service(twin, blobs, FakeGen(image=_png((10, 10, 10))))
    a = await _generate(service)
    service._generator = FakeGen(image=_png((250, 250, 250)))
    b = await _generate(service)
    compared = await service.compare(PROJECT, a["node_id"], b["node_id"])
    assert compared["pixel_difference"] == 1.0 and compared["a"]["authority"] == "illustrative"
    assert (await service.reject(PROJECT, a["node_id"], actor="me"))[
        "generation_status"
    ] == "rejected"
    with pytest.raises(SketchGenerationError):
        await service.accept(PROJECT, a["node_id"], expected_baseline=None, actor="me")
    branch = await service.branch(PROJECT, b["node_id"], actor="me", name=None)
    assert branch["baseline_for"].startswith("CONCEPT#branch-")
    overview = await service.overview(PROJECT)
    assert [line["baseline_for"] for line in overview["lines"]] == [branch["baseline_for"]]


# ── scheduling: supersede, stale, coalesce ──────────────────────────────


async def test_a_newer_request_supersedes_and_a_late_result_is_stale_history(
    twin: InMemoryTwinAPI,
    blobs: dict[str, bytes],
    publisher: ops.OperationPublisher,
    runner: JobRunner,
) -> None:
    gate = asyncio.Event()
    slow = FakeGen(gate=gate)
    service = _service(twin, blobs, slow, runner=runner)
    first = await service.start(await service.prepare(PROJECT, GenerateSketchRequest(intent="one")))
    await asyncio.sleep(0)
    second = await service.start(
        await service.prepare(PROJECT, GenerateSketchRequest(intent="two"))
    )
    assert first["target_key"] == second["target_key"] == "CONCEPT:full"
    old = runner.get(first["job_id"])
    assert old is not None and old.status == "superseded"
    # Meanwhile another concept is accepted directly, so the running one is late.
    fast = _service(twin, blobs, FakeGen())
    head = await _accepted_baseline(fast)
    gate.set()
    job = await runner.wait(second["job_id"], 5)
    await runner.flush_events()
    assert job is not None and job.status == "stale"
    stale_nodes = [
        w
        for w in await twin.list_work_products(work_product_type=WorkProductType.GENERATED_SKETCH)
        if (w.metadata or {}).get("generation_status") == "stale"
    ]
    assert len(stale_nodes) == 1  # kept as history
    assert await service.baseline_ref(PROJECT, head.split("@")[0]) == head  # baseline kept
    with pytest.raises(SketchGenerationError) as err:
        await service.accept(PROJECT, str(stale_nodes[0].id), expected_baseline=head, actor="me")
    assert err.value.status_code == 409
    states = {e.operation_id: e.state for e in publisher.store.events_after(PROJECT, 0)}
    assert states[f"job:{first['job_id']}"] == "superseded"
    assert states[f"job:{second['job_id']}"] == "stale"


async def test_identical_requests_coalesce_into_one_job(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], runner: JobRunner
) -> None:
    gate = asyncio.Event()
    gen = FakeGen(gate=gate)
    service = _service(twin, blobs, gen, runner=runner)
    a = await service.start(await service.prepare(PROJECT, GenerateSketchRequest(intent="same")))
    b = await service.start(await service.prepare(PROJECT, GenerateSketchRequest(intent="same")))
    assert b["reused"] and a["job_id"] == b["job_id"]
    gate.set()
    await runner.wait(a["job_id"], 5)
    assert len(gen.calls) == 1


# ── provenance and labels ───────────────────────────────────────────────


async def test_every_output_records_its_provenance_and_is_illustrative(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], runner: JobRunner
) -> None:
    key = await _sketch(twin)
    ref = await _image_wp(twin, blobs, "Mood")
    service = _service(
        twin,
        blobs,
        FakeGen(cost=0.04),
        runner=runner,
        records=[{"work_product_id": ref, "role": "inspiration", "source_hash": "f" * 64}],
    )
    prepared = await service.prepare(
        PROJECT,
        GenerateSketchRequest(
            intent="stool",
            sketch_key=key,
            reference_ids=[ref],
            negative=["no text"],
            style="marker_render",
        ),
    )
    reply = await service.start(prepared)
    job = await runner.wait(reply["job_id"], 5)
    assert job is not None and job.status == "succeeded"
    node = await twin.get_work_product(UUID(job.result["node_id"]))  # type: ignore[index]
    assert node is not None
    meta = node.metadata
    assert str(getattr(node.type, "value", node.type)) == "generated_sketch"
    assert meta["authority"] == "illustrative" and meta["illustrative_label"] == LABEL
    assert meta["editable"] is False and meta["representation"] == "generated_raster"
    prov = meta["provenance"]
    for field in (
        "prompt",
        "negative_prompt",
        "style",
        "mode",
        "references",
        "mask",
        "source_sketch",
        "provider",
        "model",
        "cost_usd",
        "job_id",
        "operation_id",
        "base_revision",
        "target_key",
        "target_revision",
    ):
        assert field in prov, field
    assert prov["style"] == "marker_render" and prov["mode"] == "sketch"
    assert prov["references"][0]["source_hash"] == "f" * 64
    assert prov["source_sketch"]["item_ref"] == f"{key}@1"
    assert (
        prov["provider"] == "fake" and prov["model"] == "fake-image-1" and prov["cost_usd"] == 0.04
    )
    assert prov["job_id"] == reply["job_id"] and prov["operation_id"] == f"job:{reply['job_id']}"
    assert prov["base_revision"] == "none" and prov["target_revision"] is None
    assert "no text" in prov["negative_prompt"]
    png = Image.open(io.BytesIO(blobs[meta["minio_object_key"]]))
    assert png.info.get("Description") == LABEL
    accepted = await service.accept(PROJECT, str(node.id), expected_baseline="none", actor="me")
    after = await twin.get_work_product(node.id)
    assert (
        after is not None
        and after.metadata["provenance"]["target_revision"] == accepted["accepted"]
    )


# ── provider adapters ───────────────────────────────────────────────────


async def test_openrouter_sends_every_image_but_never_a_mask() -> None:
    sent: list[dict[str, Any]] = []
    data = base64.b64encode(_png(size=8)).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "images": [{"image_url": {"url": f"data:image/png;base64,{data}"}}]
                        }
                    }
                ],
                "usage": {"cost": 0.039},
            },
        )

    gen = OpenRouterImageGenerator(
        model="m", api_key="k", timeout_s=5, transport=httpx.MockTransport(handler)
    )
    assert gen.capabilities.mask is False and gen.capabilities.partial_images is False
    from api_gateway.twin.image_generation import ConditioningImage

    out = await gen.generate_request(
        ImageRequest(
            "p",
            "1024x1024",
            (ConditioningImage(b"a"), ConditioningImage(b"b", "image/jpeg")),
            b"mask",
        )
    )
    parts = sent[0]["messages"][0]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url", "image_url"]
    assert parts[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert out.images_sent == 2 and out.masked is False and out.cost_usd == 0.039


async def test_openai_passes_the_mask_to_images_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Images:
        async def edit(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return type(
                "R",
                (),
                {"data": [type("D", (), {"b64_json": base64.b64encode(b"img").decode()})()]},
            )()

    class _Client:
        def __init__(self, **_kw: Any) -> None:
            self.images = _Images()

        async def close(self) -> None:
            return None

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)
    gen = OpenAIImageGenerator(model="gpt-image-1", api_key="k", timeout_s=5)
    assert gen.capabilities.mask is True and gen.capabilities.partial_images is False
    from api_gateway.twin.image_generation import ConditioningImage

    out = await gen.generate_request(
        ImageRequest("p", "1024x1024", (ConditioningImage(b"a"),), b"mask")
    )
    assert captured["mask"][1] == b"mask" and captured["image"][1] == b"a"
    assert out.masked and out.images_sent == 1


# ── approval and spend policy ───────────────────────────────────────────


def test_the_paid_tool_is_held_and_capped_like_the_other_image_tools() -> None:
    assert TOOL == "twin.generate_sketch"
    assert TOOL in ADDITIVE and TOOL in OPEN_WORLD
    assert not never_auto_approved(TOOL)
    assert policy_may_approve(TOOL, "auto_clean") and not policy_may_approve(TOOL, "off")
    assert SPEND_CAPS[TOOL] == SPEND_CAPS["twin.render_concept_image"]
    assert hourly_cap(TOOL) == 20
    held = decide(TOOL, caller=Caller.REMOTE, twin_mutations_enabled=True)
    assert held.requires_approval


async def test_the_create_route_asks_before_going_past_the_hourly_cap(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAFORGE_AUTO_APPROVE_IMAGE_PER_HOUR", "1")
    service = _service(twin, blobs, FakeGen())
    await _generate(service)
    prepared = await service.prepare(PROJECT, GenerateSketchRequest(intent="another"))
    with pytest.raises(SpendConfirmationRequiredError):
        await service.check_spend(prepared)
    confirmed = await service.prepare(
        PROJECT, GenerateSketchRequest(intent="another", confirm_spend=True)
    )
    assert (await service.check_spend(confirmed))["used_last_hour"] == 1


async def test_the_agent_tool_collaborator_runs_without_the_dashboard_cap(
    twin: InMemoryTwinAPI, blobs: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    def write(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        blobs[f"k/{node_id}"] = content
        return f"k/{node_id}"

    generate = make_sketch_generator(twin, None, generator=FakeGen(), blob_writer=write)
    out = await generate(project_id=PROJECT, request={"intent": "lamp"}, wait=True, session_id=None)
    assert out["proposals"][0]["generation_status"] == "proposed"
    with pytest.raises(SketchGenerationError):
        await generate(project_id=PROJECT, request={"intent": ""}, wait=True)


# ── routes ──────────────────────────────────────────────────────────────


@pytest.fixture
async def http(
    twin: InMemoryTwinAPI,
    blobs: dict[str, bytes],
    publisher: ops.OperationPublisher,
    runner: JobRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, SketchGenerationService]]:
    monkeypatch.setattr(twin_routes, "_twin", twin)
    service = _service(twin, blobs, FakeGen(), runner=runner)
    gen_routes.set_service(service)
    app = FastAPI()
    app.include_router(gen_routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, service
    gen_routes.set_service(None)


async def test_generation_routes_start_list_accept_compare(
    http: tuple[httpx.AsyncClient, SketchGenerationService], runner: JobRunner
) -> None:
    client, _service_ = http
    base = f"/v1/twin/projects/{PROJECT}/sketch-generations"
    refused = await client.post(
        base, json={"intent": "a"}, headers={"X-MetaForge-Surface": "agent"}
    )
    assert refused.status_code == 403
    started = await client.post(base, json={"intent": "a chair", "style": "line_art"})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["operation_id"] == f"job:{body['job_id']}" and body["mode"] == "text"
    job = await runner.wait(body["job_id"], 5)
    assert job is not None and job.status == "succeeded"
    listed = (await client.get(base)).json()
    assert listed["lines"] == [] and len(listed["proposals"]) == 1
    node = listed["proposals"][0]["node_id"]
    accepted = await client.post(f"{base}/{node}/accept", json={"expected_baseline": "none"})
    assert accepted.status_code == 200, accepted.text
    again = await client.post(f"{base}/{node}/accept", json={})
    assert again.status_code == 409
    compared = await client.get(
        f"{base}/compare", params={"a": node, "b": accepted.json()["node_id"]}
    )
    assert compared.status_code == 200 and compared.json()["pixel_difference"] == 0.0
    bad = await client.post(base, json={"intent": "x", "mode": "reference"})
    assert bad.status_code == 422


# ── reference routes ────────────────────────────────────────────────────


@pytest.fixture
def ref_app(
    tmp_path: Path,
    twin: InMemoryTwinAPI,
    blobs: dict[str, bytes],
    publisher: ops.OperationPublisher,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FastAPI, InMemoryProjectBackend]]:
    previous = project_routes.get_project_backend()
    backend = InMemoryProjectBackend.create()
    project_routes.init_project_backend(backend)
    create_routes.init_create_store(SqliteCreationStore(str(tmp_path / "create.db")))
    monkeypatch.setattr(twin_routes, "_twin", twin)
    app = FastAPI()
    app.include_router(create_routes.router)
    yield app, backend
    create_routes.init_create_store(None)
    create_routes.set_vision_call_factory(None)
    project_routes.init_project_backend(previous)


async def test_reference_roles_facts_and_conflicts_through_the_routes(
    ref_app: tuple[FastAPI, Any],
    twin: InMemoryTwinAPI,
    blobs: dict[str, bytes],
    publisher: ops.OperationPublisher,
) -> None:
    app, backend = ref_app
    project = await backend.create_project(name="Stool", description="", status="draft")
    pid = str(project.id)
    sheet_a = await _image_wp(
        twin, blobs, "Sheet A", project=pid, filename="a.txt", data=b"Width: 120 mm\n"
    )
    sheet_b = await _image_wp(
        twin, blobs, "Sheet B", project=pid, filename="b.txt", data=b"Width: 125 mm\n"
    )
    photo = await _image_wp(twin, blobs, "Photo", project=pid)
    foreign = await _image_wp(twin, blobs, "Foreign", project=OTHER_PROJECT)

    async def vision(image: bytes, mime: str, prompt: str) -> str:
        return json.dumps(
            {
                "summary": "a stool",
                "parts": [{"name": "seat"}],
                "proportions": [{"subject": "seat height", "value": 450, "unit": "mm"}],
            }
        )

    create_routes.set_vision_call_factory(lambda: vision)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        base = f"/v1/create/{pid}/references"
        r = await c.put(f"{base}/{sheet_a}", json={"role": "specification"})
        assert r.status_code == 200, r.text
        ref = r.json()["reference"]
        assert ref["role"] == "specification" and ref["versions"][0]["note"] == "added"
        assert ref["source_hash"] == "a" * 64
        await c.put(f"{base}/{sheet_b}", json={"role": "specification"})
        off = await c.put(f"{base}/{photo}", json={"active": False, "protected_features": ["legs"]})
        assert off.json()["reference"]["active"] is False
        assert off.json()["reference"]["protected_features"] == ["legs"]
        assert (await c.put(f"{base}/{foreign}", json={"role": "geometry"})).status_code == 403
        facts_a = await c.post(f"{base}/{sheet_a}/facts", json={})
        assert facts_a.json()["reference"]["facts"][0]["basis"] == "stated"
        assert facts_a.json()["conflicts"] == []
        facts_b = await c.post(f"{base}/{sheet_b}/facts", json={})
        conflicts = facts_b.json()["conflicts"]
        assert len(conflicts) == 1 and conflicts[0]["parameter"] == "Width"
        assert {cit["reference_id"] for cit in conflicts[0]["citations"]} == {sheet_a, sheet_b}
        image = await c.post(f"{base}/{photo}/facts", json={})
        image_facts = image.json()["reference"]["facts"]
        assert image_facts and all(f["basis"] == "estimated" for f in image_facts)
        assert image.json()["reference"]["disclosures"]  # sent to a vision model
        listed = await c.get(f"{base}/conflicts")
        assert len(listed.json()["conflicts"]) == 1
        # A brief written by a client that does not know roles keeps them.
        view = (await c.get(f"/v1/create/{pid}")).json()
        brief = {k: v for k, v in view["creation"]["brief"].items() if k != "reference_details"}
        await c.put(f"/v1/create/{pid}/brief", json={"brief": brief})
        kept = (await c.get(f"/v1/create/{pid}")).json()["creation"]["brief"]["reference_details"]
        assert {r["work_product_id"] for r in kept} == {sheet_a, sheet_b, photo}
    reviews = [e for e in publisher.store.events_after(pid, 0) if e.type == "review_required"]
    assert len(reviews) == 1
