"""AI concept images: appealing, and honest about what they are (FORGE-761).

``twin.render_concept_sketch`` draws a concept as a deterministic block
layout. That is accurate and dry. This adds the picture a person wants to
show: an image model, conditioned on two things,

1. the block layout itself, rasterised as the same isometric projection the
   sketch draws, so the image keeps the concept's proportions and
   arrangement; and
2. a short visual brief from the LLM: the product, its materials, its style.

The result is a ``render`` work product with ``metadata.authority =
"illustrative"``. It shows intent and is never evidence: the claim recorder
refuses it as the artefact of a requirement claim, like every render and
sketch. It says what conditioned it (the layout's content hash, and its
sketch node when there is one, the brief, the prompt, the provider and
model) and carries a visible label, "AI concept image, illustrative only",
stamped onto the pixels so it survives being copied out of the twin.

What the gateway image can do decides how faithful the conditioning is, and
the record says which happened rather than implying more:

* Pillow present (it is, in the gateway image): the layout is drawn as a
  PNG and sent with the prompt (``image_conditioning: "layout_png"``), and
  the label is a banner drawn into the returned image.
* Pillow absent: the brief and the layout's part list go in the prompt
  alone (``image_conditioning: "none"``), and the image is wrapped in an SVG
  whose text element carries the label.

No SVG rasteriser (cairosvg, resvg) is installed, and the provider does not
take SVG input, so the sketch's SVG is never sent as is.

Once the image exists, a vision model checks it against the concept's
selected assembly (``image_critique``) and the result is stored on the render
as ``metadata.critique``. That check is advisory and best-effort: when it
cannot run, the critique says ``unchecked`` and why, and the image is still
returned.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from api_gateway.twin.image_generation import (
    GeneratedImage,
    ImageGenerationError,
    ImageGenerationTimeoutError,
    ImageGenerationUnavailableError,
    ImageGenerator,
    generate_with_timeout,
    parse_size,
    resolve_image_generator,
    timeout_from_env,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.concept_image")

LABEL = "AI concept image, illustrative only"
MAX_BRIEF_CHARS = 1500
_FACE_FILL = {
    "face-top": (236, 236, 236),
    "face-front": (196, 196, 196),
    "face-right": (160, 160, 160),
    "cap": (226, 226, 226),
}

_metrics: Any = None


def _record(outcome: str) -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_concept_image(outcome)
    except Exception:  # noqa: BLE001 - a metric never fails a generation
        pass


def _pil() -> Any:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    return Image, ImageDraw, ImageFont


def rasterise_layout(faces: list[Any], size: str) -> bytes | None:
    """The layout's isometric faces as a PNG, or ``None`` when Pillow is absent.

    ``faces`` is ``concept_sketch.iso_faces(layout, width, height)``: (part,
    face class, outline) in painter's order, already in pixel coordinates.
    Flat greys on white, outlined: a shape reference, not a style one.
    """
    pil = _pil()
    if pil is None:
        return None
    image_mod, draw_mod, _ = pil
    width, height = parse_size(size)
    img = image_mod.new("RGB", (width, height), (255, 255, 255))
    draw = draw_mod.Draw(img)
    for _part, face, outline in faces:
        pts = [(float(x), float(y)) for x, y in outline]
        if len(pts) < 3:
            continue
        draw.polygon(pts, fill=_FACE_FILL.get(face, (200, 200, 200)), outline=(40, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _font(image_font: Any, px: int) -> Any:
    try:
        return image_font.load_default(size=px)
    except TypeError:  # Pillow < 10.1 has one fixed-size bitmap font
        return image_font.load_default()


def stamp_label(image: GeneratedImage, size: str) -> tuple[bytes, str, str]:
    """The image with :data:`LABEL` made visible: ``(bytes, mime, format)``.

    With Pillow, a dark banner is drawn across the bottom of the image and
    the result is a PNG (the label is also kept in a PNG text chunk). Without
    it, or when the bytes are not an image Pillow can open, the image is
    embedded in an SVG with the label as a text element below it.
    """
    pil = _pil()
    if pil is not None:
        image_mod, draw_mod, font_mod = pil
        try:
            from PIL.PngImagePlugin import PngInfo

            img = image_mod.open(io.BytesIO(image.image_bytes)).convert("RGB")
            w, h = img.size
            band = max(28, h // 18)
            draw = draw_mod.Draw(img)
            draw.rectangle([(0, h - band), (w, h)], fill=(20, 20, 20))
            font = _font(font_mod, int(band * 0.55))
            draw.text((band // 2, h - band + band // 5), LABEL, fill=(255, 255, 255), font=font)
            info = PngInfo()
            info.add_text("Description", LABEL)
            info.add_text("Software", f"{image.provider}/{image.model}")
            buf = io.BytesIO()
            img.save(buf, format="PNG", pnginfo=info)
            return buf.getvalue(), "image/png", "png"
        except Exception as exc:  # noqa: BLE001 - fall through to the SVG label
            logger.warning("concept_image_label_raster_failed", error=str(exc))
    width, height = parse_size(size)
    band = 40
    data = base64.b64encode(image.image_bytes).decode("ascii")
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height + band}" '
        f'viewBox="0 0 {width} {height + band}" role="img">'
        f"<title>{LABEL}</title>"
        f'<image href="data:{image.mime};base64,{data}" x="0" y="0" '
        f'width="{width}" height="{height}"/>'
        f'<rect x="0" y="{height}" width="{width}" height="{band}" fill="#141414"/>'
        f'<text x="16" y="{height + 26}" fill="#ffffff" font-family="sans-serif" '
        f'font-size="20" class="illustrative-label">{LABEL}</text>'
        "</svg>\n"
    )
    return svg.encode("utf-8"), "image/svg+xml", "svg"


#: What each orthographic view is, in words the image model is given.
_VIEW_WORDS = {
    "front": "its front face, from directly in front",
    "back": "its back face, from directly behind",
    "left": "its left side, from directly to its left",
    "right": "its right side, from directly to its right",
}


def build_prompt(
    visual_brief: str, concept_name: str, part_names: list[str], view: str = "isometric"
) -> str:
    """The prompt sent to the model: the brief, plus what the layout is.

    FORGE-777: image models follow the product's layout but improvise what it
    holds (in the bike-rack run: extra handlebars, merged frames, bikes hung
    at impossible angles). The prompt says the product's parts are exactly
    the layout's, and that anything shown in use is whole and realistic.

    ``view`` other than ``isometric`` asks for a straight-on orthographic
    view from that side, so a set of them can feed a multiview 3D model.
    """
    parts = ", ".join(part_names[:30])
    if view == "isometric":
        framing = (
            "The reference image is a grey block layout of the product seen in isometric "
            "view: keep its overall proportions and the arrangement of its parts, and render "
            "it as the finished product described above, on a plain studio background. "
        )
    else:
        if view not in _VIEW_WORDS:
            raise ValueError(f"concept image: unknown view {view!r}")
        name = view.upper()
        framing = (
            f"The reference image is a grey block layout of the product in a straight-on "
            f"orthographic {name} view, showing {_VIEW_WORDS[view]}. Render a straight-on "
            f"orthographic {name} view of the product, no perspective: the camera level and "
            "square to that side, the outline matching the reference exactly, the whole "
            "product in frame. Keep its proportions and the arrangement of its parts, and "
            "render it as the finished product described above, on a plain white studio "
            "background with soft even light. "
        )
    return (
        f"Product concept image: {concept_name}. {visual_brief.strip()}\n"
        f"{framing}"
        f"Parts, as laid out: {parts}. Draw exactly these parts, joined as laid out, and "
        "add no others. If the brief shows the product in use, every item it holds or "
        "supports is whole and correctly built, appears once each in the number the "
        "brief gives, and sits on the product the way the product holds it; never "
        "duplicate, merge or detach their parts. Do not add text, dimensions or logos."
    )


def _decision_selects(known: Any, concept_name: str) -> bool:
    """Is ``known`` a decision that is about this concept (or about no option)?"""
    if known is None:
        return False
    options = getattr(known, "options", None)
    if not options:
        return True
    from api_gateway.twin.image_critique import _option_for

    option = _option_for(concept_name, options)
    return option is not None and bool(option.selected)


def _usable(parse: Any, value: Any) -> bool:
    try:
        parse(value)
    except ValueError:
        return False
    return True


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "concept-image")[:60]


def make_concept_image_recorder(
    twin: Any,
    project_backend: Any = None,
    *,
    generator: ImageGenerator | None = None,
    blob_writer: Callable[..., str] | None = None,
    timeout_s: float | None = None,
    vision_call: Any = None,
) -> Any:
    """Return an async ``render(...)`` that generates, labels and commits one image.

    ``generator`` defaults to whatever :func:`resolve_image_generator` finds
    in the environment at call time, so a provider configured after start
    is picked up and an unconfigured one fails that call, not the gateway.
    ``blob_writer(node_id, filename, content, content_type=...) -> key`` is
    injectable for tests and defaults to MinIO. ``vision_call`` is the model
    call the image check uses (``sketch_reading.VisionCall``); it defaults to
    the gateway's vision path, made only when an assembly is known.
    """

    def _default_writer(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        from digital_twin.storage.work_product_blobs import store_work_product_blob

        key: str = store_work_product_blob(node_id, filename, content, content_type=content_type)
        return key

    write_blob = blob_writer or _default_writer

    async def render(
        *,
        visual_brief: str,
        concept_name: str,
        part_names: list[str],
        layout_faces: list[Any],
        conditioned_on: dict[str, Any],
        layout_node_id: str | None = None,
        name: str | None = None,
        size: str = "1024x1024",
        project_id: str | None = None,
        domain: str = "mechanical",
        decision_ref: str | None = None,
        assembly: dict[str, Any] | None = None,
        layout_assembly: dict[str, Any] | None = None,
        view: str = "isometric",
    ) -> dict[str, Any]:
        from api_gateway.twin.image_critique import (
            KnownAssembly,
            check_image,
            resolve_assembly,
            unchecked,
        )
        from orchestrator.design_flow.context import parse_concept_assembly
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        brief = (visual_brief or "").strip()
        if not brief:
            raise ValueError("concept image: 'visual_brief' must be a non-empty string")
        if len(brief) > MAX_BRIEF_CHARS:
            raise ValueError(f"concept image: 'visual_brief' is longer than {MAX_BRIEF_CHARS}")
        parse_size(size)
        if view != "isometric" and view not in _VIEW_WORDS:
            raise ValueError(f"concept image: unknown view {view!r}")
        # The assembly the image is checked against, found before any paid
        # call: one asked for by name or given inline that cannot be used is
        # the caller's error; a project lookup that fails only skips the check.
        known: Any = None
        lookup_failed: str | None = None
        if decision_ref is not None or assembly is not None:
            known = await resolve_assembly(
                twin, project_id=project_id, decision_ref=decision_ref, assembly=assembly
            )
        else:
            try:
                known = await resolve_assembly(twin, project_id=project_id)
            except Exception as exc:  # noqa: BLE001 - the check is best-effort
                lookup_failed = f"the concept decision could not be read: {exc}"
            # FORGE-776: an image is of one concept. The project's decision is
            # used only when it selects this concept (or names no options);
            # otherwise, or with no decision yet, the concept's own layout is
            # what the picture should show.
            if (
                lookup_failed is None
                and layout_assembly is not None
                and _usable(parse_concept_assembly, layout_assembly)
                and not _decision_selects(known, concept_name)
            ):
                known = KnownAssembly(
                    assembly=parse_concept_assembly(layout_assembly),
                    source={"kind": "sketch_layout", "layout_node_id": layout_node_id},
                )

        with tracer.start_as_current_span("twin.render_concept_image") as span:
            span.set_attribute("concept_image.concept", concept_name)
            span.set_attribute("concept_image.size", size)
            span.set_attribute("concept_image.view", view)
            try:
                gen = generator or resolve_image_generator()
            except ImageGenerationUnavailableError as exc:
                span.record_exception(exc)
                _record("unavailable")
                logger.warning("concept_image_unavailable", reason=str(exc))
                raise

            conditioning = rasterise_layout(layout_faces, size)
            mode = "layout_png" if conditioning is not None else "none"
            span.set_attribute("concept_image.conditioning", mode)
            prompt = build_prompt(brief, concept_name, part_names, view)
            limit = timeout_s if timeout_s is not None else timeout_from_env()
            try:
                image = await generate_with_timeout(
                    gen, prompt, conditioning, size, timeout_s=limit
                )
            except ImageGenerationTimeoutError as exc:
                span.record_exception(exc)
                _record("timeout")
                logger.warning("concept_image_timeout", timeout_s=limit)
                raise
            except ImageGenerationUnavailableError as exc:
                span.record_exception(exc)
                _record("unavailable")
                logger.warning("concept_image_unavailable", reason=str(exc))
                raise
            except Exception as exc:
                span.record_exception(exc)
                _record("failed")
                logger.warning("concept_image_failed", error=str(exc))
                if isinstance(exc, ImageGenerationError):
                    raise
                raise ImageGenerationError(f"image generation failed: {exc}") from exc
            if not image.image_bytes:
                _record("failed")
                raise ImageGenerationError("the image provider returned an empty image")

            # Conditioning is recorded as it actually happened: a provider
            # that dropped the input image did not follow the layout.
            used = mode if (mode == "layout_png" and image.conditioned) else "none"
            labelled, mime, fmt = stamp_label(image, size)
            content_hash = hashlib.sha256(labelled).hexdigest()
            wp_id = uuid4()
            filename = f"{_slug(concept_name)}-concept-image.{fmt}"
            try:
                key = await asyncio.to_thread(
                    write_blob, str(wp_id), filename, labelled, content_type=mime
                )
            except Exception as exc:  # noqa: BLE001 - refuse rather than store a node with no image
                span.record_exception(exc)
                _record("store_failed")
                raise ImageGenerationError(
                    f"the concept image was generated but could not be stored ({exc})"
                ) from exc

            # FORGE-772: a vision model checks the picture against the selected
            # assembly. Advisory, and never fails the image.
            if lookup_failed is not None:
                critique = unchecked(lookup_failed)
            else:
                critique = await check_image(
                    image.image_bytes,
                    image.mime,
                    known,
                    vision_call=vision_call,
                    concept_name=concept_name,
                )
            span.set_attribute("concept_image.critique", str(critique.get("verdict")))

            conditioning_record = {
                **conditioned_on,
                "view": view,
                "image_conditioning": used,
            }
            if used == "none":
                conditioning_record["image_conditioning_note"] = (
                    "no layout image reached the model (no rasteriser available, or the "
                    "provider ignored it); only the brief and the part list conditioned it"
                )
            metadata: dict[str, Any] = {
                "original_filename": filename,
                "minio_object_key": key,
                "content_sha256": content_hash,
                "image_files": {filename: key},
                # FORGE-759/761: a generated picture of intent, never evidence.
                "authority": "illustrative",
                "illustrative_label": LABEL,
                "conditioned_on": conditioning_record,
                "visual_brief": brief,
                "prompt": prompt,
                "provider": image.provider,
                "model": image.model,
                "size": size,
                "generated_by": "twin.render_concept_image",
                "concept_name": concept_name,
                # FORGE-772: advisory; the image stays illustrative either way.
                "critique": critique,
            }
            if layout_node_id:
                metadata["source_work_product_id"] = layout_node_id
                metadata["source_content_hash"] = conditioned_on.get("layout_content_hash")
            now = datetime.now(UTC)
            wp_name = name or f"Concept image: {concept_name}"
            created = await twin.create_work_product(
                WorkProduct(
                    id=wp_id,
                    name=wp_name,
                    type=WorkProductType.RENDER,
                    domain=domain,
                    file_path="",
                    content_hash=content_hash,
                    format=fmt,
                    metadata=metadata,
                    created_at=now,
                    updated_at=now,
                    created_by="twin.render_concept_image",
                    project_id=project_id,
                )
            )
            node_id = str(getattr(created, "id", wp_id))
            linked_to_layout = False
            if layout_node_id:
                await twin.add_edge(created.id, UUID(layout_node_id), EdgeType.GENERATED_FROM)
                linked_to_layout = True

            project_linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(project_id, node_id, wp_name, "render")
                    project_linked = True
                except Exception as exc:  # noqa: BLE001 - the node exists; say the link failed
                    logger.warning("concept_image_project_link_failed", error=str(exc))

            _record("generated")
            logger.info(
                "concept_image_generated",
                node_id=node_id,
                provider=image.provider,
                model=image.model,
                conditioning=used,
                view=view,
                format=fmt,
                layout_node_id=layout_node_id,
                project_id=project_id,
            )
            out: dict[str, Any] = {
                "node_id": node_id,
                "authority": "illustrative",
                "label": LABEL,
                "format": fmt,
                "content_hash": content_hash,
                "minio_object_key": key,
                "conditioned_on": conditioning_record,
                "view": view,
                "provider": image.provider,
                "model": image.model,
                "linked_to_layout": linked_to_layout,
                "project_linked": project_linked,
                "critique": critique,
            }
            if fmt == "png":
                # Lifted into an MCP image block by the server, shown in chat.
                out["images"] = [
                    {"label": LABEL, "png_base64": base64.b64encode(labelled).decode("ascii")}
                ]
            return out

    return render
