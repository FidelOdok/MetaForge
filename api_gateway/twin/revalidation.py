"""Automatic selective re-run from an ECT's real post-commit revalidation
plan (FORGE-316, spec §30: "invalidate downstream evidence automatically
after dependency-changing edits").

``twin_core/transactions/ect.py``'s ``commit()`` already marks the real
staleness (``StalenessEngine.propagate``) and builds the resulting
``revalidation_plan`` (``ImpactEngine.build_revalidation_plan``) -- this
module is the "now actually DO something about it" half: for every plan
step that names a stale Evidence entity carrying a replayable
``metadata["replay"] = {tool_id, args}`` (set by a caller like
``api_gateway/twin/metric_evaluator.py``), re-invoke that exact tool with
those exact args, record the fresh result as NEW evidence superseding the
stale one (FORGE-65's own revalidation flow -- never a mutation of the old
evidence). Anything else the plan reaches -- a Constraint the walk touched,
or an Evidence entity with no replay recipe (hand-authored, or produced
before this field existed) -- is reported as needing manual review, the
same honest language ``ImpactEngine`` already uses for "no automatic
re-run mechanism exists for this entity kind", never silently skipped nor
guessed at.

Deliberately NOT built here (each a separable piece, no acceptance-
criterion pressure to build now): a general property-level impact walk
through ``Constraint.dependencies`` (that field doesn't exist yet --
explicitly deferred since FORGE-312/Step 2, confirmed still true during
this ticket's own scoping); a dashboard UI showing the impact graph and
re-run progress (zero existing ECT-related UI surface today -- new work
from scratch, its own follow-up).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.revalidation")

_COMMITTED = "committed"


def make_revalidation_executor(twin: Any, tool_dispatch: dict[str, Any]) -> Any:
    """Return an async ``execute(ect_id) -> dict`` bound to a twin +
    ``{tool_id: callable(**args) -> dict}`` dispatch table. Only tool ids a
    caller actually wires into ``tool_dispatch`` are ever invoked -- an
    unrecognized ``tool_id`` in a replay payload is treated exactly like
    "no replay recipe", never guessed at."""

    async def execute(ect_id: str) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.execute_revalidation_plan") as span:
            span.set_attribute("ect.id", ect_id)
            ect = await twin.get_ect(UUID(ect_id))
            if ect is None:
                raise ValueError(f"twin.execute_revalidation_plan: no ECT {ect_id!r}")
            status = getattr(ect.status, "value", ect.status)
            if status != _COMMITTED:
                raise ValueError(
                    f"twin.execute_revalidation_plan: ECT {ect_id} is {status!r}, expected "
                    f"{_COMMITTED!r} -- nothing to revalidate before a commit actually happened"
                )

            re_run: list[dict[str, Any]] = []
            manual_review: list[dict[str, Any]] = []

            for step in ect.revalidation_plan:
                entity_kind = step.get("entity_kind")
                entity_id = step.get("entity_id")
                if entity_kind != "engineering_entity" or not entity_id:
                    manual_review.append({**step, "why": "not an Evidence entity"})
                    continue
                entity = await twin.get_engineering_entity(UUID(entity_id))
                if entity is None or entity.entity_type != "evidence":
                    manual_review.append({**step, "why": "not an Evidence entity"})
                    continue
                replay = entity.metadata.get("replay")
                if not isinstance(replay, dict) or not replay.get("tool_id"):
                    manual_review.append({**step, "why": "no replayable provenance recorded"})
                    continue
                tool_id = replay["tool_id"]
                tool = tool_dispatch.get(tool_id)
                if tool is None:
                    manual_review.append(
                        {**step, "why": f"replay names {tool_id!r}, not wired into this executor"}
                    )
                    continue
                args = dict(replay.get("args") or {})
                args["supersedes"] = entity_id
                try:
                    new_result = await tool(**args)
                except Exception as exc:  # noqa: BLE001 -- one failed re-run must not block the rest
                    logger.warning(
                        "revalidation_replay_failed",
                        entity_id=entity_id,
                        tool_id=tool_id,
                        error=str(exc),
                    )
                    manual_review.append({**step, "why": f"replay raised: {exc}"})
                    continue
                re_run.append(
                    {
                        "entity_id": entity_id,
                        "tool_id": tool_id,
                        "new_evidence_node_id": new_result.get("evidence_node_id"),
                    }
                )

            result = {"re_run": re_run, "manual_review_needed": manual_review}
            await twin.update_ect(UUID(ect_id), {"revalidation_result": result})
            logger.info(
                "revalidation_plan_executed",
                ect_id=ect_id,
                re_run_count=len(re_run),
                manual_review_count=len(manual_review),
            )
            return result

    return execute
