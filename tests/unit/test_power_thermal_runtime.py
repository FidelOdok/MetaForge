"""Regulator heat and battery runtime (FORGE-681)."""

from __future__ import annotations

from typing import Any

import pytest

from tool_registry.tools.power.adapter import PowerServer
from tool_registry.tools.power.budget import check_budget_dict
from tool_registry.tools.power.runtime import RuntimeRequest, battery_runtime


def fig(v: float, src: str = "datasheet") -> dict[str, Any]:
    return {"value": v, "source": src}


def _tree(regulator: dict[str, Any], ambient: float | None = 60.0) -> dict[str, Any]:
    req: dict[str, Any] = {
        "derating": 0.8,
        "rails": [
            {"name": "VBAT", "voltage_v": 12, "source_kind": "supply", "rated_current_ma": 3000},
            {
                "name": "5V",
                "voltage_v": 5,
                "input_rail": "VBAT",
                "rated_current_ma": 1000,
                **regulator,
            },
        ],
        "loads": [{"name": "servo", "rail": "5V", "current_ma": 300, "source": "servo DS"}],
    }
    if ambient is not None:
        req["ambient_c"] = ambient
    return req


class TestRegulatorThermal:
    def test_an_ldo_dropping_7_v_at_300_ma_runs_too_hot(self) -> None:
        r = check_budget_dict(
            _tree(
                {"source_kind": "ldo", "quiescent_ma": 5, "theta_ja_c_per_w": 60, "tj_max_c": 125}
            )
        )
        five = next(b for b in r["rails"] if b["name"] == "5V")
        # (12 - 5) V * 300 mA + 12 V * 5 mA = 2160 mW; 60 C + 2.16 W * 60 C/W = 189.6 C
        assert five["dissipation_mw"] == pytest.approx(2160.0)
        assert five["junction_c"] == pytest.approx(189.6)
        assert five["tj_margin_c"] == pytest.approx(-64.6)
        assert five["thermal_status"] == "fail"
        assert r["thermal_verdict"] == "fail" and r["hottest_regulator"] == "5V"
        # The load budget is untouched: the rail still carries its load.
        assert r["verdict"] == "pass"

    def test_a_buck_in_its_place_runs_cool(self) -> None:
        r = check_budget_dict(
            _tree(
                {
                    "source_kind": "switching",
                    "efficiency": 0.9,
                    "theta_ja_c_per_w": 40,
                    "tj_max_c": 125,
                }
            )
        )
        five = next(b for b in r["rails"] if b["name"] == "5V")
        # Pout 1.5 W * (1/0.9 - 1) = 166.7 mW; 60 + 0.1667 * 40 = 66.67 C
        assert five["dissipation_mw"] == pytest.approx(166.667, abs=1e-3)
        assert five["junction_c"] == pytest.approx(66.67, abs=0.01)
        assert r["thermal_verdict"] == "pass"
        assert r["regulator_dissipation_mw"] == pytest.approx(166.667, abs=1e-3)

    def test_missing_thermal_figures_are_named_not_assumed(self) -> None:
        r = check_budget_dict(_tree({"source_kind": "ldo"}, ambient=None))
        five = next(b for b in r["rails"] if b["name"] == "5V")
        assert five["dissipation_mw"] == pytest.approx(2100.0)  # still computable
        assert five["junction_c"] is None
        assert five["thermal_status"] == "not_established"
        assert set(five["thermal_unknowns"]) == {
            "theta_JA not given",
            "Tj max not given",
            "ambient temperature not given",
        }
        assert r["thermal_verdict"] == "not_established"

    def test_unknown_load_leaves_dissipation_unknown(self) -> None:
        req = _tree({"source_kind": "ldo", "theta_ja_c_per_w": 60, "tj_max_c": 125})
        req["loads"].append({"name": "radio", "rail": "5V"})
        r = check_budget_dict(req)
        five = next(b for b in r["rails"] if b["name"] == "5V")
        assert five["dissipation_mw"] is None
        assert "output current not established" in five["thermal_unknowns"]
        assert r["regulator_dissipation_mw"] is None

    def test_no_regulator_means_no_thermal_verdict(self) -> None:
        r = check_budget_dict(
            {
                "derating": 1.0,
                "rails": [
                    {"name": "V", "voltage_v": 5, "source_kind": "supply", "rated_current_ma": 100}
                ],
                "loads": [],
            }
        )
        assert r["thermal_verdict"] is None


class TestRuntime:
    def _req(self, **kw: Any) -> RuntimeRequest:
        base: dict[str, Any] = {
            "capacity_mah": fig(5000, "pack DS"),
            "nominal_voltage_v": fig(14.8, "4S"),
            "usable_fraction": fig(0.8, "REQ"),
            "average_load_mw": fig(12000, "power.check_budget"),
        }
        base.update(kw)
        return RuntimeRequest.model_validate(base)

    def test_runtime_and_the_capacity_a_requirement_needs(self) -> None:
        r = battery_runtime(self._req(required_runtime_h=fig(6, "REQ")))
        # 5000 mAh * 14.8 V = 74 Wh; * 0.8 = 59.2 Wh; / 12 W = 4.933 h
        assert r.capacity_wh == pytest.approx(74.0)
        assert r.runtime_h == pytest.approx(4.9333, abs=1e-4)
        assert r.required_capacity_wh == pytest.approx(90.0)
        assert r.required_capacity_mah == pytest.approx(6081.1, abs=0.1)
        assert r.verdict == "fail" and "short of the required 6 h" in r.findings[0]

    def test_enough_battery_passes(self) -> None:
        r = battery_runtime(self._req(required_runtime_h=fig(4, "REQ")))
        assert r.verdict == "pass" and r.runtime_margin_h == pytest.approx(0.9333, abs=1e-4)

    def test_peak_over_the_continuous_rating_fails(self) -> None:
        r = battery_runtime(
            self._req(peak_current_ma=fig(60000, "motors"), max_continuous_ma=fig(50000, "10C"))
        )
        assert r.verdict == "fail" and r.peak_margin_ma == -10000

    def test_missing_figures_are_not_established(self) -> None:
        r = battery_runtime(RuntimeRequest.model_validate({"capacity_mah": fig(5000)}))
        assert r.verdict == "not_established"
        assert {
            "nominal_voltage_v (needed to convert mAh)",
            "usable_fraction",
            "average_load_mw",
        } <= set(r.unknowns)

    def test_refusals(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            RuntimeRequest.model_validate({"capacity_mah": fig(1), "capacity_wh": fig(1)})
        with pytest.raises(ValueError, match="usable_fraction"):
            RuntimeRequest.model_validate({"usable_fraction": fig(1.2)})


async def test_adapter_serves_both_tools() -> None:
    server = PowerServer()
    assert set(server.tool_ids) == {"power.check_budget", "power.runtime"}
    out = await server.runtime(
        {"capacity_wh": fig(10), "usable_fraction": fig(1), "average_load_mw": fig(1000)}
    )
    assert out["runtime_h"] == pytest.approx(10.0) and out["verdict"] == "pass"
