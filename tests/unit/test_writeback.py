"""Tests for mechanical skill result -> Twin writeback."""

from __future__ import annotations

from uuid import uuid4

import pytest

from domain_agents.mechanical.writeback import (
    writeback_cad,
    writeback_mesh,
    writeback_stress,
    writeback_tolerance,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture
def session_id():  # type: ignore[no-untyped-def]
    return uuid4()


# ---------------------------------------------------------------------------
# writeback_cad
# ---------------------------------------------------------------------------


class TestWritebackCad:
    """writeback_cad creates a CAD_MODEL WorkProduct."""

    async def test_creates_cad_model_work_product(
        self, twin: InMemoryTwinAPI, session_id
    ):  # type: ignore[no-untyped-def]
        skill_output = {
            "skill": "generate_cad",
            "cad_file": "/out/bracket.step",
            "shape_type": "bracket",
            "volume_mm3": 1234.5,
            "surface_area_mm2": 678.9,
            "bounding_box": {"min_x": 0, "min_y": 0, "min_z": 0, "max_x": 10, "max_y": 10, "max_z": 5},
            "parameters_used": {"width": 10, "height": 10},
            "material": "aluminum_6061",
        }

        wp = await writeback_cad(twin, session_id, "main", skill_output)

        assert isinstance(wp, WorkProduct)
        assert wp.type == WorkProductType.CAD_MODEL
        assert wp.domain == "mechanical"
        assert wp.file_path == "/out/bracket.step"
        assert wp.format == "step"
        assert wp.metadata["skill"] == "generate_cad"
        assert wp.metadata["shape_type"] == "bracket"
        assert wp.metadata["volume_mm3"] == 1234.5

    async def test_includes_session_id_and_timestamp(
        self, twin: InMemoryTwinAPI, session_id
    ):  # type: ignore[no-untyped-def]
        skill_output = {
            "cad_file": "/out/plate.step",
            "shape_type": "plate",
            "volume_mm3": 100.0,
            "surface_area_mm2": 200.0,
            "material": "steel",
        }

        wp = await writeback_cad(twin, session_id, "main", skill_output)

        assert wp.metadata["session_id"] == str(session_id)
        assert "timestamp" in wp.metadata
        assert wp.created_at is not None

    async def test_persists_in_twin(
        self, twin: InMemoryTwinAPI, session_id
    ):  # type: ignore[no-untyped-def]
        skill_output = {"cad_file": "/out/cyl.step", "shape_type": "cylinder"}

        wp = await writeback_cad(twin, session_id, "main", skill_output)
        fetched = await twin.get_work_product(wp.id, branch="main")

        assert fetched is not None
        assert fetched.id == wp.id


# ---------------------------------------------------------------------------
# writeback_mesh
# ---------------------------------------------------------------------------


class TestWritebackMesh:
    """writeback_mesh creates a SIMULATION_RESULT WorkProduct."""

    async def test_creates_mesh_work_product(
        self, twin: InMemoryTwinAPI, session_id
    ):  # type: ignore[no-untyped-def]
        skill_output = {
            "skill": "generate_mesh",
            "mesh_file": "/out/bracket.inp",
            "num_nodes": 5000,
            "num_elements": 12000,
            "element_types": ["C3D10"],
            "quality_acceptable": True,
            "quality_issues": [],
            "algorithm_used": "netgen",
            "element_size_used": 1.0,
        }

        wp = await writeback_mesh(twin, session_id, "main", skill_output)

        assert isinstance(wp, WorkProduct)
        assert wp.type == WorkProductType.SIMULATION_RESULT
        assert wp.domain == "mechanical"
        assert wp.file_path == "/out/bracket.inp"
        assert wp.metadata["skill"] == "generate_mesh"
        assert wp.metadata["num_nodes"] == 5000
        assert wp.metadata["num_elements"] == 12000

    async def test_includes_session_id_and_timestamp(
        self, twin: InMemoryTwinAPI, session_id
    ):  # type: ignore[no-untyped-def]
        skill_output = {
            "mesh_file": "/out/m.inp",
            "algorithm_used": "gmsh",
            "element_size_used": 2.0,
        }

        wp = await writeback_mesh(twin, session_id, "main", skill_output)

        assert wp.metadata["session_id"] == str(session_id)
        assert "timestamp" in wp.metadata


# ---------------------------------------------------------------------------
# writeback_stress
# ---------------------------------------------------------------------------


class TestWritebackStress:
    """writeback_stress updates an existing WorkProduct with validation metadata."""

    async def _create_wp(self, twin: InMemoryTwinAPI) -> WorkProduct:
        wp = WorkProduct(
            name="bracket_cad",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="/out/bracket.step",
            content_hash="abc123",
            format="step",
            created_by="test",
        )
        return await twin.create_work_product(wp, branch="main")

    async def test_updates_work_product_with_validation_status(
        self, twin: InMemoryTwinAPI, session_id
    ):  # type: ignore[no-untyped-def]
        original = await self._create_wp(twin)

        skill_output = {
            "skill": "validate_stress",
            "fea_result": {"max_von_mises": {"body": 80.0}},
            "constraint_results": [
                {"region": "body", "stress_mpa": 80.0, "allowable_mpa": 150.0, "passed": True}
            ],
            "overall_passed": True,
        }

        updated = await writeback_stress(
            twin, session_id, "main", original.id, skill_output
        )

        assert updated.metadata["validation_status"] == "pass"
        assert updated.metadata["skill"] == "validate_stress"
        assert updated.metadata["session_id"] == str(session_id)
        assert "timestamp" in updated.metadata

    async def test_records_fail_status(
        self, twin: InMemoryTwinAPI, session_id
    ):  # type: ignore[no-untyped-def]
        original = await self._create_wp(twin)

        skill_output = {
            "overall_passed": False,
            "fea_result": {},
            "constraint_results": [],
        }

        updated = await writeback_stress(
            twin, session_id, "main", original.id, skill_output
        )

        assert updated.metadata["validation_status"] == "fail"

    async def test_includes_session_id_and_timestamp(
        self, twin: InMemoryTwinAPI, session_id
    ):  # type: ignore[no-untyped-def]
        original = await self._create_wp(twin)

        skill_output = {"overall_passed": True, "fea_result": {}, "constraint_results": []}

        updated = await writeback_stress(
            twin, session_id, "main", original.id, skill_output
        )

        assert updated.metadata["session_id"] == str(session_id)
        assert "timestamp" in updated.metadata
        assert updated.updated_at is not None


# ---------------------------------------------------------------------------
# writeback_tolerance
# ---------------------------------------------------------------------------


class TestWritebackTolerance:
    """writeback_tolerance updates an existing WorkProduct with tolerance metadata."""

    async def _create_wp(self, twin: InMemoryTwinAPI) -> WorkProduct:
        wp = WorkProduct(
            name="bracket_cad",
            type=WorkProductType.CAD_MODEL,
            domain="mechanical",
            file_path="/out/bracket.step",
            content_hash="abc123",
            format="step",
            created_by="test",
        )
        return await twin.create_work_product(wp, branch="main")

    async def test_updates_work_product_with_tolerance_results(
        self, twin: InMemoryTwinAPI, session_id
    ):  # type: ignore[no-untyped-def]
        original = await self._create_wp(twin)

        skill_output = {
            "skill": "check_tolerance",
            "overall_status": "pass",
            "total_dimensions_checked": 5,
            "passed": 5,
            "warnings": 0,
            "failures": 0,
            "summary": "All dimensions within tolerance",
        }

        updated = await writeback_tolerance(
            twin, session_id, "main", original.id, skill_output
        )

        assert updated.metadata["overall_status"] == "pass"
        assert updated.metadata["total_dimensions_checked"] == 5
        assert updated.metadata["skill"] == "check_tolerance"
        assert updated.metadata["session_id"] == str(session_id)
        assert "timestamp" in updated.metadata

    async def test_records_fail_status(
        self, twin: InMemoryTwinAPI, session_id
    ):  # type: ignore[no-untyped-def]
        original = await self._create_wp(twin)

        skill_output = {
            "overall_status": "fail",
            "total_dimensions_checked": 3,
            "passed": 1,
            "warnings": 0,
            "failures": 2,
            "summary": "2 dimensions out of tolerance",
        }

        updated = await writeback_tolerance(
            twin, session_id, "main", original.id, skill_output
        )

        assert updated.metadata["overall_status"] == "fail"
        assert updated.metadata["failures"] == 2

    async def test_includes_session_id_and_timestamp(
        self, twin: InMemoryTwinAPI, session_id
    ):  # type: ignore[no-untyped-def]
        original = await self._create_wp(twin)

        skill_output = {
            "overall_status": "pass",
            "total_dimensions_checked": 1,
            "passed": 1,
            "warnings": 0,
            "failures": 0,
            "summary": "OK",
        }

        updated = await writeback_tolerance(
            twin, session_id, "main", original.id, skill_output
        )

        assert updated.metadata["session_id"] == str(session_id)
        assert "timestamp" in updated.metadata
        assert updated.updated_at is not None
