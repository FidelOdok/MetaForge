"""The revision-specific DFM report and the release checklist (DFM-016, 3D-RC-016).

:func:`generate_report` reads the twin and stores one ``dfm_report`` record
for the product revision it read (the CAD heads, the finding sets' geometry
revisions and the manufacturing context revision), as JSON with a rendered
Markdown copy:

* the components, each with its maturity (C3D slice 2) and its current DFM
  set: kind, status, profile version, geometry and context revisions, the
  staleness of that evidence, every finding with measured against limit,
  anchors, evidence and any decision, and what was not assessed and why;
* the selected process, material and context revision, and the rule profile
  versions;
* unresolved issues (open findings, checks that did not run, stale sets),
  verification items with their state, and the release checklist with the
  readiness Create may claim.

The report is pinned to the revisions it read, so when one of them moves on
the report reads stale and stays browsable. The same content for the same
revision reuses the current report. Unresolved issues are kept in it, never
dropped.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer
from twin_core.manufacturing import DFM_STATUS_LABELS, DfmStatus

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.dfm_report")

RECORD_TYPE = "dfm_report"
META_KEY = "dfm_report"
REPORT_VERSION = 1


class DfmReportError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _metric(outcome: str) -> None:
    from api_gateway.twin.dfm_findings import _metric as metric

    metric("record_dfm_report", outcome)


def _pid(project_id: str | UUID) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise DfmReportError(f"project id {project_id!r} is not a UUID") from exc


def product_status(components: list[dict[str, Any]], checklist: dict[str, Any] | None) -> DfmStatus:
    """The product's DFM status, with verification and release as product steps (DFM-013)."""
    from twin_core.dfm import product_status as worst

    statuses = [DfmStatus(c["dfm"]["status"]) for c in components if c.get("dfm")]
    if not statuses:
        return DfmStatus.NOT_ASSESSED
    dfm_item = next((i for i in (checklist or {}).get("items") or [] if i["id"] == "dfm"), None)
    # Issues every one of which a person accepted count as resolved here.
    resolved = dfm_item is not None and dfm_item.get("status") == "pass"
    status = DfmStatus.ANALYSIS_PASSED if resolved else worst(statuses)
    if status is not DfmStatus.ANALYSIS_PASSED:
        return status
    open_verification = any(
        v.get("status") != "satisfied"
        for c in components
        for v in (c.get("dfm") or {}).get("verification_items") or []
    )
    if open_verification:
        return DfmStatus.VERIFICATION_REQUIRED
    if (
        checklist is not None
        and checklist.get("releasable")
        and all(c.get("maturity") == "Released" for c in components if c.get("required"))
    ):
        return DfmStatus.RELEASED
    return DfmStatus.ANALYSIS_PASSED


def _finding_row(f: dict[str, Any]) -> dict[str, Any]:
    decision = f.get("decision") if isinstance(f.get("decision"), dict) else None
    return {
        "id": f.get("id"),
        "rule_id": f.get("rule_id"),
        "rule_title": f.get("rule_title"),
        "rule_kind": f.get("rule_kind"),
        "severity": f.get("severity"),
        "measured": f.get("measured"),
        "limit": f.get("limit"),
        "unit": f.get("unit"),
        "comparison": f.get("comparison"),
        "confidence": f.get("confidence"),
        "limit_source": f.get("limit_source"),
        "anchor": f.get("anchor"),
        "evidence_refs": f.get("evidence_refs") or [],
        "message": f.get("message"),
        "suggested_fix": f.get("suggested_fix"),
        "decision": decision,
        "resolved": bool(
            decision and decision.get("decision") == "keep_design" and decision.get("by")
        ),
    }


async def build_report(twin: Any, project_id: str | UUID) -> dict[str, Any]:
    """The report's content for the project's current revision (nothing stored)."""
    from api_gateway.twin.dfm_findings import dfm_view
    from api_gateway.twin.manufacturing_context import effective_context
    from twin_core.dfm.release_checklist import manufacturing_checklist, readiness
    from twin_core.maturity import compute_maturity

    pid = _pid(project_id)
    view = await dfm_view(twin, pid)
    maturity = await compute_maturity(twin, pid)
    checklist_obj = await manufacturing_checklist(twin, pid)
    checklist = checklist_obj.as_dict() if checklist_obj is not None else None
    try:
        context = await effective_context(twin, pid, None)
    except Exception:  # noqa: BLE001 - an unreadable context reads as none
        context = None
    by_cid = {c.component_id: c for c in view.components}
    components: list[dict[str, Any]] = []
    seen: set[str] = set()
    for comp in maturity["components"]:
        cid = comp["component_id"]
        seen.add(cid)
        components.append(
            {
                "component_id": cid,
                "name": comp["name"],
                "required": comp["required"],
                "maturity": comp["maturity"],
                "maturity_evidence": comp.get("evidence") or [],
                "dfm": _dfm_row(by_cid.get(cid)),
            }
        )
    for cid, extra in by_cid.items():
        if cid not in seen:
            components.append(
                {
                    "component_id": cid,
                    "name": extra.name,
                    "required": True,
                    "maturity": "unknown",
                    "maturity_evidence": [],
                    "dfm": _dfm_row(extra),
                }
            )
    unresolved: list[dict[str, Any]] = []
    verification: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for c in components:
        d = c["dfm"]
        if d is None:
            if c["required"]:
                unresolved.append(
                    {
                        "component": c["name"],
                        "kind": "not_assessed",
                        "text": "manufacturing not assessed",
                    }
                )
            continue
        for f in d["findings"]:
            if not f["resolved"]:
                unresolved.append(
                    {
                        "component": c["name"],
                        "kind": "finding",
                        "severity": f["severity"],
                        "rule_id": f["rule_id"],
                        "text": f["message"],
                    }
                )
        for n in d["not_assessed"]:
            unresolved.append(
                {
                    "component": c["name"],
                    "kind": "not_assessed",
                    "rule_id": n["rule_id"],
                    "text": n["text"],
                }
            )
        if d["staleness"].get("status") not in ("current", "revalidated"):
            stale.append(
                {
                    "component": c["name"],
                    "finding_set_id": d["finding_set_id"],
                    "status": d["staleness"].get("status"),
                    "reason": d["staleness"].get("reason"),
                }
            )
        for v in d["verification_items"]:
            verification.append({"component": c["name"], **v})
    status = product_status(components, checklist)
    process = material = None
    context_ref = "unresolved"
    if context is not None:
        context_ref = context.ref
        rec = context.record
        if rec.selected_process.known:
            process = str(rec.selected_process.value)
        if rec.selected_material.known:
            material = str(rec.selected_material.value)
    refs = sorted(
        {r for c in components if c["dfm"] for r in c["dfm"]["geometry_refs"] if "@" in r}
        | set((checklist or {}).get("revision_refs") or [])
        | ({context_ref} if "@" in context_ref else set())
    )
    return {
        "report_version": REPORT_VERSION,
        "project_id": str(pid),
        "product_revision": {
            "refs": refs,
            "key": hashlib.sha256("|".join(refs).encode("utf-8")).hexdigest()[:16],
        },
        "status": status.value,
        "status_label": DFM_STATUS_LABELS[status],
        "process": process,
        "material": material,
        "context_ref": context_ref,
        "profiles": view.profiles,
        "components": components,
        "unresolved": unresolved,
        "verification_items": verification,
        "stale": {"any": bool(stale), "sets": stale},
        "checklist": checklist,
        "readiness": readiness(checklist_obj),
        "disclaimer": view.disclaimer,
    }


def _dfm_row(c: Any) -> dict[str, Any] | None:
    if c is None:
        return None
    return {
        "finding_set_id": c.finding_set_id,
        "kind": c.kind,
        "status": c.status,
        "status_label": c.status_label,
        "note": c.note,
        "process": c.process,
        "profile": c.profile,
        "geometry_level": c.geometry_level,
        "geometry_refs": c.geometry_refs,
        "context_ref": c.context_ref,
        "staleness": c.staleness,
        "complete": c.complete,
        "checks_required": c.checks_required,
        "rules_run": c.rules_run,
        "guidance_only_rules": c.guidance_only_rules,
        "findings": [_finding_row(f) for f in c.findings],
        "not_assessed": c.not_assessed,
        "verification_items": c.verification_items,
        "measurement": {
            k: c.exact_evidence.get(k)
            for k in (
                "tool",
                "kernel",
                "build_direction",
                "build_direction_source",
                "wall_method",
                "wall_limits",
                "wall_samples",
                "hole_limits",
                "measured",
            )
            if k in c.exact_evidence
        },
        "evaluated_at": c.evaluated_at,
    }


def _md_value(v: Any) -> str:
    if v is None:
        return "unknown"
    return str(v).replace("|", "/")


def render_markdown(report: dict[str, Any], *, generated_at: str = "") -> str:
    """The report as Markdown, for reading and attaching to a release package."""
    lines: list[str] = ["# DFM report", ""]
    rev = report["product_revision"]
    lines += [
        f"- Status: **{report['status_label']}**",
        f"- Process: {_md_value(report.get('process'))}; material: "
        f"{_md_value(report.get('material'))}; manufacturing context {report['context_ref']}",
        f"- Product revision: {', '.join(rev['refs']) or 'none'} (key {rev['key']})",
        "- Rule profiles: "
        + (", ".join(f"{p['id']} {p['version']}" for p in report["profiles"]) or "none"),
    ]
    if generated_at:
        lines.append(f"- Generated: {generated_at}")
    lines += ["", f"_{report['disclaimer']}_", "", "## Components", ""]
    lines += ["| Component | Required | Maturity | DFM | Evidence |", "|---|---|---|---|---|"]
    for c in report["components"]:
        d = c["dfm"]
        lines.append(
            f"| {_md_value(c['name'])} | {'yes' if c['required'] else 'no'} | {c['maturity']} | "
            + (f"{d['status_label']} ({d['kind']})" if d else "not assessed")
            + " | "
            + (f"{d['staleness'].get('status')}" if d else "")
            + " |"
        )
    for c in report["components"]:
        d = c["dfm"]
        if not d:
            continue
        lines += ["", f"### {c['name']}", ""]
        profile = d["profile"] or {}
        lines.append(
            f"{d['note']}. Profile {profile.get('id', 'none')} {profile.get('version', '')}, "
            f"geometry {', '.join(d['geometry_refs']) or 'none'}, context {d['context_ref']}."
        )
        m = d.get("measurement") or {}
        if m.get("wall_method"):
            lines.append("")
            lines.append(
                f"Measured with {m.get('tool')} ({m.get('kernel')}); build direction "
                f"{m.get('build_direction')} ({m.get('build_direction_source')}). Wall thickness: "
                f"{m.get('wall_method')}, {m.get('wall_samples')} samples. Limits: "
                + "; ".join(m.get("wall_limits") or [])
                + "."
            )
        if d["findings"]:
            lines += ["", "| Rule | Severity | Measured | Limit | Anchor | Decision |"]
            lines.append("|---|---|---|---|---|---|")
            for f in d["findings"]:
                anchor = f.get("anchor") or {}
                where = anchor.get("face_signature") or anchor.get("feature") or ""
                if anchor.get("faces"):
                    where += f" ({len(anchor['faces'])} face(s): {', '.join(anchor['faces'][:6])})"
                decision = (f.get("decision") or {}).get("decision") or "open"
                lines.append(
                    f"| {f['rule_id']} | {f['severity']} | {_md_value(f['measured'])} {f['unit']} "
                    f"| {f['comparison']} {_md_value(f['limit'])} {f['unit']} | "
                    f"{_md_value(where)} | {decision} |"
                )
        for n in d["not_assessed"]:
            lines.append(f"- {n['text']}")
    lines += ["", "## Unresolved", ""]
    lines += [
        f"- {u['component']}: {u['text']}" + (f" ({u['severity']})" if u.get("severity") else "")
        for u in report["unresolved"]
    ] or ["- none"]
    lines += ["", "## Verification", ""]
    lines += [
        f"- {v['component']}: {v['title']} ({v['status'].replace('_', ' ')}). Needed: {v['needed']}"
        for v in report["verification_items"]
    ] or ["- none required"]
    lines += ["", "## Stale evidence", ""]
    lines += [
        f"- {s['component']}: {s['status']}" + (f" ({s['reason']})" if s.get("reason") else "")
        for s in report["stale"]["sets"]
    ] or ["- none"]
    lines += ["", "## Release checklist", ""]
    checklist = report.get("checklist")
    if checklist is None:
        lines.append("- Does not apply: no process with exact DFM checks is selected.")
    else:
        for item in checklist["items"]:
            mark = {"pass": "[x]", "fail": "[ ]", "not_applicable": "[-]"}[item["status"]]
            lines.append(f"- {mark} {item['label']}: {item['detail']}")
    lines += ["", f"Readiness: {report['readiness']['label']}", ""]
    return "\n".join(lines)


async def _reports(twin: Any, pid: UUID) -> list[Any]:
    from twin_core.models.enums import WorkProductType

    try:
        nodes = await twin.list_work_products(
            work_product_type=WorkProductType.DFM_REPORT, project_id=pid
        )
    except Exception as exc:  # noqa: BLE001 - no list API reads as no reports
        logger.debug("dfm_reports_unavailable", error=str(exc))
        return []
    return sorted(nodes, key=lambda n: n.created_at.isoformat() if n.created_at else "")


def report_view(node: Any) -> dict[str, Any]:
    from api_gateway.twin.record_pins import record_view
    from twin_core.consistency.record_pins import record_staleness

    meta = dict(node.metadata or {})
    view = record_view(RECORD_TYPE, meta)
    return {
        "report_id": str(node.id),
        "generated_at": meta.get("generated_at"),
        "generated_by": meta.get("generated_by"),
        "staleness": view.get("staleness") or {"status": record_staleness(meta)},
        "report": meta.get(META_KEY),
        "markdown": meta.get("markdown"),
    }


async def generate_report(twin: Any, project_id: str | UUID, *, actor: str = "") -> dict[str, Any]:
    """Build and store the report for the current revision (or reuse the same one)."""
    from api_gateway.twin.record_pins import link_record, pin_metadata, resolve_record_pins
    from twin_core.consistency.record_pins import VALID_STATUSES, record_staleness
    from twin_core.models.enums import WorkProductType
    from twin_core.models.work_product import WorkProduct

    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.dfm.report.generate") as span:
        try:
            report = await build_report(twin, pid)
            digest = hashlib.sha256(
                json.dumps(report, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            for node in reversed(await _reports(twin, pid)):
                meta = node.metadata or {}
                if (
                    record_staleness(meta) in VALID_STATUSES
                    and meta.get("content_sha256") == digest
                ):
                    _metric("reused")
                    span.set_attribute("dfm.report.reused", True)
                    return {**report_view(node), "reused": True}
            now = datetime.now(UTC)
            markdown = render_markdown(report, generated_at=now.isoformat())
            refs = list(report["product_revision"]["refs"])
            try:
                pins = await resolve_record_pins(twin, depends_on=refs, project_id=pid)
            except Exception as exc:  # noqa: BLE001 - an unresolvable ref pins nothing
                logger.warning("dfm_report_pins_unresolved", refs=refs, error=str(exc))
                pins = []
            name = f"DFM report {report['product_revision']['key']}"
            metadata: dict[str, Any] = {
                META_KEY: report,
                "markdown": markdown,
                "content_sha256": digest,
                "generated_at": now.isoformat(),
                "generated_by": actor,
                "product_revision_key": report["product_revision"]["key"],
                "dfm_status": report["status"],
            }
            metadata.update(pin_metadata(pins))
            wp_id = uuid4()
            created = await twin.create_work_product(
                WorkProduct(
                    id=wp_id,
                    name=name,
                    type=WorkProductType.DFM_REPORT,
                    domain="manufacturing",
                    file_path="",
                    content_hash=digest,
                    format="json",
                    metadata=metadata,
                    created_at=now,
                    updated_at=now,
                    created_by="metaforge:dfm",
                    project_id=pid,
                )
            )
            node_id = getattr(created, "id", wp_id)
            await link_record(
                twin, node_id, pins, record_type=RECORD_TYPE, name=name, metadata=metadata
            )
            node = (await twin.get_work_product(node_id)) or created
        except Exception as exc:
            span.record_exception(exc)
            _metric("failed")
            logger.warning("dfm_report_failed", project_id=str(pid), error=str(exc))
            raise
        _metric("generated")
        logger.info(
            "dfm_report_generated",
            project_id=str(pid),
            report_id=str(node_id),
            status=report["status"],
            unresolved=len(report["unresolved"]),
        )
        return {**report_view(node), "reused": False}


async def latest_report(twin: Any, project_id: str | UUID) -> dict[str, Any]:
    pid = _pid(project_id)
    nodes = await _reports(twin, pid)
    if not nodes:
        raise DfmReportError("no DFM report for this project yet", 404)
    return report_view(nodes[-1])


async def report_by_id(twin: Any, project_id: str | UUID, report_id: str) -> dict[str, Any]:
    pid = _pid(project_id)
    for node in await _reports(twin, pid):
        if str(node.id) == report_id:
            return report_view(node)
    raise DfmReportError(f"no DFM report {report_id} in this project", 404)


__all__ = [
    "DfmReportError",
    "build_report",
    "generate_report",
    "latest_report",
    "product_status",
    "render_markdown",
    "report_by_id",
    "report_view",
]
