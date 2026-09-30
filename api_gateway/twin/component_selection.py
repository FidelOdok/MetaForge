"""Requirement-driven component selection (FORGE-265, gap G-C1).

``twin.record_component_selection`` (``api_gateway.twin.component_recorder``)
is presence-only today: it persists whichever candidate a caller already
decided on as a ``BOMItem``, with zero comparison against what the design
actually needs -- no margin, no rationale trail, no Decision. This module is
the missing "requirement-driven" half: given 2+ real candidate parts (each
with caller-asserted datasheet specs, e.g. a servo's published
``torque_kg_cm``) and the specs the design actually requires (e.g. "torque
must be >= 24.5 kg-cm"), it computes a real numeric margin per requirement
per candidate, records the selection as a real ``twin.record_decision``
(reusing that mechanism completely unchanged, same precedent as
``api_gateway.twin.trade_study``), and persists the winner as a real
``BOMItem`` via the existing ``component_recorder`` -- no new persistence
path.

This is a *threshold/margin* check, not a weighted-sum score --
``api_gateway.twin.trade_study``'s ``weighted_score`` shape (maximize a
weighted sum across criteria) is the wrong tool for "does this servo's rated
torque clear the required torque with enough margin," which is a per-spec
pass/fail plus a real numeric distance, not a single blended number. Hence a
sibling module rather than a new mode bolted onto ``trade_study.py``.

Honesty note (matching the precedent ``trade_study.py`` and
``docs/twin_schema.md`` already established for concept-option criteria
scores): the specs compared here are **caller-asserted real datasheet
values** -- a human or agent reads a real, published number off a real
datasheet and types it in. Nothing in this codebase parses a datasheet PDF
or scrapes a distributor page for these numbers; that ingestion pipeline
does not exist. ``digital_twin.catalog.taxonomy.CATEGORY_REGISTRY`` already
declares typed, range-queryable fields for categories like ``servo``
(``torque_kg_cm``, ``voltage_range``) and ``motor_driver`` (``v_in_max``,
``i_out_max``), but has no seeded sample data -- a live selection needs real
numbers supplied at call time.

Deliberately out of scope: any real datasheet-parsing/ingestion pipeline
(none exists anywhere in this codebase; a separate, much larger effort);
routing the margin check through ``twin_core.constraint_engine`` (that
engine evaluates work-product-scoped ``Constraint.expression`` strings via
``eval()`` against a graph-derived context -- the wrong shape and over-scoped
for a lightweight per-candidate numeric comparison).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.component_selection")

MarginOp = Literal[">=", "<="]


def check_spec_margin(required: float, actual: float, op: MarginOp) -> dict[str, Any]:
    """Compare one real candidate spec value against one required threshold
    (pure). ``op`` names the direction the requirement demands -- ">="
    (e.g. torque, current capacity: more is safer) or "<=" (e.g. a maximum
    input voltage: less is safer). ``margin`` is always signed so that a
    positive value means "passes with this much headroom" regardless of
    ``op`` -- for ">=" that's ``actual - required``, for "<=" it's
    ``required - actual``. ``margin_pct`` is ``None`` when ``required`` is
    zero (a percentage of zero is undefined, not zero)."""
    if op == ">=":
        margin = actual - required
        passed = actual >= required
    elif op == "<=":
        margin = required - actual
        passed = actual <= required
    else:
        raise ValueError(f"check_spec_margin: 'op' must be '>=' or '<=', got {op!r}")

    return {
        "pass": passed,
        "required": required,
        "actual": actual,
        "op": op,
        "margin": margin,
        "margin_pct": (margin / required * 100.0) if required != 0 else None,
    }


def check_candidate_against_requirements(
    specs: dict[str, float], required_specs: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Run :func:`check_spec_margin` for every named requirement against a
    candidate's own ``specs`` (pure). A requirement the candidate has no
    value for is reported as a failing, marginless check rather than being
    silently skipped -- "missing spec" and "spec present but fails" are both
    real reasons to reject a candidate, and only the caller-visible output
    can tell them apart if this doesn't distinguish them."""
    results: dict[str, dict[str, Any]] = {}
    for name, requirement in required_specs.items():
        op = requirement["op"]
        required_value = float(requirement["value"])
        if name not in specs:
            results[name] = {
                "pass": False,
                "required": required_value,
                "actual": None,
                "op": op,
                "margin": None,
                "margin_pct": None,
                "error": "candidate has no recorded value for this spec",
            }
            continue
        results[name] = check_spec_margin(required_value, float(specs[name]), op)
    return results


def _format_margin_summary(margins: dict[str, dict[str, Any]]) -> str:
    """Render per-requirement margin results as one human-readable string,
    e.g. ``"torque_kg_cm: FAIL margin -0.40 (required >= 20.0, actual
    19.6)"`` -- this is what ``reason_rejected`` shows on a Decision's
    ``alternatives`` entry, so it must carry the real numbers, not just a
    pass/fail label."""
    parts = []
    for name, m in margins.items():
        status = "PASS" if m["pass"] else "FAIL"
        if m.get("error"):
            parts.append(f"{name}: {status} ({m['error']})")
            continue
        parts.append(
            f"{name}: {status} margin {m['margin']:+.3g} "
            f"(required {m['op']} {m['required']:.3g}, actual {m['actual']:.3g})"
        )
    return "; ".join(parts)


def make_component_selector(twin: Any, *, decision_recorder: Any, component_recorder: Any) -> Any:
    """Return an async ``select(...)`` bound to a twin + the existing
    ``decision_recorder``/``component_recorder`` (the SAME callables
    ``twin.record_decision``/``twin.record_component_selection`` use -- no
    separate recording path for either)."""

    async def select(
        *,
        candidates: list[dict[str, Any]],
        required_specs: dict[str, dict[str, Any]],
        selected_mpn: str,
        category: str,
        purchase_unit: str,
        title: str,
        rationale: str,
        quantity: int = 1,
        domain: str = "electronics",
        project_id: str | None = None,
        requirement_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        by_mpn = {c["mpn"]: c for c in candidates}
        if selected_mpn not in by_mpn:
            raise ValueError(
                f"twin.select_component: 'selected_mpn' {selected_mpn!r} is not one of "
                f"the supplied 'candidates'"
            )

        with tracer.start_as_current_span("twin.select_component") as span:
            span.set_attribute("component_selection.candidate_count", len(candidates))
            span.set_attribute("component_selection.selected_mpn", selected_mpn)

            scored = [
                {
                    **c,
                    "margins": check_candidate_against_requirements(
                        c.get("specs") or {}, required_specs
                    ),
                }
                for c in candidates
            ]
            by_mpn_scored = {c["mpn"]: c for c in scored}
            selected = by_mpn_scored[selected_mpn]
            selected_meets_requirements = all(m["pass"] for m in selected["margins"].values())
            if not selected_meets_requirements:
                logger.warning(
                    "component_selected_despite_failed_requirement",
                    selected_mpn=selected_mpn,
                    margins=selected["margins"],
                )

            alternatives = [
                {
                    "option": f"{c['mpn']} ({c.get('manufacturer', 'unknown manufacturer')})",
                    "reason_rejected": _format_margin_summary(c["margins"]),
                }
                for c in scored
                if c["mpn"] != selected_mpn
            ]

            decision = await decision_recorder(
                title=title,
                rationale=rationale,
                alternatives=alternatives,
                parent_refs=requirement_ids,
                project_id=project_id,
                domain=domain,
            )

            recorded = await component_recorder(
                mpn=selected_mpn,
                manufacturer=selected.get("manufacturer", ""),
                category=category,
                purchase_unit=purchase_unit,
                quantity=quantity,
                specs={
                    **(selected.get("specs") or {}),
                    "requirement_margins": selected["margins"],
                },
                project_id=project_id,
            )

            # This Decision was generated from evaluating the selected
            # component against its requirements -- same GENERATED_FROM
            # semantics FORGE-289's design-loop winner and FORGE-262's
            # trade-study selection already established.
            decision_id = decision["node_id"]
            bom_item_id = recorded["node_id"]
            await twin.add_edge(UUID(decision_id), UUID(bom_item_id), EdgeType.GENERATED_FROM)

            logger.info(
                "component_selected",
                decision_node_id=decision_id,
                bom_item_node_id=bom_item_id,
                selected_mpn=selected_mpn,
                selected_meets_requirements=selected_meets_requirements,
                candidate_count=len(candidates),
                project_id=project_id,
            )
            return {
                **recorded,
                "decision_node_id": decision_id,
                "selected_mpn": selected_mpn,
                "selected_meets_requirements": selected_meets_requirements,
                "candidates": scored,
            }

    return select
