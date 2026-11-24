"""Component photos kept as evidence (FORGE-924, R2).

A photo-based model is only reviewable if the photo it was read from is kept: the
exact bytes, where they came from, under what terms, and which view of the part they
show. ``twin.record_component_photo`` stores one photo (an ``image.fetch`` image, or
bytes a person uploaded) as a ``component_photo`` work product:

* origin (the source url, or who uploaded it), sha256, type and size in pixels;
* the view: ``top``, ``side``, ``angled``, ``bottom`` or ``unclear``;
* a licence notice. A distributor's or maker's photo is copyrighted: it is kept as
  evidence only and never redistributed, and the record says so;
* the component it shows, by library entry or order code.

A photo is evidence of what a part looks like, never of what the design meets: it is
illustrative (``authority: illustrative``), so ``twin.record_claim`` refuses it as the
artefact of a requirement claim. The same photo recorded twice in one project is the
same record. Saving one asks nobody: it adds a record and changes no design state.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.component_photo")

VIEWS: tuple[str, ...] = ("top", "side", "angled", "bottom", "unclear")

#: Said on every photo whose licence nobody stated: the default is the safe one.
DEFAULT_LICENCE_NOTICE = (
    "Copyright its owner (the maker or distributor that published it). Kept as evidence "
    "of the part's appearance and size only; not for redistribution, and left out of "
    "exports."
)

_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


class ComponentPhotoError(ValueError):
    """A photo that cannot be recorded; the message says why."""


_collector: Any = None


def _count(outcome: str) -> None:
    global _collector  # noqa: PLW0603
    try:
        if _collector is None:
            from observability.metrics import collector_for

            _collector = collector_for("metaforge-gateway")
        _collector.record_component_thread("component_photo", outcome)
    except Exception:  # noqa: BLE001 - a metric never fails a record
        pass


def make_component_photo_recorder(
    twin: Any,
    project_backend: Any = None,
    *,
    blob_writer: Callable[..., str] | None = None,
) -> Any:
    """Return an async ``record(...)`` that stores one component photo as evidence.

    ``blob_writer(node_id, filename, content, content_type=...) -> key`` defaults to
    MinIO, like every other work-product file, and is injectable for tests.
    """

    def _default_writer(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        from digital_twin.storage.work_product_blobs import store_work_product_blob

        key: str = store_work_product_blob(node_id, filename, content, content_type=content_type)
        return key

    write_blob = blob_writer or _default_writer

    async def record(
        *,
        project_id: str,
        data: bytes,
        mime: str,
        width_px: int,
        height_px: int,
        view: str,
        origin: str,
        component: str = "",
        licence_notice: str = "",
        retrieved_at: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        with tracer.start_as_current_span("twin.record_component_photo") as span:
            if view not in VIEWS:
                _count("refused")
                raise ComponentPhotoError(f"view is one of {', '.join(VIEWS)}")
            if mime not in _EXT:
                _count("refused")
                raise ComponentPhotoError("a photo is a JPEG, PNG, WebP or GIF image")
            if not origin.strip():
                _count("refused")
                raise ComponentPhotoError(
                    "origin is required: the url the photo came from, or who uploaded it"
                )
            try:
                pid = UUID(str(project_id))
            except ValueError as exc:
                _count("refused")
                raise ComponentPhotoError("project_id must be a project's UUID") from exc
            sha = hashlib.sha256(data).hexdigest()
            span.set_attribute("photo.sha256", sha)
            existing = await _existing(twin, pid, sha)
            if existing is not None:
                _count("reused")
                return {**_summary(existing), "reused": True}
            wp_id = uuid4()
            filename = f"component-photo-{sha[:12]}.{_EXT[mime]}"
            key = await asyncio.to_thread(write_blob, str(wp_id), filename, data, content_type=mime)
            name = f"Photo: {component or 'component'} ({view})"
            now = datetime.now(UTC)
            metadata: dict[str, Any] = {
                "original_filename": filename,
                "minio_object_key": key,
                "content_sha256": sha,
                "image_files": {filename: key},
                "image_ref": f"image:{sha}",
                "mime": mime,
                "width_px": int(width_px),
                "height_px": int(height_px),
                "view": view,
                "origin": origin.strip(),
                "retrieved_at": retrieved_at or now.isoformat(timespec="seconds"),
                "component": component.strip(),
                "licence_notice": licence_notice.strip() or DEFAULT_LICENCE_NOTICE,
                "redistributable": False,
                # Evidence of appearance and size, never of what the design meets.
                "authority": "illustrative",
                "notes": notes.strip(),
            }
            created = await twin.create_work_product(
                WorkProduct(
                    id=wp_id,
                    name=name,
                    type=WorkProductType.COMPONENT_PHOTO,
                    domain="mechanical",
                    file_path="",
                    content_hash=sha,
                    format=_EXT[mime],
                    metadata=metadata,
                    created_at=now,
                    updated_at=now,
                    created_by="twin.record_component_photo",
                    project_id=pid,
                )
            )
            if project_backend is not None:
                try:
                    await project_backend.link_work_product(
                        str(pid), str(created.id), name, "component_photo"
                    )
                except Exception as exc:  # noqa: BLE001 - the record exists; say the link failed
                    logger.warning("component_photo_project_link_failed", error=str(exc))
            _count("recorded")
            logger.info(
                "component_photo_recorded",
                project_id=str(pid),
                node_id=str(created.id),
                view=view,
                sha256=sha[:12],
            )
            return {**_summary(created), "reused": False}

    return record


async def _existing(twin: Any, project_id: UUID, sha: str) -> Any:
    from twin_core.models.enums import WorkProductType

    try:
        found = await twin.list_work_products(
            work_product_type=WorkProductType.COMPONENT_PHOTO, project_id=project_id
        )
    except Exception:  # noqa: BLE001 - a lookup that fails records a new photo
        return None
    return next((wp for wp in found if getattr(wp, "content_hash", "") == sha), None)


def _summary(wp: Any) -> dict[str, Any]:
    meta = dict(getattr(wp, "metadata", None) or {})
    return {
        "node_id": str(wp.id),
        "name": wp.name,
        "image_ref": meta.get("image_ref"),
        "sha256": meta.get("content_sha256"),
        "view": meta.get("view"),
        "origin": meta.get("origin"),
        "component": meta.get("component"),
        "licence_notice": meta.get("licence_notice"),
        "redistributable": meta.get("redistributable", False),
    }
