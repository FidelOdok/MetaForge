"""Freeform concept sketch generation (FORGE-794, FSG-001..006, 012..017, VIS-002..004).

A person describes a product, attaches references, draws a rough sketch, and
asks for a concept. This module turns that into one image generation through
the existing provider adapters (:mod:`api_gateway.twin.image_generation`) and
stores the result as a **proposal**: a ``generated_sketch`` work product that
never replaces the accepted concept until a person accepts it.

Modes (chosen from context when the caller does not say):

* ``text``: intent, constraints and a style (industrial sketch, marker
  render, line art or concept drawing).
* ``reference``: conditioned on the selected, *active* references, each sent
  for what its role says (an inspiration image for mood, a geometry image for
  shape, a specification's stated values as constraints). Verification
  evidence is never sent: it is evidence, not style.
* ``sketch``: conditioned on a ``sketch_strokes`` revision drawn to a PNG
  here, refining or completing it. The stroke layer itself is never written.

Region-local refinement takes a polygon in sketch space (the same space the
Create stage draws in) and an instruction. Sketch space maps onto an image
with its centre at the sketch origin and its shorter side spanning one unit,
so a mask drawn on the stage lands on the same pixels. A provider that takes a
mask gets one; with any other the output is composited back outside the
mask. Either way the pixels outside the mask, and inside every locked region,
are compared with the input, and a proposal whose preservation score is under
the threshold is flagged for review rather than accepted silently.

Scheduling uses the job runner (FORGE-788): ``target_key`` is the generation
target (``SKT-X:full`` or ``SKT-X:region-<hash>``), so a newer request on the
same target supersedes an older one still running, and identical requests
coalesce. ``base_revision`` is the accepted baseline the request was built on
(``GSK-X@n``, or ``none``). A result that arrives after the baseline moved is
stored as history with status ``stale`` and its job ends stale: it is never
offered as the current proposal and cannot be accepted.

Every output records its prompt and negative prompt, style, mode, reference
ids and hashes, mask, source sketch revision, provider, model, cost when the
provider reports one, job and operation ids, and base and target revisions.
It is labelled ``authority: illustrative`` everywhere. A generated raster is
never an editable sketch or CAD (FSG-009): turning one into vectors is not
part of this module.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field, field_validator, model_validator

from api_gateway.twin.image_generation import (
    ConditioningImage,
    GeneratedImage,
    ImageGenerationError,
    ImageGenerationTimeoutError,
    ImageGenerationUnavailableError,
    ImageRequest,
    capabilities_of,
    generate_request_with_timeout,
    parse_size,
    resolve_image_generator,
    timeout_from_env,
)
from observability.tracing import get_tracer
from twin_core.items.service import ItemRevisionConflictError

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.sketch_generation")

TOOL = "twin.generate_sketch"
ITEM_TYPE = "generated_sketch"
LABEL = "Generated concept · illustrative"
AUTHORITY = "illustrative"
REPRESENTATION = "generated_raster"
SKETCH_FRAME = "image centre at the sketch origin; the shorter image side spans one sketch unit"

Mode = Literal["text", "reference", "sketch"]
Style = Literal["industrial_sketch", "marker_render", "line_art", "concept_drawing"]
MODES: tuple[str, ...] = ("text", "reference", "sketch")
STYLES: tuple[str, ...] = ("industrial_sketch", "marker_render", "line_art", "concept_drawing")
STYLE_WORDS: dict[str, str] = {
    "industrial_sketch": (
        "an industrial design sketch: confident pencil and fineliner lines, light construction "
        "lines and soft grey shading on white paper"
    ),
    "marker_render": (
        "a marker rendering: bold marker strokes, a few flat colour washes and white "
        "highlights on white paper"
    ),
    "line_art": "clean line art: black outlines of even weight on white, no shading",
    "concept_drawing": (
        "a concept drawing: loose but readable outlines with simple tonal shading, on white"
    ),
}

#: Roles whose image is sent to the model, and what the model is told about it.
IMAGE_ROLE_GUIDANCE: dict[str, str] = {
    "inspiration": "use it for mood, style and materials only; do not copy its shape",
    "geometry": "follow its overall shape and proportions",
    "functional_demo": (
        "it shows how the product is used or works; keep that function, not its look"
    ),
    "existing_design": (
        "it is the current design; keep it recognisable and change only what is asked"
    ),
}
#: Roles whose stated values are constraints in words; their files are not sent.
TEXT_ROLES = frozenset({"specification", "manufacturing_rule"})
#: Roles never sent to an image model.
EXCLUDED_ROLES = {"verification_evidence": "verification evidence is evidence, never style"}

MAX_VARIANTS = 4
MAX_REFERENCES = 8
EXPECTED_SECONDS = 25.0
#: Pixels whose grey level moved no more than this (of 255) count as kept.
PIXEL_TOLERANCE = 24
THRESHOLD_ENV = "METAFORGE_SKETCH_PRESERVATION_THRESHOLD"
DEFAULT_THRESHOLD = 0.9
#: Images a reference may be when sent to a provider (the sketch reader's limit).
MAX_REFERENCE_IMAGE_BYTES = 5 * 1024 * 1024
REFERENCE_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
MAX_COORD = 1000.0
#: DFM-004: the label a speculative concept carries instead of :data:`LABEL`.
SPECULATIVE_ITEM_LABEL = "Speculative concept · illustrative"
NOT_ASSESSED_NOTE = "Manufacturing not assessed"

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _count(mode: str, outcome: str) -> None:
    try:
        _collector().record_sketch_generation(mode, outcome)
    except Exception as exc:  # noqa: BLE001 - a counter never fails a generation
        logger.debug("sketch_generation_metric_failed", error=str(exc))


def _observe_latency(mode: str, provider: str, seconds: float) -> None:
    try:
        _collector().record_sketch_generation_latency(mode, provider, seconds)
    except Exception as exc:  # noqa: BLE001
        logger.debug("sketch_generation_metric_failed", error=str(exc))


def preservation_threshold() -> float:
    raw = os.environ.get(THRESHOLD_ENV, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_THRESHOLD
    except ValueError:
        return DEFAULT_THRESHOLD
    return min(max(value, 0.0), 1.0)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SketchGenerationError(ValueError):
    """A request that cannot be generated, with the HTTP status it maps to."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


class SpendConfirmationRequiredError(SketchGenerationError):
    """The project's hourly image cap is reached: a person must confirm the spend."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409)


class GenerationStaleError(ItemRevisionConflictError):
    """The accepted baseline moved while this ran; its output is history only.

    A revision conflict on the ``generated_sketch`` line, so the job runner
    ends the job ``stale`` (FORGE-788) rather than failed.
    """


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class Region(BaseModel):
    """A closed polygon in sketch units, and an optional name for what it protects."""

    polygon: list[tuple[float, float]] = Field(min_length=3, max_length=200)
    label: str = Field("", max_length=200)

    @field_validator("polygon")
    @classmethod
    def _finite_and_bounded(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        import math

        for x, y in v:
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError("region points must be finite numbers")
            if abs(x) > MAX_COORD or abs(y) > MAX_COORD:
                raise ValueError(f"region points must lie within +/-{MAX_COORD:g} sketch units")
        return v

    def digest(self) -> str:
        raw = ";".join(f"{x:.4f},{y:.4f}" for x, y in self.polygon)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


class GenerateSketchRequest(BaseModel):
    """One generation request (FSG-001..006, DI-002)."""

    intent: str = Field(..., min_length=1, max_length=1500)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    #: Things the image must not do or show (FSG-006).
    negative: list[str] = Field(default_factory=list, max_length=20)
    #: Features that must not change: in the prompt, and checked by pixels
    #: when a locked region names them.
    protected_features: list[str] = Field(default_factory=list, max_length=20)
    style: Style = "industrial_sketch"
    #: Left out, the mode follows from context: a sketch, else references, else text.
    mode: Mode | None = None
    #: References chosen for this request; inactive ones are left out.
    reference_ids: list[str] = Field(default_factory=list, max_length=MAX_REFERENCES)
    #: The sketch to refine (``SKT-...``); its current head revision is used.
    sketch_key: str | None = Field(None, max_length=120)
    #: The sketch revision the client drew on; refused when the head moved past it.
    sketch_revision: str | None = Field(None, max_length=200)
    mask: Region | None = None
    mask_instruction: str = Field("", max_length=500)
    locked_regions: list[Region] = Field(default_factory=list, max_length=10)
    #: FSG-017: one image unless more are asked for.
    n_variants: int = Field(1, ge=1, le=MAX_VARIANTS)
    size: str = "1024x1024"
    #: The accepted line to build on (``GSK-...``); default the source's main line.
    baseline_key: str | None = Field(None, max_length=120)
    #: The baseline the client sees; refused when another was accepted since.
    expected_baseline: str | None = Field(None, max_length=200)
    #: Go past the hourly image cap (a person's explicit choice).
    confirm_spend: bool = False
    #: DFM-004: a deliberately speculative concept. The manufacturing context
    #: is recorded with the output but does not constrain it, and the output
    #: is labelled "Speculative concept".
    speculative: bool = False
    #: The component this concept is of, for its manufacturing context
    #: (``MFC-<component>``, else the product's).
    component: str | None = Field(None, max_length=120)

    @field_validator("constraints", "negative", "protected_features")
    @classmethod
    def _short_items(cls, v: list[str]) -> list[str]:
        out = [s.strip() for s in v if isinstance(s, str) and s.strip()]
        for s in out:
            if len(s) > 300:
                raise ValueError(
                    "each constraint, negative or protected feature is 300 chars at most"
                )
        return out

    @field_validator("size")
    @classmethod
    def _known_size(cls, v: str) -> str:
        parse_size(v)
        return v

    @model_validator(mode="after")
    def _mask_has_instruction(self) -> GenerateSketchRequest:
        if self.mask is not None and not (self.mask_instruction.strip() or self.intent.strip()):
            raise ValueError("a region edit needs an instruction")
        return self


# ---------------------------------------------------------------------------
# Pure helpers: prompt, rasterising, masks, preservation
# ---------------------------------------------------------------------------


@dataclass
class PreparedReference:
    """A reference as this request uses it."""

    work_product_id: str
    name: str
    role: str
    source_hash: str
    mime: str = ""
    data: bytes | None = None
    #: Stated or estimated facts, as text, for authoritative references.
    facts: list[str] = field(default_factory=list)
    protected_features: list[str] = field(default_factory=list)
    #: Why it was not sent, when it was not.
    excluded: str | None = None
    #: Whether its image reached the provider (set after the call).
    image_sent: bool = False

    def record(self) -> dict[str, Any]:
        return {
            "id": self.work_product_id,
            "name": self.name,
            "role": self.role,
            "source_hash": self.source_hash,
            "image_sent": self.image_sent,
            "facts_sent": len(self.facts),
            "excluded": self.excluded,
        }


def infer_mode(request: GenerateSketchRequest, *, has_references: bool) -> str:
    """The mode a request means: explicit, else sketch, else reference, else text."""
    if request.mode is not None:
        return request.mode
    if request.sketch_key:
        return "sketch"
    if has_references:
        return "reference"
    return "text"


def _region_words(polygon: list[tuple[float, float]]) -> str:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]

    def pct(v: float) -> int:
        return max(0, min(100, round((v + 0.5) * 100)))

    return (
        f"from {pct(min(xs))}% to {pct(max(xs))}% across and "
        f"{pct(min(ys))}% to {pct(max(ys))}% down"
    )


def build_prompt(
    *,
    mode: str,
    style: str,
    intent: str,
    constraints: list[str],
    negative: list[str],
    protected_features: list[str],
    references: list[PreparedReference],
    first_image: str | None,
    mask: Region | None,
    mask_instruction: str,
    provider_masks: bool,
    manufacturing: str = "",
) -> tuple[str, str]:
    """``(prompt, negative_prompt)`` for one request. Deterministic.

    ``first_image`` says what the first conditioning image is: ``sketch``
    (the person's strokes), ``baseline`` (the accepted concept being refined)
    or ``None`` when only references are sent. ``manufacturing`` is the
    manufacturing context block (DFM-001); a ``manufacturing_rule``
    reference's stated values stay constraints, as before.
    """
    lines = [
        f"Freeform product concept sketch: {intent.strip()}",
        f"Draw it as {STYLE_WORDS.get(style, STYLE_WORDS['industrial_sketch'])}.",
    ]
    index = 1
    if first_image == "sketch":
        lines.append(
            "Image 1 is the person's rough hand sketch, black strokes on white. Refine it into "
            "a clean drawing in that style: keep its outline, proportions and every part it "
            "shows, complete lines it leaves unfinished, and do not move or remove what it draws."
        )
        index = 2
    elif first_image == "baseline":
        lines.append(
            "Image 1 is the accepted concept. Keep it, and change only what is asked below."
        )
        index = 2
    for ref in references:
        if ref.excluded or ref.data is None or ref.role not in IMAGE_ROLE_GUIDANCE:
            continue
        lines.append(f"Image {index} ({ref.name or 'reference'}): {IMAGE_ROLE_GUIDANCE[ref.role]}.")
        index += 1
    stated = [f for ref in references if not ref.excluded for f in ref.facts]
    all_constraints = [*constraints, *stated]
    if all_constraints:
        lines.append("Respect these constraints: " + "; ".join(all_constraints) + ".")
    protected = list(
        dict.fromkeys([*protected_features, *(p for r in references for p in r.protected_features)])
    )
    if protected:
        lines.append("Keep these unchanged: " + "; ".join(protected) + ".")
    if mask is not None:
        what = mask_instruction.strip() or intent.strip()
        if provider_masks:
            lines.append(
                f"Change only the masked region: {what}. "
                "Leave everything outside it exactly as it is."
            )
        else:
            lines.append(
                f"Change only the region {_region_words(mask.polygon)} of the image: {what}. "
                "Leave everything outside that region exactly as it is."
            )
    if negative:
        lines.append("Do not: " + "; ".join(negative) + ".")
    if manufacturing:
        lines.append(manufacturing)
    lines.append(
        "This is an illustrative concept, not an engineering drawing: add no dimensions, "
        "text, labels, logos or watermarks."
    )
    negative_prompt = "; ".join([*negative, "dimensions", "text", "logos", "watermarks"])
    return "\n".join(lines), negative_prompt


def _pil() -> Any:
    try:
        from PIL import Image, ImageChops, ImageDraw
    except ImportError:
        return None
    return Image, ImageDraw, ImageChops


def to_pixels(point: tuple[float, float], width: int, height: int) -> tuple[float, float]:
    """Sketch units to image pixels (:data:`SKETCH_FRAME`)."""
    s = min(width, height)
    return point[0] * s + width / 2, point[1] * s + height / 2


def rasterise_strokes(strokes: list[dict[str, Any]], size: str) -> bytes:
    """The strokes as black lines on white, in :data:`SKETCH_FRAME` (a PNG)."""
    pil = _pil()
    if pil is None:
        raise SketchGenerationError("drawing the sketch for the image model needs Pillow", 503)
    image_mod, draw_mod, _ = pil
    width, height = parse_size(size)
    img = image_mod.new("RGB", (width, height), (255, 255, 255))
    draw = draw_mod.Draw(img)
    line_w = max(2, round(min(width, height) * 0.004))
    for stroke in strokes:
        pts = [
            to_pixels((float(p[0]), float(p[1])), width, height)
            for p in stroke.get("points") or []
            if isinstance(p, list | tuple) and len(p) >= 2
        ]
        if len(pts) == 1:
            x, y = pts[0]
            draw.ellipse([x - line_w, y - line_w, x + line_w, y + line_w], fill=(0, 0, 0))
        elif len(pts) > 1:
            draw.line(pts, fill=(0, 0, 0), width=line_w, joint="curve")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def region_mask(regions: list[Region], width: int, height: int) -> Any:
    """A Pillow ``L`` image, 255 inside any of ``regions`` and 0 elsewhere."""
    pil = _pil()
    if pil is None:
        raise SketchGenerationError("region edits need Pillow", 503)
    image_mod, draw_mod, _ = pil
    mask = image_mod.new("L", (width, height), 0)
    draw = draw_mod.Draw(mask)
    for region in regions:
        draw.polygon([to_pixels(p, width, height) for p in region.polygon], fill=255)
    return mask


def provider_mask_png(region: Region, width: int, height: int) -> bytes:
    """The edit mask a masking provider takes: transparent inside the region."""
    pil = _pil()
    if pil is None:
        raise SketchGenerationError("region edits need Pillow", 503)
    image_mod, _, chops = pil
    inside = region_mask([region], width, height)
    alpha = chops.invert(inside)
    rgba = image_mod.new("RGBA", (width, height), (0, 0, 0, 255))
    rgba.putalpha(alpha)
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    return buf.getvalue()


def load_rgb(data: bytes, width: int, height: int) -> Any:
    """Decode an image and bring it to ``width`` x ``height`` RGB."""
    pil = _pil()
    if pil is None:
        raise SketchGenerationError("reading the generated image needs Pillow", 503)
    image_mod = pil[0]
    img = image_mod.open(io.BytesIO(data)).convert("RGB")
    if img.size != (width, height):
        img = img.resize((width, height), image_mod.Resampling.LANCZOS)
    return img


def kept_fraction(before: Any, after: Any, region: Any) -> float | None:
    """The fraction of ``region``'s pixels whose grey level moved <= :data:`PIXEL_TOLERANCE`.

    ``region`` is an ``L`` mask (255 = counted). ``None`` when it is empty.
    """
    pil = _pil()
    assert pil is not None
    _, _, chops = pil
    total = region.histogram()[255]
    if total == 0:
        return None
    diff = chops.difference(before.convert("L"), after.convert("L"))
    within = diff.point(lambda v: 255 if v <= PIXEL_TOLERANCE else 0)
    kept = chops.multiply(within, region).histogram()[255]
    return kept / total


def preservation_report(
    before: Any,
    after_raw: Any,
    after_final: Any,
    *,
    mask: Region | None,
    locked: list[Region],
    protected_features: list[str],
    method: str,
    threshold: float,
) -> dict[str, Any]:
    """Compare what had to stay the same (FSG-004, FSG-006).

    ``outside_mask_raw`` is the provider's own output; ``outside_mask`` is
    what is stored (after compositing, when there was any). A locked region
    is never composited, so its score is always the provider's.
    """
    pil = _pil()
    assert pil is not None
    _, _, chops = pil
    width, height = before.size
    report: dict[str, Any] = {
        "method": method,
        "threshold": threshold,
        "tolerance": PIXEL_TOLERANCE,
    }
    failures: list[str] = []
    if mask is not None:
        outside = chops.invert(region_mask([mask], width, height))
        raw = kept_fraction(before, after_raw, outside)
        final = kept_fraction(before, after_final, outside)
        report["outside_mask_raw"] = None if raw is None else round(raw, 4)
        report["outside_mask"] = None if final is None else round(final, 4)
        if final is not None and final < threshold:
            failures.append(f"{round((1 - final) * 100)}% of the image outside the region changed")
    regions: list[dict[str, Any]] = []
    for region in locked:
        score = kept_fraction(before, after_final, region_mask([region], width, height))
        entry = {
            "label": region.label or "locked region",
            "score": None if score is None else round(score, 4),
        }
        regions.append(entry)
        if score is not None and score < threshold:
            failures.append(f"{entry['label']} changed ({round(score * 100)}% kept)")
    report["locked_regions"] = regions
    checked = {r.label.strip().lower() for r in locked if r.label.strip()}
    report["protected_features"] = [
        {"name": p, "checked_by_pixels": p.strip().lower() in checked} for p in protected_features
    ]
    scores = [
        s for s in [report.get("outside_mask")] + [r["score"] for r in regions] if s is not None
    ]
    report["score"] = round(min(scores), 4) if scores else None
    report["passed"] = not failures
    report["failures"] = failures
    return report


def composite_outside(before: Any, after: Any, mask: Region) -> Any:
    """``after`` inside the mask, ``before`` everywhere else."""
    pil = _pil()
    assert pil is not None
    image_mod = pil[0]
    width, height = before.size
    return image_mod.composite(after, before, region_mask([mask], width, height))


def labelled_png(img: Any, *, provider: str, model: str) -> bytes:
    """The image as PNG with the illustrative label in its text chunks.

    No banner is drawn on the pixels: the image is shown under the person's
    strokes in sketch space and fed back for refinement, where a banner would
    be drawn over and composited. The label travels in the PNG and every
    record and view says it.
    """
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Description", LABEL)
    info.add_text("Authority", AUTHORITY)
    info.add_text("Software", f"{provider}/{model}")
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def pixel_difference(a: bytes, b: bytes) -> float | None:
    """The fraction of pixels that differ beyond :data:`PIXEL_TOLERANCE` (A/B compare)."""
    pil = _pil()
    if pil is None:
        return None
    image_mod = pil[0]
    first = image_mod.open(io.BytesIO(a)).convert("RGB")
    second = load_rgb(b, first.size[0], first.size[1])
    everything = image_mod.new("L", first.size, 255)
    kept = kept_fraction(first, second, everything)
    return None if kept is None else round(1 - kept, 4)


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


@dataclass
class Prepared:
    """A validated request with everything it was built from, ready to run."""

    project_id: str
    request: GenerateSketchRequest
    mode: str
    surface: str
    references: list[PreparedReference]
    sketch: dict[str, Any] | None
    baseline: dict[str, Any] | None
    line_key: str | None
    line_source: str
    target_key: str
    base_revision: str
    requested_at: float = field(default_factory=time.monotonic)
    #: The manufacturing context block this request was prepared with (DFM-001).
    manufacturing: Any = None

    def job_arguments(self) -> dict[str, Any]:
        """What identifies this request (hashed for coalescing; kept for a retry)."""
        args = self.request.model_dump(mode="json", exclude={"confirm_spend"})
        args["project_id"] = self.project_id
        args["resolved_mode"] = self.mode
        args["sketch_ref"] = (self.sketch or {}).get("item_ref")
        args["reference_hashes"] = sorted(
            f"{r.work_product_id}:{r.source_hash}" for r in self.references
        )
        args["surface"] = self.surface
        return args


#: ``(project_id) -> [ReferenceRecord-like dicts]``: the project's reference records.
ReferenceRecords = Callable[[str], Awaitable[list[dict[str, Any]]]]


class SketchGenerationService:
    """Prepares, runs, stores and decides concept sketch generations."""

    def __init__(
        self,
        twin: Any,
        project_backend: Any = None,
        *,
        generator: Any = None,
        blob_writer: Callable[..., str] | None = None,
        blob_reader: Callable[[Any], tuple[bytes, str]] | None = None,
        reference_records: ReferenceRecords | None = None,
        job_runner: Any = None,
        timeout_s: float | None = None,
        disclosure_recorder: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._twin = twin
        self._project_backend = project_backend
        self._generator = generator
        self._write_blob = blob_writer or _default_blob_writer
        self._read_blob = blob_reader or _default_blob_reader
        self._reference_records = reference_records or _create_reference_records
        self._job_runner = job_runner
        self._timeout_s = timeout_s
        self._record_disclosure = disclosure_recorder or _record_disclosures

    # -- reading the twin -------------------------------------------------

    async def _wp(self, node_id: str) -> Any:
        try:
            uid = UUID(str(node_id))
        except ValueError as exc:
            raise SketchGenerationError(f"{node_id!r} is not a node id", 400) from exc
        wp = await self._twin.get_work_product(uid)
        if wp is None:
            raise SketchGenerationError(f"no work product {node_id}", 404)
        return wp

    @staticmethod
    def _type(wp: Any) -> str:
        return str(getattr(wp.type, "value", wp.type))

    @staticmethod
    def _same_project(wp: Any, project_id: str) -> bool:
        return str(getattr(wp, "project_id", "") or "") == str(project_id)

    async def _generated(self, project_id: str) -> list[Any]:
        from twin_core.models.enums import WorkProductType

        return list(
            await self._twin.list_work_products(
                work_product_type=WorkProductType.GENERATED_SKETCH, project_id=UUID(project_id)
            )
        )

    async def _line_for(
        self, project_id: str, source: str, key: str | None
    ) -> tuple[str | None, Any]:
        """``(line key, item)`` of the accepted line for ``source`` (or the named one)."""
        from twin_core.items import find_item

        if key is None:
            for wp in await self._generated(project_id):
                meta = wp.metadata or {}
                if meta.get("baseline_for") == source and meta.get("item_key"):
                    key = str(meta["item_key"])
                    break
        if key is None:
            return None, None
        item = await find_item(self._twin, key, project_id)
        if item is None:
            raise SketchGenerationError(f"no accepted concept line {key} in this project", 404)
        return key, item

    async def baseline_ref(self, project_id: str, line_key: str | None) -> str:
        """``KEY@n`` of the line's accepted head, or ``none``."""
        if line_key is None:
            return "none"
        from twin_core.items import find_item

        item = await find_item(self._twin, line_key, project_id)
        if item is None or item.head_node_id is None:
            return "none"
        return f"{line_key}@{item.head_revision}"

    # -- prepare ------------------------------------------------------------

    async def _references(
        self, project_id: str, request: GenerateSketchRequest
    ) -> list[PreparedReference]:
        """The request's references that are active, readable and in this project (REF-004/007)."""
        from api_gateway.create.models import AUTHORITATIVE_ROLES

        if not request.reference_ids:
            return []
        records = {
            str(r.get("work_product_id")): r for r in await self._reference_records(project_id)
        }
        out: list[PreparedReference] = []
        for ref_id in dict.fromkeys(request.reference_ids):
            wp = await self._wp(ref_id)
            if not self._same_project(wp, project_id):
                # REF-007: a reference of another project is never read or sent.
                raise SketchGenerationError(
                    f"reference {ref_id} does not belong to this project", 403
                )
            record = records.get(ref_id) or {}
            if record and record.get("active") is False:
                continue  # REF-004: an inactive reference is not part of this request
            role = str(record.get("role") or "inspiration")
            meta = wp.metadata or {}
            ref = PreparedReference(
                work_product_id=ref_id,
                name=str(record.get("name") or wp.name or ""),
                role=role,
                source_hash=str(
                    record.get("source_hash") or meta.get("content_sha256") or wp.content_hash or ""
                ),
                protected_features=[str(p) for p in record.get("protected_features") or []],
            )
            if role in EXCLUDED_ROLES:
                ref.excluded = EXCLUDED_ROLES[role]
            elif role in TEXT_ROLES:
                ref.facts = [
                    f"{f.get('parameter')} {f.get('value'):g} {f.get('unit') or ''}".strip()
                    if isinstance(f.get("value"), int | float)
                    else str(f.get("text") or f.get("parameter"))
                    for f in record.get("facts") or []
                    if f.get("basis") == "stated" and role in AUTHORITATIVE_ROLES
                ][:20]
                if not ref.facts:
                    ref.excluded = "no stated values extracted yet; extract its facts first"
            else:
                await self._load_reference_image(wp, ref)
            out.append(ref)
        return out

    async def _load_reference_image(self, wp: Any, ref: PreparedReference) -> None:
        from api_gateway.twin.import_commit import ImportRejected, check_importable

        meta = wp.metadata or {}
        filename = str(meta.get("original_filename") or wp.name or "reference")
        try:
            data, _name = await asyncio.to_thread(self._read_blob, wp)
        except Exception as exc:  # noqa: BLE001 - say why it was not sent
            ref.excluded = f"its file could not be read ({str(exc)[:120]})"
            return
        try:
            check_importable(filename if "." in filename else f"{filename}.png", len(data))
        except ImportRejected as exc:
            ref.excluded = f"not a supported file: {exc}"
            return
        mime = _sniff_image(data)
        if mime not in REFERENCE_IMAGE_MIMES:
            ref.excluded = "not an image an image model can take"
            return
        if len(data) > MAX_REFERENCE_IMAGE_BYTES:
            ref.excluded = f"larger than {MAX_REFERENCE_IMAGE_BYTES // (1024 * 1024)} MB"
            return
        ref.data = data
        ref.mime = mime
        if not ref.source_hash:
            ref.source_hash = hashlib.sha256(data).hexdigest()

    async def prepare(
        self, project_id: str, request: GenerateSketchRequest, *, surface: str = "create"
    ) -> Prepared:
        """Validate a request and resolve what it is built from. Nothing is paid for here."""
        try:
            UUID(str(project_id))
        except ValueError as exc:
            raise SketchGenerationError("invalid project id", 400) from exc
        with tracer.start_as_current_span("sketch_generation.prepare") as span:
            span.set_attribute("project.id", project_id)
            references = await self._references(project_id, request)
            usable = [r for r in references if not r.excluded]
            mode = infer_mode(request, has_references=bool(usable))
            span.set_attribute("sketch_generation.mode", mode)
            if mode == "reference" and not usable:
                raise SketchGenerationError(
                    "reference mode needs at least one active reference that can be sent"
                )
            sketch: dict[str, Any] | None = None
            if mode == "sketch":
                sketch = await self._sketch(project_id, request)
            source = (sketch or {}).get("item_key") or request.baseline_key or "CONCEPT"
            line_key, item = await self._line_for(project_id, str(source), request.baseline_key)
            base_revision = "none"
            baseline: dict[str, Any] | None = None
            if item is not None and item.head_node_id is not None:
                base_revision = f"{line_key}@{item.head_revision}"
                baseline = {"item_ref": base_revision, "node_id": str(item.head_node_id)}
            if request.expected_baseline and request.expected_baseline != base_revision:
                raise SketchGenerationError(
                    f"the accepted concept is now {base_revision}, not "
                    f"{request.expected_baseline}; look at it before generating again",
                    409,
                )
            if request.mask is not None and baseline is None and sketch is None:
                raise SketchGenerationError(
                    "nothing to refine yet: accept a concept or draw a sketch "
                    "before editing a region"
                )
            region = f"region-{request.mask.digest()}" if request.mask is not None else "full"
            from api_gateway.twin.manufacturing_context import manufacturing_context_block

            manufacturing = await manufacturing_context_block(
                project_id,
                request.component,
                twin=self._twin,
                generator="sketch_generation",
                speculative=request.speculative,
                hint=request.intent,
            )
            span.set_attribute("sketch_generation.manufacturing_context_ref", manufacturing.ref)
            prepared = Prepared(
                project_id=str(project_id),
                request=request,
                mode=mode,
                surface=surface,
                references=references,
                sketch=sketch,
                baseline=baseline,
                line_key=line_key,
                line_source=str(source),
                target_key=f"{source}:{region}",
                base_revision=base_revision,
                manufacturing=manufacturing,
            )
            span.set_attribute("sketch_generation.target_key", prepared.target_key)
            span.set_attribute("sketch_generation.base_revision", base_revision)
            return prepared

    async def _sketch(self, project_id: str, request: GenerateSketchRequest) -> dict[str, Any]:
        from api_gateway.twin.sketch_strokes_recorder import current_sketch

        if not request.sketch_key:
            raise SketchGenerationError("sketch mode needs sketch_key (a saved SKT sketch)")
        current = await current_sketch(self._twin, project_id, request.sketch_key)
        if current is None or not current.item_ref:
            raise SketchGenerationError(
                f"no saved sketch {request.sketch_key} in this project", 404
            )
        if request.sketch_revision and request.sketch_revision != current.item_ref:
            raise SketchGenerationError(
                f"the sketch is now {current.item_ref}, not {request.sketch_revision}; "
                "save it and ask again",
                409,
            )
        if not current.strokes:
            raise SketchGenerationError("the sketch has no strokes to refine")
        return {
            "item_key": current.item_key,
            "item_ref": current.item_ref,
            "node_id": current.node_id,
            "strokes": current.strokes,
        }

    # -- spend ------------------------------------------------------------

    async def check_spend(self, prepared: Prepared) -> dict[str, Any]:
        """The project's hourly image cap for generations a person starts here.

        The same variable and default as ``twin.render_concept_image``
        (``METAFORGE_AUTO_APPROVE_IMAGE_PER_HOUR``). Over it, the person must
        confirm the spend; the request says so instead of running.
        """
        from api_gateway.approvals.tool_write_policy import SPEND_CAPS, hourly_cap

        cap = hourly_cap(TOOL)
        since = datetime.now(UTC) - timedelta(hours=1)
        used = 0
        for wp in await self._generated(prepared.project_id):
            meta = wp.metadata or {}
            created = getattr(wp, "created_at", None)
            if meta.get("item_key") or meta.get("surface") != prepared.surface:
                continue
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if created is not None and created >= since:
                used += 1
        info = {"used_last_hour": used, "cap": cap, "requested": prepared.request.n_variants}
        if (
            cap is not None
            and used + prepared.request.n_variants > cap
            and not prepared.request.confirm_spend
        ):
            env = SPEND_CAPS[TOOL][0]
            raise SpendConfirmationRequiredError(
                f"{used} concept images were generated in this project in the last hour (cap "
                f"{cap}, {env}). Confirm to generate {prepared.request.n_variants} more."
            )
        return info

    # -- run ----------------------------------------------------------------

    def _gen(self) -> Any:
        return self._generator or resolve_image_generator()

    async def start(
        self,
        prepared: Prepared,
        *,
        wait: bool = False,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Run now (``wait``) or as a job on the generation target (FSG-012)."""
        if wait:
            return await self.run(prepared, {})
        from tool_registry.jobs import JobRunner, default_job_runner, register_retry

        register_retry(TOOL, self._retry_factory)
        runner = self._job_runner or default_job_runner()
        holder: dict[str, Any] = {}
        job, reused = await runner.start(
            TOOL,
            prepared.job_arguments(),
            lambda: self.run(prepared, holder),
            session_id=session_id or f"create-{prepared.project_id}",
            project_id=prepared.project_id,
            expected_seconds=EXPECTED_SECONDS * prepared.request.n_variants,
            target_key=prepared.target_key,
            base_revision=prepared.base_revision,
        )
        holder["job_id"] = job.job_id
        holder["runner"] = runner
        logger.info(
            "sketch_generation_started",
            project_id=prepared.project_id,
            job_id=job.job_id,
            reused=reused,
            mode=prepared.mode,
            target_key=prepared.target_key,
            base_revision=prepared.base_revision,
            n_variants=prepared.request.n_variants,
        )
        reply = JobRunner.reply(job, reused)
        reply.update(
            {
                "operation_id": f"job:{job.job_id}",
                "mode": prepared.mode,
                "target_key": prepared.target_key,
                "base_revision": prepared.base_revision,
                "accepted_baseline": prepared.baseline,
                "authority": AUTHORITY,
            }
        )
        return reply

    def _retry_factory(self, arguments: dict[str, Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
        project_id = str(arguments.get("project_id") or "")
        surface = str(arguments.get("surface") or "create")
        known = set(GenerateSketchRequest.model_fields)
        request = GenerateSketchRequest.model_validate(
            {k: v for k, v in arguments.items() if k in known} | {"confirm_spend": True}
        )

        async def work() -> dict[str, Any]:
            prepared = await self.prepare(project_id, request, surface=surface)
            return await self.run(prepared, {})

        return work

    async def _input_images(
        self, prepared: Prepared, width: int, height: int
    ) -> tuple[str | None, bytes | None, bytes | None]:
        """``(kind, sent, before)``: image 1 as sent, and what preservation compares with.

        * A region edit refines the accepted concept when there is one. In
          sketch mode the person's strokes inside the region are drawn over
          it, so "draw over the backrest, refine that region" sends both;
          preservation still compares with the plain accepted concept.
        * Sketch mode without a region sends the drawn strokes.
        * Text and reference mode send no image 1: a new concept, not an edit.
        """
        request = prepared.request
        if request.mask is not None and prepared.baseline is not None:
            wp = await self._wp(prepared.baseline["node_id"])
            data, _ = await asyncio.to_thread(self._read_blob, wp)
            before = load_rgb(data, width, height)
            sent = before
            if prepared.sketch is not None:
                strokes = load_rgb(
                    rasterise_strokes(prepared.sketch["strokes"], request.size), width, height
                )
                pil = _pil()
                assert pil is not None
                image_mod, _, chops = pil
                ink = chops.invert(strokes.convert("L")).point(lambda v: 255 if v > 64 else 0)
                inside = chops.multiply(ink, region_mask([request.mask], width, height))
                sent = image_mod.composite(strokes, before, inside)
            return "baseline", _png(sent), _png(before)
        if prepared.sketch is not None:
            drawn = rasterise_strokes(prepared.sketch["strokes"], request.size)
            return "sketch", drawn, drawn
        return None, None, None

    async def run(self, prepared: Prepared, holder: dict[str, Any]) -> dict[str, Any]:
        """Generate the variants, check them, store them as proposals."""
        request = prepared.request
        mode = prepared.mode
        width, height = parse_size(request.size)
        with tracer.start_as_current_span("sketch_generation.run") as span:
            span.set_attribute("project.id", prepared.project_id)
            span.set_attribute("sketch_generation.mode", mode)
            span.set_attribute("sketch_generation.variants", request.n_variants)
            try:
                gen = self._gen()
            except ImageGenerationUnavailableError as exc:
                span.record_exception(exc)
                _count(mode, "unavailable")
                logger.warning("sketch_generation_unavailable", reason=str(exc))
                raise
            caps = capabilities_of(gen)
            first_kind, first_png, before_png = await self._input_images(prepared, width, height)
            if request.mask is not None and first_png is None:
                raise SketchGenerationError("nothing to refine: no accepted concept or sketch")
            images: list[ConditioningImage] = []
            if first_png is not None:
                images.append(ConditioningImage(first_png, "image/png", first_kind or "input"))
            for ref in prepared.references:
                if ref.excluded or ref.data is None:
                    continue
                if len(images) >= caps.max_images:
                    ref.excluded = f"the provider takes at most {caps.max_images} image(s) per call"
                    continue
                images.append(ConditioningImage(ref.data, ref.mime, ref.role))
                ref.image_sent = True
            provider_masks = bool(caps.mask and request.mask is not None and first_png is not None)
            prompt, negative_prompt = build_prompt(
                mode=mode,
                style=request.style,
                intent=request.intent,
                constraints=request.constraints,
                negative=request.negative,
                protected_features=request.protected_features,
                references=prepared.references,
                first_image=first_kind,
                mask=request.mask,
                mask_instruction=request.mask_instruction,
                provider_masks=provider_masks,
                manufacturing=prepared.manufacturing.text if prepared.manufacturing else "",
            )
            mask_png = (
                provider_mask_png(request.mask, width, height)
                if provider_masks and request.mask is not None
                else None
            )
            image_request = ImageRequest(
                prompt=prompt, size=request.size, images=tuple(images), mask=mask_png
            )
            limit = self._timeout_s if self._timeout_s is not None else timeout_from_env()
            generation_id = uuid4().hex[:12]
            job_id = holder.get("job_id")
            try:
                results = await asyncio.gather(
                    *(
                        generate_request_with_timeout(gen, image_request, timeout_s=limit)
                        for _ in range(request.n_variants)
                    )
                )
            except asyncio.CancelledError:
                self._count_cancelled(mode, holder)
                raise
            except ImageGenerationTimeoutError as exc:
                span.record_exception(exc)
                _count(mode, "timeout")
                logger.warning("sketch_generation_failed", reason="timeout", timeout_s=limit)
                raise
            except ImageGenerationUnavailableError as exc:
                span.record_exception(exc)
                _count(mode, "unavailable")
                logger.warning("sketch_generation_unavailable", reason=str(exc))
                raise
            except Exception as exc:
                span.record_exception(exc)
                _count(mode, "failed")
                logger.warning("sketch_generation_failed", error=str(exc))
                if isinstance(exc, ImageGenerationError):
                    raise
                raise ImageGenerationError(f"image generation failed: {exc}") from exc
            finally:
                sent = [
                    r for r in prepared.references if r.image_sent or (r.facts and not r.excluded)
                ]
                if sent:
                    await self._disclose(prepared, sent, gen, job_id)

            self._milestone(prepared, job_id, "Image received; checking what had to stay the same")
            # Stale before storing: a baseline accepted since this started wins.
            line_key = (
                prepared.line_key
                or (await self._line_for(prepared.project_id, prepared.line_source, None))[0]
            )
            head = await self.baseline_ref(prepared.project_id, line_key)
            stale = head != prepared.base_revision
            threshold = preservation_threshold()
            stored: list[dict[str, Any]] = []
            for index, image in enumerate(results):
                if not image.image_bytes:
                    _count(mode, "failed")
                    raise ImageGenerationError("the image provider returned an empty image")
                stored.append(
                    await self._store(
                        prepared,
                        image,
                        index=index,
                        generation_id=generation_id,
                        before_png=before_png,
                        first_kind=first_kind,
                        provider_masks=provider_masks,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        job_id=job_id,
                        threshold=threshold,
                        status="stale" if stale else "proposed",
                        head=head,
                    )
                )
            flagged = [s for s in stored if s.get("review_required")]
            if flagged and not stale:
                self._review(prepared, job_id, flagged)
            out: dict[str, Any] = {
                "generation_id": generation_id,
                "node_id": stored[0]["node_id"],
                "proposals": stored,
                "mode": mode,
                "target_key": prepared.target_key,
                "base_revision": prepared.base_revision,
                "authority": AUTHORITY,
                "label": stored[0].get("label") or LABEL,
                "stale": stale,
                **_manufacturing_fields(prepared),
            }
            if stale:
                for _ in stored:
                    _count(mode, "stale")
                logger.info(
                    "sketch_generation_stale",
                    project_id=prepared.project_id,
                    base_revision=prepared.base_revision,
                    head=head,
                    kept_as_history=len(stored),
                )
                raise GenerationStaleError(
                    f"the accepted concept moved to {head} while this ran from "
                    f"{prepared.base_revision}; the result is kept as history only"
                )
            return out

    def _count_cancelled(self, mode: str, holder: dict[str, Any]) -> None:
        runner = holder.get("runner")
        job_id = holder.get("job_id")
        status = "cancelled"
        if runner is not None and job_id:
            job = runner.get(job_id)
            if job is not None and job.status == "superseded":
                status = "superseded"
        _count(mode, status)
        logger.info("sketch_generation_stopped", job_id=job_id, status=status)

    def _milestone(self, prepared: Prepared, job_id: str | None, message: str) -> None:
        if not job_id:
            return
        from api_gateway.operations import publisher as operations

        operations.publish(
            prepared.project_id,
            f"job:{job_id}",
            "milestone",
            "running",
            sequence=50,
            event_id=f"sketch-gen-{job_id}-received",
            domain="sketch",
            title="Generate sketch",
            message=message,
        )

    def _review(
        self, prepared: Prepared, job_id: str | None, flagged: list[dict[str, Any]]
    ) -> None:
        if not job_id:
            return
        from api_gateway.operations import publisher as operations

        reasons = "; ".join(
            f for s in flagged for f in (s.get("preservation") or {}).get("failures", [])
        )
        operations.publish(
            prepared.project_id,
            f"job:{job_id}",
            "review_required",
            "waiting_for_user",
            sequence=60,
            event_id=f"sketch-gen-{job_id}-preservation",
            domain="sketch",
            title="Generate sketch",
            message=(
                "The new concept did not keep everything it had to"
                + (f": {reasons}" if reasons else "")
                + ". Compare it before accepting."
            )[:500],
            severity="warning",
            artifact_refs=[
                {
                    "kind": "image",
                    "id": s["node_id"],
                    "label": "Generated concept",
                    "authority": "illustrative",
                }
                for s in flagged[:5]
            ],
        )

    async def _disclose(
        self, prepared: Prepared, sent: list[PreparedReference], gen: Any, job_id: str | None
    ) -> None:
        provider = str(getattr(gen, "provider", "") or "custom")
        model = str(getattr(gen, "model", "") or "")
        logger.info(
            "sketch_generation_references_sent",
            project_id=prepared.project_id,
            provider=provider,
            model=model,
            references=[r.work_product_id for r in sent],
        )
        try:
            await self._record_disclosure(
                prepared.project_id,
                [(r.work_product_id, r.source_hash) for r in sent],
                provider=provider,
                model=model,
                operation_id=f"job:{job_id}" if job_id else "",
            )
        except Exception as exc:  # noqa: BLE001 - the log line above is the fallback record
            logger.warning("sketch_generation_disclosure_not_recorded", error=str(exc))

    async def _store(
        self,
        prepared: Prepared,
        image: GeneratedImage,
        *,
        index: int,
        generation_id: str,
        before_png: bytes | None,
        first_kind: str | None,
        provider_masks: bool,
        prompt: str,
        negative_prompt: str,
        job_id: str | None,
        threshold: float,
        status: str,
        head: str,
    ) -> dict[str, Any]:
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        request = prepared.request
        width, height = parse_size(request.size)
        raw = load_rgb(image.image_bytes, width, height)
        final = raw
        preservation: dict[str, Any] | None = None
        if before_png is not None and (request.mask is not None or request.locked_regions):
            before = load_rgb(before_png, width, height)
            method = "none"
            if request.mask is not None:
                method = "provider_mask" if provider_masks and image.masked else "composited"
                if method == "composited":
                    final = composite_outside(before, raw, request.mask)
            preservation = preservation_report(
                before,
                raw,
                final,
                mask=request.mask,
                locked=request.locked_regions,
                protected_features=request.protected_features,
                method=method,
                threshold=threshold,
            )
        review_required = bool(preservation and not preservation["passed"])
        png = labelled_png(final, provider=image.provider, model=image.model)
        content_hash = hashlib.sha256(png).hexdigest()
        wp_id = uuid4()
        filename = f"generated-concept-{generation_id}-{index + 1}.png"
        try:
            key = await asyncio.to_thread(
                self._write_blob, str(wp_id), filename, png, content_type="image/png"
            )
        except Exception as exc:  # noqa: BLE001 - never a node without its image
            _count(prepared.mode, "failed")
            raise ImageGenerationError(
                f"the concept was generated but could not be stored ({exc})"
            ) from exc
        latency = round(time.monotonic() - prepared.requested_at, 3)
        if index == 0:
            _observe_latency(prepared.mode, image.provider, latency)
        _count(
            prepared.mode,
            "stale"
            if status == "stale"
            else ("preservation_flagged" if review_required else "generated"),
        )
        mask_record = (
            {
                "polygon": [list(p) for p in request.mask.polygon],
                "instruction": request.mask_instruction,
                "raster": "provider_mask" if provider_masks and image.masked else "composited",
                "space": "sketch_unit",
            }
            if request.mask is not None
            else None
        )
        provenance: dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "intent": request.intent,
            "constraints": request.constraints,
            "negative": request.negative,
            "protected_features": request.protected_features,
            "style": request.style,
            "mode": prepared.mode,
            "references": [r.record() for r in prepared.references],
            "mask": mask_record,
            "locked_regions": [
                {"polygon": [list(p) for p in r.polygon], "label": r.label}
                for r in request.locked_regions
            ],
            "source_sketch": (
                {k: prepared.sketch[k] for k in ("item_key", "item_ref", "node_id")}
                if prepared.sketch
                else None
            ),
            "input_image": first_kind,
            "baseline_input": prepared.baseline,
            "provider": image.provider,
            "model": image.model,
            "cost_usd": image.cost_usd,
            "cost_reported": image.cost_usd is not None,
            "job_id": job_id,
            "operation_id": f"job:{job_id}" if job_id else None,
            "base_revision": prepared.base_revision,
            "target_key": prepared.target_key,
            "target_revision": None,
            "baseline_line": prepared.line_key,
            "baseline_source": prepared.line_source,
            "frame": SKETCH_FRAME,
            "size": request.size,
            "images_sent": image.images_sent or (1 if image.conditioned else 0),
            "provider_masked": bool(image.masked),
            "partial_images": False,
            **_manufacturing_fields(prepared),
        }
        metadata: dict[str, Any] = {
            "original_filename": filename,
            "minio_object_key": key,
            "content_sha256": content_hash,
            "image_files": {filename: key},
            # FSG-009 / PR-003: a generated picture of intent. Never an editable
            # sketch, never CAD, never evidence.
            "authority": AUTHORITY,
            "illustrative_label": SPECULATIVE_ITEM_LABEL if request.speculative else LABEL,
            "representation": REPRESENTATION,
            "editable": False,
            "generated_by": TOOL,
            "generation_status": status,
            "review_required": review_required,
            "generation_id": generation_id,
            "variant_index": index,
            "n_variants": request.n_variants,
            "provenance": provenance,
            "preservation": preservation,
            "latency_s": latency,
            "surface": prepared.surface,
            # 3D-RC-011 / DFM-013: the context it was made with and its DFM status.
            **_manufacturing_fields(prepared),
        }
        if status == "stale":
            metadata["stale_reason"] = (
                f"the accepted concept moved to {head} while this ran from {prepared.base_revision}"
            )
        now = datetime.now(UTC)
        name = f"Generated concept {generation_id[:6]}" + (
            f" ({index + 1}/{request.n_variants})" if request.n_variants > 1 else ""
        )
        created = await self._twin.create_work_product(
            WorkProduct(
                id=wp_id,
                name=name,
                type=WorkProductType.GENERATED_SKETCH,
                domain="concept",
                file_path="",
                content_hash=content_hash,
                format="png",
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by=TOOL,
                project_id=UUID(prepared.project_id),
            )
        )
        node_id = str(getattr(created, "id", wp_id))
        sources = [r.work_product_id for r in prepared.references if r.image_sent]
        if prepared.sketch:
            sources.append(str(prepared.sketch["node_id"]))
        if prepared.baseline and first_kind == "baseline":
            sources.append(str(prepared.baseline["node_id"]))
        for source in dict.fromkeys(sources):
            try:
                await self._twin.add_edge(created.id, UUID(source), EdgeType.GENERATED_FROM)
            except Exception as exc:  # noqa: BLE001 - the record names its sources anyway
                logger.info("sketch_generation_edge_failed", source=source, error=str(exc))
        if self._project_backend is not None:
            try:
                await self._project_backend.link_work_product(
                    prepared.project_id, node_id, name, ITEM_TYPE
                )
            except Exception as exc:  # noqa: BLE001 - the node exists; say the link failed
                logger.warning("sketch_generation_project_link_failed", error=str(exc))
        logger.info(
            "sketch_generation_stored",
            project_id=prepared.project_id,
            node_id=node_id,
            status=status,
            mode=prepared.mode,
            provider=image.provider,
            model=image.model,
            review_required=review_required,
            latency_s=latency,
        )
        return summarise(created)

    # -- decisions --------------------------------------------------------

    async def _proposal(self, project_id: str, node_id: str) -> Any:
        wp = await self._wp(node_id)
        if self._type(wp) != ITEM_TYPE or not self._same_project(wp, project_id):
            raise SketchGenerationError(
                f"{node_id} is not a generated concept of this project", 404
            )
        if (wp.metadata or {}).get("item_key"):
            raise SketchGenerationError(f"{node_id} is an accepted revision, not a proposal", 409)
        return wp

    async def _set(self, wp: Any, changes: dict[str, Any]) -> Any:
        meta = {**dict(wp.metadata or {}), **changes}
        return await self._twin.update_work_product(
            wp.id, {"metadata": meta, "updated_at": datetime.now(UTC)}
        )

    async def accept(
        self, project_id: str, node_id: str, *, expected_baseline: str | None, actor: str
    ) -> dict[str, Any]:
        """Make a proposal the accepted concept: the next revision of its line (VIS-002).

        Refused (409) for a stale, rejected or already accepted proposal, and
        when another concept was accepted since the person last looked
        (``expected_baseline``). The previous accepted concept stays as the
        line's earlier revision.
        """
        from api_gateway.twin.item_revisions import (
            finish_definition_revision,
            plan_definition_revision,
        )
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        with tracer.start_as_current_span("sketch_generation.accept") as span:
            span.set_attribute("project.id", project_id)
            span.set_attribute("node.id", node_id)
            wp = await self._proposal(project_id, node_id)
            meta = dict(wp.metadata or {})
            prov = dict(meta.get("provenance") or {})
            status = meta.get("generation_status")
            if status != "proposed":
                raise SketchGenerationError(
                    f"this concept is {status}; only a current proposal can be accepted"
                    + (
                        " (it arrived after a newer concept was accepted)"
                        if status == "stale"
                        else ""
                    ),
                    409,
                )
            source = str(prov.get("baseline_source") or "CONCEPT")
            line_key, _item = await self._line_for(project_id, source, prov.get("baseline_line"))
            head = await self.baseline_ref(project_id, line_key)
            if expected_baseline is not None and expected_baseline != head:
                raise SketchGenerationError(
                    f"the accepted concept is now {head}, not {expected_baseline}; compare again",
                    409,
                )
            base = str(prov.get("base_revision") or "none")
            if head != base and not await self._head_from_generation(
                head, line_key, project_id, meta
            ):
                await self._set(
                    wp,
                    {
                        "generation_status": "stale",
                        "stale_reason": f"built on {base}, the accepted concept is now {head}",
                    },
                )
                _count(str(prov.get("mode") or "text"), "stale")
                raise SketchGenerationError(
                    f"this concept was built on {base}, but {head} was accepted since; it is kept "
                    "as history and cannot replace the newer one",
                    409,
                )
            name = "Concept" if source == "CONCEPT" else f"Concept {source}"
            plan = await plan_definition_revision(
                self._twin,
                item_type=ITEM_TYPE,
                name=name,
                project_id=project_id,
                default_author="human",
                item_key=line_key,
                change_reason="generated concept accepted",
            )
            if plan is None:
                raise SketchGenerationError("this twin cannot keep accepted concepts", 503)
            new_id = uuid4()
            accepted_meta = {
                k: v
                for k, v in meta.items()
                if k not in {"generation_status", "review_required", "accepted_as", "stale_reason"}
            }
            accepted_meta.update(
                {
                    "generation_status": "accepted",
                    "accepted_from": str(wp.id),
                    "accepted_by": actor,
                    "accepted_at": datetime.now(UTC).isoformat(),
                    "baseline_for": source,
                    "previous_baseline": head if head != "none" else None,
                }
            )
            accepted_meta.update(plan.stamp())
            now = datetime.now(UTC)
            created = await self._twin.create_work_product(
                WorkProduct(
                    id=new_id,
                    name=name,
                    type=WorkProductType.GENERATED_SKETCH,
                    domain="concept",
                    file_path="",
                    content_hash=wp.content_hash,
                    format="png",
                    metadata=accepted_meta,
                    created_at=now,
                    updated_at=now,
                    created_by="human",
                    project_id=UUID(project_id),
                )
            )
            result: dict[str, Any] = {}
            await finish_definition_revision(
                self._twin, plan, getattr(created, "id", new_id), name=name, result=result
            )
            item_ref = result.get("item_ref") or f"{plan.key}@{plan.revision}"
            try:
                await self._twin.add_edge(created.id, wp.id, EdgeType.GENERATED_FROM)
            except Exception as exc:  # noqa: BLE001
                logger.info("sketch_generation_edge_failed", error=str(exc))
            prov_target = {**prov, "target_revision": item_ref}
            await self._set(
                wp,
                {
                    "generation_status": "accepted",
                    "accepted_as": item_ref,
                    "accepted_by": actor,
                    "provenance": prov_target,
                },
            )
            await self._set(
                await self._wp(str(getattr(created, "id", new_id))),
                {"provenance": prov_target},
            )
            _count(str(prov.get("mode") or "text"), "accepted")
            logger.info(
                "sketch_generation_accepted",
                project_id=project_id,
                proposal=node_id,
                item_ref=item_ref,
                previous=head,
            )
            return {
                "accepted": item_ref,
                "node_id": str(getattr(created, "id", new_id)),
                "proposal_id": node_id,
                "previous_baseline": head if head != "none" else None,
                "authority": AUTHORITY,
                "label": LABEL,
            }

    async def _head_from_generation(
        self, head: str, line_key: str | None, project_id: str, meta: dict[str, Any]
    ) -> bool:
        """Whether the head is a sibling variant of the same generation (a switch, not a race)."""
        if head == "none" or line_key is None:
            return False
        from twin_core.items import find_item

        item = await find_item(self._twin, line_key, project_id)
        if item is None or item.head_node_id is None:
            return False
        head_wp = await self._twin.get_work_product(item.head_node_id)
        head_meta = (head_wp.metadata or {}) if head_wp is not None else {}
        same = head_meta.get("generation_id") == meta.get("generation_id")
        prior = str(head_meta.get("previous_baseline") or "none")
        base = str((meta.get("provenance") or {}).get("base_revision") or "none")
        return bool(same and prior == base)

    async def reject(self, project_id: str, node_id: str, *, actor: str) -> dict[str, Any]:
        wp = await self._proposal(project_id, node_id)
        meta = wp.metadata or {}
        if meta.get("generation_status") == "accepted":
            raise SketchGenerationError("an accepted concept is replaced, not rejected", 409)
        await self._set(
            wp,
            {
                "generation_status": "rejected",
                "rejected_by": actor,
                "rejected_at": datetime.now(UTC).isoformat(),
            },
        )
        _count(str((meta.get("provenance") or {}).get("mode") or "text"), "rejected")
        logger.info("sketch_generation_rejected", project_id=project_id, node_id=node_id)
        return {"node_id": node_id, "generation_status": "rejected"}

    async def branch(
        self, project_id: str, node_id: str, *, actor: str, name: str | None
    ) -> dict[str, Any]:
        """A new accepted line seeded with this concept; the current one is untouched."""
        from api_gateway.twin.item_revisions import (
            finish_definition_revision,
            plan_definition_revision,
        )
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        wp = await self._wp(node_id)
        if self._type(wp) != ITEM_TYPE or not self._same_project(wp, project_id):
            raise SketchGenerationError(
                f"{node_id} is not a generated concept of this project", 404
            )
        meta = dict(wp.metadata or {})
        if meta.get("generation_status") == "stale":
            raise SketchGenerationError(
                "a stale concept is history; branch from a current one", 409
            )
        prov = dict(meta.get("provenance") or {})
        source = str(prov.get("baseline_source") or meta.get("baseline_for") or "CONCEPT").split(
            "#"
        )[0]
        branch_id = uuid4().hex[:6]
        label = (name or f"Concept {source} branch {branch_id}").strip()[:120]
        plan = await plan_definition_revision(
            self._twin,
            item_type=ITEM_TYPE,
            name=label,
            project_id=project_id,
            default_author="human",
            change_reason="branched from a generated concept",
        )
        if plan is None:
            raise SketchGenerationError("this twin cannot keep accepted concepts", 503)
        keep = {
            k: v
            for k, v in meta.items()
            if not k.startswith("item_") and k not in {"generation_status", "accepted_as"}
        }
        keep.update(
            {
                "generation_status": "accepted",
                "accepted_from": str(wp.id),
                "accepted_by": actor,
                "baseline_for": f"{source}#branch-{branch_id}",
                "branched_from": str(wp.id),
                "previous_baseline": None,
            }
        )
        keep.update(plan.stamp())
        now = datetime.now(UTC)
        new_id = uuid4()
        created = await self._twin.create_work_product(
            WorkProduct(
                id=new_id,
                name=label,
                type=WorkProductType.GENERATED_SKETCH,
                domain="concept",
                file_path="",
                content_hash=wp.content_hash,
                format="png",
                metadata=keep,
                created_at=now,
                updated_at=now,
                created_by="human",
                project_id=UUID(project_id),
            )
        )
        result: dict[str, Any] = {}
        await finish_definition_revision(
            self._twin, plan, getattr(created, "id", new_id), name=label, result=result
        )
        try:
            await self._twin.add_edge(created.id, wp.id, EdgeType.GENERATED_FROM)
        except Exception as exc:  # noqa: BLE001
            logger.info("sketch_generation_edge_failed", error=str(exc))
        logger.info(
            "sketch_generation_branched",
            project_id=project_id,
            node_id=node_id,
            item_ref=result.get("item_ref"),
        )
        return {
            "branch_key": result.get("item_key") or plan.key,
            "item_ref": result.get("item_ref"),
            "node_id": str(getattr(created, "id", new_id)),
            "baseline_for": keep["baseline_for"],
        }

    async def compare(self, project_id: str, a: str, b: str) -> dict[str, Any]:
        """Two concepts side by side: their records and how many pixels differ (VIS-002)."""
        first = await self._wp(a)
        second = await self._wp(b)
        for wp in (first, second):
            if self._type(wp) != ITEM_TYPE or not self._same_project(wp, project_id):
                raise SketchGenerationError(
                    f"{wp.id} is not a generated concept of this project", 404
                )
        difference: float | None = None
        try:
            da, _ = await asyncio.to_thread(self._read_blob, first)
            db, _ = await asyncio.to_thread(self._read_blob, second)
            difference = pixel_difference(da, db)
        except Exception as exc:  # noqa: BLE001 - the records still compare
            logger.info("sketch_generation_compare_pixels_unavailable", error=str(exc))
        pa = (first.metadata or {}).get("provenance") or {}
        pb = (second.metadata or {}).get("provenance") or {}
        changed = sorted(
            k
            for k in ("prompt", "style", "mode", "mask", "references", "source_sketch", "model")
            if pa.get(k) != pb.get(k)
        )
        return {
            "a": summarise(first),
            "b": summarise(second),
            "pixel_difference": difference,
            "changed": changed,
            "authority": AUTHORITY,
        }

    async def overview(self, project_id: str, *, limit: int = 30) -> dict[str, Any]:
        """Accepted lines with their history, and recent proposals, newest first."""
        nodes = await self._generated(project_id)
        lines: dict[str, dict[str, Any]] = {}
        proposals: list[Any] = []
        for wp in nodes:
            meta = wp.metadata or {}
            key = meta.get("item_key")
            if key:
                line = lines.setdefault(
                    str(key),
                    {"key": str(key), "baseline_for": meta.get("baseline_for"), "history": []},
                )
                line["history"].append(summarise(wp))
            else:
                proposals.append(wp)
        out_lines: list[dict[str, Any]] = []
        for key, line in lines.items():
            line["history"].sort(key=lambda s: s.get("revision") or 0, reverse=True)
            line["head_ref"] = await self.baseline_ref(project_id, key)
            head = next((h for h in line["history"] if h.get("item_ref") == line["head_ref"]), None)
            line["head"] = head
            out_lines.append(line)
        proposals.sort(key=lambda w: w.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        return {
            "lines": sorted(out_lines, key=lambda line: str(line.get("baseline_for") or "")),
            "proposals": [summarise(w) for w in proposals[:limit]],
            "authority": AUTHORITY,
            "label": LABEL,
        }


def summarise(wp: Any) -> dict[str, Any]:
    """The JSON shape of one generated concept, for routes and tool replies."""
    node_id = str(wp.id)
    meta = wp.metadata or {}
    name = wp.name
    created_at = wp.created_at.isoformat() if getattr(wp, "created_at", None) else None
    prov = meta.get("provenance") or {}
    key = meta.get("item_key")
    rev = meta.get("item_revision")
    return {
        "node_id": node_id,
        "name": name,
        "generation_status": meta.get("generation_status"),
        "review_required": bool(meta.get("review_required")),
        "generation_id": meta.get("generation_id"),
        "variant_index": meta.get("variant_index"),
        "item_key": key,
        "revision": rev,
        "item_ref": f"{key}@{rev}" if key and rev else None,
        "accepted_as": meta.get("accepted_as"),
        "accepted_from": meta.get("accepted_from"),
        "baseline_for": meta.get("baseline_for"),
        "mode": prov.get("mode"),
        "style": prov.get("style"),
        "intent": prov.get("intent"),
        "provider": prov.get("provider"),
        "model": prov.get("model"),
        "cost_usd": prov.get("cost_usd"),
        "base_revision": prov.get("base_revision"),
        "target_key": prov.get("target_key"),
        "target_revision": prov.get("target_revision"),
        "source_sketch": prov.get("source_sketch"),
        "references": prov.get("references") or [],
        "mask": prov.get("mask"),
        "preservation": meta.get("preservation"),
        "latency_s": meta.get("latency_s"),
        "size": prov.get("size"),
        "frame": prov.get("frame"),
        "stale_reason": meta.get("stale_reason"),
        "content_hash": meta.get("content_sha256"),
        "created_at": created_at,
        "authority": meta.get("authority") or AUTHORITY,
        "label": meta.get("illustrative_label") or LABEL,
        "editable": False,
        "manufacturing_context_ref": meta.get("manufacturing_context_ref"),
        "manufacturing_context_unresolved": meta.get("manufacturing_context_unresolved") or [],
        "speculative": bool(meta.get("speculative")),
        "manufacturing_note": meta.get("manufacturing_note") or NOT_ASSESSED_NOTE,
        "dfm_status": meta.get("dfm_status") or "not_assessed",
    }


def _manufacturing_fields(prepared: Prepared) -> dict[str, Any]:
    """The manufacturing context fields an output records (none before it was prepared)."""
    block = prepared.manufacturing
    if block is None:
        return {}
    out: dict[str, Any] = block.provenance()
    return out


def _png(img: Any) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _sniff_image(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


def _default_blob_writer(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
    from digital_twin.storage.work_product_blobs import store_work_product_blob

    key: str = store_work_product_blob(node_id, filename, content, content_type=content_type)
    return key


def _default_blob_reader(wp: Any) -> tuple[bytes, str]:
    from fastapi import HTTPException

    from api_gateway.twin.blob_store import resolve_work_product_blob

    try:
        return resolve_work_product_blob(wp)
    except HTTPException as exc:
        raise SketchGenerationError(f"the image could not be read: {exc.detail}", 502) from exc


async def _create_reference_records(project_id: str) -> list[dict[str, Any]]:
    """The Create brief's reference records for ``project_id``."""
    from api_gateway.create.routes import get_create_store

    creation = get_create_store().get(project_id)
    return [r.model_dump(mode="json") for r in creation.brief.reference_details]


async def _record_disclosures(
    project_id: str,
    sent: list[tuple[str, str]],
    *,
    provider: str,
    model: str,
    operation_id: str,
) -> None:
    """Note on each reference record which provider it was sent to (REF-007)."""
    from api_gateway.create.models import ReferenceDisclosure, ReferenceRecord
    from api_gateway.create.routes import get_create_store
    from api_gateway.create.store import CreationConflictError

    store = get_create_store()
    for _attempt in range(3):
        creation = store.get(project_id)
        draft = creation.model_copy(deep=True)
        for ref_id, source_hash in sent:
            record = draft.brief.reference(ref_id)
            if record is None:
                record = ReferenceRecord(work_product_id=ref_id, source_hash=source_hash)
                draft.brief.reference_details.append(record)
                if ref_id not in draft.brief.references:
                    draft.brief.references.append(ref_id)
            record.disclosures.append(
                ReferenceDisclosure(
                    provider=provider,
                    model=model,
                    purpose="concept sketch generation",
                    source_hash=source_hash,
                    operation_id=operation_id,
                )
            )
            del record.disclosures[:-50]
        try:
            store.save(draft, expected_version=creation.version)
            return
        except CreationConflictError:
            continue


def make_sketch_generator(twin: Any, project_backend: Any = None, **kwargs: Any) -> Any:
    """``twin.generate_sketch``'s collaborator: prepare and start (or run) one request.

    An agent's call has already been through the approval hold and its spend
    cap, so the gateway's own hourly confirmation does not apply here.
    """
    service = SketchGenerationService(twin, project_backend, **kwargs)

    async def generate(
        *, project_id: str, request: dict[str, Any], wait: bool, session_id: str | None = None
    ) -> dict[str, Any]:
        try:
            parsed = GenerateSketchRequest.model_validate({**request, "confirm_spend": True})
        except ValueError as exc:
            raise SketchGenerationError(f"{TOOL}: {exc}") from exc
        prepared = await service.prepare(project_id, parsed, surface="agent")
        return await service.start(prepared, wait=wait, session_id=session_id)

    return generate
