"""AI 3D reference models from a concept's images (FORGE-778).

``twin.render_concept_image`` makes the picture of a concept. This turns that
picture (or a text prompt) into a 3D mesh with an image-to-3D model, so a
reviewer can turn the concept around. It is for concept review only.

Three ways in, exactly one per call:

* ``image_node_id``: one concept image (Tripo ``image-to-model``). The model
  has to guess the sides it cannot see.
* ``view_images``: the concept's own straight-on views, ``front`` plus at
  least one of ``left`` / ``back`` / ``right``, each a concept image rendered
  with that ``view`` (Tripo ``multiview-to-model``). The model sees the back
  instead of inventing it.
* ``prompt``: text alone (Tripo ``text-to-model``).

The mesh is a ``render`` work product in GLB with ``metadata.authority =
"illustrative"``, linked ``generated_from`` every image it was made from. It
is never a ``cad_model``: its dimensions are the model's guess, not the
layout's, and the claim recorder refuses it as the artefact of a requirement
claim like every render. It records the provider, its task id and model
version, the generation options sent, and carries the label "AI 3D reference
model, illustrative only".

Each image sent is the stored concept image with its label banner cropped off
when Pillow can (a banner would be modelled as part of the product); the
record says whether it was.

After download the GLB's bounding box is read from its glTF JSON (no paid
call, no mesh library) and stored as ``bbox_m``. When the images came from a
block layout, its overall envelope is stored beside it as ``envelope_mm``.

With an envelope the model is then fitted to it (``fit_to_envelope``, on by
default then): one uniform factor, the one that minimises the summed squared
log ratio of the three axes, is applied by giving the scene a parent node
with that ``scale`` (no vertex is rewritten). The stored file is the scaled
one; the provider's own file is kept beside it as a second blob. The record
carries ``scale_factor``, the box before and after (``bbox_m_before``,
``bbox_m``) and the ``proportion_error_pct`` a uniform scale cannot remove,
so a reviewer sees at a glance that a model came out, say, 14% too deep for
its width. Because this scaling replaces the provider's own, ``auto_size``
defaults to false when there is an envelope and to true when there is none.
Without an envelope, or with ``fit_to_envelope`` false, the file is stored as
the provider made it and ``deviation_pct`` compares it with the envelope.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from api_gateway.twin.glb_bounds import (
    GlbBoundsError,
    envelope_deviation,
    envelope_fit,
    glb_bounds,
    scale_glb,
)
from api_gateway.twin.mesh_generation import (
    MAX_PROMPT_CHARS,
    MULTIVIEW_VIEWS,
    GeneratedMesh,
    MeshGenerationError,
    MeshGenerationTimeoutError,
    MeshGenerationUnavailableError,
    MeshGenerator,
    resolve_mesh_generator,
    timeout_from_env,
)
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.concept_mesh")

TOOL = "twin.generate_concept_mesh"
LABEL = "AI 3D reference model, illustrative only"
GLB_MIME = "model/gltf-binary"
#: Extra seconds past the provider's own deadline before the call is abandoned.
_GRACE_S = 30.0
#: Triangles when no ``face_limit`` is given: enough to review, small enough
#: to open in a browser (an unlimited model came back at 1.4M).
DEFAULT_FACE_LIMIT = 100_000
#: Tripo's ranges: 2M triangles at most, and 500 to 20,000 for smart low poly.
MAX_FACE_LIMIT = 2_000_000
LOW_POLY_FACE_RANGE = (500, 20_000)
PARTS_NOTE = "generate_parts needs an untextured model, so texture and pbr were sent false"

_metrics: Any = None


def _record(outcome: str, mode: str = "image") -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_concept_mesh(outcome, mode)
    except Exception:  # noqa: BLE001 - a metric never fails a generation
        pass


def strip_label_band(image: bytes, mime: str) -> tuple[bytes, str, bool]:
    """The concept image without the label banner ``stamp_label`` drew.

    ``(bytes, mime, cropped)``: unchanged when Pillow is absent or the image
    is not one it can open.
    """
    if mime != "image/png":
        return image, mime, False
    try:
        from PIL import Image
    except ImportError:
        return image, mime, False
    try:
        img = Image.open(io.BytesIO(image)).convert("RGB")
        w, h = img.size
        band = max(28, h // 18)  # the band concept_image.stamp_label draws
        if h <= band * 2:
            return image, mime, False
        buf = io.BytesIO()
        img.crop((0, 0, w, h - band)).save(buf, format="PNG")
        return buf.getvalue(), "image/png", True
    except Exception as exc:  # noqa: BLE001 - send the image as stored
        logger.warning("concept_mesh_label_crop_failed", error=str(exc))
        return image, mime, False


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "concept")[:60]


def _wp_type(wp: Any) -> str:
    return str(getattr(wp.type, "value", wp.type))


def _conditioned_on(wp: Any) -> dict[str, Any]:
    meta = getattr(wp, "metadata", None) or {}
    cond = meta.get("conditioned_on")
    return dict(cond) if isinstance(cond, dict) else {}


def generation_options(
    *,
    auto_size: Any = True,
    face_limit: Any = None,
    texture: Any = None,
    generate_parts: Any = False,
    smart_low_poly: Any = False,
) -> tuple[dict[str, Any], str | None]:
    """The options sent to the provider, and a note when one was forced.

    Refuses (``ValueError``) what the provider would refuse, before any paid
    call: ``generate_parts`` with a texture or with smart low poly, and a
    ``face_limit`` outside the provider's range.
    """
    for field, value in (
        ("auto_size", auto_size),
        ("generate_parts", generate_parts),
        ("smart_low_poly", smart_low_poly),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"{TOOL}: '{field}' must be true or false")
    if texture is not None and not isinstance(texture, bool):
        raise ValueError(f"{TOOL}: 'texture' must be true or false")
    if generate_parts and texture is True:
        raise ValueError(
            f"{TOOL}: 'generate_parts' needs an untextured model; give texture false "
            "(or leave it out) with generate_parts"
        )
    if generate_parts and smart_low_poly:
        raise ValueError(f"{TOOL}: 'generate_parts' and 'smart_low_poly' cannot be combined")
    if face_limit is None:
        face_limit = LOW_POLY_FACE_RANGE[1] if smart_low_poly else DEFAULT_FACE_LIMIT
    if isinstance(face_limit, bool) or not isinstance(face_limit, int) or face_limit <= 0:
        raise ValueError(f"{TOOL}: 'face_limit' must be a positive integer")
    if face_limit > MAX_FACE_LIMIT:
        raise ValueError(f"{TOOL}: 'face_limit' is above {MAX_FACE_LIMIT}")
    if smart_low_poly and not LOW_POLY_FACE_RANGE[0] <= face_limit <= LOW_POLY_FACE_RANGE[1]:
        lo, hi = LOW_POLY_FACE_RANGE
        raise ValueError(f"{TOOL}: with 'smart_low_poly', 'face_limit' must be {lo} to {hi}")
    note = None
    if generate_parts:
        tex = False
        note = PARTS_NOTE
    else:
        tex = True if texture is None else texture
    options = {
        "auto_size": auto_size,
        "face_limit": face_limit,
        "texture": tex,
        # PBR forces a texture, so it follows it.
        "pbr": tex,
        "smart_low_poly": smart_low_poly,
        "generate_parts": generate_parts,
    }
    return options, note


def _envelope_from_layout(layout: Any) -> list[float] | None:
    """The overall ``[width, depth, height]`` of a stored ``concept_layout``."""
    if not isinstance(layout, dict):
        return None
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for part in layout.get("parts") or []:
        try:
            pos = [float(v) for v in part["position_mm"]]
            size = [float(v) for v in part["size_mm"]]
        except (KeyError, TypeError, ValueError):
            return None
        for i in range(3):
            lo[i] = min(lo[i], pos[i])
            hi[i] = max(hi[i], pos[i] + size[i])
    if lo[0] == float("inf"):
        return None
    return [hi[i] - lo[i] for i in range(3)]


def _bbox(size: tuple[float, float, float]) -> list[float]:
    return [round(v, 4) for v in size]


def _size_and_fit(
    glb: bytes,
    envelope: list[float] | None,
    *,
    metres: bool,
    fit: bool,
) -> tuple[dict[str, Any], bytes]:
    """The size record of a downloaded model, and the file to store.

    ``metres`` says the provider scaled it to real units (``auto_size``).
    With an ``envelope`` and ``fit``, the file returned is scaled uniformly
    to fit the envelope; otherwise it is ``glb`` itself, unchanged.
    """
    record: dict[str, Any] = {}
    try:
        bounds = glb_bounds(glb)
    except GlbBoundsError as exc:
        record["bbox_note"] = f"the model's bounds could not be read: {exc}"
        if envelope is not None:
            record["envelope_mm"] = envelope
            if fit:
                record["fit_to_envelope"] = False
                record["fit_note"] = "not fitted: the model's bounds could not be read"
        return record, glb
    record["bbox_m"] = _bbox(bounds.size)
    record["bbox_axes"] = "glTF x, y (up), z"
    record["bbox_units"] = "m" if metres else "model units (auto_size off)"
    record["bbox_approximate"] = bounds.approximate
    record["bbox_method"] = bounds.method
    if envelope is None:
        return record, glb
    env = (envelope[0], envelope[1], envelope[2])
    record["envelope_mm"] = envelope
    if fit:
        try:
            fitted = envelope_fit(bounds.size, env)
            scaled = scale_glb(glb, fitted["scale_factor"])
            after = glb_bounds(scaled)
        except (ValueError, GlbBoundsError) as exc:
            record["fit_to_envelope"] = False
            record["fit_note"] = f"not fitted: {exc}"
        else:
            error = fitted["proportion_error_pct"]
            record.update(
                {
                    "fit_to_envelope": True,
                    "scale_factor": round(fitted["scale_factor"], 6),
                    "scale_method": "uniform, least squares of the log ratio per axis",
                    "bbox_m_before": record["bbox_m"],
                    "bbox_units_before": record["bbox_units"],
                    "bbox_m": _bbox(after.size),
                    "bbox_units": "m",
                    "axis_mapping": fitted["axis_mapping"],
                    "proportion_error_pct": error,
                    # After the fit the stored model is in metres, so its
                    # deviation from the envelope is its proportion error.
                    "deviation_pct": dict(error),
                    "deviation_basis": "fitted",
                }
            )
            return record, scaled
    else:
        record["fit_to_envelope"] = False
    dev = envelope_deviation(bounds.size, env, scaled_to_metres=metres)
    record["deviation_pct"] = dev["deviation_pct"]
    record["deviation_basis"] = dev["basis"]
    record["axis_mapping"] = dev["axis_mapping"]
    return record, glb


def make_concept_mesh_recorder(
    twin: Any,
    project_backend: Any = None,
    *,
    generator: MeshGenerator | None = None,
    blob_reader: Callable[[Any], tuple[bytes, str]] | None = None,
    blob_writer: Callable[..., str] | None = None,
    timeout_s: float | None = None,
) -> Any:
    """Return an async ``generate(...)`` that makes, stores and links one mesh.

    ``generator`` defaults to whatever :func:`resolve_mesh_generator` finds
    in the environment at call time, so an unconfigured provider fails that
    call and never the gateway. ``blob_reader(wp) -> (bytes, filename)`` and
    ``blob_writer(node_id, filename, content, content_type=...) -> key`` are
    injectable for tests and default to the twin's blob store.

    ``generate(..., validate_only=True)`` runs every check that refuses a
    call before the paid one (inputs, source images, a configured provider)
    and returns without generating: a caller that runs the generation as a
    background job (``twin.generate_concept_mesh``, FORGE-783) refuses a
    bad call at once instead of on the job.
    """

    def _default_reader(wp: Any) -> tuple[bytes, str]:
        from fastapi import HTTPException

        from api_gateway.twin.blob_store import resolve_work_product_blob

        try:
            return resolve_work_product_blob(wp)
        except HTTPException as exc:
            raise ValueError(f"the image could not be read: {exc.detail}") from exc

    def _default_writer(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        from digital_twin.storage.work_product_blobs import store_work_product_blob

        key: str = store_work_product_blob(node_id, filename, content, content_type=content_type)
        return key

    read_blob = blob_reader or _default_reader
    write_blob = blob_writer or _default_writer

    async def _source_image(node_ref: str, field: str = "image_node_id") -> tuple[Any, bytes, str]:
        from api_gateway.twin.image_critique import stored_image_bytes

        try:
            node_id = UUID(node_ref.strip())
        except ValueError as exc:
            raise ValueError(f"{TOOL}: '{field}' is not a node id: {node_ref!r}") from exc
        wp = await twin.get_work_product(node_id)
        metadata = dict(getattr(wp, "metadata", None) or {}) if wp is not None else {}
        if (
            wp is None
            or _wp_type(wp) != "render"
            or metadata.get("generated_by") != "twin.render_concept_image"
        ):
            raise ValueError(
                f"{TOOL}: {node_ref} is not a concept image made by twin.render_concept_image"
            )
        try:
            content, _filename = read_blob(wp)
            image, mime = stored_image_bytes(content, str(getattr(wp, "format", "") or ""))
        except Exception as exc:  # noqa: BLE001 - refuse before any paid call
            raise ValueError(
                f"{TOOL}: the concept image {node_ref} could not be read: {exc}"
            ) from exc
        if mime not in ("image/png", "image/jpeg", "image/webp"):
            raise ValueError(f"{TOOL}: the concept image is {mime}; PNG, JPEG or WebP is needed")
        return wp, image, mime

    async def _view_sources(view_images: Any) -> dict[str, tuple[Any, bytes, str]]:
        """Each view's concept image, checked, in Tripo's view order."""
        if not isinstance(view_images, dict) or not view_images:
            raise ValueError(f"{TOOL}: 'view_images' must be an object of view -> node id")
        unknown = sorted(str(k) for k in view_images if k not in MULTIVIEW_VIEWS)
        if unknown:
            raise ValueError(
                f"{TOOL}: unknown view {', '.join(unknown)}; views are {', '.join(MULTIVIEW_VIEWS)}"
            )
        if "front" not in view_images:
            raise ValueError(f"{TOOL}: 'view_images' needs a front view")
        if len(view_images) < 2:
            raise ValueError(f"{TOOL}: 'view_images' needs at least 2 views (front and another)")
        for view, ref in view_images.items():
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError(f"{TOOL}: view_images.{view} must be a non-empty string")
        refs = [str(r).strip() for r in view_images.values()]
        if len(set(refs)) != len(refs):
            raise ValueError(f"{TOOL}: each view needs its own image")
        out: dict[str, tuple[Any, bytes, str]] = {}
        layouts: set[str] = set()
        for view in MULTIVIEW_VIEWS:
            if view not in view_images:
                continue
            wp, image, mime = await _source_image(view_images[view], f"view_images.{view}")
            cond = _conditioned_on(wp)
            drawn = str(cond.get("view") or "isometric")
            if drawn != view:
                raise ValueError(
                    f"{TOOL}: view_images.{view} is a {drawn} image; render it with "
                    f"twin.render_concept_image view '{view}'"
                )
            if cond.get("layout_content_hash"):
                layouts.add(str(cond["layout_content_hash"]))
            out[view] = (wp, image, mime)
        if len(layouts) > 1:
            raise ValueError(f"{TOOL}: the view images were drawn from different layouts")
        return out

    async def _envelope_of(source: Any) -> list[float] | None:
        """The block layout envelope an image was conditioned on, in mm."""
        cond = _conditioned_on(source)
        stored = cond.get("layout_overall_mm")
        if isinstance(stored, list) and len(stored) == 3:
            try:
                return [float(v) for v in stored]
            except (TypeError, ValueError):
                pass
        meta = getattr(source, "metadata", None) or {}
        sketch_ref = cond.get("layout_node_id") or meta.get("source_work_product_id")
        if not sketch_ref:
            return None
        try:
            sketch = await twin.get_work_product(UUID(str(sketch_ref)))
        except Exception:  # noqa: BLE001 - no envelope, no check
            return None
        if sketch is None:
            return None
        return _envelope_from_layout((sketch.metadata or {}).get("concept_layout"))

    async def generate(
        *,
        image_node_id: str | None = None,
        prompt: str | None = None,
        view_images: dict[str, str] | None = None,
        name: str | None = None,
        project_id: str | None = None,
        domain: str = "mechanical",
        auto_size: bool | None = None,
        face_limit: int | None = None,
        texture: bool | None = None,
        generate_parts: bool = False,
        smart_low_poly: bool = False,
        fit_to_envelope: bool | None = None,
        validate_only: bool = False,
    ) -> dict[str, Any]:
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        # Everything that can be refused is refused before any paid call.
        given = [v for v in (image_node_id, prompt, view_images) if v is not None]
        if len(given) != 1:
            raise ValueError(
                f"{TOOL}: give exactly one of 'image_node_id', 'view_images' or 'prompt'"
            )
        if fit_to_envelope is not None and not isinstance(fit_to_envelope, bool):
            raise ValueError(f"{TOOL}: 'fit_to_envelope' must be true or false")
        # auto_size is checked here and settled once the envelope is known.
        options, options_note = generation_options(
            auto_size=True if auto_size is None else auto_size,
            face_limit=face_limit,
            texture=texture,
            generate_parts=generate_parts,
            smart_low_poly=smart_low_poly,
        )
        source: Any = None
        image: bytes | None = None
        mime = ""
        cropped = False
        text: str | None = None
        views: dict[str, tuple[Any, bytes, str]] = {}
        view_cropped: dict[str, bool] = {}
        if image_node_id is not None:
            if not isinstance(image_node_id, str) or not image_node_id.strip():
                raise ValueError(f"{TOOL}: 'image_node_id' must be a non-empty string")
            source, raw, mime = await _source_image(image_node_id)
            image, mime, cropped = strip_label_band(raw, mime)
        elif view_images is not None:
            for view, (wp, raw, raw_mime) in (await _view_sources(view_images)).items():
                sent, sent_mime, was_cropped = strip_label_band(raw, raw_mime)
                views[view] = (wp, sent, sent_mime)
                view_cropped[view] = was_cropped
            source = views["front"][0]
            cropped = all(view_cropped.values())
        else:
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f"{TOOL}: 'prompt' must be a non-empty string")
            text = prompt.strip()
            if len(text) > MAX_PROMPT_CHARS:
                raise ValueError(f"{TOOL}: 'prompt' is longer than {MAX_PROMPT_CHARS} characters")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError(f"{TOOL}: 'name' must be a non-empty string")
        if source is not None and project_id is None and getattr(source, "project_id", None):
            project_id = str(source.project_id)

        concept = ""
        if source is not None:
            from api_gateway.twin.image_critique import concept_name_of

            concept = concept_name_of(source)
        if views:
            mode, label = "multiview_to_model", "multiview"
        elif image is not None:
            mode, label = "image_to_model", "image"
        else:
            mode, label = "text_to_model", "text"
        envelope = await _envelope_of(source) if source is not None else None
        has_envelope = envelope is not None and all(v > 0 for v in envelope)
        # Our own fit to the envelope replaces the provider's auto_size; with
        # no envelope, auto_size is the only way to real units.
        options["auto_size"] = (not has_envelope) if auto_size is None else auto_size
        fit = has_envelope if fit_to_envelope is None else (fit_to_envelope and has_envelope)
        if validate_only:
            # Everything a paid call could be refused for, checked so a caller
            # starting a background job hears of it now and not on the job.
            if generator is None:
                try:
                    resolve_mesh_generator()
                except MeshGenerationUnavailableError as exc:
                    _record("unavailable", label)
                    logger.warning("concept_mesh_unavailable", reason=str(exc), mode=label)
                    raise
            return {"validated": True, "mode": mode, "fit_to_envelope": fit}

        with tracer.start_as_current_span(TOOL) as span:
            span.set_attribute("concept_mesh.mode", mode)
            span.set_attribute("concept_mesh.view_count", len(views))
            try:
                gen = generator or resolve_mesh_generator()
            except MeshGenerationUnavailableError as exc:
                span.record_exception(exc)
                _record("unavailable", label)
                logger.warning("concept_mesh_unavailable", reason=str(exc), mode=label)
                raise

            limit = timeout_s if timeout_s is not None else timeout_from_env()
            view_payload = {v: (b, m) for v, (_wp, b, m) in views.items()} or None
            try:
                mesh: GeneratedMesh = await asyncio.wait_for(
                    gen(image, mime, text, views=view_payload, options=options),
                    timeout=limit + _GRACE_S,
                )
            except (MeshGenerationTimeoutError, TimeoutError) as exc:
                span.record_exception(exc)
                _record("timeout", label)
                logger.warning("concept_mesh_timeout", timeout_s=limit, error=str(exc), mode=label)
                if isinstance(exc, MeshGenerationTimeoutError):
                    raise
                raise MeshGenerationTimeoutError(
                    f"the mesh provider did not finish in {limit:g}s"
                ) from exc
            except MeshGenerationUnavailableError as exc:
                span.record_exception(exc)
                _record("unavailable", label)
                logger.warning("concept_mesh_unavailable", reason=str(exc), mode=label)
                raise
            except Exception as exc:
                span.record_exception(exc)
                _record("failed", label)
                logger.warning("concept_mesh_failed", error=str(exc), mode=label)
                if isinstance(exc, MeshGenerationError):
                    raise
                raise MeshGenerationError(f"mesh generation failed: {exc}") from exc
            if not mesh.glb_bytes:
                _record("failed", label)
                raise MeshGenerationError("the mesh provider returned an empty model")
            span.set_attribute("concept_mesh.task_id", mesh.task_id)
            span.set_attribute("concept_mesh.size_bytes", len(mesh.glb_bytes))

            sent_options = dict(mesh.options) if mesh.options else {}
            size_check, stored_glb = _size_and_fit(
                mesh.glb_bytes,
                envelope if has_envelope else None,
                metres=bool(sent_options.get("auto_size")),
                fit=fit,
            )
            if "deviation_pct" in size_check:
                span.set_attribute(
                    "concept_mesh.max_deviation_pct",
                    max(abs(v) for v in size_check["deviation_pct"].values() if v is not None),
                )
            if "scale_factor" in size_check:
                span.set_attribute("concept_mesh.scale_factor", size_check["scale_factor"])

            content_hash = hashlib.sha256(stored_glb).hexdigest()
            wp_id = uuid4()
            stem = f"{_slug(concept or 'concept')}-reference-mesh"
            filename = f"{stem}.glb"
            raw_record: dict[str, Any] = {}
            if stored_glb is not mesh.glb_bytes:
                # The provider's own file, kept beside the scaled one.
                raw_name = f"{stem}.provider.glb"
                try:
                    raw_key = await asyncio.to_thread(
                        write_blob, str(wp_id), raw_name, mesh.glb_bytes, content_type=GLB_MIME
                    )
                    raw_record = {
                        "provider_glb_object_key": raw_key,
                        "provider_glb_filename": raw_name,
                        "provider_glb_sha256": hashlib.sha256(mesh.glb_bytes).hexdigest(),
                        "provider_glb_size_bytes": len(mesh.glb_bytes),
                    }
                except Exception as exc:  # noqa: BLE001 - the scaled mesh is what is stored
                    logger.warning("concept_mesh_provider_glb_store_failed", error=str(exc))
                    raw_record = {"provider_glb_note": f"the provider's file was not kept ({exc})"}
            try:
                key = await asyncio.to_thread(
                    write_blob, str(wp_id), filename, stored_glb, content_type=GLB_MIME
                )
            except Exception as exc:  # noqa: BLE001 - refuse rather than store a node with no mesh
                span.record_exception(exc)
                _record("store_failed", label)
                raise MeshGenerationError(
                    f"the mesh was generated (task {mesh.task_id}) but could not be stored ({exc})"
                ) from exc
            size_check.update(raw_record)

            source_id = str(source.id) if source is not None else None
            view_ids = {v: str(wp.id) for v, (wp, _b, _m) in views.items()}
            metadata: dict[str, Any] = {
                "original_filename": filename,
                "minio_object_key": key,
                "content_sha256": content_hash,
                # Never evidence, never CAD: its dimensions are a model's guess.
                "authority": "illustrative",
                "illustrative_label": LABEL,
                "provider": mesh.provider,
                "model_version": mesh.model_version,
                "task_id": mesh.task_id,
                "mode": mesh.mode,
                "generated_by": TOOL,
                "size_bytes": len(stored_glb),
                "generation_options": sent_options,
                **size_check,
            }
            if options_note:
                metadata["generation_options_note"] = options_note
            if source is not None:
                metadata["source_image_node_id"] = source_id
                metadata["source_work_product_id"] = source_id
                metadata["source_content_hash"] = source.content_hash
                metadata["label_band_removed"] = cropped
                if concept:
                    metadata["concept_name"] = concept
            if views:
                metadata["view_image_node_ids"] = view_ids
                metadata["label_band_removed_views"] = view_cropped
            if text is not None:
                metadata["prompt"] = text
            now = datetime.now(UTC)
            wp_name = name.strip() if name else f"Reference model: {concept or 'concept'}"
            created = await twin.create_work_product(
                WorkProduct(
                    id=wp_id,
                    name=wp_name,
                    type=WorkProductType.RENDER,
                    domain=domain,
                    file_path="",
                    content_hash=content_hash,
                    format="glb",
                    metadata=metadata,
                    created_at=now,
                    updated_at=now,
                    created_by=TOOL,
                    project_id=project_id,
                )
            )
            node_id = str(getattr(created, "id", wp_id))
            if views:
                for wp, _b, _m in views.values():
                    await twin.add_edge(created.id, wp.id, EdgeType.GENERATED_FROM)
            elif source is not None:
                await twin.add_edge(created.id, source.id, EdgeType.GENERATED_FROM)

            project_linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(project_id, node_id, wp_name, "render")
                    project_linked = True
                except Exception as exc:  # noqa: BLE001 - the node exists; say the link failed
                    logger.warning("concept_mesh_project_link_failed", error=str(exc))

            _record("generated", label)
            logger.info(
                "concept_mesh_generated",
                node_id=node_id,
                provider=mesh.provider,
                model_version=mesh.model_version,
                task_id=mesh.task_id,
                mode=mesh.mode,
                size_bytes=len(stored_glb),
                source_image_node_id=source_id,
                view_image_node_ids=view_ids or None,
                bbox_m=size_check.get("bbox_m"),
                deviation_pct=size_check.get("deviation_pct"),
                scale_factor=size_check.get("scale_factor"),
                proportion_error_pct=size_check.get("proportion_error_pct"),
                project_id=project_id,
            )
            out: dict[str, Any] = {
                "node_id": node_id,
                "format": "glb",
                "mode": mesh.mode,
                "provider": mesh.provider,
                "model_version": mesh.model_version,
                "task_id": mesh.task_id,
                "size_bytes": len(stored_glb),
                "source_image_node_id": source_id,
                "authority": "illustrative",
                "label": LABEL,
                "content_hash": content_hash,
                "minio_object_key": key,
                "project_linked": project_linked,
                "generation_options": sent_options,
                **size_check,
            }
            if views:
                out["view_image_node_ids"] = view_ids
            if options_note:
                out["generation_options_note"] = options_note
            return out

    return generate
