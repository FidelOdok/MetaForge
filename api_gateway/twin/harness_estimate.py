"""Per-joint harness (cable) length estimate derived from a committed
assembly's real joint anchors (FORGE-275, gap G-E2).

**What this is.** ``twin.get_harness_estimate`` derives a per-joint cable
length estimate from a work product's real ``metadata.assembly.joints``
(FORGE-271/245 -- the same joint data FORGE-276's firmware scaffold and
FORGE-295's bring-up checklist already read).

**Why cumulative, not a single straight line from a fixed origin.** Each
joint's ``anchor`` is expressed relative to its own immediate parent
frame (standard URDF/kinematic-tree convention -- confirmed on the real
AR4 robot_description: joint_2's anchor is ``(0, -64.2, 169.77)`` relative
to joint_1's frame, not an absolute position from the robot's base). A
single straight-line distance from one fixed global origin to a raw
relative anchor would be physically meaningless. Instead, this sums each
joint's own anchor-offset *segment length* along the real base->follower
kinematic chain (the same topological order ``bringup_checklist.py``
already derives) from the chain's root to that joint -- a "cable routed
along the arm's links" estimate, which is also a more physically sensible
model for where a real wiring harness actually runs than a straight line
through solid material would be.

**This is an estimate, not a routed length.** It is the sum of straight
LINE SEGMENTS along the kinematic chain, not a real routed path around
real obstacles/mounting hardware -- documented clearly here and in the
dashboard copy. Multiple independent root parts (parallel subassemblies)
are supported exactly as ``bringup_checklist.py``'s topological sort
already handles them: each root's own chain starts its cumulative length
at 0.

**Deliberately out of scope** (see Jira FORGE-275 for the full reasoning):
- Real connector/wire-gauge selection -- no schema concept exists anywhere
  in this codebase for either.
- A real routed (vs. straight-segment) cable path.
- Real motor-driver component selection/sizing -- the `motor_driver`/`esc`
  taxonomy categories already exist (``digital_twin/catalog/taxonomy.py``)
  but no real BOM line uses them; a separate, comparably-sized ticket.
"""

from __future__ import annotations

import math
from typing import Any

import structlog

from api_gateway.twin.bringup_checklist import AssemblyGraphError, _topological_steps
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.harness_estimate")

__all__ = ["AssemblyGraphError", "make_harness_estimate_getter"]


def _segment_length_mm(anchor: Any) -> float:
    """Euclidean length of one joint's own (relative) anchor offset."""
    if not anchor or len(anchor) != 3:
        return 0.0
    x, y, z = anchor
    return math.sqrt(float(x) ** 2 + float(y) ** 2 + float(z) ** 2)


def _cumulative_harness_lengths(joints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Topologically order the joints and accumulate cable length along the
    real base->follower chain -- each joint's own segment length added to
    its base part's already-accumulated length."""
    ordered = _topological_steps(joints)
    cumulative_mm: dict[str, float] = {}
    out: list[dict[str, Any]] = []
    for j in ordered:
        base_len = cumulative_mm.get(j["base"], 0.0)
        segment_mm = _segment_length_mm(j.get("anchor"))
        total_mm = base_len + segment_mm
        cumulative_mm[j["follower"]] = total_mm
        out.append(
            {
                "step_number": j["step_number"],
                "joint_name": j["name"],
                "joint_type": j["type"],
                "base": j["base"],
                "follower": j["follower"],
                "segment_length_mm": round(segment_mm, 2),
                "cable_length_estimate_mm": round(total_mm, 2),
            }
        )
    return out


def make_harness_estimate_getter(twin: Any) -> Any:
    """Return an async get(*, work_product_id: str) -> dict bound to a twin.
    Reads the real ``metadata.assembly.joints`` off ``work_product_id`` and
    returns a per-joint cumulative cable-length-estimate table."""

    async def get(*, work_product_id: str) -> dict[str, Any]:
        from uuid import UUID

        with tracer.start_as_current_span("twin.get_harness_estimate") as span:
            wp_id = UUID(work_product_id)
            span.set_attribute("harness_estimate.work_product_id", work_product_id)

            wp = await twin.get_work_product(wp_id)
            if wp is None:
                raise ValueError(f"twin.get_harness_estimate: no work_product {work_product_id!r}")

            assembly = wp.metadata.get("assembly") if wp.metadata else None
            joints = assembly.get("joints", []) if isinstance(assembly, dict) else []
            if not joints:
                raise ValueError(
                    f"twin.get_harness_estimate: work_product {work_product_id!r} "
                    "has no assembly.joints metadata to derive a harness estimate from"
                )

            table = _cumulative_harness_lengths(joints)
            span.set_attribute("harness_estimate.joint_count", len(table))

            logger.info(
                "harness_estimate_computed",
                work_product_id=work_product_id,
                joint_count=len(table),
            )
            return {"work_product_id": work_product_id, "joints": table}

    return get
