"""Evidence recorder for twin.record_evidence (FORGE-64, epic FORGE-35,
Phase 6: Evidence Integration).

Persists an ``EngineeringEntity(entity_type="evidence")`` shaped per the
spec's own Evidence schema (section 50) -- ``evidence_type``, ``producer``,
``inputs``, ``result``, ``supports``/``contradicts``, ``valid_against``,
``validity`` -- but, per the discipline the whole Engineering Intent &
Requirements Harness build has followed since Phase 1, as validated
``metadata`` on the ONE generic ``EngineeringEntity`` node (FORGE-44), not a
bespoke ``class Evidence(EngineeringEntity)`` subclass the doc's own pseudo-
code sketches. This recorder is what makes evidence a real, checkable graph
fact rather than "an agent said so":

- The caller passes the tool's REAL structured output as ``result`` --
  never a free-text assertion (enforced loosely: ``result`` must be a
  non-empty object; there is no way to cryptographically prove upstream the
  caller didn't fabricate it, but a structured, hashed payload is the honest
  ceiling this layer can enforce).
- ``result_hash`` (sha256 of the canonical, sorted-key JSON of ``result``)
  and ``execution_timestamp`` are computed here, not caller-supplied, so
  they can't be backdated or mismatched.
- ``supports``/``contradicts`` resolve through the same exact-name-or-UUID
  resolver every other recorder in this package uses
  (``_ref_resolver.py``) and become real graph edges (``EdgeType.SATISFIES``/
  ``EdgeType.CONFLICTS_WITH`` -- reusing existing relation vocabulary rather
  than inventing new edge types for the same concepts FORGE-45/61 already
  named).
- ``valid_against`` (the spec's ``REQ-MASS-001@3`` revision-pin syntax) is
  NOT a new mechanism -- it IS FORGE-59's ``StalenessEngine.
  declare_dependencies``. An unspecified ``revision`` pins to whatever
  revision the referenced Constraint/EngineeringEntity is at RIGHT NOW
  (declaration time), matching FORGE-59's own "declared dependencies" model.
  This is what makes G8's "stale evidence resolved" check
  (``twin_core.consistency.gates.evaluate_g8_release``, FORGE-63) start
  finding REAL staleness instead of an always-empty evidence set: this
  recorder is the first thing in the whole epic that actually creates
  evidence with a real, propagatable ``staleness`` status.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from api_gateway.twin._ref_resolver import resolve_ref, resolve_refs
from observability.tracing import get_tracer
from twin_core.consistency.staleness import Dependency, StalenessEngine, StalenessStatus
from twin_core.models.engineering_entity import EngineeringEntity
from twin_core.models.enums import EdgeType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.evidence_recorder")

_EVIDENCE_TYPES = frozenset(
    {
        "calculation",
        "simulation",
        "test",
        "inspection",
        "demonstration",
        "datasheet",
        "external_reference",
    }
)
_VALID_ENTITY_KINDS = frozenset({"constraint", "engineering_entity"})

_SUPPORTS_EDGE = EdgeType.SATISFIES
_CONTRADICTS_EDGE = EdgeType.CONFLICTS_WITH


def _result_hash(result: dict[str, Any]) -> str:
    canonical = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _resolve_valid_against(
    twin: Any, entries: list[dict[str, Any]], *, project_id: str | None
) -> list[Dependency]:
    dependencies: list[Dependency] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("ref"):
            raise ValueError(f"evidence recorder: valid_against[{index}] must have a 'ref'")
        entity_kind = entry.get("entity_kind")
        if entity_kind not in _VALID_ENTITY_KINDS:
            raise ValueError(
                f"evidence recorder: valid_against[{index}] 'entity_kind' must be one of "
                f"{sorted(_VALID_ENTITY_KINDS)}, got {entity_kind!r}"
            )
        entity_id = await resolve_ref(twin, str(entry["ref"]), project_id=project_id)
        revision = entry.get("revision")
        if revision is None:
            current = (
                await twin.get_constraint(entity_id)
                if entity_kind == "constraint"
                else await twin.get_engineering_entity(entity_id)
            )
            if current is None:
                raise ValueError(
                    f"evidence recorder: valid_against[{index}] ref {entry['ref']!r} "
                    f"resolved to {entity_id} but no {entity_kind} exists with that id"
                )
            revision = current.revision
        dependencies.append(
            Dependency(entity_kind=entity_kind, entity_id=entity_id, revision=int(revision))
        )
    return dependencies


def make_evidence_recorder(twin: Any, project_backend: Any = None) -> Any:
    """Return an async ``record(...)`` bound to a twin + project backend."""

    async def record(
        *,
        evidence_type: str,
        producer: dict[str, Any],
        inputs: dict[str, Any],
        result: dict[str, Any],
        statement: str | None = None,
        supports: list[str] | None = None,
        contradicts: list[str] | None = None,
        valid_against: list[dict[str, Any]] | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if evidence_type not in _EVIDENCE_TYPES:
            raise ValueError(
                f"evidence recorder: 'evidence_type' must be one of {sorted(_EVIDENCE_TYPES)}, "
                f"got {evidence_type!r}"
            )
        if not isinstance(producer, dict) or not producer.get("tool"):
            raise ValueError(
                "evidence recorder: 'producer' must be an object with at least a 'tool' key"
            )
        if not isinstance(inputs, dict):
            raise ValueError("evidence recorder: 'inputs' must be an object")
        if not isinstance(result, dict) or not result:
            raise ValueError(
                "evidence recorder: 'result' must be a non-empty object -- record the tool's "
                "real structured output, not an assertion"
            )

        with tracer.start_as_current_span("twin.record_evidence") as span:
            span.set_attribute("evidence.type", evidence_type)
            span.set_attribute("evidence.producer_tool", str(producer.get("tool")))

            # Resolve everything BEFORE the entity exists -- zero partial
            # writes on an unresolvable ref, same discipline as every other
            # recorder in this package.
            resolved_supports = (
                await resolve_refs(twin, supports, project_id=project_id) if supports else []
            )
            resolved_contradicts = (
                await resolve_refs(twin, contradicts, project_id=project_id) if contradicts else []
            )
            dependencies = await _resolve_valid_against(
                twin, valid_against or [], project_id=project_id
            )

            now = datetime.now(UTC)
            metadata: dict[str, Any] = {
                "evidence_type": evidence_type,
                "producer": producer,
                "inputs": inputs,
                "result": result,
                "result_hash": _result_hash(result),
                "execution_timestamp": now.isoformat(),
                "staleness": StalenessStatus.CURRENT.value,
            }
            if session_id:
                metadata["session_id"] = session_id

            entity = EngineeringEntity(
                entity_type="evidence",
                statement=statement or f"{evidence_type} evidence from {producer.get('tool')}",
                project_id=UUID(project_id) if project_id else None,
                metadata=metadata,
            )
            created = await twin.create_engineering_entity(entity)

            for target_id in resolved_supports:
                await twin.add_edge(
                    created.id, target_id, _SUPPORTS_EDGE, metadata={"kind": "evidence_support"}
                )
            for target_id in resolved_contradicts:
                await twin.add_edge(
                    created.id,
                    target_id,
                    _CONTRADICTS_EDGE,
                    metadata={"kind": "evidence_contradiction"},
                )

            if dependencies:
                # FORGE-59's own mechanism, not a parallel one -- this is
                # what lets propagate() later mark this evidence STALE for
                # real when one of its pinned inputs moves on.
                await StalenessEngine(twin).declare_dependencies(
                    "engineering_entity", created.id, dependencies
                )

            linked = False
            if project_id and project_backend is not None:
                try:
                    await project_backend.link_work_product(
                        project_id, str(created.id), entity.statement or "evidence", "evidence"
                    )
                    linked = True
                except Exception as exc:  # noqa: BLE001 -- link is best-effort
                    logger.warning("evidence_project_link_failed", error=str(exc))

            logger.info(
                "evidence_recorded",
                node_id=str(created.id),
                project_id=project_id,
                evidence_type=evidence_type,
                producer_tool=producer.get("tool"),
                supports_count=len(resolved_supports),
                contradicts_count=len(resolved_contradicts),
                valid_against_count=len(dependencies),
                linked=linked,
            )
            return {
                "node_id": str(created.id),
                "result_hash": metadata["result_hash"],
                "execution_timestamp": metadata["execution_timestamp"],
                "supports": [str(t) for t in resolved_supports],
                "contradicts": [str(t) for t in resolved_contradicts],
                "valid_against_count": len(dependencies),
                "project_linked": linked,
            }

    return record
