"""power.check_budget: per-rail worst-case budget (FORGE-544)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tool_registry.tools.power.adapter import PowerServer
from tool_registry.tools.power.budget import check_budget_dict

VBAT = {"name": "VBAT", "voltage_v": 7.4, "source_kind": "supply", "rated_current_ma": 3000}
BUCK = {
    "name": "5V0",
    "voltage_v": 5.0,
    "source_kind": "switching",
    "rated_current_ma": 2000,
    "input_rail": "VBAT",
    "efficiency": 0.9,
}
LDO = {
    "name": "3V3",
    "voltage_v": 3.3,
    "source_kind": "ldo",
    "rated_current_ma": 600,
    "input_rail": "5V0",
    "quiescent_ma": 0.055,
}


def _rails(result: dict) -> dict[str, dict]:
    return {r["name"]: r for r in result["rails"]}


class TestArithmetic:
    def test_regulator_input_current_is_carried_upstream(self) -> None:
        result = check_budget_dict(
            {
                "derating": 0.8,
                "rails": [VBAT, BUCK, LDO],
                "loads": [
                    {"name": "ESP32", "rail": "3V3", "current_ma": 240},
                    {"name": "IMU", "rail": "3V3", "current_ma": 1},
                    {"name": "servo", "rail": "5V0", "power_mw": 2500},
                ],
            }
        )
        rails = _rails(result)
        assert rails["3V3"]["load_ma"] == pytest.approx(241.0)
        # LDO: its output plus quiescent, drawn from 5V0
        assert rails["5V0"]["load_ma"] == pytest.approx(500 + 241.055)
        # buck: Vout * Iout / (eff * Vin)
        assert rails["VBAT"]["load_ma"] == pytest.approx(5 * 741.055 / (0.9 * 7.4), abs=1e-3)
        assert result["source_power_mw"] == pytest.approx(rails["VBAT"]["load_ma"] * 7.4, abs=0.01)
        assert rails["3V3"]["allowed_ma"] == pytest.approx(480.0)
        assert result["verdict"] == "pass"
        assert result["worst_rail"] == "3V3"

    def test_over_budget_rail_fails(self) -> None:
        result = check_budget_dict(
            {
                "derating": 0.8,
                "rails": [VBAT, BUCK, LDO],
                "loads": [{"name": "radio", "rail": "3V3", "current_ma": 500}],
            }
        )
        assert _rails(result)["3V3"]["status"] == "fail"
        assert result["verdict"] == "fail"
        assert result["passed"] is False
        assert result["worst_rail"] == "3V3"


class TestUnknowns:
    def test_unknown_load_leaves_the_rail_and_its_parents_open(self) -> None:
        result = check_budget_dict(
            {
                "derating": 0.8,
                "rails": [VBAT, BUCK, LDO],
                "loads": [{"name": "GPS", "rail": "3V3"}],
            }
        )
        rails = _rails(result)
        assert rails["3V3"]["status"] == "not_established"
        assert rails["5V0"]["status"] == "not_established"
        assert result["verdict"] == "not_established"
        assert result["source_power_mw"] is None

    def test_known_overload_fails_even_with_unknowns(self) -> None:
        result = check_budget_dict(
            {
                "derating": 1.0,
                "rails": [LDO | {"input_rail": None, "source_kind": "supply"}],
                "loads": [
                    {"name": "radio", "rail": "3V3", "current_ma": 700},
                    {"name": "GPS", "rail": "3V3"},
                ],
            }
        )
        assert result["verdict"] == "fail"

    def test_missing_rating_or_efficiency_is_not_established(self) -> None:
        result = check_budget_dict(
            {
                "derating": 0.8,
                "rails": [VBAT, BUCK | {"efficiency": None}, LDO | {"rated_current_ma": None}],
                "loads": [{"name": "MCU", "rail": "3V3", "current_ma": 10}],
            }
        )
        rails = _rails(result)
        assert rails["3V3"]["status"] == "not_established"
        assert any("efficiency" in u for u in rails["VBAT"]["unknowns"])

    def test_missing_quiescent_is_noted(self) -> None:
        result = check_budget_dict(
            {
                "derating": 0.8,
                "rails": [VBAT, BUCK, LDO | {"quiescent_ma": None}],
                "loads": [],
            }
        )
        assert any("quiescent" in n for n in _rails(result)["5V0"]["notes"])


class TestValidation:
    @pytest.mark.parametrize(
        "payload, match",
        [
            ({"derating": 0.8, "rails": [VBAT, LDO]}, "not a rail"),
            ({"derating": 0.8, "rails": [BUCK | {"input_rail": None}]}, "needs input_rail"),
            (
                {
                    "derating": 0.8,
                    "rails": [BUCK | {"input_rail": "3V3"}, LDO],
                },
                "loop",
            ),
            (
                {
                    "derating": 0.8,
                    "rails": [VBAT],
                    "loads": [{"name": "x", "rail": "VBAT", "current_ma": 1, "power_mw": 1}],
                },
                "not both",
            ),
        ],
    )
    def test_ill_formed_trees_are_refused(self, payload: dict, match: str) -> None:
        with pytest.raises((ValueError, ValidationError), match=match):
            check_budget_dict(payload)

    def test_derating_is_required(self) -> None:
        with pytest.raises(ValidationError):
            check_budget_dict({"rails": [VBAT]})


class TestAdapter:
    async def test_tool_is_served_and_computes(self) -> None:
        server = PowerServer()
        assert "power.check_budget" in server.tool_ids
        out = await server.check_budget(
            {
                "derating": 0.8,
                "rails": [VBAT],
                "loads": [{"name": "x", "rail": "VBAT", "current_ma": 100}],
            }
        )
        assert out["verdict"] == "pass"

    def test_input_schema_has_no_refs(self) -> None:
        import json

        manifest = PowerServer()._tools["power.check_budget"].manifest
        assert "$ref" not in json.dumps(manifest.input_schema)
