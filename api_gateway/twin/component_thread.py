"""Gather a project's component threads from the twin, the studio and the library (FORGE-903).

The joining is :mod:`twin_core.component_thread`; this module only reads. Every source
is best effort: one that fails leaves its stage empty and adds a warning, so a thread
is never refused because, say, the library is unreachable.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.component_thread import (
    BoardPart,
    MakeLine,
    Selection,
    StudioPart,
    build_threads,
    mpn_key,
    same_family,
    summary,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.component_thread")


#: Keys of a selection's specifications that describe the record, not the part.
_META_SPECS = frozenset(
    {
        "category",
        "purchase_unit",
        "role",
        "source",
        "session_id",
        "selection",
        "manufacturer_org",
        "supplier_org",
        "thread",
        "recorded_at",
        "library_entry",
    }
)


_collector: Any = None


def count(operation: str, outcome: str) -> None:
    """One component thread operation (FORGE-903); a metric never fails a request."""
    global _collector  # noqa: PLW0603
    try:
        if _collector is None:
            from observability.metrics import collector_for

            _collector = collector_for("metaforge-gateway")
        _collector.record_component_thread(operation, outcome)
    except Exception:  # noqa: BLE001
        pass


def _meta(node: Any) -> dict[str, Any]:
    return dict(getattr(node, "metadata", None) or {})


def _created(node: Any) -> str:
    for attr in ("created_at", "updated_at"):
        value = getattr(node, attr, None)
        if value is not None:
            return value.isoformat() if hasattr(value, "isoformat") else str(value)
    return ""


def library_components() -> list[dict[str, Any]]:
    """The component library the caller may see: ``{id, name, mpn, manufacturer, shared}``."""
    from api_gateway.library.catalog_routes import get_catalog

    catalog = get_catalog()
    out = []
    for entry in catalog.list("component"):
        data = entry.as_dict() if hasattr(entry, "as_dict") else dict(entry)
        if isinstance(data.get("shared"), bool):
            # The gateway's view (the MCP sidecar) says so itself.
            shared = bool(data["shared"])
        else:
            owner = catalog.owner_of(str(data.get("id"))) if hasattr(catalog, "owner_of") else ""
            shared = not owner
        out.append(
            {
                "id": data.get("id"),
                "name": data.get("name"),
                "mpn": data.get("mpn") or "",
                "manufacturer": data.get("manufacturer") or data.get("manufacturer_org") or "",
                "shared": shared,
            }
        )
    return out


#: The library writes a few units its own way; the requirement check reads pint units.
_LIBRARY_UNITS = {"C": "degC"}


def library_specs(entry_id: str) -> dict[str, Any]:
    """A library entry's numeric specs as ``{key: {value, unit}}``, for the requirement check.

    The selection's own specs win; these fill what the selection left out, so a part
    whose datasheet figures live in the library is still checked (FORGE-903).
    """
    from api_gateway.library.catalog_routes import get_catalog

    try:
        data = get_catalog().get(entry_id, "component").as_dict()
    except Exception:  # noqa: BLE001 - an entry that cannot be read has no specs
        return {}
    out: dict[str, Any] = {}
    for key, spec in (data.get("specs") or {}).items():
        if isinstance(spec, dict) and isinstance(spec.get("value"), (int, float)):
            unit = str(spec.get("unit") or "")
            out[key] = {"value": float(spec["value"]), "unit": _LIBRARY_UNITS.get(unit, unit)}
    return out


def library_for(mpn: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The library entry for an order code: the exact one first, else the family."""
    key = mpn_key(mpn)
    if not key:
        return None
    exact = [e for e in entries if mpn_key(e.get("mpn")) == key]
    family = [e for e in entries if same_family(e.get("mpn"), mpn)]
    # An account's own entry wins over a shared one, as the library itself does.
    for pool in (exact, family):
        if pool:
            pool.sort(key=lambda e: bool(e.get("shared")))
            e = pool[0]
            return {"id": e.get("id"), "name": e.get("name"), "shared": bool(e.get("shared"))}
    return None


def studio_parts_of(state: dict[str, Any]) -> list[StudioPart]:
    """The studio's parts, each marked chosen when the studio picks it for its role."""
    from api_gateway.imagine.service import _chosen_parts, _parts, _parts_in_use

    config = state.get("config") or {}
    cols = state.get("collections") or {}
    parts = _parts(config)
    try:
        chosen = {str(p.get("id")) for _, p in _parts_in_use(config, cols)}
        decided = set(_chosen_parts(config, cols))
    except Exception:  # noqa: BLE001 - a studio that cannot say treats every part as chosen
        chosen, decided = set(parts), set()

    def is_chosen(pid: str, p: dict[str, Any]) -> bool:
        # A part is an alternative only where the studio chooses one part for its role
        # and the form does not draw it; a part whose role the studio does not choose
        # for (an adopted "other") is in.
        if p.get("excluded"):
            return False
        return pid in chosen or str(p.get("role") or "") not in decided

    return [
        StudioPart(
            id=pid,
            name=str(p.get("name") or pid),
            role=str(p.get("role") or ""),
            mpn=str(p.get("mpn") or ""),
            manufacturer=str(p.get("manufacturer") or ""),
            library=str(p.get("library") or ""),
            chosen=is_chosen(pid, p),
        )
        for pid, p in parts.items()
    ]


async def studio_state(
    service: Any, project_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The studio head's state and ``{status, revision}``; ``(None, {})`` with no studio."""
    if service is None:
        return None, {}
    head = await service._head(project_id)
    if head is None:
        return None, {}
    return head.state, {"status": head.state.get("status") or "draft", "revision": head.revision}


def selection_of(
    item: Any,
    constraints: list[Any],
    library: list[dict[str, Any]],
    specs_of: Any = None,
) -> Selection:
    from twin_core.consistency.spec_margins import compare_specs_to_requirements

    specs = dict(getattr(item, "specifications", None) or {})
    raw_thread = specs.get("thread")
    thread: dict[str, Any] = raw_thread if isinstance(raw_thread, dict) else {}
    mpn = str(getattr(item, "part_number", "") or "")
    numbers: dict[str, Any] = {
        k: v
        for k, v in specs.items()
        if k not in _META_SPECS and isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    lib_ref = specs.get("library_entry") if isinstance(specs.get("library_entry"), dict) else None
    lib_ref = lib_ref or library_for(mpn, library)
    if specs_of is not None and lib_ref and lib_ref.get("id"):
        for key, value in specs_of(str(lib_ref["id"])).items():
            numbers.setdefault(key, value)
    margins: list[Any] = []
    unchecked: list[Any] = []
    if not numbers:
        # Typed requirements that wait on this part's ratings: none recorded yet.
        unchecked = [c for c in constraints if getattr(c, "metric", "")]
    elif constraints:
        try:
            margins, unchecked = compare_specs_to_requirements(numbers, constraints)
        except Exception as exc:  # noqa: BLE001 - a margin that fails is unchecked
            logger.warning("component_thread_margins_failed", mpn=mpn, error=str(exc))
    return Selection(
        node_id=str(getattr(item, "id", "")),
        mpn=mpn,
        manufacturer=str(getattr(item, "manufacturer", "") or ""),
        role=str(specs.get("role") or ""),
        selection=str(specs.get("selection") or "selected"),
        studio_parts=[str(p) for p in thread.get("studio_parts") or []],
        library=lib_ref,
        recorded_at=str(specs.get("recorded_at") or ""),
        datasheet=str(getattr(item, "datasheet_url", "") or ""),
        purchase_unit=str(specs.get("purchase_unit") or ""),
        violates=[m.requirement for m in margins if not m.satisfied],
        margins=len(margins),
        unchecked=len(unchecked),
    )


def _latest(nodes: list[Any]) -> Any | None:
    return max(nodes, key=_created) if nodes else None


def boards_of(arch: Any) -> tuple[list[str], list[BoardPart]]:
    if arch is None:
        return [], []
    boards: list[str] = []
    parts: list[BoardPart] = []
    for c in _meta(arch).get("components") or []:
        if not isinstance(c, dict) or c.get("kind") != "board":
            continue
        name = str(c.get("name") or "")
        boards.append(name)
        for p in c.get("parts") or []:
            if isinstance(p, dict) and p.get("ref"):
                parts.append(
                    BoardPart(
                        board=name,
                        ref=str(p["ref"]),
                        mpn=str(p.get("mpn") or ""),
                        name=str(p.get("name") or ""),
                    )
                )
    return boards, parts


def make_lines_of(boms: list[Any], orders: list[Any]) -> list[MakeLine]:
    out: list[MakeLine] = []
    # The newest BOM file per board (or per name when it names no board).
    newest: dict[str, Any] = {}
    for bom in boms:
        meta = _meta(bom)
        if not meta.get("bom_lines"):
            continue
        key = str((meta.get("designFiles") or {}).get("board") or meta.get("board") or bom.name)
        if key not in newest or _created(bom) >= _created(newest[key]):
            newest[key] = bom
    for key, bom in newest.items():
        meta = _meta(bom)
        board = str((meta.get("designFiles") or {}).get("board") or meta.get("board") or "")
        for line in meta.get("bom_lines") or []:
            if isinstance(line, dict):
                out.append(
                    MakeLine(
                        record=str(bom.id),
                        kind="bom",
                        part=str(line.get("part") or ""),
                        quantity=line.get("qty") or 1,
                        refs=[str(r) for r in line.get("refs") or []],
                        text=str(line.get("text") or ""),
                        board=board,
                    )
                )
    for order in orders:
        for li in _meta(order).get("line_items") or []:
            if isinstance(li, dict):
                out.append(
                    MakeLine(
                        record=str(order.id),
                        kind="procurement",
                        part=str(li.get("part_number") or ""),
                        quantity=li.get("quantity") or 1,
                        text=" ".join(
                            str(li.get(k) or "") for k in ("description", "manufacturer")
                        ),
                    )
                )
    return out


async def gather_threads(
    twin: Any, project_id: str, *, studio_service: Any = None
) -> dict[str, Any]:
    """``{project_id, studio, threads, summary, warnings}`` for one project."""
    from twin_core.component_identity import project_identity
    from twin_core.models.enums import WorkProductType

    warnings: list[str] = []
    pid = UUID(project_id)
    with tracer.start_as_current_span("component_thread.gather") as span:
        span.set_attribute("project_id", project_id)

        state: dict[str, Any] | None = None
        studio: dict[str, Any] = {}
        try:
            state, studio = await studio_state(studio_service, project_id)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"The Imagine studio could not be read: {exc}")
        parts = studio_parts_of(state) if state else []

        library: list[dict[str, Any]] = []
        try:
            library = library_components()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"The component library could not be read: {exc}")

        constraints: list[Any] = []
        try:
            constraints = list(await twin.list_constraints(project_id=pid))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"The requirements could not be read: {exc}")

        selections: list[Selection] = []
        try:
            items = await twin.list_bom_items(pid)
            memo: dict[str, dict[str, Any]] = {}

            def specs_of(entry_id: str) -> dict[str, Any]:
                if entry_id not in memo:
                    memo[entry_id] = library_specs(entry_id)
                return memo[entry_id]

            selections = [selection_of(i, constraints, library, specs_of) for i in items]
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"The component selections could not be read: {exc}")

        async def of_type(t: WorkProductType) -> list[Any]:
            try:
                return list(await twin.list_work_products(work_product_type=t, project_id=pid))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"The {t.value} records could not be read: {exc}")
                return []

        boards, board_parts = boards_of(_latest(await of_type(WorkProductType.SYSTEM_ARCHITECTURE)))
        lines = make_lines_of(
            await of_type(WorkProductType.BOM), await of_type(WorkProductType.PROCUREMENT_RECORD)
        )

        component_ids: dict[str, str] = {}
        try:
            ident = await project_identity(twin, pid)
            for cid, comp in (ident.get("components") or {}).items():
                if comp.get("studio_part"):
                    component_ids[str(comp["studio_part"])] = cid
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"The concept components could not be read: {exc}")

        threads = build_threads(
            studio_parts=parts,
            studio=studio,
            selections=selections,
            board_parts=board_parts,
            boards=boards,
            make_lines=lines,
            component_ids=component_ids,
        )
        counts = summary(threads)
        span.set_attribute("component_thread.threads", len(threads))
        count("read", "warned" if warnings else "ok")
        logger.info(
            "component_threads_gathered",
            project_id=project_id,
            threads=len(threads),
            warnings=len(warnings),
            **counts,
        )
        return {
            "project_id": project_id,
            "studio": studio or None,
            "threads": threads,
            "summary": counts,
            "warnings": warnings,
        }


async def link_for_selection(
    twin: Any,
    project_id: str | None,
    *,
    mpn: str,
    role: str | None = None,
    studio_parts: list[str] | None = None,
) -> dict[str, Any]:
    """Which studio parts a new component selection fills, and its library entry.

    ``{studio_parts, linked_by, studio_revision, library}``; empty fields when there is
    no studio or nothing matches. Never raises: a selection is recorded without a link
    rather than refused.
    """
    from twin_core.component_thread import link_selection

    out: dict[str, Any] = {"studio_parts": [], "linked_by": None}
    try:
        out["library"] = library_for(mpn, library_components())
    except Exception as exc:  # noqa: BLE001
        logger.warning("component_thread_library_unavailable", error=str(exc))
    if not project_id:
        return out
    try:
        from api_gateway.imagine.service import ImagineService

        state, studio = await studio_state(ImagineService(twin), str(project_id))
        if not state:
            return out
        parts = studio_parts_of(state)
        sel = Selection(node_id="", mpn=mpn, role=role or "", studio_parts=list(studio_parts or []))
        pids, how = link_selection(sel, parts)
        if studio_parts and not pids:
            known = ", ".join(p.id for p in parts) or "none"
            raise ValueError(
                f"studio_parts {studio_parts} are not parts of the studio (it has: {known})"
            )
        out.update(studio_parts=pids, linked_by=how or None, studio_revision=studio.get("revision"))
        count("link", "ok" if pids else "unlinked")
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        count("link", "failed")
        logger.warning("component_thread_link_failed", project_id=project_id, error=str(exc))
    return out
