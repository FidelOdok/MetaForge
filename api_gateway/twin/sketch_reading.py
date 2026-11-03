"""Read a hand sketch or reference image into proposed concept intent.

A person uploads a sketch of a product idea. A vision-language model reads
it and proposes what it sees: the parts, the connections and joints between
them, rough proportions, and the questions it cannot answer from the picture.

That reading is a proposal, never a record of truth:

* The image is stored as an ``image`` work product with
  ``metadata.authority = "illustrative"``, so ``twin.record_claim`` refuses
  it as the artefact of a claim (see ``claim_recorder.is_illustrative``).
* Every proportion carries ``tag: "assumption"``. A size read off a drawing
  is a guess about intent; whatever tag the model returns is replaced.
* The reading is kept in ``metadata.sketch_reading`` with
  ``status: "proposed"``. Only a person moves it to ``confirmed`` or
  ``rejected`` (``make_sketch_reading_decider``, exposed as the
  human-authority tool ``twin.confirm_sketch_reading``), and only a confirmed
  reading is handed to the concept phase (``confirmed_sketch_readings``).

The model call is injected (``VisionCall``) so tests use a fake. The default
one goes through the harness provider pipeline with the gateway's configured
providers, keeps only those whose adapter passes image content through, and
is bounded by a timeout, so a missing or silent provider is a clear error
rather than a hang.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.sketch_reading")

#: Image types every vision provider here accepts. SVG is not an image to a
#: vision model, so it is refused rather than sent and misread.
SUPPORTED_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_EXTENSION_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}
#: The smallest per-image limit among the providers (Anthropic, 5 MB).
MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: How long one reading may take before it is reported as unavailable.
DEFAULT_TIMEOUT_S = 120.0
TIMEOUT_ENV = "METAFORGE_SKETCH_READING_TIMEOUT_S"

STATUS_PROPOSED = "proposed"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
_DECISIONS = {"confirm": STATUS_CONFIRMED, "reject": STATUS_REJECTED}


class SketchReadingError(ValueError):
    """The model's reading could not be used; the message says why."""


class SketchVisionUnavailableError(RuntimeError):
    """No vision model could be asked, or none answered in time."""


# ── The proposal schema ────────────────────────────────────────────────────


class SketchPart(BaseModel):
    """One part the model sees in the sketch."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    description: str = ""
    count: int = Field(default=1, ge=1)


class SketchConnection(BaseModel):
    """A connection or joint between two named parts."""

    model_config = ConfigDict(extra="ignore")

    from_part: str = Field(min_length=1)
    to_part: str = Field(min_length=1)
    joint_type: str = "unknown"
    note: str = ""


class SketchProportion(BaseModel):
    """A rough size or ratio read off the image. Always an assumption."""

    model_config = ConfigDict(extra="ignore")

    subject: str = Field(min_length=1)
    value: float | None = None
    unit: str = ""
    relative_to: str | None = None
    note: str = ""
    tag: Literal["assumption"] = "assumption"

    @field_validator("tag", mode="before")
    @classmethod
    def _always_assumption(cls, value: Any) -> str:
        # An image cannot verify a size. Whatever the model claimed, the
        # stored tag is "assumption".
        return "assumption"


class SketchReading(BaseModel):
    """The model's whole proposal for one sketch."""

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(min_length=1)
    parts: list[SketchPart] = Field(default_factory=list)
    connections: list[SketchConnection] = Field(default_factory=list)
    proportions: list[SketchProportion] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @field_validator("open_questions", mode="before")
    @classmethod
    def _questions_as_text(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [str(q).strip() for q in value if str(q).strip()]
        return value

    @model_validator(mode="after")
    def _connections_name_known_parts(self) -> SketchReading:
        names = {p.name.strip().lower() for p in self.parts}
        for c in self.connections:
            for end in (c.from_part, c.to_part):
                if end.strip().lower() not in names:
                    raise ValueError(
                        f"connection {c.from_part!r} -> {c.to_part!r} names {end!r}, "
                        "which is not one of the parts"
                    )
        return self


_PROMPT = """You are reading a hand sketch or reference image of a product idea for a
hardware design team. Describe only what the image shows. Do not invent parts.

Reply with ONLY one JSON object, no prose and no code fences, in this schema:
{{"summary": "<one or two sentences: what the product appears to be>",
  "parts": [{{"name": "<short unique name>", "description": "<what it is>", "count": 1}}],
  "connections": [{{"from_part": "<a part name>", "to_part": "<a part name>",
                   "joint_type": "<e.g. screwed, glued, hinged, dowelled, slotted, unknown>",
                   "note": "<what the image shows about it>"}}],
  "proportions": [{{"subject": "<what is measured>", "value": <number or null>,
                   "unit": "<mm, ratio, ...>", "relative_to": "<part or null>",
                   "note": "<how you read it>", "tag": "assumption"}}],
  "open_questions": ["<anything you cannot tell from the image>"]}}

Rules:
- Every connection must name two parts from "parts", using the same names.
- Every proportion is an assumption read from a drawing, never a verified value.
  Use "relative_to" for ratios when the image has no scale.
- Put anything ambiguous (hidden faces, materials, sizes with no scale, how
  parts join) in "open_questions" rather than guessing.
{note_block}"""


def build_prompt(note: str = "") -> str:
    """The instruction sent with the image."""
    note = (note or "").strip()
    note_block = f"\nThe person who uploaded it says: {note}\n" if note else ""
    return _PROMPT.format(note_block=note_block)


def _json_object(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a reply, tolerating code fences or prose."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise SketchReadingError("the model's reply contained no JSON object")
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SketchReadingError(f"the model's reply was not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise SketchReadingError("the model's reply was not a JSON object")
    return parsed


def parse_reading(text: str) -> SketchReading:
    """Validate a model reply into a :class:`SketchReading`, or raise cleanly."""
    if not isinstance(text, str) or not text.strip():
        raise SketchReadingError("the model returned an empty reply")
    raw = _json_object(text)
    try:
        return SketchReading.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ())) or "reply"
        raise SketchReadingError(
            f"the model's reading did not match the schema ({where}: {first.get('msg')})"
        ) from exc


# ── The model call ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class VisionReply:
    """What a vision call returns: the text, and who answered."""

    text: str
    provider: str | None = None
    model: str | None = None


#: ``(image_bytes, mime_type, prompt) -> reply``. A plain string is accepted
#: as the reply text with no provenance.
VisionCall = Callable[[bytes, str, str], Awaitable["VisionReply | str"]]

#: Provider families whose adapter passes a message's content list through
#: unchanged. Gemini, Bedrock and Codex flatten messages to text, so an image
#: would never reach the model.
_ANTHROPIC_FAMILY = frozenset({"anthropic", "claude"})
_TEXT_ONLY_FAMILIES = frozenset(
    {"gemini", "google", "vertex", "bedrock", "aws-bedrock", "openai-codex", "codex"}
)


def _vision_content(family: str, image: bytes, mime_type: str, prompt: str) -> list[dict[str, Any]]:
    data = base64.b64encode(image).decode("ascii")
    if family in _ANTHROPIC_FAMILY:
        image_block: dict[str, Any] = {
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": data},
        }
    else:
        image_block = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{data}"},
        }
    return [image_block, {"type": "text", "text": prompt}]


def _timeout_s() -> float:
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_S
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


def make_default_vision_call(
    *, invoke: Any = None, provider_config: Any = None, timeout_s: float | None = None
) -> VisionCall:
    """A :data:`VisionCall` over the gateway's configured model providers.

    ``invoke`` and ``provider_config`` are injectable for tests; by default the
    provider chain is the one chat uses (``provider_config_from_env``), less
    any family that cannot carry an image.
    """

    async def call(image: bytes, mime_type: str, prompt: str) -> VisionReply:
        from orchestrator.harness.providers import (
            AllProvidersFailedError,
            ProviderPipeline,
            RoleModelSlots,
            default_invoke,
        )
        from orchestrator.harness.providers.usage import usage_scope

        try:
            if provider_config is not None:
                config = provider_config
            else:
                from api_gateway.chat.harness_backend import provider_config_from_env

                config = provider_config_from_env()
            candidates = list(config.slots.candidates("generator"))
        except Exception as exc:  # noqa: BLE001 - reported as unavailable, never a crash
            raise SketchVisionUnavailableError(
                f"no model provider is configured for reading sketches: {exc}"
            ) from exc
        usable = [s for s in candidates if s.name.lower() not in _TEXT_ONLY_FAMILIES]
        if not usable:
            names = ", ".join(sorted({s.name for s in candidates})) or "none"
            raise SketchVisionUnavailableError(
                "no vision-capable model provider is configured (configured: "
                f"{names}); reading a sketch needs Anthropic or an OpenAI-compatible "
                "provider with a vision model"
            )

        answered: dict[str, str] = {}
        send = invoke or default_invoke

        async def _invoke(spec: Any, _request: Any) -> Any:
            family = spec.name.lower()
            request = {
                "messages": [
                    {
                        "role": "user",
                        "content": _vision_content(family, image, mime_type, prompt),
                    }
                ],
                "max_tokens": 4096,
                "temperature": 0.0,
            }
            result = await send(spec, request)
            answered.update(provider=spec.name, model=spec.model)
            return result

        pipeline = ProviderPipeline(
            RoleModelSlots(slots={"vision": usable}), retry_policy=config.retry
        )
        limit = timeout_s if timeout_s is not None else _timeout_s()
        try:
            with usage_scope(role="sketch_reading"):
                result = await asyncio.wait_for(pipeline.complete("vision", None, _invoke), limit)
        except TimeoutError as exc:
            raise SketchVisionUnavailableError(
                f"no vision model answered within {limit:g} s"
            ) from exc
        except AllProvidersFailedError as exc:
            raise SketchVisionUnavailableError(f"no vision model could read the sketch: {exc}") from exc
        text = result.get("text", "") if isinstance(result, dict) else str(result or "")
        return VisionReply(text=text, provider=answered.get("provider"), model=answered.get("model"))

    return call


# ── Metrics ───────────────────────────────────────────────────────────────

_metrics: Any = None


def _record(outcome: str) -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_sketch_reading(outcome)
    except Exception:  # noqa: BLE001 - a metric never fails a reading
        return


# ── Reading one image ─────────────────────────────────────────────────────


def normalise_mime_type(mime_type: str | None, filename: str = "") -> str:
    """A supported image mime type, from the given one or the file's extension."""
    mime = (mime_type or "").split(";")[0].strip().lower()
    if not mime and filename:
        mime = _EXTENSION_MIME.get(filename.rsplit(".", 1)[-1].lower(), "")
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in SUPPORTED_MIME_TYPES:
        raise ValueError(
            f"sketch reading: unsupported image type {mime or 'unknown'!r}; "
            f"send one of {', '.join(sorted(SUPPORTED_MIME_TYPES))}"
        )
    return mime


async def read_sketch(
    image: bytes, mime_type: str, *, note: str = "", vision_call: VisionCall
) -> tuple[SketchReading, VisionReply]:
    """Ask the vision model to read one image and validate what it says."""
    if not image:
        raise ValueError("sketch reading: the image is empty")
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"sketch reading: the image is {len(image)} bytes; the limit is {MAX_IMAGE_BYTES}"
        )
    with tracer.start_as_current_span("twin.read_sketch") as span:
        span.set_attribute("sketch_reading.mime_type", mime_type)
        span.set_attribute("sketch_reading.bytes", len(image))
        started = time.monotonic()
        try:
            raw = await vision_call(image, mime_type, build_prompt(note))
        except SketchVisionUnavailableError as exc:
            span.record_exception(exc)
            _record("unavailable")
            logger.warning("sketch_reading_unavailable", error=str(exc))
            raise
        reply = raw if isinstance(raw, VisionReply) else VisionReply(text=str(raw or ""))
        try:
            reading = parse_reading(reply.text)
        except SketchReadingError as exc:
            span.record_exception(exc)
            _record("refused_output")
            logger.warning(
                "sketch_reading_refused_output",
                error=str(exc),
                provider=reply.provider,
                model=reply.model,
                reply_chars=len(reply.text),
            )
            raise
        span.set_attribute("sketch_reading.parts", len(reading.parts))
        span.set_attribute("sketch_reading.open_questions", len(reading.open_questions))
        logger.info(
            "sketch_read",
            parts=len(reading.parts),
            connections=len(reading.connections),
            proportions=len(reading.proportions),
            open_questions=len(reading.open_questions),
            provider=reply.provider,
            model=reply.model,
            duration_s=round(time.monotonic() - started, 3),
        )
        return reading, reply


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "sketch")[:60]


def _reading_record(reading: SketchReading, reply: VisionReply, note: str) -> dict[str, Any]:
    return {
        "status": STATUS_PROPOSED,
        "proposal": reading.model_dump(),
        "note": note,
        "read_by": {"provider": reply.provider, "model": reply.model},
        "proposed_at": datetime.now(UTC).isoformat(),
        "decided_by": None,
        "decided_at": None,
        "decision_reason": None,
    }


def make_sketch_interpreter(
    twin: Any, project_backend: Any = None, *, vision_call: VisionCall | None = None
) -> Any:
    """Return an async ``interpret(...)`` that reads a sketch and stores the proposal."""
    call = vision_call or make_default_vision_call()

    async def interpret(
        *,
        name: str | None = None,
        image_bytes: bytes | None = None,
        mime_type: str | None = None,
        work_product_id: str | None = None,
        note: str = "",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        if (image_bytes is None) == (work_product_id is None):
            raise ValueError(
                "sketch reading: give exactly one of an image or the work_product_id of "
                "an uploaded image"
            )
        existing: Any = None
        if work_product_id is not None:
            existing = await twin.get_work_product(UUID(str(work_product_id)))
            if existing is None:
                raise ValueError(f"sketch reading: work product {work_product_id} not found")
            if existing.type != WorkProductType.IMAGE:
                raise ValueError(
                    f"sketch reading: work product {work_product_id} is a "
                    f"{existing.type.value}, not an uploaded image"
                )
            current = (existing.metadata or {}).get("sketch_reading") or {}
            if current.get("status") == STATUS_CONFIRMED:
                raise ValueError(
                    f"sketch reading: the reading of {work_product_id} is already confirmed; "
                    "upload the image again to read it afresh"
                )
            image_bytes, filename = _stored_image(existing)
            mime = normalise_mime_type(existing.metadata.get("mime_type"), filename)
        else:
            assert image_bytes is not None
            filename = ""
            mime = normalise_mime_type(mime_type)

        with tracer.start_as_current_span("twin.interpret_sketch") as span:
            reading, reply = await read_sketch(image_bytes, mime, note=note, vision_call=call)
            record = _reading_record(reading, reply, note)
            content_hash = hashlib.sha256(image_bytes).hexdigest()
            now = datetime.now(UTC)

            if existing is not None:
                node_id = str(existing.id)
                await _update_metadata(
                    twin,
                    existing,
                    {"authority": "illustrative", "sketch_reading": record},
                    "sketch read by a vision model; proposal awaits a person",
                )
                minio_object_key = existing.metadata.get("minio_object_key")
                display_name = existing.name
            else:
                wp_id = uuid4()
                display_name = name or "Sketch"
                ext = mime.split("/")[-1].replace("jpeg", "jpg")
                stored_name = f"{_slug(display_name)}.{ext}"
                minio_object_key = None
                try:
                    from digital_twin.storage.work_product_blobs import store_work_product_blob

                    minio_object_key = store_work_product_blob(
                        str(wp_id), stored_name, image_bytes, content_type=mime
                    )
                except Exception as exc:  # noqa: BLE001 - kept like design_sketch_recorder
                    logger.warning("sketch_image_blob_store_skipped", error=str(exc))
                metadata: dict[str, Any] = {
                    "original_filename": stored_name,
                    "content_sha256": content_hash,
                    "mime_type": mime,
                    # An image shows intent; nothing measured comes from it.
                    "authority": "illustrative",
                    "sketch_reading": record,
                }
                if minio_object_key:
                    metadata["minio_object_key"] = minio_object_key
                wp = WorkProduct(
                    id=wp_id,
                    name=display_name,
                    type=WorkProductType.IMAGE,
                    domain="mechanical",
                    file_path="",
                    content_hash=content_hash,
                    format=ext,
                    metadata=metadata,
                    created_at=now,
                    updated_at=now,
                    created_by="twin.interpret_sketch",
                    project_id=project_id,  # pydantic coerces str -> UUID
                )
                created = await twin.create_work_product(wp)
                node_id = str(getattr(created, "id", wp_id))
                if project_id and project_backend is not None:
                    try:
                        await project_backend.link_work_product(
                            project_id, node_id, display_name, "image"
                        )
                    except Exception as exc:  # noqa: BLE001 - link is best-effort
                        logger.warning("sketch_image_project_link_failed", error=str(exc))

            span.set_attribute("sketch_reading.node_id", node_id)
            _record("proposed")
            logger.info(
                "sketch_reading_proposed",
                node_id=node_id,
                project_id=project_id,
                reused_image=existing is not None,
            )
            return {
                "node_id": node_id,
                "name": display_name,
                "status": STATUS_PROPOSED,
                "authority": "illustrative",
                "reading": record["proposal"],
                "read_by": record["read_by"],
                "minio_object_key": minio_object_key,
                "next_step": (
                    "This is a proposal, not a record. A person must confirm or reject it "
                    "(twin.confirm_sketch_reading); only a confirmed reading is offered to "
                    "the concept phase, and its proportions stay assumptions."
                ),
            }

    return interpret


def _stored_image(wp: Any) -> tuple[bytes, str]:
    from fastapi import HTTPException

    from api_gateway.twin.blob_store import resolve_work_product_blob

    try:
        return resolve_work_product_blob(wp)
    except HTTPException as exc:
        raise ValueError(f"sketch reading: the image could not be read: {exc.detail}") from exc


async def _update_metadata(twin: Any, wp: Any, changes: dict[str, Any], why: str) -> None:
    from api_gateway.twin.version_service import VersionService

    updated = {**dict(wp.metadata or {}), **changes}
    revision = VersionService.build_revision(wp, why, snapshot_override=updated)
    final = VersionService.append_to_metadata(updated, revision)
    await twin.update_work_product(wp.id, {"metadata": final, "updated_at": datetime.now(UTC)})


def make_sketch_reading_decider(twin: Any) -> Any:
    """Return an async ``decide(node_id, *, decision, decided_by, ...)``.

    A person's decision on a proposed reading. ``decided_by`` must come from
    the approval that let the call run, never from the model (the MCP tool is
    a human-authority tool, see ``mcp_core.guardrails``).
    """

    async def decide(
        node_id: str,
        *,
        decision: str,
        decided_by: str,
        reason: str = "",
    ) -> dict[str, Any]:
        status = _DECISIONS.get(decision)
        if status is None:
            raise ValueError("sketch reading: 'decision' must be 'confirm' or 'reject'")
        if not decided_by or not decided_by.strip():
            raise ValueError("sketch reading: a decision needs the person who made it")
        with tracer.start_as_current_span("twin.confirm_sketch_reading") as span:
            span.set_attribute("sketch_reading.node_id", node_id)
            span.set_attribute("sketch_reading.decision", status)
            wp = await twin.get_work_product(UUID(str(node_id)))
            if wp is None:
                raise ValueError(f"sketch reading: node {node_id} not found")
            current = (wp.metadata or {}).get("sketch_reading")
            if not isinstance(current, dict):
                raise ValueError(f"sketch reading: node {node_id} has no sketch reading")
            if current.get("status") != STATUS_PROPOSED:
                raise ValueError(
                    f"sketch reading: the reading of {node_id} is already {current.get('status')}"
                )
            record = {
                **current,
                "status": status,
                "decided_by": decided_by,
                "decided_at": datetime.now(UTC).isoformat(),
                "decision_reason": reason or None,
            }
            await _update_metadata(
                twin, wp, {"sketch_reading": record}, f"sketch reading {status} by {decided_by}"
            )
            _record(status)
            logger.info(
                "sketch_reading_decided", node_id=node_id, status=status, decided_by=decided_by
            )
            return {
                "node_id": str(wp.id),
                "status": status,
                "decided_by": decided_by,
                "decided_at": record["decided_at"],
            }

    return decide


# ── What the concept phase is given ───────────────────────────────────────


async def confirmed_sketch_readings(twin: Any, project_id: str | None) -> list[dict[str, Any]]:
    """The project's sketch readings a person confirmed, and nothing else."""
    from twin_core.models.enums import WorkProductType

    if not project_id:
        return []
    try:
        pid = UUID(str(project_id))
    except ValueError:
        return []
    images = await twin.list_work_products(work_product_type=WorkProductType.IMAGE, project_id=pid)
    out: list[dict[str, Any]] = []
    for wp in images:
        record = (wp.metadata or {}).get("sketch_reading")
        if not isinstance(record, dict) or record.get("status") != STATUS_CONFIRMED:
            continue
        out.append(
            {
                "node_id": str(wp.id),
                "name": wp.name,
                "note": record.get("note") or "",
                "confirmed_by": record.get("decided_by"),
                "reading": record.get("proposal") or {},
            }
        )
    return out


def concept_inputs_block(readings: list[dict[str, Any]]) -> str:
    """The confirmed readings as prompt text for the concept phase."""
    if not readings:
        return ""
    lines = [
        "Sketches the person uploaded, read by a vision model and confirmed by a person "
        "as concept input. They show intent: every proportion is an assumption, the "
        "image is illustrative and never evidence, and open questions are still open."
    ]
    for r in readings:
        reading = r.get("reading") or {}
        lines.append(f"- {r['name']} (node {r['node_id']}): {reading.get('summary', '')}")
        parts = ", ".join(p.get("name", "") for p in reading.get("parts") or [])
        if parts:
            lines.append(f"  parts: {parts}")
        for c in reading.get("connections") or []:
            lines.append(f"  joint: {c.get('from_part')} to {c.get('to_part')}, {c.get('joint_type')}")
        for p in reading.get("proportions") or []:
            value = p.get("value")
            size = f"{value:g} {p.get('unit', '')}".strip() if isinstance(value, int | float) else ""
            rel = f" relative to {p['relative_to']}" if p.get("relative_to") else ""
            lines.append(f"  assumption: {p.get('subject')} {size}{rel}".rstrip())
        for q in reading.get("open_questions") or []:
            lines.append(f"  open: {q}")
    return "\n".join(lines) + "\n"
