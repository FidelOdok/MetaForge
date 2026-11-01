"""Render a twin work product with Blender and keep the images (FORGE-630).

``blender.render`` (FORGE-628) renders a mesh. A designer wants to see a
*work product*: the part or assembly in the twin. This closes that gap:

1. resolve the work product's stored blob (MinIO first, then the local file);
2. turn it into a mesh Blender can read. A STEP goes through the same OCCT
   converter the dashboard viewer uses, so a render shows exactly what the
   viewer shows, STEP colours and part names included. A mesh is used as is;
3. call ``blender.render`` over MCP (never Blender directly);
4. store the PNGs as a ``render`` work product: the first view is its
   primary blob, the others are named blobs in ``metadata.image_files``. It
   is linked to the model by a GENERATED_FROM edge and records the model's
   content hash, so a render of an older revision is recognisably stale;
5. return the images inline as well, so a harness can show them in chat
   (the MCP server sends them as image content, FORGE-373).

A part can be shown in a library **appearance** (FORGE-632): ``appearances``
maps a part name to an appearance's name or id, looked up with
``library.get_appearance`` over the same bridge. Its sourced screen colour
and its render roughness and metallic go to Blender, and the render records
which appearance, at which version, it used, so a later change to the
appearance shows the render is out of date. An appearance that does not
exist is refused; one with an assumed value says so in ``needs_review``.

Nothing falls back quietly. A converter that is down produces a stub GLB
with no geometry (``ConversionService._write_fallback``); rendering that
would store a picture of nothing, so it is refused, and the stub is dropped
from the conversion cache so it cannot be served later either. Storage that
fails refuses the render rather than recording a node with no images.

Mirrors ``interference_check``'s shape: a factory closure over the twin and
a lazily bound ``mcp_bridge``, wired in the gateway and the MCP sidecar.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.render_recorder")

#: What Blender imports directly (matches the adapter's MESH_FORMATS).
MESH_FORMATS = frozenset({"glb", "gltf", "stl", "obj", "ply"})
#: What the OCCT converter turns into GLB first.
CAD_FORMATS = frozenset({"step", "stp"})

#: Render options passed through to ``blender.render`` unchanged.
_PASS_THROUGH = (
    "views",
    "turntable_frames",
    "resolution",
    "samples",
    "projection",
    "transparent",
    "background",
    "colours",
)

#: A STEP of a large assembly can take a while to tessellate and render.
_RENDER_TIMEOUT_S = 900


class RenderError(ValueError):
    """This work product could not be rendered; the message says why."""


def _hex_to_rgba(value: str) -> list[float]:
    """``#RRGGBB`` as sRGB [r, g, b, 1] in 0..1 (what blender.render takes)."""
    return [int(value[i : i + 2], 16) / 255 for i in (1, 3, 5)] + [1.0]


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "render")[:60]


def _format_of(wp: Any, filename: str) -> str:
    fmt = str(getattr(wp, "format", "") or "").lower().lstrip(".")
    if fmt in MESH_FORMATS | CAD_FORMATS:
        return fmt
    return Path(filename).suffix.lower().lstrip(".")


def convert_step_to_glb(content: bytes, filename: str) -> bytes:
    """The GLB the dashboard viewer would show for this STEP (cached by hash).

    Raises :class:`RenderError` when the converter is unreachable (its stub
    has no geometry) or rejects the file.
    """
    from api_gateway.convert.service import ConversionError, ConversionService

    service = ConversionService()
    try:
        result = service.convert(content, filename, "standard")
    except ConversionError as exc:
        raise RenderError(f"the OCCT converter could not convert {filename}: {exc}") from exc
    file_hash = str(result["hash"])
    glb_path = service.get_glb_path(file_hash, "standard")
    glb = glb_path.read_bytes() if glb_path is not None else b""
    if (result.get("metadata") or {}).get("converter_unavailable") or not glb:
        # The stub would otherwise be served from the cache from now on.
        service.discard(file_hash, "standard")
        raise RenderError(
            "the OCCT converter is unreachable, so the STEP could not be turned into a "
            "mesh; nothing was rendered (check the occt-converter service)"
        )
    return glb


def make_work_product_renderer(
    twin: Any,
    project_backend: Any = None,
    *,
    mcp_bridge: Any,
    step_converter: Any = None,
    blob_writer: Any = None,
) -> Any:
    """Return an async ``render(*, work_product_id, ...) -> dict``.

    ``step_converter(content, filename) -> glb bytes`` and
    ``blob_writer(node_id, filename, content, content_type=...) -> key`` are
    injectable for tests; they default to the OCCT converter and MinIO.
    """

    convert = step_converter or convert_step_to_glb

    def _default_writer(node_id: str, filename: str, content: bytes, *, content_type: str) -> str:
        from digital_twin.storage.work_product_blobs import store_work_product_blob

        key: str = store_work_product_blob(node_id, filename, content, content_type=content_type)
        return key

    write_blob = blob_writer or _default_writer

    async def _appearance_surfaces(
        appearances: dict[str, str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Each part's appearance as a blender.render surface, and the record of it."""
        surfaces: dict[str, Any] = {}
        used: list[dict[str, Any]] = []
        for part, ref in appearances.items():
            try:
                found = await mcp_bridge.invoke("library.get_appearance", {"ref": str(ref)})
            except Exception as exc:
                raise RenderError(f"appearance {ref!r} for {part!r}: {exc}") from exc
            entry = (found or {}).get("entry") or {}
            colour = entry.get("colour") or {}
            if not colour.get("srgb_hex"):
                raise RenderError(f"appearance {ref!r} has no screen colour to render")
            surface: dict[str, Any] = {"rgba": _hex_to_rgba(str(colour["srgb_hex"]))}
            props = entry.get("properties") or {}
            review = ["colour"] if colour.get("needs_review") else []
            for key, field in (("render_roughness", "roughness"), ("render_metallic", "metallic")):
                if isinstance(props.get(key), dict):
                    surface[field] = float(props[key]["value"])
                    if props[key].get("needs_review"):
                        review.append(key)
            surfaces[str(part)] = surface
            used.append(
                {
                    "part": str(part),
                    "appearance": entry.get("name"),
                    "appearance_id": entry.get("id"),
                    "version": entry.get("version"),
                    "finish": entry.get("finish"),
                    "colour": " ".join(p for p in (colour.get("code"), colour.get("name")) if p),
                    "srgb_hex": colour.get("srgb_hex"),
                    "needs_review": review,
                }
            )
        return surfaces, used

    async def render(
        *,
        work_product_id: str,
        store: bool = True,
        name: str | None = None,
        project_id: str | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        from api_gateway.twin.blob_store import resolve_work_product_blob

        with tracer.start_as_current_span("twin.render_work_product") as span:
            span.set_attribute("twin.work_product_id", work_product_id)
            try:
                source_id = UUID(str(work_product_id))
            except ValueError as exc:
                raise RenderError(f"invalid work_product_id {work_product_id!r}") from exc
            wp = await twin.get_work_product(source_id)
            if wp is None:
                raise RenderError(f"no work product {work_product_id!r}")

            try:
                content, filename = resolve_work_product_blob(wp)
            except Exception as exc:  # noqa: BLE001 - HTTPException from the resolver
                detail = getattr(exc, "detail", None) or str(exc)
                raise RenderError(f"{wp.name!r} has no readable model file: {detail}") from exc

            appearances = options.pop("appearances", None) or {}
            if not isinstance(appearances, dict):
                raise RenderError("appearances must map a part name to an appearance name")
            surfaces, used = await _appearance_surfaces(appearances)
            clash = sorted(set(surfaces) & set(options.get("colours") or {}))
            if clash:
                raise RenderError(
                    f"{', '.join(clash)} given both a colour and an appearance; give one"
                )

            fmt = _format_of(wp, filename)
            span.set_attribute("render.source_format", fmt)
            if fmt in CAD_FORMATS:
                model = await asyncio.to_thread(convert, content, filename)
                model_format = "glb"
            elif fmt in MESH_FORMATS:
                model, model_format = content, fmt
            else:
                raise RenderError(
                    f"{wp.name!r} is a {fmt or 'format-less'} file; renders need a STEP "
                    f"({', '.join(sorted(CAD_FORMATS))}) or a mesh "
                    f"({', '.join(sorted(MESH_FORMATS))})"
                )

            arguments: dict[str, Any] = {
                key: options[key] for key in _PASS_THROUGH if options.get(key) is not None
            }
            if surfaces:
                arguments["colours"] = {**(arguments.get("colours") or {}), **surfaces}
            arguments["model_base64"] = base64.b64encode(model).decode("ascii")
            arguments["model_format"] = model_format
            if fmt in CAD_FORMATS:
                # The converter keeps CAD's Z-up axes in its GLB (the dashboard
                # viewer turns it the same way, FORGE-621).
                arguments["model_up_axis"] = "z"
            try:
                result = await mcp_bridge.invoke(
                    "blender.render", arguments, timeout=_RENDER_TIMEOUT_S
                )
            except Exception as exc:
                span.record_exception(exc)
                raise RenderError(f"blender.render failed: {exc}") from exc
            images = [i for i in (result.get("images") or []) if i.get("png_base64")]
            if not images:
                raise RenderError("blender.render returned no images")
            span.set_attribute("render.images", len(images))

            source_hash = (
                str(getattr(wp, "content_hash", "") or "") or hashlib.sha256(content).hexdigest()
            )
            settings = {
                "views": [str(i.get("view")) for i in images],
                "resolution": [images[0].get("width"), images[0].get("height")],
                "samples": result.get("samples"),
                "engine": result.get("engine"),
                "blender_version": result.get("blender_version"),
                "appearances": used,
                "unmatched_colours": result.get("unmatched_colours", []),
            }
            response: dict[str, Any] = {
                "source_work_product_id": str(source_id),
                "source_name": wp.name,
                "source_content_hash": source_hash,
                **settings,
                "coloured_objects": result.get("coloured_objects", []),
                "images": images,
                "stored": False,
            }
            if not store:
                logger.info(
                    "work_product_rendered", source=str(source_id), images=len(images), stored=False
                )
                return response

            node = await _store(
                wp,
                images,
                settings,
                source_hash=source_hash,
                name=name or f"{wp.name} renders",
                project_id=project_id or (str(wp.project_id) if wp.project_id else None),
            )
            response.update(node, stored=True)
            logger.info(
                "work_product_rendered",
                source=str(source_id),
                images=len(images),
                stored=True,
                node_id=node["node_id"],
            )
            return response

    async def _store(
        source: Any,
        images: list[dict[str, Any]],
        settings: dict[str, Any],
        *,
        source_hash: str,
        name: str,
        project_id: str | None,
    ) -> dict[str, Any]:
        from twin_core.models.enums import EdgeType, WorkProductType
        from twin_core.models.work_product import WorkProduct

        wp_id = uuid4()
        stem = _slug(source.name)
        files: list[tuple[str, bytes]] = [
            (f"{stem}-{_slug(str(i.get('view')))}.png", base64.b64decode(i["png_base64"]))
            for i in images
        ]
        keys: dict[str, str] = {}
        for filename, data in files:
            try:
                keys[filename] = await asyncio.to_thread(
                    write_blob, str(wp_id), filename, data, content_type="image/png"
                )
            except Exception as exc:  # noqa: BLE001 - refuse rather than store an empty node
                raise RenderError(
                    f"the renders could not be stored ({exc}); pass store=false to get them "
                    "inline without storing"
                ) from exc

        primary_name, primary = files[0]
        metadata: dict[str, Any] = {
            "original_filename": primary_name,
            "minio_object_key": keys[primary_name],
            "content_sha256": hashlib.sha256(primary).hexdigest(),
            "image_files": keys,
            "source_work_product_id": str(source.id),
            "source_content_hash": source_hash,
            "rendered_by": "blender.render",
            **settings,
        }
        now = datetime.now(UTC)
        created = await twin.create_work_product(
            WorkProduct(
                id=wp_id,
                name=name,
                type=WorkProductType.RENDER,
                domain=str(getattr(source, "domain", "") or "mechanical"),
                file_path="",
                content_hash=metadata["content_sha256"],
                format="png",
                metadata=metadata,
                created_at=now,
                updated_at=now,
                created_by="twin.render_work_product",
                project_id=project_id,
            )
        )
        node_id = str(getattr(created, "id", wp_id))
        await twin.add_edge(created.id, source.id, EdgeType.GENERATED_FROM)

        linked = False
        if project_id and project_backend is not None:
            # The Projects page reads this junction; the node's own
            # project_id serves the /twin filter. Both, or it shows in one.
            try:
                await project_backend.link_work_product(project_id, node_id, name, "render")
                linked = True
            except Exception as exc:  # noqa: BLE001 - the node exists; say the link failed
                logger.warning("render_project_link_failed", node_id=node_id, error=str(exc))
        return {
            "node_id": node_id,
            "files": list(keys),
            "project_id": project_id,
            "project_linked": linked,
        }

    return render
