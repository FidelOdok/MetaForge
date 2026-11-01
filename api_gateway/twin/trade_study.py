"""Concept generation and trade study orchestration (FORGE-262, gap G-B2,
target lifecycle spec section 26.12's "Decision Agent").

``twin_core.consistency.gates``'s G5 (Concept Selection) already evaluates
whatever it finds: "Trade study performed" is a per-decision PASS once a
recorded ``twin.record_decision`` has a non-empty ``alternatives`` list --
that module's own docstring says explicitly it never builds the Decision
Agent that would GENERATE alternatives, run the trade study, and select one.
This module is exactly that missing half, and it needs almost no new
machinery:

- **Concept generation** needs no new tool at all. A "candidate
  architecture" is just a ``concept_option`` ``EngineeringEntity``
  (FORGE-262 added this ``entity_type`` alongside the existing
  ``intent``/``objective``/... set) recorded via the already-generic
  ``twin.record_engineering_entity`` -- its ``extra`` carries
  ``criteria_scores`` (e.g. ``{"mass_kg": 1.8, "cost_usd": 340, "risk": 3,
  "performance": 7}``) and ``evidence_backed_criteria`` (which of those keys
  came from a real measured source, e.g. a CAD work product's own
  ``mass_kg``, rather than an asserted number -- only mass has a real source
  anywhere in this codebase today; cost/risk/performance are always
  asserted, and this module never pretends otherwise). An agent (or a human,
  via the dashboard's own "+ add option" form) records 2-4 of these directly;
  there is no deterministic "architecture generator" algorithm to build, and
  none is specified anywhere in the Planner spec either.
- **The trade study + selection** is the one genuinely new piece:
  ``make_trade_study_selector`` fetches the named options, computes each
  one's weighted score from real recorded numbers (pure, unit-tested
  separately as ``weighted_score``), and records the selection as a real
  ``twin.record_decision`` -- reusing that mechanism completely unchanged
  rather than inventing a second "Decision-like" node type (same precedent
  FORGE-289's own docs already established: "No new Decision node type was
  built"). Every non-selected option becomes one real ``alternatives`` entry
  with its weighted score as ``reason_rejected`` -- not prose, and not
  empty, so G5's "Trade study performed" check now actually passes instead
  of reporting ``NOT_EVALUATED``. The Decision is also linked to the
  selected option via ``EdgeType.GENERATED_FROM`` (previously declared,
  reused by FORGE-289's design-loop-winner precedent for the identical
  "this Decision was generated from evaluating this real node" relationship)
  -- no new edge type either.

Deliberately out of scope: a live-editable weight grid with per-cell
evidence re-fetch (the dashboard recomputes weighted scores client-side from
already-fetched ``criteria_scores`` -- only the FINAL committed weights get
baked into the Decision's rationale at selection time); an LLM-driven
"generate candidate architectures" action (generation stays a chat-agent/CLI
action recording ``concept_option`` entities one at a time, matching every
other recorder in this codebase); grounding risk/performance in any real
measured source (none exists in general -- this module reports which
criteria are asserted rather than pretending otherwise).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.trade_study")


def weighted_score(criteria_scores: dict[str, float], weights: dict[str, float]) -> float:
    """Sum of ``weight * criteria_scores[criterion]`` over every criterion
    named in ``weights`` (pure). A criterion the option has no score for
    contributes 0 -- silently ignoring an unscored criterion would hide a
    real data gap, so ``score_concept_options`` surfaces missing scores
    separately rather than this function raising."""
    return sum(
        weight * criteria_scores.get(criterion, 0.0) for criterion, weight in weights.items()
    )


def score_concept_options(
    options: list[dict[str, Any]], weights: dict[str, float]
) -> list[dict[str, Any]]:
    """Score every option (pure) -- ``options`` are
    ``{"id", "title", "criteria_scores", "evidence_backed_criteria"}`` dicts.
    Returns the same shape plus ``"weighted_score"``, sorted highest first."""
    scored = [
        {**opt, "weighted_score": weighted_score(opt.get("criteria_scores") or {}, weights)}
        for opt in options
    ]
    return sorted(scored, key=lambda o: o["weighted_score"], reverse=True)


def make_trade_study_selector(twin: Any, *, decision_recorder: Any) -> Any:
    """Return an async ``select(...)`` bound to a twin + the existing
    ``decision_recorder`` (``api_gateway.twin.decision_recorder.
    make_decision_recorder``, the SAME callable ``twin.record_decision``
    uses -- no separate recording path)."""

    async def select(
        *,
        option_ids: list[str],
        selected_option_id: str,
        weights: dict[str, float],
        title: str,
        rationale: str,
        project_id: str | None = None,
        requirement_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if selected_option_id not in option_ids:
            raise ValueError(
                "twin.select_concept: 'selected_option_id' must be one of 'option_ids'"
            )
        with tracer.start_as_current_span("twin.select_concept") as span:
            span.set_attribute("trade_study.option_count", len(option_ids))

            options: list[dict[str, Any]] = []
            for option_id in option_ids:
                entity = await twin.get_engineering_entity(UUID(option_id))
                if entity is None:
                    raise ValueError(f"twin.select_concept: no concept_option {option_id!r}")
                if entity.entity_type != "concept_option":
                    raise ValueError(
                        f"twin.select_concept: {option_id!r} is a "
                        f"{entity.entity_type!r} entity, not a concept_option"
                    )
                options.append(
                    {
                        "id": option_id,
                        "title": entity.title or entity.statement or option_id,
                        "criteria_scores": entity.metadata.get("criteria_scores") or {},
                        "evidence_backed_criteria": entity.metadata.get("evidence_backed_criteria")
                        or [],
                    }
                )

            scored = score_concept_options(options, weights)
            selected = next(o for o in scored if o["id"] == selected_option_id)
            rejected = [o for o in scored if o["id"] != selected_option_id]

            alternatives = [
                {
                    "option": o["title"],
                    "reason_rejected": (
                        f"weighted score {o['weighted_score']:.3g} vs "
                        f"selected {selected['weighted_score']:.3g}"
                    ),
                }
                for o in rejected
            ]

            decision = await decision_recorder(
                title=title,
                rationale=rationale,
                alternatives=alternatives,
                parent_refs=requirement_ids,
                project_id=project_id,
                domain="systems",
            )

            # This Decision was generated from evaluating the selected
            # option -- same GENERATED_FROM semantics FORGE-289 already
            # established for a converged design loop's own winner.
            decision_id = UUID(decision["node_id"])
            await twin.add_edge(decision_id, UUID(selected_option_id), EdgeType.GENERATED_FROM)

            logger.info(
                "concept_selected",
                decision_node_id=str(decision_id),
                selected_option_id=selected_option_id,
                option_count=len(option_ids),
                project_id=project_id,
            )
            return {
                **decision,
                "selected_option_id": selected_option_id,
                "scores": scored,
            }

    return select
