"""Generic "structured document" work-product recorder, shared by five Twin
work-product types added in one pass (hazard analysis, system architecture,
technical drawing, compliance checklist, procurement record) that all follow
the exact shape ``decision_recorder.py`` established: render structured input
to markdown, blob it to MinIO, create a validated ``WorkProduct``, link it to
its project, and publish ``WORK_PRODUCT_CREATED`` for knowledge indexing.

One shared persistence helper instead of five near-identical ~150-line files
-- the five types differ only in their markdown template and metadata shape,
both of which stay in this module as small per-type ``render_*``/``make_*``
pairs; the MCP adapter still gets five distinctly-named recorder callables
(one ``make_X_recorder(twin, project_backend)`` factory per type below) so
``tool_registry``'s constructor/bootstrap wiring reads the same as every
other recorder in this codebase.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

from api_gateway.twin.work_product_events import publish_work_product_created
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.structured_document_recorder")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "document")[:60]


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """A minimal, dependency-free GFM table renderer."""
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    lines += ["| " + " | ".join(esc(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


async def _persist_structured_document(
    *,
    twin: Any,
    project_backend: Any,
    work_product_type: Any,
    name: str,
    markdown: str,
    metadata: dict[str, Any],
    source_tool: str,
    project_id: str | None,
    domain: str,
    source_node_ids: list[str] | None,
) -> dict[str, Any]:
    from twin_core.models.enums import EdgeType
    from twin_core.models.work_product import WorkProduct

    content = markdown.encode("utf-8")
    content_hash = hashlib.sha256(content).hexdigest()
    filename = f"{_slug(name)}.md"
    wp_id = uuid4()

    with tracer.start_as_current_span(f"twin.commit_{work_product_type.value}") as span:
        span.set_attribute("document.name", name)
        span.set_attribute("document.type", work_product_type.value)

        minio_object_key: str | None = None
        try:
            from digital_twin.storage.work_product_blobs import store_work_product_blob

            minio_object_key = store_work_product_blob(
                str(wp_id), filename, content, content_type="text/markdown"
            )
        except Exception as exc:  # noqa: BLE001 — degrade like every other recorder
            logger.warning(
                "structured_document_blob_store_skipped", wp_id=str(wp_id), error=str(exc)
            )

        meta = dict(metadata)
        meta["original_filename"] = filename
        meta["content_sha256"] = content_hash
        meta["recorded_by"] = source_tool
        if minio_object_key:
            meta["minio_object_key"] = minio_object_key

        now = datetime.now(UTC)
        wp = WorkProduct(
            id=wp_id,
            name=name,
            type=work_product_type,
            domain=domain,
            file_path="",
            content_hash=content_hash,
            format="md",
            metadata=meta,
            created_at=now,
            updated_at=now,
            created_by=source_tool,
            project_id=project_id,  # pydantic coerces str -> UUID
        )
        created = await twin.create_work_product(wp)
        node_id = str(getattr(created, "id", wp_id))

        edge_failures = 0
        for source_id in source_node_ids or []:
            try:
                await twin.add_edge(created.id, source_id, EdgeType.PARENT_OF)
            except Exception as exc:  # noqa: BLE001 — provenance edge is best-effort
                edge_failures += 1
                logger.warning(
                    "structured_document_source_edge_failed",
                    node_id=node_id,
                    source_id=source_id,
                    error=str(exc),
                )

        linked = False
        if project_id and project_backend is not None:
            try:
                await project_backend.link_work_product(
                    project_id, node_id, name, work_product_type.value
                )
                linked = True
            except Exception as exc:  # noqa: BLE001 — link is best-effort
                logger.warning("structured_document_project_link_failed", error=str(exc))

        indexed = await publish_work_product_created(
            work_product_id=node_id,
            work_product_type=work_product_type.value,
            name=name,
            content=markdown,
            project_id=project_id,
            source=source_tool,
            metadata={"content_sha256": content_hash},
        )

        logger.info(
            "structured_document_recorded",
            work_product_type=work_product_type.value,
            node_id=node_id,
            project_id=project_id,
            linked=linked,
            indexed=indexed,
            minio_object_key=minio_object_key,
            edge_failures=edge_failures,
        )
        return {
            "node_id": node_id,
            "minio_object_key": minio_object_key,
            "content_hash": content_hash,
            "project_linked": linked,
            "knowledge_indexed": indexed,
        }


# ---------------------------------------------------------------------------
# 1. Hazard analysis
# ---------------------------------------------------------------------------

_RISK_LEVELS = [(20, "critical"), (12, "high"), (6, "medium"), (0, "low")]


def _risk_level(score: int) -> str:
    for threshold, label in _RISK_LEVELS:
        if score >= threshold:
            return label
    return "low"  # pragma: no cover — unreachable, thresholds bottom out at 0


def render_hazard_analysis_markdown(
    name: str, system_name: str, hazards: list[dict[str, Any]]
) -> tuple[str, dict[str, Any]]:
    rows = []
    highest = 0
    unmitigated = 0
    for h in hazards:
        severity, likelihood = int(h["severity"]), int(h["likelihood"])
        score = severity * likelihood
        highest = max(highest, score)
        if not str(h.get("mitigation", "")).strip():
            unmitigated += 1
        rows.append(
            [
                h["hazard"],
                h["cause"],
                h["effect"],
                str(severity),
                str(likelihood),
                str(score),
                _risk_level(score),
                h.get("mitigation", "") or "(none)",
            ]
        )
    rows.sort(key=lambda r: int(r[5]), reverse=True)
    md = "\n".join(
        [
            f"# Hazard Analysis: {system_name}",
            "",
            _md_table(
                [
                    "Hazard",
                    "Cause",
                    "Effect",
                    "Severity",
                    "Likelihood",
                    "Risk Score",
                    "Risk Level",
                    "Mitigation",
                ],
                rows,
            ),
            "",
        ]
    )
    metadata = {
        "system_name": system_name,
        "hazard_count": len(hazards),
        "highest_risk_score": highest,
        "unmitigated_count": unmitigated,
        "hazards": hazards,
    }
    return md, metadata


def make_hazard_analysis_recorder(twin: Any, project_backend: Any = None) -> Any:
    async def commit(
        *,
        name: str,
        system_name: str,
        hazards: list[dict[str, Any]],
        project_id: str | None = None,
        domain: str = "compliance",
        source_tool: str = "compliance.analyze_hazards",
    ) -> dict[str, Any]:
        from twin_core.models.enums import WorkProductType

        if not name:
            raise ValueError("hazard_analysis commit: 'name' is required")
        if not hazards:
            raise ValueError("hazard_analysis commit: 'hazards' must be non-empty")
        markdown, metadata = render_hazard_analysis_markdown(name, system_name, hazards)
        result = await _persist_structured_document(
            twin=twin,
            project_backend=project_backend,
            work_product_type=WorkProductType.HAZARD_ANALYSIS,
            name=name,
            markdown=markdown,
            metadata=metadata,
            source_tool=source_tool,
            project_id=project_id,
            domain=domain,
            source_node_ids=None,
        )
        return {
            **result,
            "hazard_count": metadata["hazard_count"],
            "highest_risk_score": metadata["highest_risk_score"],
            "unmitigated_count": metadata["unmitigated_count"],
        }

    return commit


# ---------------------------------------------------------------------------
# 2. System architecture
# ---------------------------------------------------------------------------


def render_system_architecture_markdown(
    name: str,
    system_name: str,
    components: list[dict[str, Any]],
    interfaces: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    known = {c["name"] for c in components}
    dangling = [i for i in interfaces if i["from"] not in known or i["to"] not in known]
    mermaid_lines = ["```mermaid", "graph LR"]
    for c in components:
        mermaid_lines.append(f'  {_slug(c["name"])}["{c["name"]}"]')
    for i in interfaces:
        mermaid_lines.append(
            f"  {_slug(i['from'])} -->|{i.get('interface_type', '')}| {_slug(i['to'])}"
        )
    mermaid_lines.append("```")

    md = "\n".join(
        [
            f"# System Architecture: {system_name}",
            "",
            "\n".join(mermaid_lines),
            "",
            "## Components",
            "",
            _md_table(
                ["Name", "Discipline", "Description"],
                [
                    [c["name"], c.get("discipline", ""), c.get("description", "")]
                    for c in components
                ],
            ),
            "",
            "## Interfaces",
            "",
            _md_table(
                ["From", "To", "Type", "Description"],
                [
                    [i["from"], i["to"], i.get("interface_type", ""), i.get("description", "")]
                    for i in interfaces
                ],
            ),
            "",
        ]
    )
    metadata = {
        "system_name": system_name,
        "component_count": len(components),
        "interface_count": len(interfaces),
        "dangling_interfaces": dangling,
        "components": components,
        "interfaces": interfaces,
    }
    return md, metadata


def make_system_architecture_recorder(twin: Any, project_backend: Any = None) -> Any:
    async def commit(
        *,
        name: str,
        system_name: str,
        components: list[dict[str, Any]],
        interfaces: list[dict[str, Any]] | None = None,
        project_id: str | None = None,
        domain: str = "systems",
        source_tool: str = "shared.define_system_architecture",
    ) -> dict[str, Any]:
        from twin_core.models.enums import WorkProductType

        if not name:
            raise ValueError("system_architecture commit: 'name' is required")
        if not components:
            raise ValueError("system_architecture commit: 'components' must be non-empty")
        markdown, metadata = render_system_architecture_markdown(
            name, system_name, components, interfaces or []
        )
        result = await _persist_structured_document(
            twin=twin,
            project_backend=project_backend,
            work_product_type=WorkProductType.SYSTEM_ARCHITECTURE,
            name=name,
            markdown=markdown,
            metadata=metadata,
            source_tool=source_tool,
            project_id=project_id,
            domain=domain,
            source_node_ids=None,
        )
        return {
            **result,
            "component_count": metadata["component_count"],
            "interface_count": metadata["interface_count"],
            "dangling_interfaces": metadata["dangling_interfaces"],
        }

    return commit


# ---------------------------------------------------------------------------
# 3. Technical drawing
# ---------------------------------------------------------------------------


def render_technical_drawing_markdown(
    name: str,
    part_name: str,
    dimensions: list[dict[str, Any]],
    gdt_callouts: list[dict[str, Any]],
    surface_finishes: list[dict[str, Any]],
    inspection_requirements: list[str],
) -> tuple[str, dict[str, Any]]:
    md = "\n".join(
        [
            f"# Technical Drawing Package: {part_name}",
            "",
            "> Structured drawing spec (dimensions, GD&T, finishes, inspection) -- "
            "not a rendered 2D vector drawing; MetaForge has no TechDraw-equivalent "
            "generator wired up yet.",
            "",
            "## Dimensions",
            "",
            _md_table(
                ["Feature", "Nominal (mm)", "+Tol (mm)", "-Tol (mm)"],
                [
                    [
                        d["feature"],
                        str(d["nominal_mm"]),
                        str(d.get("tolerance_plus_mm", 0)),
                        str(d.get("tolerance_minus_mm", 0)),
                    ]
                    for d in dimensions
                ],
            ),
            "",
            "## GD&T Callouts",
            "",
            _md_table(
                ["Feature", "Symbol", "Tolerance (mm)", "Datum Refs"],
                [
                    [
                        g["feature"],
                        g["symbol"],
                        str(g["tolerance_value_mm"]),
                        ", ".join(g.get("datum_refs", [])),
                    ]
                    for g in gdt_callouts
                ],
            ),
            "",
            "## Surface Finishes",
            "",
            _md_table(
                ["Feature", "Ra (µm)"],
                [[s["feature"], str(s["ra_um"])] for s in surface_finishes],
            ),
            "",
            "## Inspection Requirements",
            "",
            "\n".join(f"- {r}" for r in inspection_requirements) or "(none specified)",
            "",
        ]
    )
    metadata = {
        "part_name": part_name,
        "dimension_count": len(dimensions),
        "gdt_callout_count": len(gdt_callouts),
        "surface_finish_count": len(surface_finishes),
        "inspection_requirement_count": len(inspection_requirements),
        "dimensions": dimensions,
        "gdt_callouts": gdt_callouts,
        "surface_finishes": surface_finishes,
        "inspection_requirements": inspection_requirements,
    }
    return md, metadata


def make_technical_drawing_recorder(twin: Any, project_backend: Any = None) -> Any:
    async def commit(
        *,
        name: str,
        part_name: str,
        dimensions: list[dict[str, Any]],
        gdt_callouts: list[dict[str, Any]] | None = None,
        surface_finishes: list[dict[str, Any]] | None = None,
        inspection_requirements: list[str] | None = None,
        source_node_ids: list[str] | None = None,
        project_id: str | None = None,
        domain: str = "mechanical",
        source_tool: str = "mechanical.generate_technical_drawing",
    ) -> dict[str, Any]:
        from twin_core.models.enums import WorkProductType

        if not name:
            raise ValueError("technical_drawing commit: 'name' is required")
        if not dimensions:
            raise ValueError("technical_drawing commit: 'dimensions' must be non-empty")
        markdown, metadata = render_technical_drawing_markdown(
            name,
            part_name,
            dimensions,
            gdt_callouts or [],
            surface_finishes or [],
            inspection_requirements or [],
        )
        result = await _persist_structured_document(
            twin=twin,
            project_backend=project_backend,
            work_product_type=WorkProductType.TECHNICAL_DRAWING,
            name=name,
            markdown=markdown,
            metadata=metadata,
            source_tool=source_tool,
            project_id=project_id,
            domain=domain,
            source_node_ids=source_node_ids,
        )
        return {
            **result,
            "dimension_count": metadata["dimension_count"],
            "gdt_callout_count": metadata["gdt_callout_count"],
            "surface_finish_count": metadata["surface_finish_count"],
            "inspection_requirement_count": metadata["inspection_requirement_count"],
        }

    return commit


# ---------------------------------------------------------------------------
# 4. Compliance checklist
# ---------------------------------------------------------------------------


def render_compliance_checklist_markdown(
    name: str, target_markets: list[str], items: list[dict[str, Any]], coverage_percent: float
) -> tuple[str, dict[str, Any]]:
    md = "\n".join(
        [
            f"# Compliance Checklist: {name}",
            "",
            f"Target markets: {', '.join(target_markets)} · "
            f"Evidence coverage: {coverage_percent:.1f}%",
            "",
            _md_table(
                ["Regime", "Category", "Requirement", "Standard", "Evidence", "Status"],
                [
                    [
                        i["regime"],
                        i["category"],
                        i["requirement"],
                        i["standard"],
                        i["evidence_type"],
                        i["evidence_status"],
                    ]
                    for i in items
                ],
            ),
            "",
        ]
    )
    metadata = {
        "target_markets": target_markets,
        "total_items": len(items),
        "coverage_percent": coverage_percent,
        "items": items,
    }
    return md, metadata


def make_compliance_checklist_recorder(twin: Any, project_backend: Any = None) -> Any:
    async def commit(
        *,
        name: str,
        target_markets: list[str],
        items: list[dict[str, Any]],
        coverage_percent: float = 0.0,
        project_id: str | None = None,
        domain: str = "compliance",
        source_tool: str = "compliance.record_compliance_checklist",
    ) -> dict[str, Any]:
        from twin_core.models.enums import WorkProductType

        if not name:
            raise ValueError("compliance_checklist commit: 'name' is required")
        if not items:
            raise ValueError("compliance_checklist commit: 'items' must be non-empty")
        markdown, metadata = render_compliance_checklist_markdown(
            name, target_markets, items, coverage_percent
        )
        result = await _persist_structured_document(
            twin=twin,
            project_backend=project_backend,
            work_product_type=WorkProductType.COMPLIANCE_CHECKLIST,
            name=name,
            markdown=markdown,
            metadata=metadata,
            source_tool=source_tool,
            project_id=project_id,
            domain=domain,
            source_node_ids=None,
        )
        return {
            **result,
            "total_items": metadata["total_items"],
            "coverage_percent": metadata["coverage_percent"],
        }

    return commit


# ---------------------------------------------------------------------------
# 5. Procurement record
# ---------------------------------------------------------------------------


def render_procurement_record_markdown(
    name: str, line_items: list[dict[str, Any]], notes: str
) -> tuple[str, dict[str, Any]]:
    currencies = {li.get("currency", "USD") for li in line_items}
    if len(currencies) > 1:
        raise ValueError(
            f"procurement_record: mixed currencies {sorted(currencies)} not supported "
            "in one record -- split into separate records per currency"
        )
    currency = next(iter(currencies), "USD")
    total_cost = sum(float(li["quantity"]) * float(li["unit_cost"]) for li in line_items)
    max_lead_time = max((int(li.get("lead_time_days", 0)) for li in line_items), default=0)

    rows = [
        [
            li["part_number"],
            li.get("description", ""),
            str(li["quantity"]),
            f"{float(li['unit_cost']):.2f}",
            f"{float(li['quantity']) * float(li['unit_cost']):.2f}",
            li.get("distributor", ""),
            str(li.get("lead_time_days", "")),
        ]
        for li in line_items
    ]
    md = "\n".join(
        [
            f"# Procurement Record: {name}",
            "",
            f"Total cost: {total_cost:.2f} {currency} · Max lead time: {max_lead_time} days",
            "",
            _md_table(
                [
                    "Part Number",
                    "Description",
                    "Qty",
                    "Unit Cost",
                    "Line Total",
                    "Distributor",
                    "Lead Time (days)",
                ],
                rows,
            ),
            "",
            *(["## Notes", "", notes, ""] if notes else []),
        ]
    )
    metadata = {
        "line_item_count": len(line_items),
        "total_cost": round(total_cost, 2),
        "currency": currency,
        "max_lead_time_days": max_lead_time,
        "line_items": line_items,
        "notes": notes,
    }
    return md, metadata


def make_procurement_record_recorder(twin: Any, project_backend: Any = None) -> Any:
    async def commit(
        *,
        name: str,
        line_items: list[dict[str, Any]],
        notes: str = "",
        source_node_ids: list[str] | None = None,
        project_id: str | None = None,
        domain: str = "supply_chain",
        source_tool: str = "supply_chain.create_procurement_record",
    ) -> dict[str, Any]:
        from twin_core.models.enums import WorkProductType

        if not name:
            raise ValueError("procurement_record commit: 'name' is required")
        if not line_items:
            raise ValueError("procurement_record commit: 'line_items' must be non-empty")
        markdown, metadata = render_procurement_record_markdown(name, line_items, notes)
        result = await _persist_structured_document(
            twin=twin,
            project_backend=project_backend,
            work_product_type=WorkProductType.PROCUREMENT_RECORD,
            name=name,
            markdown=markdown,
            metadata=metadata,
            source_tool=source_tool,
            project_id=project_id,
            domain=domain,
            source_node_ids=source_node_ids,
        )
        return {
            **result,
            "line_item_count": metadata["line_item_count"],
            "total_cost": metadata["total_cost"],
            "currency": metadata["currency"],
            "max_lead_time_days": metadata["max_lead_time_days"],
        }

    return commit
