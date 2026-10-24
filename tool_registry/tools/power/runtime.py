"""Battery runtime and capacity (FORGE-681).

Pure arithmetic over figures the caller supplies, like the power budget:
every figure is a :class:`Figure` with its source; one not given is unknown,
and a result that depends on it is ``not_established``.

    usable energy  E = capacity_Wh * usable_fraction
    runtime        t = E / average_load_W
    capacity for a required runtime   C = average_load_W * t_required / usable_fraction

Capacity may be given in Wh, or in mAh with the nominal voltage
(``Wh = mAh * V / 1000``). The average load is taken at the battery terminals
(the budget's ``source_power_mw``), so converter losses are already in it.
The peak current is checked against the battery's continuous discharge
rating, the figure that limits it.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

__all__ = ["Figure", "RuntimeRequest", "RuntimeResult", "battery_runtime"]

Verdict = Literal["pass", "fail", "not_established"]


class Figure(BaseModel):
    """One figure and where it came from."""

    value: float
    source: str = Field(..., min_length=1, description="Datasheet, requirement, measurement")

    @model_validator(mode="after")
    def _finite(self) -> Figure:
        if not math.isfinite(self.value):
            raise ValueError("a figure must be finite")
        return self


def _v(fig: Figure | None) -> float | None:
    return None if fig is None else fig.value


class RuntimeRequest(BaseModel):
    capacity_wh: Figure | None = Field(default=None, description="Rated capacity, Wh")
    capacity_mah: Figure | None = Field(default=None, description="Rated capacity, mAh")
    nominal_voltage_v: Figure | None = Field(
        default=None, description="Nominal pack voltage, V (needed with mAh)"
    )
    usable_fraction: Figure | None = Field(
        default=None, description="Usable share of capacity (depth of discharge, ageing), 0..1"
    )
    average_load_mw: Figure | None = Field(
        default=None, description="Average power drawn at the battery terminals, mW"
    )
    peak_current_ma: Figure | None = Field(default=None, description="Peak battery current, mA")
    max_continuous_ma: Figure | None = Field(
        default=None, description="Battery's rated continuous discharge current, mA"
    )
    required_runtime_h: Figure | None = Field(
        default=None, description="Runtime the requirements ask for, h"
    )

    @model_validator(mode="after")
    def _ranges(self) -> RuntimeRequest:
        for name in ("capacity_wh", "capacity_mah", "nominal_voltage_v", "average_load_mw"):
            fig = getattr(self, name)
            if fig is not None and fig.value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.usable_fraction is not None and not 0 < self.usable_fraction.value <= 1:
            raise ValueError("usable_fraction must be in (0, 1]")
        if self.capacity_wh is not None and self.capacity_mah is not None:
            raise ValueError("give capacity_wh or capacity_mah, not both")
        return self


class RuntimeResult(BaseModel):
    verdict: Verdict
    capacity_wh: float | None = None
    usable_energy_wh: float | None = None
    runtime_h: float | None = None
    runtime_margin_h: float | None = Field(default=None, description="runtime - required")
    required_capacity_wh: float | None = Field(
        default=None, description="Rated capacity the required runtime needs"
    )
    required_capacity_mah: float | None = None
    peak_margin_ma: float | None = Field(
        default=None, description="max continuous - peak (>= 0 passes)"
    )
    unknowns: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def battery_runtime(request: RuntimeRequest) -> RuntimeResult:
    """Runtime from the battery and the average load; capacity a required runtime needs."""
    result = RuntimeResult(
        verdict="not_established",
        assumptions=[
            "average load taken at the battery terminals (converter losses included)",
            "constant power draw; no temperature or rate derating beyond usable_fraction",
        ],
    )
    unknowns = result.unknowns
    volts = _v(request.nominal_voltage_v)
    cap_wh = _v(request.capacity_wh)
    if cap_wh is None and request.capacity_mah is not None:
        if volts is None:
            unknowns.append("nominal_voltage_v (needed to convert mAh)")
        else:
            cap_wh = request.capacity_mah.value * volts / 1000.0
    if cap_wh is None and request.capacity_mah is None:
        unknowns.append("capacity_wh or capacity_mah")
    usable = _v(request.usable_fraction)
    if usable is None:
        unknowns.append("usable_fraction")
    load_w = request.average_load_mw.value / 1000.0 if request.average_load_mw else None
    if load_w is None:
        unknowns.append("average_load_mw")
    result.capacity_wh = round(cap_wh, 4) if cap_wh is not None else None

    if cap_wh is not None and usable is not None:
        result.usable_energy_wh = round(cap_wh * usable, 4)
    if result.usable_energy_wh is not None and load_w:
        result.runtime_h = round(result.usable_energy_wh / load_w, 4)

    required = _v(request.required_runtime_h)
    if required is not None and load_w and usable:
        need_wh = load_w * required / usable
        result.required_capacity_wh = round(need_wh, 4)
        if volts:
            result.required_capacity_mah = round(need_wh * 1000.0 / volts, 1)
    if required is not None and result.runtime_h is not None:
        result.runtime_margin_h = round(result.runtime_h - required, 4)

    peak, rating = _v(request.peak_current_ma), _v(request.max_continuous_ma)
    if peak is not None and rating is not None:
        result.peak_margin_ma = round(rating - peak, 3)
    elif peak is not None:
        unknowns.append("max_continuous_ma (to check the peak current)")

    fails: list[str] = []
    if result.runtime_margin_h is not None and result.runtime_margin_h < 0:
        fails.append(
            f"runtime {result.runtime_h:.2f} h is short of the required {required:g} h; "
            f"the load needs {result.required_capacity_wh:.1f} Wh rated"
        )
    if result.peak_margin_ma is not None and result.peak_margin_ma < 0:
        fails.append(f"peak {peak:g} mA exceeds the battery's {rating:g} mA continuous rating")
    result.findings.extend(fails)
    if fails:
        result.verdict = "fail"
    elif not unknowns and result.runtime_h is not None:
        result.verdict = "pass"
    return result
