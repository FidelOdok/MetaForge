"""``/v1/library/processes`` and ``/v1/library/materials`` (FORGE-451, FORGE-446).

Manufacturing techniques and materials as library data, in the same store
file as the machine library. Over MCP the writes are held for approval like
any other write; here the caller is a person or a service acting for one.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from twin_core.library.catalog import CatalogError, CatalogNotFoundError, SqliteCatalog

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


def _list(kind: str, family: str | None) -> dict[str, Any]:
    entries = get_catalog().list(kind, family=family or None)
    return {"entries": [e.as_dict() for e in entries]}


def _get(kind: str, ref: str) -> dict[str, Any]:
    catalog = get_catalog()
    try:
        entry = catalog.get(ref, kind)
    except CatalogNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no {kind} '{ref}'") from exc
    return {"entry": entry.as_dict(), "history": catalog.history(entry.id)}


def _register(kind: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    by = _by(request, str(body.pop("by", "") or ""))
    with tracer.start_as_current_span(f"library.register_{kind}") as span:
        try:
            entry = get_catalog().register(kind, body, by=by)
        except CatalogError as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        span.set_attribute("entry.id", entry.id)
    logger.info("catalog_entry_registered", kind=kind, entry_id=entry.id, name=entry.name, by=by)
    return {"entry": entry.as_dict()}


def _update(kind: str, ref: str, body: UpdateEntryRequest, request: Request) -> dict[str, Any]:
    by = _by(request, body.by)
    catalog = get_catalog()
    try:
        current = catalog.get(ref, kind)
        entry = catalog.update(current.id, body.changes, by=by)
    except CatalogNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no {kind} '{ref}'") from exc
    except CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"entry": entry.as_dict()}


@router.get("/processes")
async def list_processes(family: str | None = None) -> dict[str, Any]:
    return _list("process", family)


@router.get("/processes/{ref}")
async def get_process(ref: str) -> dict[str, Any]:
    return _get("process", ref)


@router.get("/processes/{ref}/limits")
async def process_limits(ref: str, material: str | None = None) -> dict[str, Any]:
    """The process's rules as DFM limits and a ranking candidate, optionally for a material."""
    try:
        return get_catalog().process_limits(ref, material or None)
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
async def list_materials(family: str | None = None) -> dict[str, Any]:
    return _list("material", family)


@router.get("/materials/resolve")
async def resolve_material(name: str) -> dict[str, Any]:
    """The figures a tool call carries for ``name`` (FORGE-446); 422 with candidates."""
    from twin_core.library.material_resolver import MaterialResolutionError, block, match

    try:
        return block(match(get_catalog().list("material"), name))
    except MaterialResolutionError as exc:
        raise HTTPException(
            status_code=422, detail={"message": str(exc), "candidates": exc.candidates}
        ) from exc


@router.get("/materials/{ref}")
async def get_material(ref: str) -> dict[str, Any]:
    return _get("material", ref)


@router.post("/materials", status_code=201)
async def register_material(body: dict[str, Any], request: Request) -> dict[str, Any]:
    return _register("material", body, request)


@router.patch("/materials/{ref}")
async def update_material(ref: str, body: UpdateEntryRequest, request: Request) -> dict[str, Any]:
    return _update("material", ref, body, request)


@router.get("/components")
async def list_components(family: str | None = None) -> dict[str, Any]:
    """Mechanical components (FORGE-449): standard and purchased parts."""
    return _list("component", family)


@router.get("/components/standard")
async def standard_part(family: str, size: str, length_mm: float | None = None) -> dict[str, Any]:
    """A standard part built from its standard's table; stores nothing."""
    from twin_core.library.standard_parts import StandardPartError, build_standard_part

    try:
        return {"entry": build_standard_part(family, size, length_mm)}
    except StandardPartError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/components/{ref}")
async def get_component(ref: str) -> dict[str, Any]:
    return _get("component", ref)


@router.post("/components", status_code=201)
async def register_component(body: dict[str, Any], request: Request) -> dict[str, Any]:
    return _register("component", body, request)


@router.patch("/components/{ref}")
async def update_component(ref: str, body: UpdateEntryRequest, request: Request) -> dict[str, Any]:
    return _update("component", ref, body, request)


@router.get("/appearances")
async def list_appearances(family: str | None = None) -> dict[str, Any]:
    """Appearances (FORGE-631): colour, material and finish; family is the finish."""
    return _list("appearance", family)


@router.get("/appearances/{ref}")
async def get_appearance(ref: str) -> dict[str, Any]:
    return _get("appearance", ref)


@router.post("/appearances", status_code=201)
async def register_appearance(body: dict[str, Any], request: Request) -> dict[str, Any]:
    return _register("appearance", body, request)


@router.patch("/appearances/{ref}")
async def update_appearance(ref: str, body: UpdateEntryRequest, request: Request) -> dict[str, Any]:
    return _update("appearance", ref, body, request)
