"""Gather a project's form thread from the studio and Develop's CAD (FORGE-915).

The comparing is :mod:`twin_core.form_thread`; this module only reads. A source that
fails leaves its side empty and adds a warning, as the component thread does.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.form_thread import CadItem, FormPart, build_rows, summary

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.form_thread")

_collector: Any = None


def count(operation: str, outcome: str) -> None:
    """One form thread operation; a metric never fails a request."""
    global _collector  # noqa: PLW0603
    try:
        if _collector is None:
            from observability.metrics import collector_for

            _collector = collector_for("metaforge-gateway")
        _collector.record_component_thread(f"form_{operation}", outcome)
    except Exception:  # noqa: BLE001
        pass


def form_parts_of(config: dict[str, Any]) -> list[FormPart]:
    """The parts the studio draws itself (not bought components: their thread is the
    component thread)."""
    authored = (config.get("form") or {}).get("authored") or {}
    out = []
    for p in authored.get("parts") or []:
        if not isinstance(p, dict) or str(p.get("kind") or "generated") == "component":
            continue
        pid = str(p.get("id") or "")
        if not pid:
            continue
        accepted = p.get("develop_form") if isinstance(p.get("develop_form"), dict) else None
        out.append(
            FormPart(
                id=pid,
                name=str(p.get("name") or pid),
                kind=str(p.get("kind") or "generated"),
                develop_item=str(p.get("develop_item") or ""),
                accepted=accepted,
            )
        )
    return out


def cad_heads(work_products: list[Any]) -> list[CadItem]:
    """The head revision of each CAD item that has an item key."""
    heads: dict[str, CadItem] = {}
    for wp in work_products:
        meta = dict(getattr(wp, "metadata", None) or {})
        key = str(meta.get("item_key") or "")
        rev = meta.get("item_revision")
        if not key or not isinstance(rev, int) or isinstance(rev, bool):
            continue
        if key in heads and heads[key].revision >= rev:
            continue
        validity = meta.get("validity") if isinstance(meta.get("validity"), dict) else {}
        bbox = validity.get("bounding_box") if isinstance(validity, dict) else None
        heads[key] = CadItem(
            item_key=key,
            revision=rev,
            name=str(getattr(wp, "name", "") or key),
            node_id=str(getattr(wp, "id", "")),
            bbox=bbox if isinstance(bbox, dict) else None,
        )
    return list(heads.values())


async def gather_form_thread(
    twin: Any, project_id: str, *, studio_service: Any = None
) -> dict[str, Any]:
    """``{project_id, studio, rows, summary, warnings, limitations}`` for one project."""
    from twin_core.models.enums import WorkProductType

    warnings: list[str] = []
    with tracer.start_as_current_span("form_thread.gather") as span:
        span.set_attribute("project_id", project_id)
        studio: dict[str, Any] = {}
        parts: list[FormPart] = []
        sizes: dict[str, list[float]] = {}
        try:
            head = await studio_service._head(project_id) if studio_service else None
            if head is not None:
                studio = {
                    "status": head.state.get("status") or "draft",
                    "revision": head.revision,
                }
                parts = form_parts_of(head.state.get("config") or {})
                record = await studio_service.get_form_sizes(project_id)
                if record:
                    sizes = {z["id"]: list(z["size_mm"]) for z in record.get("sizes") or []}
                    studio["sizes_at"] = record.get("at")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"The Imagine studio could not be read: {exc}")
        items: list[CadItem] = []
        try:
            wps = await twin.list_work_products(
                work_product_type=WorkProductType.CAD_MODEL, project_id=UUID(project_id)
            )
            items = cad_heads(list(wps))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Develop's CAD could not be read: {exc}")
        rows = build_rows(parts, sizes, items)
        span.set_attribute("form_thread.rows", len(rows))
        count("gather", "ok")
        return {
            "project_id": project_id,
            "studio": studio,
            "rows": [r.as_dict() for r in rows],
            "summary": summary(rows),
            "warnings": warnings,
            "limitations": [
                "Sizes are boxes on the studio's axes (Y up); openings and mounts are not "
                "compared yet.",
                "The studio side is what the page drew at its last look; look again after a "
                "change.",
            ],
        }
