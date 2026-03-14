"""Skill result -> Twin writeback for mechanical work products.

After a skill (generate_cad, validate_stress, etc.) completes, these
functions automatically create or update a WorkProduct in the Digital Twin.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.mechanical.writeback")


async def writeback_cad(
    twin: Any,
    session_id: UUID,
    branch: str,
    skill_output: dict[str, Any],
) -> WorkProduct:
    """Create a CAD_MODEL work product from generate_cad skill output.

    Args:
        twin: TwinAPI instance.
        session_id: Current agent session ID.
        branch: Twin branch to write to.
        skill_output: Dict from the generate_cad skill result.

    Returns:
        The created WorkProduct.
    """
    with tracer.start_as_current_span("writeback.cad") as span:
        span.set_attribute("session.id", str(session_id))
        span.set_attribute("branch", branch)

        now = datetime.now(UTC)
        wp = WorkProduct(
            id=uuid4(),
            name=f"cad_{skill_output.get('shape_type', 'unknown')}",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path=skill_output.get("cad_file", ""),
            content_hash="",
            format="step",
            metadata={
                "session_id": str(session_id),
                "skill": "generate_cad",
                "shape_type": skill_output.get("shape_type", ""),
                "volume_mm3": skill_output.get("volume_mm3", 0.0),
                "surface_area_mm2": skill_output.get("surface_area_mm2", 0.0),
                "bounding_box": skill_output.get("bounding_box", {}),
                "parameters_used": skill_output.get("parameters_used", {}),
                "material": skill_output.get("material", ""),
                "timestamp": now.isoformat(),
            },
            created_at=now,
            updated_at=now,
            created_by=f"mechanical-agent:{session_id}",
        )

        created = cast(WorkProduct, await twin.create_work_product(wp, branch=branch))
        span.set_attribute("work_product.id", str(created.id))

        logger.info(
            "writeback_cad_created",
            work_product_id=str(created.id),
            cad_file=skill_output.get("cad_file", ""),
            session_id=str(session_id),
        )
        return created


async def writeback_mesh(
    twin: Any,
    session_id: UUID,
    branch: str,
    skill_output: dict[str, Any],
) -> WorkProduct:
    """Create a SIMULATION_RESULT work product from generate_mesh skill output.

    Args:
        twin: TwinAPI instance.
        session_id: Current agent session ID.
        branch: Twin branch to write to.
        skill_output: Dict from the generate_mesh skill result.

    Returns:
        The created WorkProduct.
    """
    with tracer.start_as_current_span("writeback.mesh") as span:
        span.set_attribute("session.id", str(session_id))
        span.set_attribute("branch", branch)

        now = datetime.now(UTC)
        wp = WorkProduct(
            id=uuid4(),
            name=f"mesh_{skill_output.get('algorithm_used', 'unknown')}",
            type=WorkProductType.SIMULATION_RESULT,
            domain="mechanical",
            file_path=skill_output.get("mesh_file", ""),
            content_hash="",
            format=skill_output.get("mesh_file", "").rsplit(".", maxsplit=1)[-1]
            if skill_output.get("mesh_file", "")
            else "inp",
            metadata={
                "session_id": str(session_id),
                "skill": "generate_mesh",
                "num_nodes": skill_output.get("num_nodes", 0),
                "num_elements": skill_output.get("num_elements", 0),
                "element_types": skill_output.get("element_types", []),
                "quality_acceptable": skill_output.get("quality_acceptable", False),
                "quality_issues": skill_output.get("quality_issues", []),
                "algorithm_used": skill_output.get("algorithm_used", ""),
                "element_size_used": skill_output.get("element_size_used", 0.0),
                "timestamp": now.isoformat(),
            },
            created_at=now,
            updated_at=now,
            created_by=f"mechanical-agent:{session_id}",
        )

        created = cast(WorkProduct, await twin.create_work_product(wp, branch=branch))
        span.set_attribute("work_product.id", str(created.id))

        logger.info(
            "writeback_mesh_created",
            work_product_id=str(created.id),
            mesh_file=skill_output.get("mesh_file", ""),
            session_id=str(session_id),
        )
        return created


async def writeback_stress(
    twin: Any,
    session_id: UUID,
    branch: str,
    work_product_id: UUID,
    skill_output: dict[str, Any],
) -> WorkProduct:
    """Update an existing WorkProduct with stress validation metadata.

    Args:
        twin: TwinAPI instance.
        session_id: Current agent session ID.
        branch: Twin branch to write to.
        work_product_id: ID of the WorkProduct to update.
        skill_output: Dict from the validate_stress skill result.

    Returns:
        The updated WorkProduct.
    """
    with tracer.start_as_current_span("writeback.stress") as span:
        span.set_attribute("session.id", str(session_id))
        span.set_attribute("branch", branch)
        span.set_attribute("work_product.id", str(work_product_id))

        now = datetime.now(UTC)
        updates: dict[str, Any] = {
            "metadata": {
                "session_id": str(session_id),
                "skill": "validate_stress",
                "validation_status": "pass"
                if skill_output.get("overall_passed", False)
                else "fail",
                "fea_result": skill_output.get("fea_result", {}),
                "constraint_results": skill_output.get("constraint_results", []),
                "timestamp": now.isoformat(),
            },
            "updated_at": now,
        }

        updated = cast(
            WorkProduct,
            await twin.update_work_product(work_product_id, updates, branch=branch),
        )

        logger.info(
            "writeback_stress_updated",
            work_product_id=str(work_product_id),
            validation_status=updates["metadata"]["validation_status"],
            session_id=str(session_id),
        )
        return updated


async def writeback_tolerance(
    twin: Any,
    session_id: UUID,
    branch: str,
    work_product_id: UUID,
    skill_output: dict[str, Any],
) -> WorkProduct:
    """Update an existing WorkProduct with tolerance check metadata.

    Args:
        twin: TwinAPI instance.
        session_id: Current agent session ID.
        branch: Twin branch to write to.
        work_product_id: ID of the WorkProduct to update.
        skill_output: Dict from the check_tolerance skill result.

    Returns:
        The updated WorkProduct.
    """
    with tracer.start_as_current_span("writeback.tolerance") as span:
        span.set_attribute("session.id", str(session_id))
        span.set_attribute("branch", branch)
        span.set_attribute("work_product.id", str(work_product_id))

        now = datetime.now(UTC)
        updates: dict[str, Any] = {
            "metadata": {
                "session_id": str(session_id),
                "skill": "check_tolerance",
                "overall_status": skill_output.get("overall_status", "unknown"),
                "total_dimensions_checked": skill_output.get("total_dimensions_checked", 0),
                "passed": skill_output.get("passed", 0),
                "warnings": skill_output.get("warnings", 0),
                "failures": skill_output.get("failures", 0),
                "summary": skill_output.get("summary", ""),
                "timestamp": now.isoformat(),
            },
            "updated_at": now,
        }

        updated = cast(
            WorkProduct,
            await twin.update_work_product(work_product_id, updates, branch=branch),
        )

        logger.info(
            "writeback_tolerance_updated",
            work_product_id=str(work_product_id),
            overall_status=updates["metadata"]["overall_status"],
            session_id=str(session_id),
        )
        return updated
