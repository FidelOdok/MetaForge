"""Auto-BOM population — rank components against a constraint set (MET-473).

Given a free-text design-intent query plus a list of structured
constraints, this module:

1. Runs the query through ``KnowledgeService.search`` to surface
   candidate components from the L1 corpus.
2. Extracts the unique ``mpn`` set from the result metadata.
3. For each MPN, calls ``service.extract_properties`` to fetch typed
   values for the constraint properties (Tier 1 verbatim, Tier 2 LLM,
   Tier 3 derived per MET-462/MET-422).
4. Scores each candidate by per-constraint pass/margin and the
   property-extraction confidence.
5. Returns the top-K ranked candidates with full breakdown so callers
   (the MCP tool, the dashboard, agents) can show *why* a component
   was chosen, not just the score.

Pure functions only — no LightRAG, no Neo4j, no HTTP. The MCP adapter
in ``tool_registry/tools/knowledge/adapter.py`` is the only place that
wires this to a live service.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Literal

from digital_twin.knowledge.service import ExtractedProperties, KnowledgeService
from digital_twin.knowledge.types import KnowledgeType

# Constraint comparison operators we recognise. ``>=``/``<=`` are
# margin-aware — a candidate that just barely passes scores lower than
# one with comfortable headroom. ``==``/``in`` are binary (pass / fail
# with margin 1.0 on match) since "is this the right family?" doesn't
# have headroom semantics.
ConstraintOp = Literal["<=", ">=", "==", "in"]
_ALLOWED_OPS: tuple[ConstraintOp, ...] = ("<=", ">=", "==", "in")


@dataclass(frozen=True)
class BomConstraint:
    """One requirement against a component property.

    ``op`` semantics:
    * ``>=``: candidate's property must be at least ``value``.
      Margin = (candidate - value) / |value| (positive = headroom).
    * ``<=``: candidate's property must be at most ``value``.
      Margin = (value - candidate) / |value|.
    * ``==``: candidate's property must equal ``value`` (string or numeric).
      Margin = 1.0 on match, 0.0 on miss.
    * ``in``:  candidate's property must be one of the values in ``value``
      (which must be a list). Margin = 1.0 on match.
    """

    property: str
    op: ConstraintOp
    value: Any
    weight: float = 1.0
    """Relative weight of this constraint in the final score. Defaults
    to 1.0; bump key constraints (supply voltage) higher and treat
    nice-to-haves lower."""


@dataclass(frozen=True)
class ConstraintResult:
    """Per-constraint evaluation for one candidate component."""

    property: str
    op: ConstraintOp
    required_value: Any
    extracted_value: Any
    extraction_method: str
    extraction_confidence: float
    passed: bool
    margin: float | None
    """Headroom as a unit fraction (e.g. 0.20 = 20% over the requirement).
    ``None`` when the property couldn't be parsed numerically or wasn't
    found in the datasheet."""


@dataclass(frozen=True)
class BomCandidate:
    """One ranked component suggestion."""

    mpn: str
    score: float
    """0.0–1.0. Higher = better fit. Failing any constraint forces 0.0."""
    all_constraints_passed: bool
    constraint_results: tuple[ConstraintResult, ...]
    extraction_confidence_avg: float
    source_path: str | None
    """``sourcePath`` of the first matching chunk surfaced by knowledge.search."""
    citation: str | None
    """First chunk's heading + chunk_index — for the "show why" UX."""


@dataclass(frozen=True)
class BomPopulateResult:
    """Aggregate result envelope returned to the MCP tool."""

    suggestions: tuple[BomCandidate, ...]
    candidates_evaluated: int
    """Total distinct MPNs we scored, including ones that failed
    constraints and got dropped from ``suggestions``."""
    total_search_hits: int
    query_time_ms: float


# ---------------------------------------------------------------------------
# Scoring (pure helpers — exhaustively unit-tested)
# ---------------------------------------------------------------------------


def _coerce_float(value: Any) -> float | None:
    """Parse a possibly-stringy value into a float; return ``None`` on fail.

    Datasheet values arrive as raw strings like ``"3.3"``, ``"3.3 V"``,
    ``"-40 to +85"``. For Tier-1 evaluation we only need the leading
    numeric token; richer parsing (ranges, units) is upstream's job.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # Strip a leading sign, then take the first contiguous numeric run.
    sign = 1.0
    if text[0] in "+-":
        sign = -1.0 if text[0] == "-" else 1.0
        text = text[1:]
    head = ""
    saw_dot = False
    for ch in text:
        if ch.isdigit():
            head += ch
        elif ch == "." and not saw_dot:
            head += ch
            saw_dot = True
        else:
            break
    if not head or head == ".":
        return None
    try:
        return sign * float(head)
    except ValueError:
        return None


def evaluate_constraint(
    constraint: BomConstraint,
    extracted_value: Any,
    extraction_method: str,
    extraction_confidence: float,
) -> ConstraintResult:
    """Evaluate one constraint against one extracted value.

    Returns a populated :class:`ConstraintResult`. A property the
    extractor couldn't find (``extracted_value is None`` /
    ``extraction_method == "not_found"``) automatically fails the
    constraint with ``margin=None``.
    """
    if extracted_value is None or extraction_method == "not_found":
        return ConstraintResult(
            property=constraint.property,
            op=constraint.op,
            required_value=constraint.value,
            extracted_value=None,
            extraction_method=extraction_method,
            extraction_confidence=extraction_confidence,
            passed=False,
            margin=None,
        )

    if constraint.op == "==":
        passed = extracted_value == constraint.value
        margin = 1.0 if passed else 0.0
    elif constraint.op == "in":
        if not isinstance(constraint.value, (list, tuple)):
            raise ValueError("constraint with op='in' requires a list/tuple of allowed values")
        passed = extracted_value in constraint.value
        margin = 1.0 if passed else 0.0
    else:
        # Numeric operators — both sides must coerce cleanly.
        extracted_num = _coerce_float(extracted_value)
        required_num = _coerce_float(constraint.value)
        if extracted_num is None or required_num is None:
            return ConstraintResult(
                property=constraint.property,
                op=constraint.op,
                required_value=constraint.value,
                extracted_value=extracted_value,
                extraction_method=extraction_method,
                extraction_confidence=extraction_confidence,
                passed=False,
                margin=None,
            )
        denom = abs(required_num) if required_num != 0 else 1.0
        if constraint.op == ">=":
            passed = extracted_num >= required_num
            margin = (extracted_num - required_num) / denom
        else:  # "<="
            passed = extracted_num <= required_num
            margin = (required_num - extracted_num) / denom

    return ConstraintResult(
        property=constraint.property,
        op=constraint.op,
        required_value=constraint.value,
        extracted_value=extracted_value,
        extraction_method=extraction_method,
        extraction_confidence=extraction_confidence,
        passed=passed,
        margin=margin,
    )


def score_candidate(results: list[ConstraintResult]) -> float:
    """Aggregate a candidate's per-constraint results into a single score.

    Returns 0.0 if any constraint fails. Otherwise the score is the
    confidence-weighted mean of ``min(margin, 1.0)`` clamped to
    ``[0, 1]`` per constraint (so a 200% headroom doesn't drown out
    tighter requirements). Each constraint's contribution is also
    weighted by its ``extraction_confidence`` so Tier-2 LLM matches
    rank below Tier-1 verbatim hits when the margins are equal.
    """
    if not results:
        return 0.0
    if any(not r.passed for r in results):
        return 0.0
    weighted_total = 0.0
    weight_sum = 0.0
    for r in results:
        margin = r.margin if r.margin is not None else 0.0
        # Saturate margin to 1.0 — 200% headroom is plenty.
        contribution = max(0.0, min(1.0, margin))
        weight = max(0.0, r.extraction_confidence)
        weighted_total += contribution * weight
        weight_sum += weight
    if weight_sum == 0.0:
        return 0.0
    return weighted_total / weight_sum


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


@dataclass
class _RawCandidate:
    """Internal — bookkeeping while we iterate MPNs."""

    mpn: str
    source_path: str | None = None
    citation: str | None = None
    extracted: ExtractedProperties | None = None
    seen_in_chunks: int = 0


def _extract_mpn_from_metadata(metadata: dict[str, Any]) -> str | None:
    """Pull a canonical MPN from a search-hit's metadata dict.

    Accepts the common keys we've seen across ingest paths.
    """
    for key in ("mpn", "MPN", "manufacturer_part_number"):
        value = metadata.get(key) if metadata else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def populate_bom(
    service: KnowledgeService,
    *,
    search_query: str,
    constraints: list[BomConstraint],
    top_k: int = 5,
    candidate_limit: int = 30,
    property_aliases: dict[str, list[str]] | None = None,
) -> BomPopulateResult:
    """Run the auto-BOM pipeline against a KnowledgeService.

    ``search_query`` is the free-text design-intent query (e.g.
    "low-power BLE microcontroller with USB"). The pipeline first
    surfaces ~``candidate_limit`` candidate chunks via
    ``service.search``, deduplicates them by MPN, then runs
    ``service.extract_properties`` once per MPN for the constraint set.

    Failed-constraint candidates are still returned to the caller (with
    score 0.0) when fewer than ``top_k`` candidates pass — gives the
    UX a "we looked but nothing fits" surface instead of an empty
    list. They're sorted to the end.
    """
    t0 = time.monotonic()

    # 1. Surface candidate chunks
    hits = await service.search(
        query=search_query,
        top_k=candidate_limit,
        knowledge_type=KnowledgeType.COMPONENT,
    )

    # 2. Deduplicate by MPN; keep the first chunk's source/citation per MPN.
    by_mpn: dict[str, _RawCandidate] = {}
    for hit in hits:
        mpn = _extract_mpn_from_metadata(getattr(hit, "metadata", {}) or {})
        if mpn is None:
            continue
        if mpn not in by_mpn:
            heading = getattr(hit, "heading", None)
            chunk_idx = getattr(hit, "chunk_index", None)
            citation_bits: list[str] = []
            if heading:
                citation_bits.append(str(heading))
            if chunk_idx is not None:
                citation_bits.append(f"chunk_{chunk_idx}")
            by_mpn[mpn] = _RawCandidate(
                mpn=mpn,
                source_path=getattr(hit, "source_path", None),
                citation=" / ".join(citation_bits) if citation_bits else None,
            )
        by_mpn[mpn].seen_in_chunks += 1

    # 3. Extract properties per candidate (one call per MPN, not per chunk)
    property_names = [c.property for c in constraints]
    for raw in by_mpn.values():
        try:
            raw.extracted = await service.extract_properties(
                mpn=raw.mpn,
                properties=property_names,
                aliases=property_aliases,
            )
        except Exception:  # noqa: BLE001 — bad extraction shouldn't kill the BOM run
            raw.extracted = None

    # 4. Score each candidate against the constraint set
    scored: list[BomCandidate] = []
    for raw in by_mpn.values():
        items_by_name: dict[str, Any] = {}
        if raw.extracted is not None:
            for extracted_item in raw.extracted.items:
                items_by_name[extracted_item.property_name] = extracted_item

        constraint_results: list[ConstraintResult] = []
        confidences: list[float] = []
        for constraint in constraints:
            prop_match = items_by_name.get(constraint.property)
            if prop_match is None:
                # Property wasn't requested or extractor returned nothing
                result = ConstraintResult(
                    property=constraint.property,
                    op=constraint.op,
                    required_value=constraint.value,
                    extracted_value=None,
                    extraction_method="not_found",
                    extraction_confidence=0.0,
                    passed=False,
                    margin=None,
                )
            else:
                result = evaluate_constraint(
                    constraint,
                    extracted_value=prop_match.value,
                    extraction_method=str(prop_match.extraction_method),
                    extraction_confidence=float(prop_match.confidence or 0.0),
                )
            constraint_results.append(result)
            confidences.append(result.extraction_confidence)

        candidate_score = score_candidate(constraint_results)
        confidence_avg = (sum(confidences) / len(confidences)) if confidences else 0.0
        scored.append(
            BomCandidate(
                mpn=raw.mpn,
                score=candidate_score,
                all_constraints_passed=all(r.passed for r in constraint_results)
                and bool(constraint_results),
                constraint_results=tuple(constraint_results),
                extraction_confidence_avg=confidence_avg,
                source_path=raw.source_path,
                citation=raw.citation,
            )
        )

    # 5. Sort by (passed-first, score desc, confidence desc) and trim.
    scored.sort(
        key=lambda c: (
            not c.all_constraints_passed,
            -c.score,
            -c.extraction_confidence_avg,
            c.mpn,
        )
    )
    truncated = tuple(scored[: max(0, top_k)])

    elapsed_ms = (time.monotonic() - t0) * 1000.0
    return BomPopulateResult(
        suggestions=truncated,
        candidates_evaluated=len(scored),
        total_search_hits=len(hits),
        query_time_ms=round(elapsed_ms, 2),
    )


def to_dict(result: BomPopulateResult) -> dict[str, Any]:
    """Render a :class:`BomPopulateResult` to a JSON-safe dict.

    Mirrors the shape the MCP tool returns to clients. Kept here (not
    in the adapter) so unit tests can assert the wire shape without
    pulling in the MCP machinery.
    """
    return {
        "suggestions": [
            {
                "mpn": c.mpn,
                "score": round(c.score, 4),
                "all_constraints_passed": c.all_constraints_passed,
                "extraction_confidence_avg": round(c.extraction_confidence_avg, 4),
                "source_path": c.source_path,
                "citation": c.citation,
                "constraint_results": [
                    {
                        "property": r.property,
                        "op": r.op,
                        "required_value": r.required_value,
                        "extracted_value": r.extracted_value,
                        "extraction_method": r.extraction_method,
                        "extraction_confidence": round(r.extraction_confidence, 4),
                        "passed": r.passed,
                        "margin": (
                            None if r.margin is None or math.isnan(r.margin) else round(r.margin, 4)
                        ),
                    }
                    for r in c.constraint_results
                ],
            }
            for c in result.suggestions
        ],
        "candidates_evaluated": result.candidates_evaluated,
        "total_search_hits": result.total_search_hits,
        "query_time_ms": result.query_time_ms,
    }


def parse_constraints(raw: list[dict[str, Any]]) -> list[BomConstraint]:
    """Coerce a list of wire-format constraint dicts into BomConstraints.

    Raises ``ValueError`` on bad shape — the MCP adapter catches and
    surfaces as an InvalidArgument error.
    """
    if not isinstance(raw, list):
        raise ValueError("'constraints' must be a list of {property, op, value} objects")
    out: list[BomConstraint] = []
    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            raise ValueError(f"constraints[{i}] must be an object")
        prop = c.get("property")
        op = c.get("op")
        value = c.get("value")
        weight = c.get("weight", 1.0)
        if not isinstance(prop, str) or not prop.strip():
            raise ValueError(f"constraints[{i}].property must be a non-empty string")
        if op not in _ALLOWED_OPS:
            raise ValueError(f"constraints[{i}].op must be one of {_ALLOWED_OPS}; got {op!r}")
        if "value" not in c:
            raise ValueError(f"constraints[{i}].value is required")
        try:
            weight_f = float(weight)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"constraints[{i}].weight must be a number") from exc
        if weight_f < 0:
            raise ValueError(f"constraints[{i}].weight must be >= 0")
        out.append(BomConstraint(property=prop.strip(), op=op, value=value, weight=weight_f))
    if not out:
        raise ValueError("'constraints' must include at least one constraint")
    return out


__all__ = (
    "BomCandidate",
    "BomConstraint",
    "BomPopulateResult",
    "ConstraintOp",
    "ConstraintResult",
    "evaluate_constraint",
    "parse_constraints",
    "populate_bom",
    "score_candidate",
    "to_dict",
)
