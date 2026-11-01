"""Corrugated carton arithmetic: compression strength and RSC dieline (FORGE-672).

Pure arithmetic over figures the caller supplies, like the power budget
(FORGE-544): nothing is looked up and nothing is assumed. Every figure is a
:class:`Figure` with its source; one not given is *unknown*, and a result
that depends on it is ``not_established``.

**Box compression** (:func:`box_compression`). The simplified McKee formula
estimates the top-to-bottom compression strength of a regular slotted
container from the board's edge crush test value, its caliper and the box
perimeter::

    BCT = k * ECT * sqrt(caliper * perimeter)

with ``k = 5.87`` in consistent SI units (N, N/m, m) -- McKee, Gander and
Wachuta (1963), the form used throughout the corrugated industry. It is an
estimate for RSC-style boxes with the flutes vertical; other styles, holes
and inserts change it, which is why it is reported as an estimate and a real
compression test outranks it.

The estimate is reduced by every derating factor given (humidity, storage
time, pallet overhang, handling), each between 0 and 1 with its source, and
compared with the worst-case stack load -- the boxes above the bottom one --
times the required safety factor.

**RSC dieline** (:func:`rsc_dieline`). The blank of a regular slotted
container (FEFCO 0201) from its *inside* dimensions: four side panels plus
the manufacturer's glue flap across, and the depth plus two half-width flaps
down, each fold adding the converter's score allowance. The blank is checked
against the converter's sheet size, minimum glue flap and minimum panel.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "CompressionRequest",
    "CompressionResult",
    "DielineRequest",
    "DielineResult",
    "Figure",
    "box_compression",
    "rsc_dieline",
]

#: McKee constant for the simplified formula in SI units (N, N/m, m).
MCKEE_K = 5.87
#: Standard gravity (CODATA), m/s^2 -- a defined constant, not a design value.
STANDARD_GRAVITY_M_S2 = 9.80665

Verdict = Literal["pass", "fail", "not_established"]


class Figure(BaseModel):
    """One engineering figure and where it came from."""

    value: float
    source: str = Field(..., min_length=1, description="Datasheet, requirement, measurement")

    @model_validator(mode="after")
    def _finite(self) -> Figure:
        if not math.isfinite(self.value):
            raise ValueError("a figure must be finite")
        return self


def _v(fig: Figure | None) -> float | None:
    return None if fig is None else fig.value


class Derating(BaseModel):
    """One factor that reduces the estimated strength (0 < factor <= 1)."""

    name: str = Field(..., min_length=1, description="e.g. 'humidity 85% RH', 'storage 90 days'")
    factor: Figure

    @model_validator(mode="after")
    def _range(self) -> Derating:
        if not 0 < self.factor.value <= 1:
            raise ValueError(f"derating {self.name!r}: factor must be in (0, 1]")
        return self


class CompressionRequest(BaseModel):
    ect_n_per_m: Figure | None = Field(default=None, description="Board edge crush (ECT), N/m")
    caliper_m: Figure | None = Field(default=None, description="Board thickness, m")
    inner_length_m: Figure | None = None
    inner_width_m: Figure | None = None
    gross_mass_kg: Figure | None = Field(default=None, description="One packed box, contents in")
    stack_count: int | None = Field(
        default=None, ge=1, le=100, description="Boxes in the stack, the bottom one included"
    )
    required_safety_factor: Figure | None = Field(
        default=None, description="Required BCT / stack load (from the requirements)"
    )
    deratings: list[Derating] = Field(default_factory=list)

    @model_validator(mode="after")
    def _positive(self) -> CompressionRequest:
        for name in (
            "ect_n_per_m",
            "caliper_m",
            "inner_length_m",
            "inner_width_m",
            "gross_mass_kg",
        ):
            fig = getattr(self, name)
            if fig is not None and fig.value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.required_safety_factor is not None and self.required_safety_factor.value < 1:
            raise ValueError("required_safety_factor must be at least 1")
        return self


class CompressionResult(BaseModel):
    verdict: Verdict
    perimeter_m: float | None = None
    bct_estimate_n: float | None = None
    derating_factor: float | None = None
    bct_derated_n: float | None = None
    stack_load_n: float | None = None
    required_strength_n: float | None = None
    compression_margin: float | None = Field(
        default=None, description="Derated BCT / required strength - 1 (>= 0 passes)"
    )
    unknowns: list[str] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def box_compression(request: CompressionRequest) -> CompressionResult:
    """McKee BCT estimate, derated, against the stack load times the safety factor."""
    unknowns = [
        name
        for name in (
            "ect_n_per_m",
            "caliper_m",
            "inner_length_m",
            "inner_width_m",
            "gross_mass_kg",
            "stack_count",
            "required_safety_factor",
        )
        if getattr(request, name) is None
    ]
    result = CompressionResult(
        verdict="not_established",
        unknowns=unknowns,
        assumptions=[
            f"simplified McKee: BCT = {MCKEE_K} * ECT * sqrt(caliper * perimeter), an estimate "
            "for an RSC with vertical flutes; a compression test outranks it",
            "perimeter from the inside dimensions",
            "stack load is the boxes above the bottom one, static",
        ],
    )
    ect, t = _v(request.ect_n_per_m), _v(request.caliper_m)
    length, width = _v(request.inner_length_m), _v(request.inner_width_m)
    if length is not None and width is not None:
        result.perimeter_m = 2 * (length + width)
    if None not in (ect, t, result.perimeter_m):
        result.bct_estimate_n = MCKEE_K * ect * math.sqrt(t * result.perimeter_m)  # type: ignore[operator]
    factor = 1.0
    for d in request.deratings:
        factor *= d.factor.value
    result.derating_factor = factor
    if not request.deratings:
        result.findings.append(
            "no derating factors given: the estimate is for standard conditions only"
        )
    if result.bct_estimate_n is not None:
        result.bct_derated_n = result.bct_estimate_n * factor
    mass, count = _v(request.gross_mass_kg), request.stack_count
    if mass is not None and count is not None:
        result.stack_load_n = (count - 1) * mass * STANDARD_GRAVITY_M_S2
    sf = _v(request.required_safety_factor)
    if result.stack_load_n is not None and sf is not None:
        result.required_strength_n = result.stack_load_n * sf
    if result.bct_derated_n is not None and result.required_strength_n:
        result.compression_margin = result.bct_derated_n / result.required_strength_n - 1
    if result.compression_margin is not None and result.compression_margin < 0:
        result.verdict = "fail"
        result.findings.append(
            f"derated strength {result.bct_derated_n:.0f} N is below the required "
            f"{result.required_strength_n:.0f} N"
        )
    elif result.compression_margin is not None and not result.unknowns:
        result.verdict = "pass"
    elif result.stack_load_n == 0 and result.bct_derated_n is not None and not result.unknowns:
        # A single box carries no stack: nothing to fail.
        result.compression_margin = None
        result.verdict = "pass"
    return result


class DielineRequest(BaseModel):
    inner_length_m: Figure | None = None
    inner_width_m: Figure | None = None
    inner_depth_m: Figure | None = None
    caliper_m: Figure | None = None
    score_allowance_m: Figure | None = Field(
        default=None, description="Added per fold by the converter's scoring (their rules)"
    )
    glue_flap_m: Figure | None = Field(default=None, description="Manufacturer's joint flap")
    min_glue_flap_m: Figure | None = Field(default=None, description="Converter's rule")
    min_panel_m: Figure | None = Field(default=None, description="Converter's smallest panel")
    max_sheet_length_m: Figure | None = Field(default=None, description="Converter's sheet")
    max_sheet_width_m: Figure | None = None

    @model_validator(mode="after")
    def _non_negative(self) -> DielineRequest:
        for name, fig in self:
            if isinstance(fig, Figure) and fig.value < 0:
                raise ValueError(f"{name} must not be negative")
        return self


class DielineResult(BaseModel):
    verdict: Verdict
    blank_length_m: float | None = Field(default=None, description="Across the panels")
    blank_width_m: float | None = Field(default=None, description="Depth plus the flaps")
    flap_depth_m: float | None = None
    violations: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


def rsc_dieline(request: DielineRequest) -> DielineResult:
    """The RSC (FEFCO 0201) blank and the converter's rules it must meet."""
    geometry = (
        "inner_length_m",
        "inner_width_m",
        "inner_depth_m",
        "score_allowance_m",
        "glue_flap_m",
    )
    rules = ("min_glue_flap_m", "min_panel_m", "max_sheet_length_m", "max_sheet_width_m")
    result = DielineResult(
        verdict="not_established",
        unknowns=[n for n in (*geometry, *rules) if getattr(request, n) is None],
        assumptions=[
            "FEFCO 0201 regular slotted container; flaps meet at the centre (half the width each)",
            "each fold adds the converter's score allowance to the panel it bounds",
        ],
    )
    length, width = _v(request.inner_length_m), _v(request.inner_width_m)
    depth, allow = _v(request.inner_depth_m), _v(request.score_allowance_m)
    glue = _v(request.glue_flap_m)
    if None not in (length, width, allow, glue):
        # Four panels, each grown by one score allowance, plus the glue flap.
        result.blank_length_m = 2 * (length + width) + 4 * allow + glue  # type: ignore[operator]
    if None not in (width, depth, allow):
        result.flap_depth_m = width / 2 + allow  # type: ignore[operator]
        result.blank_width_m = depth + 2 * allow + 2 * result.flap_depth_m  # type: ignore[operator]

    v = result.violations
    min_glue, min_panel = _v(request.min_glue_flap_m), _v(request.min_panel_m)
    if glue is not None and min_glue is not None and glue < min_glue:
        v.append(
            f"glue flap {glue * 1000:.0f} mm is below the converter's {min_glue * 1000:.0f} mm"
        )
    if min_panel is not None:
        for name, value in (("length", length), ("width", width), ("depth", depth)):
            if value is not None and value < min_panel:
                v.append(
                    f"panel {name} {value * 1000:.0f} mm is below the converter's "
                    f"{min_panel * 1000:.0f} mm minimum"
                )
    max_l, max_w = _v(request.max_sheet_length_m), _v(request.max_sheet_width_m)
    if result.blank_length_m is not None and max_l is not None and result.blank_length_m > max_l:
        v.append(
            f"blank length {result.blank_length_m * 1000:.0f} mm exceeds the "
            f"{max_l * 1000:.0f} mm sheet"
        )
    if result.blank_width_m is not None and max_w is not None and result.blank_width_m > max_w:
        v.append(
            f"blank width {result.blank_width_m * 1000:.0f} mm exceeds the "
            f"{max_w * 1000:.0f} mm sheet"
        )
    if v:
        result.verdict = "fail"
    elif not result.unknowns:
        result.verdict = "pass"
    return result
