"""The project brief — one implementation, several callers (FORGE-355).

Written for the chat harness (`api_gateway/chat/routes.py::_project_brief`)
and then needed verbatim by the MCP `metaforge://twin/brief/{project_id}`
resource. Copying it would have meant copying three fixes that are easy to
lose and expensive to re-learn:

* **FORGE-244** — work products sort newest-first before the positional
  cutoff. A busy project's current design sorted straight past a flat slice,
  so the brief described an old 3-joint URDF instead of the actual arm.
* **FORGE-86** — the most recent requirement docs are inlined, not just
  named. A list of filenames is not context.
* **MET-584** — a project with no recorded intent gets told to elicit one
  rather than being left to assume.
* **FORGE-530**: a project that has items is briefed from its baseline: one
  line per item at its current revision (``KEY@n``, key facts, evidence
  state), then recent real decisions, then the open run's own drafts. The
  newest-work-products list (and the FORGE-244 ordering) is kept for projects
  with no items. See ``baseline_brief.py``.

The second copy is always the one that drifts, and here it would drift in
the direction of an agent confidently describing the wrong design.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Most work products listed in the brief.
PROJECT_WP_LIMIT = 30
#: Work-product types worth inlining rather than merely naming.
BRIEF_DOC_TYPES = {"prd", "constraint_set"}
#: How many of those to inline, most-recently-updated first.
BRIEF_DOC_LIMIT = 3

#: Most characters of the project brief before the rest is replaced by a
#: pointer (FORGE-479). About 2.5k tokens; every turn pays for the brief again.
BRIEF_CHAR_LIMIT = 10_000
#: Env var overriding :data:`BRIEF_CHAR_LIMIT`.
BRIEF_CHAR_LIMIT_ENV = "METAFORGE_BRIEF_CHAR_LIMIT"
#: Where the full, uncapped brief can be read.
BRIEF_RESOURCE_TEMPLATE = "metaforge://twin/brief/{project_id}"

DocExcerpt = Callable[[str], Awaitable[str | None]]


def brief_char_limit() -> int:
    raw = os.environ.get(BRIEF_CHAR_LIMIT_ENV, "").strip()
    try:
        value = int(raw) if raw else BRIEF_CHAR_LIMIT
    except ValueError:
        return BRIEF_CHAR_LIMIT
    return value if value > 0 else BRIEF_CHAR_LIMIT


def cap_brief(text: str, project_id: str, limit: int | None = None) -> str:
    """Cap ``text`` at ``limit`` chars at a line boundary, pointing at the rest.

    The work-product list is already newest first (FORGE-244), so what a cap
    keeps is the most recent work. The closing directives sit at the end of the
    brief, so they are re-appended rather than cut: a brief that lost its
    ``project_id`` instructions would be worse than a shorter list.
    """
    cap = brief_char_limit() if limit is None else limit
    if len(text) <= cap:
        return text
    marker_head = "\n\n(Project brief shortened"
    tail_start = text.rfind("\nAny CAD model you generate")
    tail = text[tail_start:] if tail_start != -1 else ""
    link = BRIEF_RESOURCE_TEMPLATE.format(project_id=project_id)
    note = f"{marker_head}: {len(text) - cap} chars omitted. Read the full brief at {link}.)"
    room = max(0, cap - len(note) - len(tail))
    head = text[: tail_start if tail_start != -1 else len(text)][:room]
    cut = head.rfind("\n")
    if cut > room // 2:
        head = head[:cut]
    return f"{head}{note}{tail}"


def _call_run_id() -> str | None:
    """The design-flow run of the current MCP call, if any."""
    try:
        from api_gateway.twin.item_revisions import revision_run_id
    except ImportError:  # pragma: no cover -- ships with the gateway
        return None
    return revision_run_id()


def _record_brief(mode: str, *, project_id: str, items: int, drafts: int, text: str) -> None:
    capped = len(text) > brief_char_limit()
    logger.info(
        "project_brief_composed",
        project_id=project_id,
        mode=mode,
        items=items,
        drafts=drafts,
        chars=len(text),
        capped=capped,
    )
    try:
        from observability.metrics import collector_for

        collector_for("metaforge-gateway").record_project_brief(mode, len(text), capped)
    except Exception as exc:  # noqa: BLE001 -- metrics never break the brief
        logger.debug("project_brief_metric_failed", error=str(exc))


async def _baseline_lines(
    project: Any, twin: Any, run_id: str | None, doc_excerpt: DocExcerpt
) -> tuple[list[str], int, int] | None:
    """The item-baseline section of the brief, or ``None`` for a legacy project."""
    from api_gateway.projects.baseline_brief import (
        other_record_counts,
        read_baseline,
        recent_decisions,
    )

    try:
        baseline = await read_baseline(twin, project.id, run_id)
    except Exception as exc:  # noqa: BLE001 -- fall back to the legacy list
        logger.warning("project_brief_baseline_failed", project_id=str(project.id), error=str(exc))
        return None
    if baseline is None or not (baseline.entries or baseline.drafts):
        return None
    lines = [
        f"\nCurrent design baseline ({len(baseline.entries)} items, one line each at its "
        "current revision, KEY@n). Older, superseded and rejected revisions are not "
        "listed; `twin.item_history` with the key shows them:"
    ]
    lines.extend(entry.line() for entry in baseline.entries)
    if baseline.drafts:
        lines.append(
            f"\nDrafts written by this run ({len(baseline.drafts)}). They are not part of "
            "the baseline until the phase gate approves them:"
        )
        lines.extend(entry.line() for entry in baseline.drafts)
    if baseline.lessons:
        lines.append("\nAlready tried and rejected (do not repeat these unchanged):")
        lines.extend(f"- {lesson}" for lesson in baseline.lessons)
    decisions = await recent_decisions(project, twin)
    if decisions:
        lines.append("\nRecent design decisions (newest first):")
        lines.extend(decisions)
    item_node_ids = {str(wp.id) for wp in project.work_products if _is_item_type(wp)}
    others = other_record_counts(project, item_node_ids)
    if others:
        lines.append(f"\nOther records in this project: {others}.")
    # FORGE-86, now from the current revision only: the old newest-first pick
    # inlined three revisions of the same constraint set.
    for node_id, label in baseline.docs[:BRIEF_DOC_LIMIT]:
        excerpt = await doc_excerpt(node_id)
        if excerpt:
            lines.append(f"\n### {label}\n{excerpt}")
    return lines, len(baseline.entries), len(baseline.drafts)


def _is_item_type(wp: Any) -> bool:
    from twin_core.items import is_definition

    return is_definition(str(getattr(wp.type, "value", wp.type)))


async def build_project_brief(
    project: Any,
    *,
    doc_excerpt: DocExcerpt,
    full: bool = False,
    twin: Any = None,
    run_id: str | None = None,
) -> str:
    """Render ``project`` as the brief an agent is given before it works.

    ``doc_excerpt`` fetches the text of a work product by id. Injected so
    this module stays free of storage concerns and can be exercised without
    one.

    With a ``twin`` that holds items for this project (FORGE-530) the brief
    lists the item baseline instead of the newest work products; ``run_id``
    (default: the design-flow run of the current MCP call) adds that run's
    own drafts. Without either, the brief is exactly the legacy one.

    The brief is capped at :func:`brief_char_limit` characters (FORGE-479)
    with a pointer to the ``metaforge://twin/brief/<id>`` resource; the
    resource itself passes ``full=True`` so the link leads to everything.
    """
    lines = [
        f"You are working inside the MetaForge project **{project.name}** "
        f"(project_id `{project.id}`, status {project.status}).",
    ]
    if project.description:
        lines.append(f"Project intent: {project.description}")

    baseline = None
    if twin is not None:
        baseline = await _baseline_lines(project, twin, run_id or _call_run_id(), doc_excerpt)
    if baseline is not None:
        lines.extend(baseline[0])
    else:
        await _legacy_work_product_lines(project, lines, doc_excerpt)

    _closing_lines(project, lines)
    text = "\n".join(lines)
    _record_brief(
        "baseline" if baseline is not None else "legacy",
        project_id=str(project.id),
        items=baseline[1] if baseline is not None else 0,
        drafts=baseline[2] if baseline is not None else 0,
        text=text,
    )
    return text if full else cap_brief(text, str(project.id))


async def _legacy_work_product_lines(
    project: Any, lines: list[str], doc_excerpt: DocExcerpt
) -> None:
    """The pre-item brief body: newest work products and requirement excerpts."""
    # FORGE-244: work_products is insertion order (oldest first) -- a busy
    # project's newest, most-relevant work (live-observed: 14 AR4 robot-arm
    # parts + its robot description, all at positions 38-52) sorted straight
    # past a flat [:PROJECT_WP_LIMIT] slice, so the brief described an old
    # 3-joint URDF instead of the actual current design. Sorting by recency
    # first means the newest work is always what a plain positional cutoff
    # keeps, not what it drops.
    wps_by_recency = sorted(project.work_products, key=lambda wp: wp.updated_at, reverse=True)
    wps = wps_by_recency[:PROJECT_WP_LIMIT]
    if wps:
        lines.append(
            f"\nExisting work products in this project "
            f"({len(project.work_products)}, newest first):"
        )
        for wp in wps:
            lines.append(f"- {wp.name} — {wp.type} (status {wp.status})")
        remaining = len(project.work_products) - PROJECT_WP_LIMIT
        if remaining > 0:
            lines.append(
                f"- …and {remaining} more (older) work product(s) not shown here. "
                "Read metaforge://twin/brief/"
                f"{project.id} or call twin.find_by_property (or project.get) "
                "if you need to see something not listed above."
            )
    else:
        lines.append("\nThis project has no work products yet.")

    # FORGE-86: inline the actual content of the most-recently-updated
    # requirement docs, not just their names — see _brief_doc_excerpt.
    doc_wps = sorted(
        (wp for wp in project.work_products if wp.type in BRIEF_DOC_TYPES),
        key=lambda wp: wp.updated_at,
        reverse=True,
    )[:BRIEF_DOC_LIMIT]
    for wp in doc_wps:
        excerpt = await doc_excerpt(wp.id)
        if excerpt:
            lines.append(f"\n### {wp.name} ({wp.type})\n{excerpt}")


def _closing_lines(project: Any, lines: list[str]) -> None:
    """Directives every brief ends with, whatever its body."""
    # MET-584: requirements-discovery directive. Chat has no gates, so the
    # elicitation nudge lives in the brief — the enforcement twin of this is
    # the design-flow Requirements gate (MET-582/583), and — for intent/needs
    # specifically — the G0/G1 gates (FORGE-48, epic FORGE-35).
    types = {str(getattr(wp.type, "value", wp.type)) for wp in project.work_products}
    if not types & {"intent", "stakeholder_need"}:
        lines.append(
            "\nThis project has NO recorded intent or stakeholder needs (no "
            "intent or stakeholder_need entity). Before substantive design "
            "work, elicit WHY this product exists and who it's for — ask the "
            "user rather than assuming. Record the intent first with "
            "`twin.record_engineering_entity` (`entity_type='intent'`, give it "
            "a short `title` so later entries can reference it), then any "
            "stakeholder needs the same way (`entity_type='stakeholder_need'`, "
            "`parent_refs=[the intent's title]`, `relation='motivates'`)."
        )
    if not types & {"prd", "constraint_set"}:
        lines.append(
            "\nThis project has NO recorded requirements or constraints (no prd "
            "or constraint_set work product). Before substantive design work — "
            "authoring or committing geometry, selecting components — elicit the "
            "key quantified requirements from the user (loads, mass/envelope "
            "budgets, power, cost, safety factors) and record them with "
            "`twin.record_constraint_set` (and the rationale with "
            "`twin.record_decision`). Ask before you assume. If an intent/need "
            "was recorded above, link each requirement back to it with "
            "`parent_refs=[the need's title]`."
        )
    if types & {"prd", "constraint_set"}:
        lines.append(
            "\nAs the design goes deeper — sizing a specific subsystem or "
            "component — quantify what THAT specifically needs (e.g. this "
            "leg's actuator torque, not just the system's overall payload) "
            "and record it with `twin.record_constraint_set`, setting "
            "`parent_refs` to the higher-level requirement it implements. This "
            "keeps the chain from stated intent down to a specific part "
            "traceable instead of stopping at the system level."
        )

    lines.append(
        f"\nAny CAD model you generate in this project is NOT saved until you call "
        f'`twin.commit_geometry` with `project_id="{project.id}"` — do this before your '
        f"final answer whenever you generated or modified geometry this turn. Record "
        f'design decisions the same way with `twin.record_decision` (`project_id="{project.id}"`). '
        f"Ground your answers in the work products above."
    )
    lines.append(
        f"\nWhen checking what's currently broken, call `twin.constraint_violations` "
        f'with `project_id="{project.id}"` — without it, the result may include '
        f"other projects' violations (FORGE-75)."
    )
    lines.append(
        f'\nAlways pass `project_id="{project.id}"` on `twin.record_engineering_entity` '
        f"too (intent, stakeholder_need, objective, risk, budget, invariant, waiver, "
        f"release_approval, ...) — without it the entity is unscoped and invisible to "
        f"this project's gate checks (e.g. a waiver recorded with no project_id never "
        f"counts toward the G8 release gate, FORGE-78)."
    )


# ---------------------------------------------------------------------------
# The other project resources (FORGE-355)
# ---------------------------------------------------------------------------
#
# Each renders an existing source rather than computing anything new. A
# resource that did its own analysis would be a second opinion the dashboard
# and the gate do not share, and the one an agent reads would be the one
# nobody validated.


def render_hierarchy(nodes: list[Any]) -> str:
    """The product breakdown, indented, with rolled-up mass and cost."""
    if not nodes:
        return "This project has no product hierarchy recorded yet."

    by_parent: dict[Any, list[Any]] = {}
    for node in nodes:
        by_parent.setdefault(getattr(node, "parent_id", None), []).append(node)

    lines = ["# Product hierarchy", ""]

    def walk(parent: Any, depth: int) -> None:
        for node in sorted(by_parent.get(parent, []), key=lambda n: str(getattr(n, "name", ""))):
            bits = []
            mass = getattr(node, "mass_kg", None)
            cost = getattr(node, "cost", None)
            if mass is not None:
                bits.append(f"{mass} kg")
            if cost is not None:
                bits.append(f"cost {cost}")
            suffix = f" — {', '.join(bits)}" if bits else ""
            lines.append(f"{'  ' * depth}- {getattr(node, 'name', '?')}{suffix}")
            walk(getattr(node, "id", None), depth + 1)

    walk(None, 0)
    return "\n".join(lines)


def render_requirements(rows: list[Any]) -> str:
    """Requirements against the evidence that verifies them.

    Statuses come straight from the matrix (FORGE-318). ``no_data`` is
    reported as itself — an unverified requirement must never read as a
    satisfied one.
    """
    if not rows:
        return "No requirements recorded for this project yet."

    lines = ["# Requirement matrix", "", "| Requirement | Status | Evidence |", "|---|---|---|"]
    for row in rows:
        name = getattr(row, "requirement_name", None) or getattr(row, "requirement_id", "?")
        status = getattr(row, "status", "no_data")
        evidence = getattr(row, "evidence", None) or []
        lines.append(f"| {name} | `{status}` | {len(evidence)} |")

    counts: dict[str, int] = {}
    for row in rows:
        counts[str(getattr(row, "status", "no_data"))] = (
            counts.get(str(getattr(row, "status", "no_data")), 0) + 1
        )
    unverified = counts.get("no_data", 0)
    lines.append("")
    lines.append(
        f"{len(rows)} requirement(s): " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    )
    if unverified:
        lines.append(
            f"\n{unverified} requirement(s) have no evidence at all. That is "
            "not a pass — it is a gap."
        )
    return "\n".join(lines)


def render_entities(entities: list[Any], *, kind: str, title: str) -> str:
    """Recorded decisions or risks, newest first."""
    if not entities:
        return f"No {kind} entities recorded for this project yet."

    ordered = sorted(
        entities,
        key=lambda e: getattr(e, "updated_at", None) or getattr(e, "created_at", 0),
        reverse=True,
    )
    lines = [f"# {title}", ""]
    for entity in ordered:
        lines.append(f"## {getattr(entity, 'title', None) or getattr(entity, 'id', '?')}")
        body = getattr(entity, "rationale", None) or getattr(entity, "description", None)
        if body:
            lines.append(str(body))
        alternatives = getattr(entity, "alternatives", None)
        if alternatives:
            lines.append("Alternatives considered: " + ", ".join(str(a) for a in alternatives))
        lines.append("")
    return "\n".join(lines).rstrip()
