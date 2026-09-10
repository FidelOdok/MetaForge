"""Constraint-synthesis Apply: suggestion stub (MET-519) + parametric binding (MET-531)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api_gateway.constraint.routes import (
    DeltaTransform,
    RotationDelta,
    ScaleDelta,
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


class TestSynthesizeRotation:
    async def _rotate(self, group: str, axis: str, angle_deg: float):
        return await synthesize_constraint(
            SynthesizeRequest(
                group_name=group,
                delta=DeltaTransform(rotation=RotationDelta(axis=axis, angle_deg=angle_deg)),
            )
        )

    async def test_rotation_becomes_a_constraint(self) -> None:
        resp = await self._rotate("hinge_group", "y", 45.0)
        assert resp.status == "ok"
        assert resp.constraint is not None
        assert resp.constraint.parameter == "hinge_group_rotation_y"
        assert resp.constraint.value == 45.0
        assert resp.constraint.unit == "deg"
        assert "about Y" in resp.suggestion

    async def test_zero_rotation_is_noop(self) -> None:
        resp = await self._rotate("g", "x", 0.0)
        assert resp.status == "noop"
        assert resp.constraint is None


class TestSynthesizeScale:
    async def _scale(self, group: str, axis: str, factor: float):
        return await synthesize_constraint(
            SynthesizeRequest(
                group_name=group,
                delta=DeltaTransform(scale=ScaleDelta(axis=axis, factor=factor)),
            )
        )

    async def test_scale_becomes_a_constraint(self) -> None:
        resp = await self._scale("bracket_group", "z", 1.5)
        assert resp.status == "ok"
        assert resp.constraint is not None
        assert resp.constraint.parameter == "bracket_group_scale_z"
        assert resp.constraint.value == 1.5
        assert "along Z" in resp.suggestion

    async def test_identity_scale_is_noop(self) -> None:
        resp = await self._scale("g", "x", 1.0)
        assert resp.status == "noop"
        assert resp.constraint is None

    async def test_oversized_scale_is_conflict(self) -> None:
        resp = await self._scale("g", "x", 15.0)
        assert resp.status == "conflict"
        assert resp.constraint is None
        assert resp.conflict_reason and "feasible" in resp.conflict_reason

    async def test_negative_scale_is_conflict(self) -> None:
        resp = await self._scale("g", "x", -1.0)
        assert resp.status == "conflict"
        assert resp.constraint is None


class TestParametricBindingRotationAndScale:
    """MET-611: rotation/scale Apply also binds when session_id + obj_id supplied."""

    async def test_rotation_binds_with_default_property_path(self) -> None:
        fake = AsyncMock()
        fake.apply_parametric_binding.return_value = {"bound": True, "expression": "e"}
        with patch("api_gateway.constraint.routes._freecad_client", fake):
            resp = await synthesize_constraint(
                SynthesizeRequest(
                    group_name="hinge",
                    delta=DeltaTransform(rotation=RotationDelta(axis="y", angle_deg=30.0)),
                    session_id="s",
                    obj_id="o",
                )
            )
        assert resp.bound is True
        kwargs = fake.apply_parametric_binding.call_args.kwargs
        assert kwargs["parameter"] == "hinge_rotation_y"
        assert kwargs["property_path"] == "Placement.Rotation.Angle"

    async def test_scale_binds_with_default_property_path(self) -> None:
        fake = AsyncMock()
        fake.apply_parametric_binding.return_value = {"bound": True, "expression": "e"}
        with patch("api_gateway.constraint.routes._freecad_client", fake):
            resp = await synthesize_constraint(
                SynthesizeRequest(
                    group_name="bracket",
                    delta=DeltaTransform(scale=ScaleDelta(axis="x", factor=2.0)),
                    session_id="s",
                    obj_id="o",
                )
            )
        assert resp.bound is True
        kwargs = fake.apply_parametric_binding.call_args.kwargs
        assert kwargs["parameter"] == "bracket_scale_x"
        assert kwargs["property_path"] == "Scale"


class TestParametricBinding:
    """MET-531: with session_id + obj_id, Apply binds the parameter into FreeCAD."""

    async def _apply(self, **kw):
        return await synthesize_constraint(SynthesizeRequest(**kw))

    async def test_binds_into_freecad_when_session_and_obj_supplied(self) -> None:
        fake = AsyncMock()
        fake.apply_parametric_binding.return_value = {
            "bound": True,
            "parameter": "motor_position_x",
            "value": 5.0,
            "expression": "<<ConstraintParams>>.motor_position_x",
            "varset": "ConstraintParams",
        }
        with patch("api_gateway.constraint.routes._freecad_client", fake):
            resp = await self._apply(
                group_name="motor",
                delta=DeltaTransform(dx=5.0, dy=0.4),
                session_id="sess1",
                obj_id="primitive_1",
            )
        assert resp.status == "ok"
        assert resp.bound is True
        assert resp.expression == "<<ConstraintParams>>.motor_position_x"
        # The dominant axis (x) drives Placement.Base.x.
        kwargs = fake.apply_parametric_binding.call_args.kwargs
        assert kwargs["parameter"] == "motor_position_x"
        assert kwargs["value"] == 5.0
        assert kwargs["property_path"] == "Placement.Base.x"
        assert kwargs["session_id"] == "sess1"
        assert kwargs["obj_id"] == "primitive_1"

    async def test_custom_property_path_is_passed_through(self) -> None:
        fake = AsyncMock()
        fake.apply_parametric_binding.return_value = {"bound": True, "expression": "e"}
        with patch("api_gateway.constraint.routes._freecad_client", fake):
            await self._apply(
                group_name="g",
                delta=DeltaTransform(dz=12.0),
                session_id="s",
                obj_id="o",
                property_path="Length",
            )
        assert fake.apply_parametric_binding.call_args.kwargs["property_path"] == "Length"

    async def test_no_session_means_suggestion_only(self) -> None:
        fake = AsyncMock()
        with patch("api_gateway.constraint.routes._freecad_client", fake):
            resp = await self._apply(group_name="g", delta=DeltaTransform(dx=5.0))
        assert resp.status == "ok"
        assert resp.bound is False
        fake.apply_parametric_binding.assert_not_called()

    async def test_binding_failure_is_graceful(self) -> None:
        fake = AsyncMock()
        fake.apply_parametric_binding.side_effect = RuntimeError("adapter down")
        with patch("api_gateway.constraint.routes._freecad_client", fake):
            resp = await self._apply(
                group_name="g", delta=DeltaTransform(dx=5.0), session_id="s", obj_id="o"
            )
        # Suggestion survives; failure surfaced, not raised.
        assert resp.status == "ok"
        assert resp.bound is False
        assert resp.binding_error == "adapter down"

    async def test_conflict_does_not_attempt_binding(self) -> None:
        fake = AsyncMock()
        with patch("api_gateway.constraint.routes._freecad_client", fake):
            resp = await self._apply(
                group_name="g", delta=DeltaTransform(dx=600.0), session_id="s", obj_id="o"
            )
        assert resp.status == "conflict"
        assert resp.bound is False
        fake.apply_parametric_binding.assert_not_called()
