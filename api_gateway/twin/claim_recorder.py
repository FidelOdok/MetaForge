"""Requirement satisfaction claim recorder for twin.record_claim (FORGE-65,
epic FORGE-35, Phase 6: Evidence Integration).

Persists the FACT of a claim ("this artefact satisfies this requirement,
citing this evidence") as a real graph edge -- see
``twin_core.consistency.claims`` for why ``status`` is computed live rather
than stored here. Resolves ``requirement_ref``/``artefact_ref``/
``evidence_refs`` through the same exact-name-or-UUID resolver every
recorder in this package uses (``_ref_resolver.py``); the artefact ref
additionally searches ``WorkProduct.name`` (``include_work_products=True``)
since an artefact -- a CAD model, a schematic -- is a WorkProduct, not a
Constraint or EngineeringEntity, and the shared resolver didn't search that
node kind until this ticket.

Each cited evidence ref is verified to actually resolve to an
``entity_type="evidence"`` EngineeringEntity -- a claim citing a Constraint
or an unrelated entity as its "evidence" would be a silent lie the graph
could never catch later.
"""

from __future__ import annotations

from typing import Any

import structlog

from api_gateway.twin._ref_resolver import resolve_ref, resolve_refs
from observability.tracing import get_tracer
from twin_core.consistency.claims import CLAIM_EDGE_KIND, evaluate_claim
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.claim_recorder")


#: Work product types that only ever show intent (FORGE-759).
ILLUSTRATIVE_TYPES = frozenset({"design_sketch", "render", "image"})


def _type_name(wp: Any) -> str:
    t = getattr(wp, "type", "")
    return str(getattr(t, "value", t))


def is_illustrative(wp: Any) -> bool:
    """A sketch, render or image, or anything its metadata marks illustrative."""
    meta = getattr(wp, "metadata", None) or {}
    return _type_name(wp) in ILLUSTRATIVE_TYPES or meta.get("authority") == "illustrative"


async def _work_product(twin: Any, node_id: Any) -> Any:
    getter = getattr(twin, "get_work_product", None)
    if getter is None:
        return None
    try:
        return await getter(node_id)
    except Exception:  # noqa: BLE001 - not a work product: an entity or constraint
        return None


_DEFAULT_CLAIM_TYPE = "satisfies"


def make_claim_recorder(twin: Any) -> Any:
    """Return an async ``record(...)`` bound to a twin."""

    async def record(
        *,
        requirement_ref: str,
        artefact_ref: str,
        evidence_refs: list[str] | None = None,
        claim_type: str = _DEFAULT_CLAIM_TYPE,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if not requirement_ref or not isinstance(requirement_ref, str):
            raise ValueError("claim recorder: 'requirement_ref' is required (non-empty string)")
        if not artefact_ref or not isinstance(artefact_ref, str):
            raise ValueError("claim recorder: 'artefact_ref' is required (non-empty string)")
        try:
            claim_edge = EdgeType(claim_type)
        except ValueError as exc:
            raise ValueError(
                f"claim recorder: 'claim_type' must be a valid EdgeType, got {claim_type!r}"
            ) from exc

        with tracer.start_as_current_span("twin.record_claim") as span:
            requirement_id = await resolve_ref(twin, requirement_ref, project_id=project_id)
            artefact_id = await resolve_ref(
                twin, artefact_ref, project_id=project_id, include_work_products=True
            )
            # FORGE-759: a sketch, render or image shows intent; claiming it
            # satisfies a requirement would make a picture into evidence.
            artefact = await _work_product(twin, artefact_id)
            if artefact is not None and is_illustrative(artefact):
                raise ValueError(
                    f"claim recorder: {artefact_ref!r} is an illustrative "
                    f"{_type_name(artefact)}, which shows intent and cannot satisfy a "
                    "requirement; claim against the CAD model, analysis or test that does"
                )
            resolved_evidence_ids = (
                await resolve_refs(twin, evidence_refs, project_id=project_id)
                if evidence_refs
                else []
            )
            for evidence_id in resolved_evidence_ids:
                evidence = await twin.get_engineering_entity(evidence_id)
                if evidence is None or evidence.entity_type != "evidence":
                    raise ValueError(
                        f"claim recorder: evidence ref resolved to {evidence_id}, which is not "
                        "an 'evidence' EngineeringEntity"
                    )

            span.set_attribute("claim.type", claim_type)
            span.set_attribute("claim.evidence_count", len(resolved_evidence_ids))

            await twin.add_edge(
                artefact_id,
                requirement_id,
                claim_edge,
                metadata={
                    "kind": CLAIM_EDGE_KIND,
                    "evidence": [str(e) for e in resolved_evidence_ids],
                },
            )

            claim = await evaluate_claim(twin, artefact_id, requirement_id)

            logger.info(
                "claim_recorded",
                artefact_id=str(artefact_id),
                requirement_id=str(requirement_id),
                claim_type=claim_type,
                evidence_count=len(resolved_evidence_ids),
                status=claim.status.value,
            )
            return {
                "artefact_id": str(artefact_id),
                "requirement_id": str(requirement_id),
                "claim_type": claim_type,
                "evidence": [str(e) for e in resolved_evidence_ids],
                "status": claim.status.value,
            }

    return record
