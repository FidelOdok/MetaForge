"""Constraint-synthesis stub for rigid-group Apply (MET-519)."""

from __future__ import annotations

import pytest

from api_gateway.constraint.routes import (
    DeltaTransform,
    SynthesizeRequest,
    synthesize_constraint,
)


async def _call(group: str, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0):
    return await synthesize_constraint(
        SynthesizeRequest(group_name=group, delta=DeltaTransform(dx=dx, dy=dy, dz=dz))
    )


class TestSynthesize:
    async def test_dominant_axis_becomes_a_constraint(self) -> None:
        resp = await _call("motor_group", dx=5.0, dy=0.4)
        assert resp.status == "ok"
        assert resp.constraint is not None
        assert resp.constraint.parameter == "motor_group_position_x"
        assert resp.constraint.value == 5.0
        assert "along X" in resp.suggestion
        assert "5.0mm" in resp.suggestion

    async def test_picks_largest_axis(self) -> None:
        resp = await _call("g", dx=1.0, dy=-9.0, dz=2.0)
        assert resp.constraint is not None
        assert resp.constraint.parameter == "g_position_y"
        assert resp.constraint.value == -9.0

    async def test_zero_delta_is_noop(self) -> None:
        resp = await _call("g")
        assert resp.status == "noop"
        assert resp.constraint is None

    async def test_oversized_delta_is_conflict(self) -> None:
        resp = await _call("g", dx=600.0)
        assert resp.status == "conflict"
        assert resp.constraint is None
        assert resp.conflict_reason and "feasible" in resp.conflict_reason

    def test_empty_group_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            SynthesizeRequest(group_name="", delta=DeltaTransform(dx=1.0))
