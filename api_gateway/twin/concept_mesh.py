"""AI 3D reference models from a concept image (FORGE-778).

``twin.render_concept_image`` makes the picture of a concept. This turns that
picture (or a text prompt) into a 3D mesh with an image-to-3D model, so a
reviewer can turn the concept around. It is for concept review only.

The mesh is a ``render`` work product in GLB with ``metadata.authority =
"illustrative"``, linked ``generated_from`` the image it was made from. It is
never a ``cad_model``: its dimensions are the model's guess, not the layout's,
and the claim recorder refuses it as the artefact of a requirement claim like
every render. It records the provider, its task id and model version, and
carries the label "AI 3D reference model, illustrative only".

The image sent is the stored concept image with its label banner cropped off
when Pillow can (a banner would be modelled as part of the product); the
record says whether it was.
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

from api_gateway.twin.mesh_generation import (
    MAX_PROMPT_CHARS,
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

_metrics: Any = None


def _record(outcome: str) -> None:
    global _metrics  # noqa: PLW0603
    try:
        if _metrics is None:
            from observability.metrics import collector_for

            _metrics = collector_for("metaforge-gateway")
        _metrics.record_concept_mesh(outcome)
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

    async def _source_image(node_ref: str) -> tuple[Any, bytes, str]:
        from api_gateway.twin.image_critique import stored_image_bytes

        try:
            node_id = UUID(node_ref.strip())
        except ValueError as exc:
            raise ValueError(f"{TOOL}: 'image_node_id' is not a node id: {node_ref!r}") from exc
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

    async def generate(
        *,
        image_node_id: str | None = None,
        prompt: str | None = None,
        name: str | None = None,
        project_id: str | None = None,
        domain: str = "mechanical",
    ) -> dict[str, Any]:
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        # Everything that can be refused is refused before any paid call.
        if (image_node_id is None) == (prompt is None):
            raise ValueError(f"{TOOL}: give exactly one of 'image_node_id' or 'prompt'")
        source: Any = None
        image: bytes | None = None
        mime = ""
        cropped = False
        text: str | None = None
        if image_node_id is not None:
            if not isinstance(image_node_id, str) or not image_node_id.strip():
                raise ValueError(f"{TOOL}: 'image_node_id' must be a non-empty string")
            source, raw, mime = await _source_image(image_node_id)
            image, mime, cropped = strip_label_band(raw, mime)
            if project_id is None and getattr(source, "project_id", None):
                project_id = str(source.project_id)
        else:
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f"{TOOL}: 'prompt' must be a non-empty string")
            text = prompt.strip()
            if len(text) > MAX_PROMPT_CHARS:
                raise ValueError(f"{TOOL}: 'prompt' is longer than {MAX_PROMPT_CHARS} characters")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError(f"{TOOL}: 'name' must be a non-empty string")

        concept = ""
        if source is not None:
            from api_gateway.twin.image_critique import concept_name_of

            concept = concept_name_of(source)
        mode = "image_to_model" if image is not None else "text_to_model"

        with tracer.start_as_current_span(TOOL) as span:
            span.set_attribute("concept_mesh.mode", mode)
            try:
                gen = generator or resolve_mesh_generator()
            except MeshGenerationUnavailableError as exc:
                span.record_exception(exc)
                _record("unavailable")
                logger.warning("concept_mesh_unavailable", reason=str(exc))
                raise

            limit = timeout_s if timeout_s is not None else timeout_from_env()
            try:
                mesh: GeneratedMesh = await asyncio.wait_for(
                    gen(image, mime, text), timeout=limit + _GRACE_S
                )
            except (MeshGenerationTimeoutError, TimeoutError) as exc:
                span.record_exception(exc)
                _record("timeout")
                logger.warning("concept_mesh_timeout", timeout_s=limit, error=str(exc))
                if isinstance(exc, MeshGenerationTimeoutError):
                    raise
                raise MeshGenerationTimeoutError(
                    f"the mesh provider did not finish in {limit:g}s"
                ) from exc
            except MeshGenerationUnavailableError as exc:
                span.record_exception(exc)
                _record("unavailable")
                logger.warning("concept_mesh_unavailable", reason=str(exc))
                raise
            except Exception as exc:
                span.record_exception(exc)
                _record("failed")
                logger.warning("concept_mesh_failed", error=str(exc))
                if isinstance(exc, MeshGenerationError):
                    raise
                raise MeshGenerationError(f"mesh generation failed: {exc}") from exc
            if not mesh.glb_bytes:
                _record("failed")
                raise MeshGenerationError("the mesh provider returned an empty model")
            span.set_attribute("concept_mesh.task_id", mesh.task_id)
            span.set_attribute("concept_mesh.size_bytes", len(mesh.glb_bytes))

            content_hash = hashlib.sha256(mesh.glb_bytes).hexdigest()
            wp_id = uuid4()
            filename = f"{_slug(concept or 'concept')}-reference-mesh.glb"
            try:
                key = await asyncio.to_thread(
                    write_blob, str(wp_id), filename, mesh.glb_bytes, content_type=GLB_MIME
                )
            except Exception as exc:  # noqa: BLE001 - refuse rather than store a node with no mesh
                span.record_exception(exc)
                _record("store_failed")
                raise MeshGenerationError(
                    f"the mesh was generated (task {mesh.task_id}) but could not be stored ({exc})"
                ) from exc

            source_id = str(source.id) if source is not None else None
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
                "size_bytes": len(mesh.glb_bytes),
            }
            if source is not None:
                metadata["source_image_node_id"] = source_id
                metadata["source_work_product_id"] = source_id
                metadata["source_content_hash"] = source.content_hash
                metadata["label_band_removed"] = cropped
                if concept:
                    metadata["concept_name"] = concept
            else:
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
            if source is not None:
                await twin.add_edge(created.id, source.id, EdgeType.GENERATED_FROM)

            project_linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(project_id, node_id, wp_name, "render")
                    project_linked = True
                except Exception as exc:  # noqa: BLE001 - the node exists; say the link failed
                    logger.warning("concept_mesh_project_link_failed", error=str(exc))

            _record("generated")
            logger.info(
                "concept_mesh_generated",
                node_id=node_id,
                provider=mesh.provider,
                model_version=mesh.model_version,
                task_id=mesh.task_id,
                mode=mesh.mode,
                size_bytes=len(mesh.glb_bytes),
                source_image_node_id=source_id,
                project_id=project_id,
            )
            return {
                "node_id": node_id,
                "format": "glb",
                "provider": mesh.provider,
                "model_version": mesh.model_version,
                "task_id": mesh.task_id,
                "size_bytes": len(mesh.glb_bytes),
                "source_image_node_id": source_id,
                "authority": "illustrative",
                "label": LABEL,
                "content_hash": content_hash,
                "minio_object_key": key,
                "project_linked": project_linked,
            }

    return generate
