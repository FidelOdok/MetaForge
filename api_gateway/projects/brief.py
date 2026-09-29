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

The second copy is always the one that drifts, and here it would drift in
the direction of an agent confidently describing the wrong design.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

#: Most work products listed in the brief.
PROJECT_WP_LIMIT = 30
#: Work-product types worth inlining rather than merely naming.
BRIEF_DOC_TYPES = {"prd", "constraint_set"}
#: How many of those to inline, most-recently-updated first.
BRIEF_DOC_LIMIT = 3

DocExcerpt = Callable[[str], Awaitable[str | None]]


async def build_project_brief(project: Any, *, doc_excerpt: DocExcerpt) -> str:
    """Render ``project`` as the brief an agent is given before it works.

    ``doc_excerpt`` fetches the text of a work product by id. Injected so
    this module stays free of storage concerns and can be exercised without
    one.
    """
    lines = [
        f"You are working inside the MetaForge project **{project.name}** "
        f"(project_id `{project.id}`, status {project.status}).",
    ]
    if project.description:
        lines.append(f"Project intent: {project.description}")

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
                "Call twin.find_by_property (or project.get) if you need to see "
                "something not listed above."
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
    return "\n".join(lines)
