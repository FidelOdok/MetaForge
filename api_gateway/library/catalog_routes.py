"""``/v1/library/processes`` and ``/v1/library/materials`` (FORGE-451, FORGE-446).

Manufacturing techniques and materials as library data, in the same store
file as the machine library. Over MCP the writes are held for approval like
any other write; here the caller is a person or a service acting for one.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api_gateway.library.routes import history_with_authority
from api_gateway.library.tenancy import library_tenant, with_ownership
from observability.tracing import get_tracer
from twin_core.library.catalog import (
    CatalogError,
    CatalogNotFoundError,
    CatalogPermissionError,
    SqliteCatalog,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.library.catalog_routes")

router = APIRouter(prefix="/v1/library", tags=["library"])

AGENT_HEADER = "X-MetaForge-Agent"

_catalog: SqliteCatalog | None = None


def get_catalog() -> SqliteCatalog:
    """The process's catalog; an in-memory one until the lifespan installs the file."""
    global _catalog  # noqa: PLW0603
    if _catalog is None:
        _catalog = SqliteCatalog()
    return _catalog


def init_catalog(catalog: SqliteCatalog | None) -> None:
    """Install ``catalog`` (the lifespan), or ``None`` to start over (tests)."""
    global _catalog  # noqa: PLW0603
    _catalog = catalog


class UpdateEntryRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)
    by: str = ""


def _by(request: Request, named: str) -> str:
    return (named or request.headers.get(AGENT_HEADER) or "").strip()[:100] or "unknown"


def _shown(entry: Any) -> dict[str, Any]:
    return with_ownership(entry.as_dict(), get_catalog().owner_of(entry.id))


def _list(kind: str, family: str | None, request: Request) -> dict[str, Any]:
    entries = get_catalog().list(kind, family=family or None, viewer=library_tenant(request))
    return {"entries": [_shown(e) for e in entries]}


def _get(kind: str, ref: str, request: Request) -> dict[str, Any]:
    catalog = get_catalog()
    try:
        entry = catalog.get(ref, kind, viewer=library_tenant(request))
    except CatalogNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no {kind} '{ref}'") from exc
    return {"entry": _shown(entry), "history": history_with_authority(catalog.history(entry.id))}


def _register(kind: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    by = _by(request, str(body.pop("by", "") or ""))
    # FORGE-876: on a shared gateway the entry is the caller's account's own.
    owner = library_tenant(request)
    with tracer.start_as_current_span(f"library.register_{kind}") as span:
        span.set_attribute("entry.shared", owner is None)
        try:
            entry = get_catalog().register(kind, body, by=by, owner=owner)
        except CatalogError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        span.set_attribute("entry.id", entry.id)
    logger.info(
        "catalog_entry_registered",
        kind=kind,
        entry_id=entry.id,
        name=entry.name,
        by=by,
        shared=owner is None,
    )
    return {"entry": _shown(entry)}


def _update(kind: str, ref: str, body: UpdateEntryRequest, request: Request) -> dict[str, Any]:
    by = _by(request, body.by)
    catalog = get_catalog()
    editor = library_tenant(request)
    try:
        current = catalog.get(ref, kind, viewer=editor)
        entry = catalog.update(current.id, body.changes, by=by, editor=editor)
    except CatalogNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no {kind} '{ref}'") from exc
    except CatalogPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"entry": _shown(entry)}


@router.get("/processes")
async def list_processes(family: str | None = None, *, request: Request) -> dict[str, Any]:
    return _list("process", family, request)


@router.get("/processes/{ref}")
async def get_process(ref: str, request: Request) -> dict[str, Any]:
    return _get("process", ref, request)


@router.get("/processes/{ref}/limits")
async def process_limits(ref: str, request: Request, material: str | None = None) -> dict[str, Any]:
    """The process's rules as DFM limits and a ranking candidate, optionally for a material."""
    try:
        return get_catalog().process_limits(ref, material or None, viewer=library_tenant(request))
    except CatalogNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no entry '{exc.args[0]}'") from exc
    except CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/processes", status_code=201)
async def register_process(body: dict[str, Any], request: Request) -> dict[str, Any]:
    return _register("process", body, request)


@router.patch("/processes/{ref}")
async def update_process(ref: str, body: UpdateEntryRequest, request: Request) -> dict[str, Any]:
    return _update("process", ref, body, request)


@router.get("/materials")
async def list_materials(family: str | None = None, *, request: Request) -> dict[str, Any]:
    return _list("material", family, request)


@router.get("/materials/resolve")
async def resolve_material(name: str, request: Request) -> dict[str, Any]:
    """The figures a tool call carries for ``name`` (FORGE-446); 422 with candidates."""
    from twin_core.library.material_resolver import MaterialResolutionError, block, match

    try:
        materials = get_catalog().list("material", viewer=library_tenant(request))
        return block(match(materials, name))
    except MaterialResolutionError as exc:
        raise HTTPException(
            status_code=422, detail={"message": str(exc), "candidates": exc.candidates}
        ) from exc


@router.get("/materials/{ref}")
async def get_material(ref: str, request: Request) -> dict[str, Any]:
    return _get("material", ref, request)


@router.post("/materials", status_code=201)
async def register_material(body: dict[str, Any], request: Request) -> dict[str, Any]:
    return _register("material", body, request)


@router.patch("/materials/{ref}")
async def update_material(ref: str, body: UpdateEntryRequest, request: Request) -> dict[str, Any]:
    return _update("material", ref, body, request)


@router.get("/components")
async def list_components(family: str | None = None, *, request: Request) -> dict[str, Any]:
    """Mechanical components (FORGE-449): standard and purchased parts."""
    return _list("component", family, request)


@router.get("/components/standard")
async def standard_part(family: str, size: str, length_mm: float | None = None) -> dict[str, Any]:
    """A standard part built from its standard's table; stores nothing."""
    from twin_core.library.standard_parts import StandardPartError, build_standard_part

    try:
        return {"entry": build_standard_part(family, size, length_mm)}
    except StandardPartError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/components/{ref}")
async def get_component(ref: str, request: Request) -> dict[str, Any]:
    return _get("component", ref, request)


@router.get("/components/{ref}/mesh")
async def component_mesh(ref: str, request: Request) -> dict[str, Any]:
    """The entry's attached maker model as a coloured, Y-up, centred mesh (2026-11-12).

    For drawing in the studio and the library viewer only. A model whose licence MetaForge
    does not accept is shown for internal work, never handed to an engineering tool.
    """
    import asyncio

    from api_gateway.library import model_mesh

    try:
        entry = get_catalog().get(ref, "component", viewer=library_tenant(request)).as_dict()
    except CatalogNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return await asyncio.to_thread(
            model_mesh.make_mesh,
            entry,
            fetch=model_mesh.http_fetch,
            convert=model_mesh.occt_faces,
            cache_dir=model_mesh.cache_root(),
        )
    except model_mesh.MeshError as exc:
        logger.info("library_model_mesh_refused", ref=ref, reason=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/components", status_code=201)
async def register_component(body: dict[str, Any], request: Request) -> dict[str, Any]:
    return _register("component", body, request)


@router.patch("/components/{ref}")
async def update_component(ref: str, body: UpdateEntryRequest, request: Request) -> dict[str, Any]:
    return _update("component", ref, body, request)


@router.get("/appearances")
async def list_appearances(family: str | None = None, *, request: Request) -> dict[str, Any]:
    """Appearances (FORGE-631): colour, material and finish; family is the finish."""
    return _list("appearance", family, request)


@router.get("/appearances/{ref}")
async def get_appearance(ref: str, request: Request) -> dict[str, Any]:
    return _get("appearance", ref, request)


@router.post("/appearances", status_code=201)
async def register_appearance(body: dict[str, Any], request: Request) -> dict[str, Any]:
    return _register("appearance", body, request)


@router.patch("/appearances/{ref}")
async def update_appearance(ref: str, body: UpdateEntryRequest, request: Request) -> dict[str, Any]:
    return _update("appearance", ref, body, request)


@router.get("/ergonomic-limits")
async def list_ergonomic_limits(family: str | None = None, *, request: Request) -> dict[str, Any]:
    """Ergonomic design limits (FORGE-635); family is the feature kind they bound."""
    return _list("ergonomic_limit", family, request)


@router.get("/ergonomic-limits/{ref}")
async def get_ergonomic_limit(ref: str, request: Request) -> dict[str, Any]:
    return _get("ergonomic_limit", ref, request)


@router.post("/ergonomic-limits", status_code=201)
async def register_ergonomic_limit(body: dict[str, Any], request: Request) -> dict[str, Any]:
    return _register("ergonomic_limit", body, request)


@router.patch("/ergonomic-limits/{ref}")
async def update_ergonomic_limit(
    ref: str, body: UpdateEntryRequest, request: Request
) -> dict[str, Any]:
    return _update("ergonomic_limit", ref, body, request)


# -- the whole library as one file (2026-11-12) ------------------------------------------


@router.get("/export")
async def export_library(history: bool = False) -> dict[str, Any]:
    """Every current catalog entry and machine as one JSON document, to keep or move.

    ``history=true`` adds each entry's versions. Nothing secret is in it: a machine's
    connection names only the environment variable that holds its credential.
    """
    from api_gateway.library.routes import get_machine_library
    from twin_core.library.transfer import export_library as export

    with tracer.start_as_current_span("library.export") as span:
        doc = export(get_catalog(), get_machine_library(), history=history)
        span.set_attribute("library.entries", sum(doc["counts"].values()))
    return doc


@router.post("/import")
async def import_library(body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Add what an export holds and this library lacks; an entry already here is skipped."""
    from api_gateway.library.routes import get_machine_library
    from twin_core.library.transfer import import_library as load

    by = _by(request, str(body.pop("by", "") or "")) if isinstance(body, dict) else "unknown"
    with tracer.start_as_current_span("library.import") as span:
        try:
            report = load(body, get_catalog(), get_machine_library(), by=f"import by {by}")
        except ValueError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return report


@router.get(
    "/engine/{name}",
    response_class=Response,
    responses={200: {"content": {"text/javascript": {}, "text/css": {}}}},
)
async def get_engine_asset(name: str) -> Response:
    """The drawing engine (``authored.js``) and its assets, for drawing an entry's form code.

    The same files the Imagine studio loads; served here so a page that shows the library
    needs no project.
    """
    from api_gateway.imagine import engine_page

    if name not in engine_page.ASSETS:
        raise HTTPException(status_code=404, detail="No such engine asset")
    return Response(
        engine_page.asset_bytes(name),
        media_type=engine_page.asset_media_type(name),
        headers=engine_page.asset_headers(),
    )
