"""Baseline creation: a real gateway/MCP-reachable wrapper around
``twin_core.transactions.baseline.create_baseline`` (FORGE-405, follow-up to
FORGE-299).

**Why this exists.** FORGE-299's release-package creation gates on the real
G8 release gate (``twin_core.consistency.gates.evaluate_g8_release``), one of
whose checks is "configuration baseline fixed" -- it requires at least one
``Baseline`` node to exist for the project. ``create_baseline`` itself was
already real, tested, and reachable from ``twin_core.transactions`` -- but
nothing in ``tool_registry``/``api_gateway`` ever called it, so no agent or
dashboard action could actually create one. This module is the same kind of
injection-seam wrapper as every other ``make_*_recorder``/``make_*_creator``
in this package (e.g. ``release_package.py``'s ``make_release_package_creator``).

**Member discovery.** ``create_baseline``'s own signature takes an explicit
``members: list[tuple[ControlledEntityKind, UUID]]`` -- each member's
*current* revision gets pinned, so the transaction's optimistic-concurrency
guarantee only means something if the caller is baselining real, live
entities. Rather than push that enumeration onto every caller, this wrapper
auto-discovers the project's full controlled-entity set at call time: every
``Constraint`` and every ``EngineeringEntity`` currently linked to the
project (via ``twin.list_constraints``/``twin.list_engineering_entities``,
the same project-scoped list calls ``release_package.py`` already uses for
its own snapshot). A baseline with zero members is rejected by
``create_baseline`` itself (``ValueError``); this wrapper surfaces that as a
clear error rather than creating a vacuous baseline.

**Items (FORGE-526).** The same baseline now also pins every current item of
the project (``KEY@n``, see ``twin_core.items``): a project whose only
definitions are parts or assemblies can be baselined too.
:func:`create_item_baseline` is the one-line seam a gate approval calls
(FORGE-525 owns the approval path), and :func:`baseline_to_dict` is the JSON
shape the MCP tool and the REST routes share.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.models.patch import ControlledEntityKind
from twin_core.transactions.baseline import baseline_item_refs
from twin_core.transactions.baseline import create_baseline as _create_baseline_txn
from twin_core.transactions.baseline import create_item_baseline as _create_item_baseline
from twin_core.transactions.engine import TransactionEngine

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.baseline")


def make_baseline_creator(twin: Any, *, engine: Any = None) -> Any:
    """Return an async ``create(*, project_id, approved_by, reason,
    name=None) -> dict`` bound to a twin (+ optional shared
    ``TransactionEngine``, constructed lazily per call otherwise -- same
    "engine or TransactionEngine(twin)" pattern ``twin_core.transactions.
    ect`` already uses)."""

    async def create(
        *,
        project_id: str,
        approved_by: list[str],
        reason: str,
        name: str | None = None,
    ) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.create_baseline") as span:
            pid = UUID(project_id)
            span.set_attribute("baseline.project_id", project_id)

            constraints = await twin.list_constraints(project_id=pid)
            entities = await twin.list_engineering_entities(project_id=pid)
            members: list[tuple[ControlledEntityKind, UUID]] = [
                ("constraint", c.id) for c in constraints
            ] + [("engineering_entity", e.id) for e in entities]
            items = await baseline_item_refs(twin, pid)

            if not members and not items:
                raise ValueError(
                    "twin.create_baseline: no constraints, engineering entities or items "
                    "recorded for this project -- there is nothing to baseline yet"
                )

            txn = engine or TransactionEngine(twin)
            created_at = datetime.now(UTC)
            baseline_name = name or f"Baseline {created_at.isoformat()}"

            result = await _create_baseline_txn(
                twin,
                txn,
                name=baseline_name,
                members=members,
                approved_by=approved_by,
                reason=reason,
                project_id=pid,
                items=items,
            )

            span.set_attribute("baseline.status", result.status)
            if result.status == "conflict":
                raise ValueError(
                    "twin.create_baseline: conflict -- a member changed "
                    f"underneath this call: {'; '.join(result.conflicts)}"
                )

            assert result.baseline is not None
            baseline = result.baseline

            logger.info(
                "baseline_created",
                project_id=project_id,
                node_id=str(baseline.id),
                member_count=len(baseline.includes),
                constraint_count=len(constraints),
                entity_count=len(entities),
                item_count=len(items),
            )

            return {
                "node_id": str(baseline.id),
                "name": baseline.name,
                "member_count": len(baseline.includes),
                "constraint_count": len(constraints),
                "entity_count": len(entities),
                "item_count": len(baseline.items),
                "items": [r.ref for r in baseline.items],
                "approved_by": baseline.approved_by,
                "reason": baseline.reason,
                "created_at": baseline.created_at.isoformat(),
            }

    return create


async def create_item_baseline(
    project_id: str | None,
    gate_id: str | None,
    approver: str,
    run_id: str | None = None,
    *,
    twin: Any = None,
) -> dict[str, Any] | None:
    """Record the baseline a gate approval implies (FORGE-526 seam for FORGE-525).

    Pins every current item of ``project_id`` with the approver, gate and run.
    Idempotent per (project, gate, run). Best-effort by design: an approval
    must never fail because its baseline could not be recorded, so a failure
    is logged (``item_baseline_failed``), counted, and returns ``None``.
    ``twin`` defaults to the gateway's active twin.
    """
    if not project_id:
        return None
    if twin is None:
        from api_gateway.twin.routes import get_twin

        twin = get_twin()
    with tracer.start_as_current_span("twin.create_item_baseline") as span:
        span.set_attribute("baseline.project_id", project_id)
        span.set_attribute("baseline.gate_id", gate_id or "")
        try:
            baseline = await _create_item_baseline(
                twin,
                project_id=project_id,
                approved_by=[approver or "unknown"],
                gate_id=gate_id,
                run_id=run_id,
            )
        except ValueError as exc:
            logger.info(
                "item_baseline_skipped",
                project_id=project_id,
                gate_id=gate_id,
                run_id=run_id,
                reason=str(exc),
            )
            return None
        except Exception as exc:  # noqa: BLE001 -- never fail the approval
            span.record_exception(exc)
            logger.error(
                "item_baseline_failed",
                project_id=project_id,
                gate_id=gate_id,
                run_id=run_id,
                error=str(exc),
            )
            from observability.metrics import collector_for

            collector_for("metaforge-twin-baselines").record_twin_baseline("gate", "failed")
            return None
        return baseline_to_dict(baseline)


def baseline_to_dict(baseline: Any, *, include_items: bool = True) -> dict[str, Any]:
    """The JSON shape of a baseline, shared by the MCP tool and the REST routes."""
    out: dict[str, Any] = {
        "id": str(baseline.id),
        "name": baseline.name,
        "project_id": str(baseline.project_id) if baseline.project_id else None,
        "created_at": baseline.created_at.isoformat(),
        "approved_by": list(baseline.approved_by),
        "reason": baseline.reason,
        "gate_id": getattr(baseline, "gate_id", None),
        "run_id": getattr(baseline, "run_id", None),
        "source": getattr(baseline, "source", "manual"),
        "item_count": len(getattr(baseline, "items", [])),
        "member_count": len(baseline.includes),
    }
    if include_items:
        out["items"] = [
            {
                "key": r.key,
                "item_type": r.item_type,
                "revision": r.revision,
                "ref": r.ref,
                "node_id": str(r.node_id),
                "name": r.name,
            }
            for r in baseline.items
        ]
        out["members"] = [
            {
                "entity_kind": m.entity_kind,
                "entity_id": str(m.entity_id),
                "revision": m.revision,
            }
            for m in baseline.includes
        ]
    return out
