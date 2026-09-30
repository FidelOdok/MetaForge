"""Assembly bring-up checklist derived from a committed assembly's real
joint list (FORGE-295, gap G-H3).

**What this is.** `twin.create_bringup_checklist` mechanically derives a
step-by-step assembly sequence from a work product's real
``metadata.assembly.joints`` (FORGE-271/245 -- see
``api_gateway/twin/schemas.py``'s ``AssemblyJoint``). Each joint already
carries everything a step needs as real structured fields: ``name``,
``type``, ``base`` (parent part), ``follower`` (child part). The joint
list itself is flat and unordered -- there is no sequence field anywhere
in this codebase -- so this module topologically sorts the base->follower
dependency graph (a part that is never a ``follower`` is already placed;
a joint becomes buildable once its ``base`` part is placed) into a real
step order, then formats one instruction per joint
(``"Step {n}: attach {follower} to {base} via {name} ({type} joint)"``).
This is derivation over existing structured data, not new authoring --
the same discipline ``api_gateway/twin/test_plan.py`` (FORGE-298) applied
to requirement->test-step derivation.

A cyclic joint graph (a joint's ``base`` never becomes reachable by
placing joints starting from the real root part(s)) is reported as a
clear error, never guessed at with a partial/wrong order. Multiple
independent root parts (e.g. two parallel subassemblies) are valid and
supported -- that is a disconnected-but-acyclic graph, not an error.

**`bringup_checklist`'s metadata shape** (new -- see
``docs/twin_schema.md``): one EngineeringEntity per call holding
``{"work_product_id": str, "steps": [{"step_number", "joint_name",
"joint_type", "base", "follower", "instruction"}, ...]}`` -- one entity
per checklist (not one entity per step), matching
``release_package``'s "one entity holding an ordered/aggregated payload"
convention rather than ``test_plan``'s "one entity per item" convention,
since a checklist's steps are read together, not tracked/verified
independently the way test cases are.

**Deliberately out of scope**:
- **Any 3D visualization (exploded-per-step or even a simple current-step
  cross-highlight).** Neither exists as reusable infrastructure in this
  codebase -- ``StructureView.tsx``'s own header comment states FORGE-261's
  Structure tab was "deliberately trimmed... no 3D cross-highlight." Both
  are separate, comparably-sized future tickets. This ships a plain
  ordered checklist/table.
- **EVT/DVT/PVT staging distinctions.** One flat checklist per call, not
  staged variants.
- **Writing to the literal `tests/bringup.md` file in a user's project
  repo.** That is a `forge setup`-created project-structure file the
  gateway has no filesystem access pattern to -- a different mechanism
  than this Twin-graph-backed capability. The mismatch is intentional,
  not a gap to bridge here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.bringup_checklist")


class AssemblyGraphError(ValueError):
    """Raised when a work product's ``assembly.joints`` graph is cyclic --
    some joint's ``base`` part never becomes reachable by placing joints
    starting from the real root part(s). The checklist generator refuses
    to guess at an order rather than silently producing a wrong/partial
    one. Multiple independent root parts (disconnected-but-acyclic, e.g.
    two parallel subassemblies) are valid and are NOT an error case."""


def _topological_steps(joints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Topologically sort joints by base->follower dependency into a real
    build order. A part that never appears as a ``follower`` is already
    placed (a root, or the root of an independent subassembly). A joint
    becomes buildable once its ``base`` part is placed. Ties within one
    "wave" of newly-buildable joints are broken deterministically by joint
    name."""
    if not joints:
        return []

    bases = {j["base"] for j in joints}
    followers = {j["follower"] for j in joints}
    placed = bases - followers
    if not placed:
        raise AssemblyGraphError(
            "assembly joint graph has no root part -- every part is some "
            "joint's follower, which means the graph is cyclic; refusing "
            "to guess an order"
        )

    remaining = list(joints)
    ordered: list[dict[str, Any]] = []
    step_no = 1
    while remaining:
        buildable = sorted(
            (j for j in remaining if j["base"] in placed),
            key=lambda j: j["name"],
        )
        if not buildable:
            unresolved = sorted({j["name"] for j in remaining})
            raise AssemblyGraphError(
                "assembly joint graph is cyclic -- joint(s) "
                f"{unresolved} never become buildable from the real root "
                "part(s); refusing to guess an order"
            )
        for j in buildable:
            ordered.append({**j, "step_number": step_no})
            placed.add(j["follower"])
            step_no += 1
            remaining.remove(j)
    return ordered


def _derive_instruction(step: dict[str, Any]) -> str:
    return (
        f"Step {step['step_number']}: attach {step['follower']} to "
        f"{step['base']} via {step['name']} ({step['type']} joint)"
    )


def make_bringup_checklist_creator(
    twin: Any,
    *,
    engineering_entity_recorder: Any,
) -> Any:
    """Return an async ``create(*, work_product_id, project_id=None) ->
    dict`` bound to a twin + engineering_entity_recorder. Reads the real
    ``metadata.assembly.joints`` off ``work_product_id``, derives a real
    step order, and records one new ``bringup_checklist`` entity. Not
    idempotent-by-intent -- calling this twice on the same work product
    creates two checklist entities (mirrors ``release_package``'s own
    create-a-new-snapshot-each-call semantics)."""

    async def create(*, work_product_id: str, project_id: str | None = None) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.create_bringup_checklist") as span:
            wp_id = UUID(work_product_id)
            span.set_attribute("bringup.work_product_id", work_product_id)

            wp = await twin.get_work_product(wp_id)
            if wp is None:
                raise ValueError(
                    f"twin.create_bringup_checklist: no work_product {work_product_id!r}"
                )

            assembly = wp.metadata.get("assembly") if wp.metadata else None
            joints = assembly.get("joints", []) if isinstance(assembly, dict) else []
            if not joints:
                raise ValueError(
                    f"twin.create_bringup_checklist: work_product {work_product_id!r} "
                    "has no assembly.joints metadata to derive a checklist from"
                )

            ordered = _topological_steps(joints)
            steps = [
                {
                    "step_number": j["step_number"],
                    "joint_name": j["name"],
                    "joint_type": j["type"],
                    "base": j["base"],
                    "follower": j["follower"],
                    "instruction": _derive_instruction(j),
                }
                for j in ordered
            ]
            span.set_attribute("bringup.step_count", len(steps))

            statement = (
                f"Bring-up checklist: {len(steps)} step(s) derived from "
                f"{len(joints)} assembly joint(s) on {wp.name}"
            )
            recorded = await engineering_entity_recorder(
                entity_type="bringup_checklist",
                statement=statement,
                title=f"Bring-up checklist: {wp.name}",
                extra={"work_product_id": work_product_id, "steps": steps},
                parent_refs=[work_product_id],
                project_id=project_id,
            )

            logger.info(
                "bringup_checklist_created",
                work_product_id=work_product_id,
                node_id=recorded["node_id"],
                step_count=len(steps),
            )
            return {**recorded, "statement": statement, "steps": steps}

    return create


def make_bringup_checklist_lister(twin: Any) -> Any:
    """Return an async ``list_checklists(*, work_product_id) -> list[dict]``,
    oldest first, reading real ``bringup_checklist`` entities back from the
    graph and filtering by ``metadata.work_product_id`` -- entities aren't
    project-scoped by this tool (a work product may exist before/without a
    project association), so this filters client-side rather than pushing
    an unsupported filter onto ``list_engineering_entities``."""

    async def list_checklists(*, work_product_id: str) -> list[dict[str, Any]]:
        entities = await twin.list_engineering_entities(entity_type="bringup_checklist")
        matching = [e for e in entities if e.metadata.get("work_product_id") == work_product_id]
        matching_sorted = sorted(matching, key=lambda e: e.created_at)
        return [
            {
                "node_id": str(e.id),
                "created_at": e.created_at.isoformat(),
                "title": e.title,
                "statement": e.statement,
                "steps": e.metadata.get("steps", []),
            }
            for e in matching_sorted
        ]

    return list_checklists
