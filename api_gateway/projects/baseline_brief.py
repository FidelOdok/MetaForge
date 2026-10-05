"""The project brief read from the item baseline (FORGE-530).

Before items existed the brief listed the newest work products under the char
cap. On the live shelf project that meant 16 cad_models for 4 real parts, 8
constraint set revisions and 14 phase-summary decisions, so the agent read
four copies of each part and could not tell which was current.

For a project that has items, the brief now lists **one line per item**, at
its current revision, with the facts an agent needs to reason about it
(bounding box, material and volume for a part, constraint count and key
limits for a requirement set, and the evidence state: ``FEA @3 ok`` when a
simulation result is pinned to the current revision, ``FEA stale (@2)`` when
the newest one belongs to an older revision). Records follow: recent real
design decisions, never the phase-summary fallbacks. When the call belongs to
a design-flow run, that run's own draft revisions are listed and labelled as
drafts; other runs' drafts never appear.

A project with no items keeps the legacy list (``brief.py``).

Reads only. Every per-item read is best-effort: a failure costs that item its
facts, never the brief.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.consistency.record_pins import is_valid_evidence, record_staleness
from twin_core.items.facts import (
    cad_facts,
    constraint_nodes,
    constraint_value,
    num,
    render_cad_facts,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.projects.baseline_brief")

#: Most recent design decisions listed after the items.
DECISION_LIMIT = 5
#: Characters of a decision's rationale shown on its line.
DECISION_RATIONALE_CHARS = 160
#: Constraint limits quoted on a requirement set's line.
CONSTRAINT_VALUE_LIMIT = 4
#: Characters of an engineering entity's statement shown on its line.
STATEMENT_CHARS = 140
#: Most rejected revisions listed as lessons.
LESSON_LIMIT = 5

#: Item types in the order the brief lists them: why, what is required, what
#: is built, what is bought. Anything else sorts after, by key.
_TYPE_ORDER = (
    "intent",
    "stakeholder_need",
    "objective",
    "prd",
    "constraint_set",
    "assembly",
    "cad_model",
    "cad_source_script",
    "bom",
    "component_selection",
)

#: Edges a simulation result uses to point at the geometry it analysed.
_EVIDENCE_EDGES = ("derives_from", "parent_of", "validates", "verified_by")


@dataclass
class BaselineEntry:
    """One item as the brief shows it."""

    ref: str
    item_type: str
    name: str
    facts: list[str] = field(default_factory=list)
    evidence: str | None = None
    draft: bool = False

    def line(self) -> str:
        bits = [b for b in (*self.facts, self.evidence) if b]
        tail = f": {'; '.join(bits)}" if bits else ""
        label = " (draft, not yet approved)" if self.draft else ""
        return f"- {self.ref}{label} {self.item_type} '{self.name}'{tail}"


@dataclass
class Baseline:
    entries: list[BaselineEntry]
    drafts: list[BaselineEntry]
    #: Revisions superseded, rejected or drafted by other runs, left out.
    hidden_revisions: int = 0
    #: ``(node_id, label)`` of current requirement docs worth inlining, newest first.
    docs: list[tuple[str, str]] = field(default_factory=list)
    #: "already tried X@n, failed because ..." lines, newest first.
    lessons: list[str] = field(default_factory=list)


def _type_rank(item_type: str) -> int:
    return _TYPE_ORDER.index(item_type) if item_type in _TYPE_ORDER else len(_TYPE_ORDER)


def _is_simulation(node: Any) -> bool:
    raw = getattr(node, "type", None)
    return str(getattr(raw, "value", raw)) == "simulation_result"


def _sim_label(meta: dict[str, Any]) -> str:
    kind = str(meta.get("analysis_type") or meta.get("analysis") or "").lower()
    return "thermal" if "therm" in kind else "FEA"


def _sim_outcome(meta: dict[str, Any]) -> str:
    passed = meta.get("passed")
    if passed is False:
        return "fail"
    status = str(meta.get("status") or meta.get("verdict") or "").lower()
    if status in {"fail", "failed", "error", "not_ok"}:
        return "fail"
    factor = num(meta.get("safety_factor") or meta.get("min_safety_factor"))
    if factor is not None and factor < 1.0:
        return "fail"
    return "ok"


def analysed_node_id(meta: dict[str, Any]) -> str | None:
    """The geometry node a simulation_result says it analysed (FORGE-532 pin)."""
    pin = meta.get("analysed_geometry")
    raw = pin.get("node_id") if isinstance(pin, dict) else None
    raw = raw or meta.get("analysed_geometry_node_id")
    return str(raw) if raw else None


async def _simulations_for(twin: Any, node_id: UUID) -> list[Any]:
    """Simulation results that analysed ``node_id``.

    FORGE-532 pins the analysed geometry on the result (``analysed_geometry``)
    and adds a DERIVES_FROM edge to it. The pin is the dependency: a result
    pinned to another node is not evidence for this one even if some other
    provenance edge points here. A result with no pin (recorded before
    FORGE-532) counts by its edge, as before.
    """
    edges = await twin.graph.get_edges(node_id, direction="incoming")
    sims = []
    seen: set[Any] = set()
    for edge in edges:
        if str(getattr(edge.edge_type, "value", edge.edge_type)) not in _EVIDENCE_EDGES:
            continue
        if edge.source_id in seen:
            continue
        seen.add(edge.source_id)
        node = await twin.graph.get_node(edge.source_id)
        if node is None or not _is_simulation(node):
            continue
        pinned = analysed_node_id(dict(getattr(node, "metadata", None) or {}))
        if pinned is not None and pinned != str(node_id):
            continue
        sims.append(node)
    return sims


async def evidence_state(twin: Any, views: list[Any], current: Any) -> str | None:
    """``FEA @3 ok`` / ``FEA @3 fail`` / ``FEA stale (@2)``, or ``None`` with no simulation."""
    on_current = await _simulations_for(twin, current.node_id)
    if on_current:
        newest = max(on_current, key=lambda n: str(getattr(n, "created_at", "")))
        meta = dict(getattr(newest, "metadata", None) or {})
        # FORGE-527: the same flag the gate reads (e.g. a named requirement set moved).
        if not is_valid_evidence(meta):
            return f"{_sim_label(meta)} @{current.revision} {record_staleness(meta)}"
        return f"{_sim_label(meta)} @{current.revision} {_sim_outcome(meta)}"
    for view in reversed([v for v in views if v.revision < current.revision]):
        older = await _simulations_for(twin, view.node_id)
        if older:
            meta = dict(getattr(older[0], "metadata", None) or {})
            return f"{_sim_label(meta)} stale (@{view.revision}, not re-run on @{current.revision})"
    return None


def _statement(node: Any) -> str | None:
    for attr in ("statement", "description", "rationale"):
        value = getattr(node, attr, None)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text if len(text) <= STATEMENT_CHARS else text[: STATEMENT_CHARS - 3] + "..."
    return None


async def _entry_for(twin: Any, view: Any, views: list[Any], *, draft: bool) -> BaselineEntry:
    item = view.item
    node = view.node
    meta = dict(view.metadata or {})
    name = getattr(node, "name", None) or getattr(node, "title", None) or item.name
    entry = BaselineEntry(ref=view.ref, item_type=item.item_type, name=str(name), draft=draft)
    try:
        if item.item_type in {"cad_model", "assembly"}:
            entry.facts = render_cad_facts(cad_facts(meta))
            entry.evidence = await evidence_state(twin, views, view)
        elif item.item_type == "constraint_set":
            nodes = await constraint_nodes(twin, view.node_id)
            count = meta.get("constraint_count") or len(nodes)
            values = [v for v in (constraint_value(n) for n in nodes) if v]
            fact = f"{count} constraints"
            if values:
                shown = values[:CONSTRAINT_VALUE_LIMIT]
                more = len(values) - len(shown)
                fact += f" ({', '.join(shown)}{f', +{more} more' if more > 0 else ''})"
            entry.facts = [fact]
        else:
            statement = _statement(node)
            if statement:
                entry.facts = [statement]
    except Exception as exc:  # noqa: BLE001 -- facts are best-effort, the line is not
        logger.debug("baseline_brief_facts_failed", item_key=item.key, error=str(exc))
    return entry


async def read_baseline(twin: Any, project_id: Any, run_id: str | None = None) -> Baseline | None:
    """The project's items at their current revision, plus ``run_id``'s drafts.

    ``None`` when the twin cannot hold items or the project has none, which is
    the caller's cue to render the legacy brief.
    """
    from api_gateway.twin.item_revisions import lesson_text
    from twin_core.items import list_items, supports_items
    from twin_core.items.state import REJECTED, current_revision, revision_views, run_drafts

    if twin is None or not supports_items(twin):
        return None
    try:
        pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError:
        return None
    with tracer.start_as_current_span("project.brief.baseline") as span:
        span.set_attribute("project.id", str(pid))
        items = await list_items(twin, project_id=pid)
        span.set_attribute("brief.items_total", len(items))
        if not items:
            return None
        entries: list[BaselineEntry] = []
        drafts: list[BaselineEntry] = []
        hidden = 0
        docs: list[tuple[Any, str, str]] = []
        rejected: list[Any] = []
        for item in sorted(items, key=lambda i: (_type_rank(i.item_type), i.key)):
            try:
                views = await revision_views(twin, item)
            except Exception as exc:  # noqa: BLE001 -- one bad item never kills the brief
                logger.debug("baseline_brief_item_failed", item_key=item.key, error=str(exc))
                continue
            current = current_revision(views)
            own = run_drafts(views, run_id)
            shown = (1 if current is not None else 0) + len(own)
            hidden += max(len(views) - shown, 0)
            if current is not None:
                entry = await _entry_for(twin, current, views, draft=False)
                entries.append(entry)
                # FORGE-86 from the current revision only. FORGE-528 made the
                # prd's prose an item too, so it is inlined the same way.
                if item.item_type in {"constraint_set", "prd"}:
                    docs.append(
                        (item.updated_at, str(current.node_id), f"{entry.name} ({entry.ref})")
                    )
            for view in own:
                drafts.append(await _entry_for(twin, view, views, draft=True))
            rejected.extend(v for v in views if v.status == REJECTED)
        docs.sort(key=lambda pair: pair[0], reverse=True)
        span.set_attribute("brief.items", len(entries))
        span.set_attribute("brief.drafts", len(drafts))
        return Baseline(
            entries=entries,
            drafts=drafts,
            hidden_revisions=hidden,
            docs=[(node_id, label) for _, node_id, label in docs],
            lessons=[
                lesson_text(v.ref, v.reason, v.change_reason)
                for v in sorted(
                    rejected, key=lambda v: str(getattr(v.node, "created_at", "")), reverse=True
                )[:LESSON_LIMIT]
            ],
        )


def is_phase_summary(name: str) -> bool:
    """A design-flow backstop decision (titled ``"<Phase> ... phase summary"``), not a real one."""
    return "phase summary" in (name or "").lower()


async def recent_decisions(project: Any, twin: Any, limit: int = DECISION_LIMIT) -> list[str]:
    """Lines for the newest real design decisions, de-duplicated by title."""
    decisions = [
        wp
        for wp in project.work_products
        if str(getattr(wp.type, "value", wp.type)) == "design_decision"
        and not is_phase_summary(str(wp.name))
    ]
    decisions.sort(key=lambda wp: wp.updated_at, reverse=True)
    seen: set[str] = set()
    lines: list[str] = []
    for wp in decisions:
        title = " ".join(str(wp.name).split())
        if title.lower() in seen:
            continue
        seen.add(title.lower())
        rationale: str | None = None
        if twin is not None:
            try:
                node = await twin.get_work_product(UUID(str(wp.id)))
                raw = (getattr(node, "metadata", None) or {}).get("rationale")
                if isinstance(raw, str) and raw.strip():
                    rationale = " ".join(raw.split())
            except Exception as exc:  # noqa: BLE001 -- the title alone still helps
                logger.debug("baseline_brief_decision_failed", wp_id=str(wp.id), error=str(exc))
        if rationale and len(rationale) > DECISION_RATIONALE_CHARS:
            rationale = rationale[: DECISION_RATIONALE_CHARS - 3] + "..."
        lines.append(f"- {title}" + (f": {rationale}" if rationale else ""))
        if len(lines) >= limit:
            break
    return lines


def other_record_counts(project: Any, item_node_ids: set[str]) -> str | None:
    """``simulation_result 4, documentation 2``: work products neither items nor decisions."""
    counts: dict[str, int] = {}
    for wp in project.work_products:
        kind = str(getattr(wp.type, "value", wp.type))
        if kind == "design_decision" or str(wp.id) in item_node_ids:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return None
    return ", ".join(f"{k} {n}" for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
