"""In-skill tests for check_tolerance (run via pytest discovery)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from skill_registry.skill_base import SkillContext

from .handler import CheckToleranceHandler
from .schema import (
    CheckToleranceInput,
    ManufacturingProcess,
    ToleranceSpec,
)


@pytest.fixture()
def mock_context() -> SkillContext:
    ctx = MagicMock(spec=SkillContext)
    ctx.twin = AsyncMock()
    ctx.logger = MagicMock()
    ctx.logger.bind = MagicMock(return_value=ctx.logger)
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


@pytest.fixture()
def cnc_process() -> ManufacturingProcess:
    return ManufacturingProcess(
        process_type="cnc_milling",
        achievable_tolerance=0.05,
        surface_finish_ra=1.6,
        min_feature_size=0.5,
        max_aspect_ratio=10.0,
    )


@pytest.fixture()
def passing_input(cnc_process: ManufacturingProcess) -> CheckToleranceInput:
    return CheckToleranceInput(
        artifact_id="artifact-123",
        tolerances=[
            ToleranceSpec(
                dimension_id="D1",
                feature_name="bore_diameter",
                nominal_value=25.0,
                upper_tolerance=0.1,
                lower_tolerance=-0.1,
            ),
        ],
        manufacturing_process=cnc_process,
    )


class TestSkillInline:
    async def test_handler_executes(
        self, mock_context: SkillContext, passing_input: CheckToleranceInput
    ) -> None:
        mock_context.twin.get_artifact.return_value = {"id": passing_input.artifact_id}
        handler = CheckToleranceHandler(mock_context)
        output = await handler.execute(passing_input)
        assert output.overall_status == "pass"
