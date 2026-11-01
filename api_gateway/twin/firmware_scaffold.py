"""Firmware scaffold derived from a committed assembly's real joint list
(FORGE-276, gap G-E3).

**What this is.** ``twin.create_firmware_scaffold`` mechanically derives a
per-joint CAN node table and a minimal C header skeleton from a work
product's real ``metadata.assembly.joints`` (FORGE-271/245 -- see
``api_gateway/twin/schemas.py``'s ``AssemblyJoint``), the same joint data
FORGE-295's bring-up checklist and FORGE-283's joint-load analysis already
read. This is derivation over existing structured data, not new authoring
-- the same discipline ``api_gateway/twin/bringup_checklist.py`` (FORGE-295)
applied to joint-to-assembly-step derivation.

**CAN node ID assignment is a deliberate placeholder scheme.** There is no
real hardware-specific CAN-ID allocation input anywhere in this codebase
(no target MCU, no bus topology). Rather than invent one, each joint's CAN
ID is simply its position in the SAME topological build order
``bringup_checklist.py`` already derives (1-indexed) -- deterministic,
reproducible, and honestly documented as a stand-in pending a real
hardware-specific allocation, mirroring ``api_gateway/runs/fw_handlers.py``'s
own "deterministic + honest: a bring-up scaffold is a real Phase-1
deliverable, not a claim of tested firmware" framing.

**Two real artifacts, not one.** Unlike bringup_checklist/release_package/
test_plan (which record a single EngineeringEntity holding structured
metadata), a firmware scaffold produces two loadable text work products via
the generic ``document_recorder`` (``api_gateway/twin/document_recorder.py``,
the same facade ``api_gateway/runs/fw_handlers.py`` already uses for its
own pinmap/firmware_source pair inside a full design-flow run): a PINMAP
CSV (signal/CAN-ID/joint table) and a FIRMWARE_SOURCE C header (per-joint
struct with CAN ID + limit constants pulled from the real joint ``limits``
data). Both are linked back to the source robot_description via a
PARENT_OF edge (``source_part_node_ids``), and the per-joint table is also
mirrored into the PINMAP work product's own metadata (``extra_metadata``)
so the dashboard can render it without parsing the CSV blob -- the same
"content for loadability, metadata for direct reads" convention
``twin.record_document``'s own docstring establishes for simulation_result.

**Deliberately out of scope** (see Jira FORGE-276 for the full reasoning):
- Real CAN bus protocol/message-format implementation (e.g. CANopen frame
  definitions) -- this emits a struct of constants, not protocol logic.
- Actual control-loop/PID logic -- a scaffold, not a working controller.
- Real GPIO/MCU-specific pin assignment beyond the CAN-ID placeholder --
  that needs a real target-MCU selection, which doesn't exist as an input.
- RTOS configuration -- ``domain_agents/firmware``'s existing generic
  ``configure_rtos`` skill already covers this; unrelated to this
  joint-specific ticket.
- Hardware-in-the-loop testing.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from api_gateway.twin.bringup_checklist import AssemblyGraphError, _topological_steps
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.firmware_scaffold")

__all__ = ["AssemblyGraphError", "make_firmware_scaffold_creator"]


def _assign_can_ids(joints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Topologically order the real joints (reusing bringup_checklist's
    base->follower sort for a single, consistent build/bring-up order
    across the codebase) and assign each a sequential 1-indexed CAN node
    ID -- a deterministic placeholder, not a real hardware allocation."""
    ordered = _topological_steps(joints)
    return [{**j, "can_id": j["step_number"]} for j in ordered]


def _render_pinmap_csv(joints: list[dict[str, Any]]) -> str:
    out = ["joint_name,joint_type,can_id,lower_limit,upper_limit"]
    for j in joints:
        limits = j.get("limits") or {}
        lower = limits.get("lower", "")
        upper = limits.get("upper", "")
        out.append(f"{j['name']},{j['type']},{j['can_id']},{lower},{upper}")
    return "\n".join(out) + "\n"


def _c_identifier(name: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in name).strip("_")
    return (safe or "JOINT").upper()


def _render_firmware_header_c(robot_name: str, joints: list[dict[str, Any]]) -> str:
    """Render a minimal, syntactically-valid C header: a per-joint struct
    plus CAN ID and limit constants -- structural scaffolding from real
    joint data, not a control loop or CAN protocol implementation."""
    lines = [
        f"// {robot_name} -- firmware scaffold (joint CAN IDs + limits)",
        "// Generated firmware scaffold (bring-up skeleton, not tested firmware).",
        "// CAN node IDs are a deterministic placeholder (build-order index),",
        "// pending a real hardware-specific allocation.",
        "#ifndef FIRMWARE_JOINTS_H",
        "#define FIRMWARE_JOINTS_H",
        "",
        "#include <stdint.h>",
        "",
        "typedef struct {",
        "    uint8_t can_id;",
        "    float lower_limit;",
        "    float upper_limit;",
        "} joint_config_t;",
        "",
    ]
    for j in joints:
        limits = j.get("limits") or {}
        lower = float(limits.get("lower", 0.0))
        upper = float(limits.get("upper", 0.0))
        ident = _c_identifier(j["name"])
        lines.append(f"// {j['name']} ({j['type']} joint): {j['base']} -> {j['follower']}")
        lines.append(
            f"static const joint_config_t JOINT_{ident}_CONFIG = {{ "
            f".can_id = {j['can_id']}, .lower_limit = {lower:.6g}f, "
            f".upper_limit = {upper:.6g}f }};"
        )
    lines += ["", "#endif // FIRMWARE_JOINTS_H", ""]
    return "\n".join(lines)


def make_firmware_scaffold_creator(twin: Any, *, document_recorder: Any) -> Any:
    """Return an async ``create(*, work_product_id, project_id=None) ->
    dict`` bound to a twin + document_recorder. Reads the real
    ``metadata.assembly.joints`` off ``work_product_id``, assigns
    deterministic CAN IDs, and records one PINMAP + one FIRMWARE_SOURCE
    work product. Not idempotent-by-intent -- calling this twice on the
    same work product creates two fresh scaffold pairs (mirrors
    ``bringup_checklist``/``release_package``'s own
    create-a-new-snapshot-each-call semantics)."""

    async def create(*, work_product_id: str, project_id: str | None = None) -> dict[str, Any]:
        with tracer.start_as_current_span("twin.create_firmware_scaffold") as span:
            wp_id = UUID(work_product_id)
            span.set_attribute("firmware_scaffold.work_product_id", work_product_id)

            wp = await twin.get_work_product(wp_id)
            if wp is None:
                raise ValueError(
                    f"twin.create_firmware_scaffold: no work_product {work_product_id!r}"
                )

            assembly = wp.metadata.get("assembly") if wp.metadata else None
            joints = assembly.get("joints", []) if isinstance(assembly, dict) else []
            if not joints:
                raise ValueError(
                    f"twin.create_firmware_scaffold: work_product {work_product_id!r} "
                    "has no assembly.joints metadata to derive a scaffold from"
                )

            joints_with_ids = _assign_can_ids(joints)
            span.set_attribute("firmware_scaffold.joint_count", len(joints_with_ids))

            table = [
                {
                    "joint_name": j["name"],
                    "joint_type": j["type"],
                    "can_id": j["can_id"],
                    "limits": j.get("limits"),
                }
                for j in joints_with_ids
            ]

            pinmap_rec = await document_recorder(
                content=_render_pinmap_csv(joints_with_ids),
                name=f"{wp.name} firmware pinmap",
                wp_type="pinmap",
                domain="firmware",
                fmt="csv",
                link_type="pinmap",
                source_tool="twin.create_firmware_scaffold",
                project_id=project_id,
                extra_metadata={"work_product_id": work_product_id, "joints": table},
                source_part_node_ids=[work_product_id],
            )
            source_rec = await document_recorder(
                content=_render_firmware_header_c(wp.name, joints_with_ids),
                name=f"{wp.name} firmware scaffold",
                wp_type="firmware_source",
                domain="firmware",
                fmt="h",
                link_type="firmware_source",
                source_tool="twin.create_firmware_scaffold",
                project_id=project_id,
                extra_metadata={"work_product_id": work_product_id},
                source_part_node_ids=[work_product_id],
            )

            logger.info(
                "firmware_scaffold_created",
                work_product_id=work_product_id,
                pinmap_node_id=pinmap_rec["node_id"],
                firmware_source_node_id=source_rec["node_id"],
                joint_count=len(joints_with_ids),
            )
            return {
                "pinmap_node_id": pinmap_rec["node_id"],
                "firmware_source_node_id": source_rec["node_id"],
                "joints": table,
            }

    return create
