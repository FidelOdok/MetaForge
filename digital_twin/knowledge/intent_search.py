"""Intent-search orchestration — build-vs-buy component search (MET-436, mode 3).

Ties three pieces together for one free-text intent:

1. ``intent_translator.translate_intent`` — LLM step, text -> category
   candidates + inferred spec bounds.
2. Per candidate: try the parametric catalog (mode 1,
   ``digital_twin.catalog.query``) first; fall back to the existing,
   unmodified ``bom_populator.populate_bom`` (mode 2, fuzzy/semantic) when
   the parametric query returns nothing.
3. Role resolution against ``subsystem_templates`` so a recognized
   subsystem's role list doesn't silently truncate to whatever the LLM
   happened to mention.

Results are partitioned strictly on ``purchase_unit`` into
``buy_complete`` (COTS assemblies) and ``build_from_parts`` (discrete
components) — the two are never merged into one ranked list; see the
MET-436 plan for why (a $219 complete board and a $6 IC answer the
question at different levels and conflating them is a known failure mode).

This module imports from ``digital_twin.catalog`` — both are sibling
sub-packages under the ``digital_twin`` layer (see ``digital_twin/CLAUDE.md``:
the "do not import" list bars *other top-level layers*
— orchestrator/domain_agents/api_gateway/skill_registry/mcp_core/
tool_registry — not sibling packages within digital_twin itself). The
one-directional rule that matters is ``digital_twin.catalog`` must never
import ``digital_twin.knowledge`` (documented in
``digital_twin/catalog/__init__.py``); that import only ever flows this
direction.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import structlog

from digital_twin.catalog.query import CatalogQueryResult, ComponentCatalogRow, ComponentFilter
from digital_twin.catalog.taxonomy import PurchaseUnit
from digital_twin.knowledge.bom_populator import BomCandidate, BomConstraint, populate_bom
from digital_twin.knowledge.intent_translator import (
    CategoryCandidate,
    IntentConstraint,
    IntentLLM,
    translate_intent,
)
from digital_twin.knowledge.service import KnowledgeService
from digital_twin.knowledge.subsystem_templates import SubsystemTemplate
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.intent_search")

ParametricSearchCallable = Callable[
    [str, list[ComponentFilter], int], Awaitable[CatalogQueryResult]
]
"""Injected mode-1 search callable: ``(category, filters, limit) -> CatalogQueryResult``.

Typed as a plain callable (not the concrete ``ComponentCatalogStore``) so
this module stays testable with a fake and doesn't need to know how the
store is constructed — the MCP adapter wires a real
``store.query(CatalogQuery(...))`` closure in.
"""

_MAX_OP_DOWNGRADE_WARNING = (
    "constraint {property!r} op {op!r} downgraded to {downgraded!r} for the fuzzy "
    "fallback (strict inequality not supported by bom_populator's constraint set)"
)
_DROPPED_OP_WARNING = (
    "constraint {property!r} op {op!r} has no fuzzy-fallback equivalent — dropped, "
    "not silently mapped to the wrong operator"
)


@dataclass(frozen=True)
class RoleCandidate:
    """One search hit for a role/category, tagged with which mode produced it."""

    mpn: str
    source: Literal["parametric", "fuzzy_fallback"]
    score: float | None
    """``BomCandidate.score`` for fuzzy hits; ``None`` for parametric hits —
    a range query returns only passing rows, so there's no natural margin
    score to report (see the plan's confidence-reconciliation risk note)."""
    cost_usd: float | None
    raw: ComponentCatalogRow | BomCandidate


@dataclass(frozen=True)
class RoleResult:
    """Search outcome for one category/role slot."""

    role: str | None
    category: str
    purchase_unit: PurchaseUnit
    constraints: tuple[IntentConstraint, ...]
    candidates: tuple[RoleCandidate, ...]
    search_mode_used: Literal["parametric", "fuzzy_fallback", "none"]


@dataclass(frozen=True)
class IntentSearchResult:
    """Full outcome of one ``search_intent()`` call."""

    intent_text: str
    subsystem_hint: str | None
    buy_complete: tuple[RoleResult, ...]
    build_from_parts: tuple[RoleResult, ...]
    warnings: tuple[str, ...]
    translation_reasoning: str | None
    query_time_ms: float


def _downgrade_constraint(constraint: IntentConstraint) -> tuple[BomConstraint | None, str | None]:
    """Down-convert one 8-op ``IntentConstraint`` to ``bom_populator``'s 4-op set.

    ``==``/``in``/``>=``/``<=`` carry over unchanged. ``>``/``<`` degrade to
    their nearest ``>=``/``<=`` — a defensible approximation, same
    direction, just loses strict-inequality nuance. ``!=``/``not_in`` have
    no correct equivalent (there is no "not equal" operator in the fuzzy
    fallback) and are dropped with a warning rather than silently mapped to
    the wrong-direction ``==``/``in``.
    """
    if constraint.op in ("==", "in", ">=", "<="):
        return (
            BomConstraint(
                property=constraint.property,
                op=constraint.op,  # type: ignore[arg-type]  # narrowed by the branch above
                value=constraint.value,
                weight=constraint.weight,
            ),
            None,
        )
    if constraint.op in (">", "<"):
        downgraded: Literal[">=", "<="] = ">=" if constraint.op == ">" else "<="
        return (
            BomConstraint(
                property=constraint.property,
                op=downgraded,
                value=constraint.value,
                weight=constraint.weight,
            ),
            _MAX_OP_DOWNGRADE_WARNING.format(
                property=constraint.property, op=constraint.op, downgraded=downgraded
            ),
        )
    return None, _DROPPED_OP_WARNING.format(property=constraint.property, op=constraint.op)


def _resolve_roles(
    candidates: tuple[CategoryCandidate, ...],
    subsystem_hint: str | None,
    subsystem_templates: Mapping[str, SubsystemTemplate] | None,
) -> tuple[list[CategoryCandidate], list[str]]:
    """Merge the LLM's own category candidates with a known subsystem template.

    LLM-supplied roles always win on name conflict. Template roles the LLM
    didn't mention are appended as zero-confidence, zero-constraint
    candidates (still worth searching — a category-only query surfaces
    *some* options for that slot) and flagged in the returned warnings so
    callers can tell "known-complete list" from "best guess." If the
    subsystem has a COTS category and the LLM didn't already offer it, it's
    added too — the whole point of build-vs-buy is that the complete-board
    option shouldn't require the LLM to have thought of it.
    """
    warnings: list[str] = []
    llm_roles = {c.role for c in candidates if c.role is not None}
    resolved = list(candidates)

    template = (
        subsystem_templates.get(subsystem_hint)
        if (subsystem_templates and subsystem_hint)
        else None
    )

    if template is not None:
        for role_def in template.roles:
            if role_def.role in llm_roles:
                continue
            resolved.append(
                CategoryCandidate(
                    category=role_def.category,
                    purchase_unit="discrete_part",
                    role=role_def.role,
                    confidence=0.0,
                    constraints=(),
                )
            )
            warnings.append(
                f"role {role_def.role!r} (category {role_def.category!r}) added from the "
                f"{template.subsystem!r} subsystem template — the model's own response "
                "didn't include it; treat as a suggested slot, not a confirmed requirement."
            )

        if template.cots_category is not None:
            has_cots = any(
                c.purchase_unit == "cots_assembly" and c.category == template.cots_category
                for c in resolved
            )
            if not has_cots:
                resolved.append(
                    CategoryCandidate(
                        category=template.cots_category,
                        purchase_unit="cots_assembly",
                        role=None,
                        confidence=0.0,
                        constraints=(),
                    )
                )
                warnings.append(
                    f"added complete-assembly category {template.cots_category!r} from the "
                    f"{template.subsystem!r} template's buy-complete option — not present in "
                    "the model's own response."
                )
    elif subsystem_hint is not None:
        warnings.append(
            f"subsystem {subsystem_hint!r} has no known template — role list is model-inferred "
            "only and may be incomplete."
        )

    return resolved, warnings


async def _search_one_role(
    candidate: CategoryCandidate,
    *,
    intent_text: str,
    search_parametric: ParametricSearchCallable,
    knowledge_service: KnowledgeService,
    top_k: int,
) -> tuple[RoleResult, list[str]]:
    warnings: list[str] = []
    filters = [
        ComponentFilter(property=c.property, op=c.op, value=c.value) for c in candidate.constraints
    ]

    catalog_result: CatalogQueryResult | None = None
    try:
        catalog_result = await search_parametric(candidate.category, filters, top_k)
    except Exception as exc:  # noqa: BLE001 — a broken parametric backend must not kill intent search
        warnings.append(f"parametric search failed for category {candidate.category!r}: {exc}")

    if catalog_result is not None and catalog_result.rows:
        role_candidates = tuple(
            RoleCandidate(
                mpn=row.mpn, source="parametric", score=None, cost_usd=row.cost_usd, raw=row
            )
            for row in catalog_result.rows
        )
        return (
            RoleResult(
                role=candidate.role,
                category=candidate.category,
                purchase_unit=candidate.purchase_unit,
                constraints=candidate.constraints,
                candidates=role_candidates,
                search_mode_used="parametric",
            ),
            warnings,
        )

    # Mode-1 miss (or error) — fall back to the existing fuzzy path (mode 2).
    bom_constraints: list[BomConstraint] = []
    for constraint in candidate.constraints:
        converted, warn = _downgrade_constraint(constraint)
        if warn:
            warnings.append(warn)
        if converted is not None:
            bom_constraints.append(converted)

    role_candidates2: tuple[RoleCandidate, ...] = ()
    search_mode: Literal["parametric", "fuzzy_fallback", "none"] = "none"
    try:
        bom_result = await populate_bom(
            knowledge_service,
            search_query=f"{candidate.category} {intent_text}",
            constraints=bom_constraints,
            top_k=top_k,
        )
        role_candidates2 = tuple(
            RoleCandidate(
                mpn=bc.mpn, source="fuzzy_fallback", score=bc.score, cost_usd=None, raw=bc
            )
            for bc in bom_result.suggestions
        )
        if role_candidates2:
            search_mode = "fuzzy_fallback"
    except Exception as exc:  # noqa: BLE001 — fallback failure must not kill intent search
        warnings.append(f"fuzzy fallback failed for category {candidate.category!r}: {exc}")

    return (
        RoleResult(
            role=candidate.role,
            category=candidate.category,
            purchase_unit=candidate.purchase_unit,
            constraints=candidate.constraints,
            candidates=role_candidates2,
            search_mode_used=search_mode,
        ),
        warnings,
    )


async def search_intent(
    *,
    intent_text: str,
    llm: IntentLLM,
    search_parametric: ParametricSearchCallable,
    knowledge_service: KnowledgeService,
    subsystem_templates: Mapping[str, SubsystemTemplate] | None = None,
    known_categories: Sequence[str] | None = None,
    top_k_per_role: int = 5,
) -> IntentSearchResult:
    """Translate ``intent_text`` and search each resulting category/role.

    Known gap (per the MET-436 plan, not solved here): the mode-2 fallback
    for ``cots_assembly`` roles needs ``KnowledgeType.COMPONENT`` search
    results taggable by ``purchase_unit`` to reliably distinguish "a
    complete board" from "a random discrete part that happened to match" —
    today's knowledge-ingestion pipeline has no such metadata key, so a
    fuzzy-fallback hit for a COTS role should be treated as lower-trust than
    a parametric hit for the same role.
    """
    with tracer.start_as_current_span("intent_search.search_intent") as span:
        t0 = time.monotonic()
        span.set_attribute("intent.text_length", len(intent_text))

        known_subsystems = list(subsystem_templates.keys()) if subsystem_templates else None
        translation = await translate_intent(
            llm,
            intent_text=intent_text,
            known_categories=known_categories,
            known_subsystems=known_subsystems,
        )

        warnings: list[str] = []
        if translation.parse_error:
            warnings.append(f"intent translation issue: {translation.parse_error}")

        candidates, role_warnings = _resolve_roles(
            translation.candidates, translation.subsystem_hint, subsystem_templates
        )
        warnings.extend(role_warnings)

        buy_complete: list[RoleResult] = []
        build_from_parts: list[RoleResult] = []
        for candidate in candidates:
            role_result, role_warns = await _search_one_role(
                candidate,
                intent_text=intent_text,
                search_parametric=search_parametric,
                knowledge_service=knowledge_service,
                top_k=top_k_per_role,
            )
            warnings.extend(role_warns)
            if candidate.purchase_unit == "cots_assembly":
                buy_complete.append(role_result)
            else:
                build_from_parts.append(role_result)

        elapsed_ms = (time.monotonic() - t0) * 1000.0
        span.set_attribute("intent.buy_complete_count", len(buy_complete))
        span.set_attribute("intent.build_from_parts_count", len(build_from_parts))
        span.set_attribute("intent.warning_count", len(warnings))
        logger.info(
            "intent_search_completed",
            subsystem=translation.subsystem_hint,
            buy_complete_count=len(buy_complete),
            build_from_parts_count=len(build_from_parts),
            warning_count=len(warnings),
            duration_ms=round(elapsed_ms, 2),
        )
        return IntentSearchResult(
            intent_text=intent_text,
            subsystem_hint=translation.subsystem_hint,
            buy_complete=tuple(buy_complete),
            build_from_parts=tuple(build_from_parts),
            warnings=tuple(warnings),
            translation_reasoning=translation.reasoning,
            query_time_ms=round(elapsed_ms, 2),
        )


def _role_result_to_dict(result: RoleResult) -> dict[str, Any]:
    return {
        "role": result.role,
        "category": result.category,
        "purchase_unit": result.purchase_unit,
        "search_mode_used": result.search_mode_used,
        "constraints": [
            {
                "property": c.property,
                "op": c.op,
                "value": c.value,
                "source": c.source.value,
                "confidence": round(c.inference_confidence, 4),
            }
            for c in result.constraints
        ],
        "candidates": [
            {
                "mpn": rc.mpn,
                "source": rc.source,
                "score": None if rc.score is None else round(rc.score, 4),
                "cost_usd": rc.cost_usd,
            }
            for rc in result.candidates
        ],
    }


def to_dict(result: IntentSearchResult) -> dict[str, Any]:
    """Render an ``IntentSearchResult`` to a JSON-safe dict — the MCP wire shape.

    Kept here (not in the adapter), mirroring ``bom_populator.to_dict``, so
    unit tests can assert the wire shape without pulling in MCP machinery.
    """
    return {
        "intent_text": result.intent_text,
        "subsystem_hint": result.subsystem_hint,
        "buy_complete": [_role_result_to_dict(r) for r in result.buy_complete],
        "build_from_parts": [_role_result_to_dict(r) for r in result.build_from_parts],
        "warnings": list(result.warnings),
        "translation_reasoning": result.translation_reasoning,
        "query_time_ms": result.query_time_ms,
    }


__all__ = [
    "IntentSearchResult",
    "ParametricSearchCallable",
    "RoleCandidate",
    "RoleResult",
    "search_intent",
    "to_dict",
]
