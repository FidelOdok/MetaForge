"""A vision model checks a concept image against the selected assembly.

Image models drift. Asked to draw a concept from its block layout, they add
parts, drop parts, and draw joints the concept does not have. After
``twin.render_concept_image`` makes a picture, a vision-language model reads
it and compares it with the concept's selected assembly (the ``assembly`` on
the concept ``design_decision``, see ``parse_concept_assembly``):

* is every *made* part visible;
* do the connections and joints look as the decision says;
* is anything major drawn that the concept does not have.

The model answers in strict JSON (:class:`ImageCritique`). The result is
advisory, about an illustrative picture: it is stored on the render under
``metadata.critique`` with the model that judged, shown at the concept gate as
a note, and never makes the image evidence. The render keeps
``metadata.authority = "illustrative"``, so ``twin.record_claim`` still refuses
it.

It is best-effort. No vision provider, a model that does not answer, or a
reply that does not fit the schema stores ``{"verdict": "unchecked",
"reason": ...}``; it never fails the image call.

The model call is the vision path sketch reading already uses
(``sketch_reading.make_default_vision_call``, the gateway's provider chain
with OpenAI-style ``image_url`` parts), injected so tests use a fake.
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from api_gateway.twin.sketch_reading import (
    MAX_IMAGE_BYTES,
    VisionCall,
    VisionReply,
    normalise_mime_type,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.image_critique")

VERDICTS: tuple[str, ...] = ("consistent", "inconsistent", "unclear")
UNCHECKED = "unchecked"
ADVISORY_NOTE = "advisory, about an illustrative image; never evidence"
CRITIQUE_TOOL = "twin.critique_concept_image"


class ImageCritiqueError(ValueError):
    """The model's reply was not a critique in the schema."""


# ── The schema the model answers in ───────────────────────────────────────


class PartSeen(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    present: bool
    note: str = ""


class JointSeen(BaseModel):
    model_config = ConfigDict(extra="ignore")

    between: list[str] = Field(min_length=2, max_length=2)
    matches: bool
    note: str = ""


class ImageCritique(BaseModel):
    """What the vision model says about one image against one assembly."""

    model_config = ConfigDict(extra="ignore")

    parts_seen: list[PartSeen] = Field(default_factory=list)
    joints: list[JointSeen] = Field(default_factory=list)
    extras: list[str] = Field(default_factory=list)
    verdict: Literal["consistent", "inconsistent", "unclear"]
    summary: str = Field(min_length=1)

    @field_validator("extras", mode="before")
    @classmethod
    def _extras_as_text(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return value


_PROMPT = """You are checking an AI-generated concept image of a product against the
assembly the design team selected. Image models drift: they add parts, drop
parts and draw joints the design does not have. Judge only what the image shows.

Concept: {concept}

The selected assembly. Parts (source: made = the team makes it, bought or
standard = an off-the-shelf part):
{parts}
Connections and their joints:
{connections}

Reply with ONLY one JSON object, no prose and no code fences, in this schema:
{{"parts_seen": [{{"name": "<a part name from the list>", "present": true,
                  "note": "<where or how it appears, or why it is not visible>"}}],
  "joints": [{{"between": ["<part>", "<part>"], "matches": true,
              "note": "<what the image shows at that joint>"}}],
  "extras": ["<a major part or structure drawn that the assembly does not have>"],
  "verdict": "consistent" | "inconsistent" | "unclear",
  "summary": "<one sentence>"}}

Rules:
- List every part above in "parts_seen", using its exact name. A made part is
  present only if you can see it.
- List every connection above in "joints", naming its two parts exactly.
  "matches" is false when the image shows the parts joined differently, or not
  joined.
- "extras" lists only major additions (a missing-from-the-design leg, arm,
  shelf, wheel or panel), not styling, background or texture.
- "verdict" is "inconsistent" when a made part is missing, a joint does not
  match, or there is a major extra; "unclear" when the image does not let you
  judge; otherwise "consistent".
"""


def _assembly_lines(assembly: Any) -> tuple[str, str]:
    parts = "\n".join(
        f"- {p.name} ({p.source}"
        + (f", {p.material}" if p.material else "")
        + (f", x{p.quantity}" if p.quantity > 1 else "")
        + ")"
        for p in assembly.parts
    )
    connections = (
        "\n".join(
            f"- {c.parts[0]} to {c.parts[1]}: {c.joint}"
            + (f" ({c.fasteners})" if c.fasteners else "")
            for c in assembly.connections
        )
        or "- (none recorded)"
    )
    return parts, connections


def build_prompt(assembly: Any, concept_name: str = "") -> str:
    """The instruction sent with the image."""
    parts, connections = _assembly_lines(assembly)
    return _PROMPT.format(
        concept=concept_name.strip() or "(unnamed)", parts=parts, connections=connections
    )


def _json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ImageCritiqueError("the model's reply contained no JSON object")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ImageCritiqueError(f"the model's reply was not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ImageCritiqueError("the model's reply was not a JSON object")
    return parsed


def parse_critique(text: str) -> ImageCritique:
    """Validate a model reply into an :class:`ImageCritique`, or raise cleanly."""
    if not isinstance(text, str) or not text.strip():
        raise ImageCritiqueError("the model returned an empty reply")
    raw = _json_object(text)
    try:
        return ImageCritique.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ())) or "reply"
        raise ImageCritiqueError(
            f"the model's critique did not match the schema ({where}: {first.get('msg')})"
        ) from exc


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()


def issues_of(critique: ImageCritique, assembly: Any) -> list[str]:
    """The findings that make an image inconsistent, as short lines.

    A made part the model did not mention at all is "not assessed": silence
    is not a pass.
    """
    seen = {_key(p.name): p for p in critique.parts_seen}
    issues: list[str] = []
    for part in assembly.parts:
        if part.source != "made":
            continue
        found = seen.get(_key(part.name))
        if found is None:
            issues.append(f"{part.name} not assessed")
        elif not found.present:
            issues.append(f"{part.name} not visible")
    for joint in critique.joints:
        if not joint.matches:
            detail = f" ({joint.note.strip()})" if joint.note.strip() else ""
            issues.append(f"{joint.between[0]} to {joint.between[1]} joint differs{detail}")
    issues += [f"extra: {extra}" for extra in critique.extras]
    return issues


def settled_verdict(critique: ImageCritique, issues: list[str]) -> str:
    """The model's verdict, held to its own findings.

    "consistent" with a missing made part, a mismatched joint or a major extra
    is not consistent; with only unassessed parts it is unclear.
    """
    if critique.verdict != "consistent" or not issues:
        return critique.verdict
    if all(i.endswith(" not assessed") for i in issues):
        return "unclear"
    return "inconsistent"


# ── Metrics ───────────────────────────────────────────────────────────────

_metrics: Any = None


def _record(verdict: str) -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_concept_image_critique(verdict)
    except Exception:  # noqa: BLE001 - a metric never fails a critique
        return


# ── Records ───────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat()


def unchecked(reason: str, *, reply: VisionReply | None = None, **extra: Any) -> dict[str, Any]:
    """The record stored when no critique could be made, saying why."""
    record: dict[str, Any] = {
        "verdict": UNCHECKED,
        "reason": reason,
        "authority": "advisory",
        "advisory_note": ADVISORY_NOTE,
        "checked_at": _now(),
        **extra,
    }
    if reply is not None and (reply.provider or reply.model):
        record["judged_by"] = {"provider": reply.provider, "model": reply.model}
    return record


@dataclass(frozen=True)
class KnownAssembly:
    """The assembly to compare against, and where it came from."""

    assembly: Any
    source: dict[str, Any]
    options: tuple[Any, ...] | None = field(default=None)


def _wp_type(wp: Any) -> str:
    return str(getattr(getattr(wp, "type", None), "value", getattr(wp, "type", None)))


def _epoch(wp: Any) -> float:
    for attr in ("updated_at", "created_at"):
        value = getattr(wp, attr, None)
        if isinstance(value, datetime):
            return value.timestamp()
    return 0.0


def _from_decision(wp: Any) -> KnownAssembly:
    from orchestrator.design_flow.context import parse_concept_assembly, parse_concept_options

    metadata = dict(getattr(wp, "metadata", None) or {})
    assembly = parse_concept_assembly(metadata.get("assembly"))
    options = None
    if metadata.get("concept_options") is not None:
        try:
            options = parse_concept_options(metadata["concept_options"])
        except ValueError:
            options = None
    return KnownAssembly(
        assembly=assembly,
        source={
            "kind": "decision",
            "decision_id": str(getattr(wp, "id", "")),
            "decision_name": str(getattr(wp, "name", "") or ""),
        },
        options=options,
    )


async def _decisions(twin: Any, project_id: str | None) -> list[Any]:
    from twin_core.models.enums import WorkProductType

    if not project_id:
        return []
    found = await twin.list_work_products(
        work_product_type=WorkProductType.DESIGN_DECISION, project_id=UUID(str(project_id))
    )
    return sorted(found, key=_epoch)


async def resolve_assembly(
    twin: Any,
    *,
    project_id: str | None,
    decision_ref: str | None = None,
    assembly: dict[str, Any] | None = None,
) -> KnownAssembly | None:
    """The assembly an image is checked against, or ``None`` when none is known.

    An explicit ``assembly`` or ``decision_ref`` that cannot be used is a
    ``ValueError``: the caller asked for something specific. With neither, the
    project's latest concept decision carrying an ``assembly`` is used, and a
    project with none gives ``None``.
    """
    from orchestrator.design_flow.context import parse_concept_assembly

    if assembly is not None:
        try:
            parsed = parse_concept_assembly(assembly)
        except ValueError as exc:
            raise ValueError(f"concept image check: bad assembly: {exc}") from exc
        return KnownAssembly(assembly=parsed, source={"kind": "inline"})
    if decision_ref is not None:
        ref = decision_ref.strip() if isinstance(decision_ref, str) else ""
        if not ref:
            raise ValueError("concept image check: 'decision_ref' must be a non-empty string")
        wp: Any = None
        try:
            wp = await twin.get_work_product(UUID(ref))
        except ValueError:
            named = [d for d in await _decisions(twin, project_id) if d.name == ref]
            if len(named) > 1:
                raise ValueError(
                    f"concept image check: decision {ref!r} is ambiguous; give its id"
                ) from None
            wp = named[0] if named else None
        if wp is None or _wp_type(wp) != "design_decision":
            raise ValueError(f"concept image check: {ref!r} is not a design decision")
        try:
            return _from_decision(wp)
        except ValueError as exc:
            raise ValueError(
                f"concept image check: decision {ref!r} carries no usable assembly: {exc}"
            ) from exc
    for wp in reversed(await _decisions(twin, project_id)):
        if (getattr(wp, "metadata", None) or {}).get("assembly") is None:
            continue
        try:
            return _from_decision(wp)
        except ValueError:
            continue  # a malformed record is not a decision
    return None


def _option_for(concept_name: str, options: tuple[Any, ...] | None) -> Any:
    """The concept option an image's concept name refers to, or ``None``."""
    key = _key(concept_name)
    if not key or not options:
        return None
    for option in options:
        okey = _key(option.name)
        if okey and (okey == key or key.startswith(okey + " ") or okey in key.split(" ")):
            return option
    for option in options:
        okey = _key(option.name)
        if okey and (okey in key or key in okey):
            return option
    return None


# ── Checking one image ────────────────────────────────────────────────────


def _fit_for_vision(image: bytes, mime: str) -> tuple[bytes, str]:
    """The image in a type and size every vision provider takes.

    An image over the providers' smallest limit is re-encoded as a JPEG at a
    smaller size when Pillow is present; otherwise it is refused.
    """
    mime = normalise_mime_type(mime)
    if len(image) <= MAX_IMAGE_BYTES:
        return image, mime
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError(f"the image is {len(image)} bytes, over {MAX_IMAGE_BYTES}") from exc
    img = Image.open(io.BytesIO(image)).convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


async def critique_image(
    image: bytes,
    mime_type: str,
    assembly: Any,
    *,
    vision_call: VisionCall,
    concept_name: str = "",
) -> tuple[ImageCritique, VisionReply, list[str]]:
    """Ask the vision model to check one image; raises on a bad reply.

    Returns the critique (its verdict held to its findings), who answered, and
    the issues found. ``ImageCritiqueError`` for a reply that is not a
    critique, ``ValueError`` for an image no vision model can take; whatever
    the model call raises propagates.
    """
    if not image:
        raise ValueError("the image is empty")
    data, mime = _fit_for_vision(image, mime_type)
    raw = await vision_call(data, mime, build_prompt(assembly, concept_name))
    reply = raw if isinstance(raw, VisionReply) else VisionReply(text=str(raw or ""))
    try:
        critique = parse_critique(reply.text)
    except ImageCritiqueError as exc:
        raise _BadReply(str(exc), reply) from exc
    issues = issues_of(critique, assembly)
    verdict = settled_verdict(critique, issues)
    if verdict != critique.verdict:
        critique = critique.model_copy(update={"verdict": verdict})
    return critique, reply, issues


class _BadReply(ImageCritiqueError):
    def __init__(self, message: str, reply: VisionReply) -> None:
        super().__init__(message)
        self.reply = reply


async def check_image(
    image: bytes,
    mime_type: str,
    known: KnownAssembly | None,
    *,
    vision_call: VisionCall | None,
    concept_name: str = "",
) -> dict[str, Any]:
    """The critique record for one image. Never raises.

    ``known`` is what :func:`resolve_assembly` found. An image of a concept the
    decision did not select is not compared with the selected one's assembly:
    it would be inconsistent by construction.
    """
    with tracer.start_as_current_span("twin.critique_concept_image") as span:
        span.set_attribute("concept_image_critique.concept", concept_name)
        if known is None:
            record = unchecked(
                "no concept assembly on record to compare against (record the concept "
                f"decision with its assembly, then run {CRITIQUE_TOOL})"
            )
            return _done(record, span, concept_name)
        source = dict(known.source)
        option = _option_for(concept_name, known.options)
        if option is not None and not option.selected and source.get("kind") == "decision":
            chosen = next((o.name for o in known.options or () if o.selected), "another concept")
            record = unchecked(
                f"the decision's assembly is for the selected concept {chosen}, not "
                f"{option.name}; give that concept's assembly to check this image",
                assembly_source=source,
            )
            return _done(record, span, concept_name)
        if vision_call is None:
            from api_gateway.twin.sketch_reading import make_default_vision_call

            vision_call = make_default_vision_call()
        try:
            critique, reply, issues = await critique_image(
                image, mime_type, known.assembly, vision_call=vision_call, concept_name=concept_name
            )
        except _BadReply as exc:
            span.record_exception(exc)
            logger.warning(
                "concept_image_critique_refused_output",
                error=str(exc),
                provider=exc.reply.provider,
                model=exc.reply.model,
            )
            record = unchecked(str(exc), reply=exc.reply, assembly_source=source)
            return _done(record, span, concept_name)
        except Exception as exc:  # noqa: BLE001 - best-effort: never fails the image
            span.record_exception(exc)
            logger.warning("concept_image_critique_unavailable", error=str(exc))
            record = unchecked(f"no vision model checked the image: {exc}", assembly_source=source)
            return _done(record, span, concept_name)
        record = {
            **critique.model_dump(),
            "issues": issues,
            "judged_by": {"provider": reply.provider, "model": reply.model},
            "assembly_source": source,
            "authority": "advisory",
            "advisory_note": ADVISORY_NOTE,
            "checked_at": _now(),
        }
        return _done(record, span, concept_name)


def _done(record: dict[str, Any], span: Any, concept_name: str) -> dict[str, Any]:
    verdict = str(record.get("verdict"))
    span.set_attribute("concept_image_critique.verdict", verdict)
    _record(verdict)
    logger.info(
        "concept_image_critiqued",
        concept=concept_name,
        verdict=verdict,
        issues=len(record.get("issues") or []),
        reason=record.get("reason"),
        model=(record.get("judged_by") or {}).get("model"),
    )
    return record


# ── Checking an image already in the twin ─────────────────────────────────

_SVG_IMAGE = re.compile(r'href="data:(image/[a-z0-9.+-]+);base64,([^"]+)"', re.IGNORECASE)


def stored_image_bytes(content: bytes, fmt: str) -> tuple[bytes, str]:
    """A stored concept image as ``(bytes, mime)``; an SVG wrapper is unwrapped."""
    if fmt == "svg" or content[:5] in (b"<svg ", b"<?xml"):
        match = _SVG_IMAGE.search(content.decode("utf-8", errors="replace"))
        if match is None:
            raise ValueError("the stored SVG carries no embedded image")
        return base64.b64decode(match.group(2)), match.group(1).lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
    return content, mime.get(fmt, "image/png")


def concept_name_of(wp: Any) -> str:
    metadata = getattr(wp, "metadata", None) or {}
    name = str(metadata.get("concept_name") or "")
    if name:
        return name
    wp_name = str(getattr(wp, "name", "") or "")
    return wp_name.removeprefix("Concept image: ")


def make_concept_image_critic(
    twin: Any,
    project_backend: Any = None,
    *,
    vision_call: VisionCall | None = None,
    blob_reader: Any = None,
) -> Any:
    """Return an async ``critique(node_id, *, decision_ref=None, assembly=None)``.

    Checks a concept image already in the twin and stores the result on it,
    in a new revision of its metadata. ``blob_reader(wp) -> (bytes, filename)``
    is injectable for tests and defaults to the twin's blob store.
    """
    del project_backend  # decisions are found through the twin; kept for wiring symmetry

    def _default_reader(wp: Any) -> tuple[bytes, str]:
        from fastapi import HTTPException

        from api_gateway.twin.blob_store import resolve_work_product_blob

        try:
            return resolve_work_product_blob(wp)
        except HTTPException as exc:
            raise ValueError(f"the image could not be read: {exc.detail}") from exc

    read_blob = blob_reader or _default_reader

    async def critique(
        node_id: str,
        *,
        decision_ref: str | None = None,
        assembly: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from api_gateway.twin.sketch_reading import _update_metadata

        tool = CRITIQUE_TOOL
        try:
            wp = await twin.get_work_product(UUID(str(node_id)))
        except ValueError as exc:
            raise ValueError(f"{tool}: 'node_id' is not a node id: {node_id!r}") from exc
        metadata = dict(getattr(wp, "metadata", None) or {}) if wp is not None else {}
        if (
            wp is None
            or _wp_type(wp) != "render"
            or metadata.get("generated_by") != "twin.render_concept_image"
        ):
            raise ValueError(
                f"{tool}: {node_id} is not a concept image (twin.render_concept_image)"
            )
        project_id = str(wp.project_id) if getattr(wp, "project_id", None) else None
        known = await resolve_assembly(
            twin, project_id=project_id, decision_ref=decision_ref, assembly=assembly
        )
        concept = concept_name_of(wp)
        try:
            content, _filename = read_blob(wp)
            image, mime = stored_image_bytes(content, str(getattr(wp, "format", "") or ""))
        except Exception as exc:  # noqa: BLE001 - say why, store nothing wrong
            record = unchecked(f"the stored image could not be read: {exc}")
            _record(UNCHECKED)
        else:
            record = await check_image(
                image, mime, known, vision_call=vision_call, concept_name=concept
            )
        await _update_metadata(
            twin, wp, {"critique": record}, f"concept image checked: {record['verdict']}"
        )
        return {"node_id": str(wp.id), "concept_name": concept, "critique": record}

    return critique
