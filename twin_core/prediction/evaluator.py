"""Tiered metric evaluator -- pure tier-0 hand-calc + band/escalation math
(FORGE-315, target lifecycle spec §30: "prefer the minimum sufficient
fidelity; escalate only when evidence quality demands it").

Scope cut for this ticket, following the same discipline as every prior
step of this epic (implement exactly what the acceptance criterion needs,
defer the rest with a clear reason rather than guess):

- Only the ticket's own acceptance-criterion metric -- cantilever tip
  deflection -- gets a real tier-0 hand-calc. A general per-metric tier
  *registry* (the ticket's own ``twin_core/prediction/evaluator.py``
  scope line) is deliberately NOT built here; that's real, separable
  design work (what other metrics need what other closed-form estimates)
  with no acceptance-criterion pressure to build it now.
- Tier 1 (torque hand-calc) is skipped entirely -- the acceptance
  criterion only exercises tier 0 -> tier 2 (deflection).
- The error band is a fixed PRIOR (a configurable fraction of the limit),
  exactly as the ticket's own guardrail text says ("prior now"). Real
  calibrated bands from measurement residuals are Step 11's own scope
  (``digital_twin/calibration/``), not this ticket's.
- "Cheap tier runs on each design change" (an automatic hook off every
  geometry commit / ECT event) is NOT wired here -- this module and its
  orchestration layer (``api_gateway/twin/metric_evaluator.py``) expose an
  explicitly-invoked evaluation, the same shape every other twin.* tool in
  this codebase uses. Wiring it to fire automatically on every design
  change is a separate, larger piece (a real trigger/subscription
  mechanism this codebase doesn't have yet for ANY consistency check, not
  just this one) -- noted, not silently dropped.

The orchestration (fetching a WorkProduct's real geometry, recording
Evidence, escalating to a real ``calculix.run_fea`` MCP call) lives in
``api_gateway/twin/metric_evaluator.py`` -- this module has no twin/MCP
dependency at all, mirroring the twin_core/api_gateway split every other
piece of this epic (e.g. ``twin_core/consistency/staleness.py`` vs.
``api_gateway/twin/evidence_recorder.py``) already uses.
"""

from __future__ import annotations

from pydantic import BaseModel


class Tier0DeflectionResult(BaseModel):
    """A tier-0 cantilever-beam tip-deflection estimate, plus the
    escalation decision against an optional limit."""

    metric: str = "tip_deflection"
    tier: int = 0
    value_mm: float
    band_mm: float
    limit_mm: float | None = None
    margin_mm: float | None = None
    escalate: bool = False


def cantilever_tip_deflection_mm(
    *,
    length_mm: float,
    width_mm: float,
    height_mm: float,
    load_n: float,
    youngs_modulus_mpa: float,
) -> float:
    """Closed-form cantilever beam tip deflection: delta = F L^3 / (3 E I),
    with I = w h^3 / 12 (solid rectangular cross-section, load applied
    perpendicular to the h-axis at the free tip, fixed at the other end).

    ``length_mm`` is the beam's own axis -- the caller decides which of a
    part's bounding-box extents that is (the longest one, for the arm-link
    cantilever case this ticket's acceptance criterion targets); this
    function has no opinion about geometry beyond the three numbers it's
    given. Units: mm / N / MPa (N/mm^2) throughout -- MPa, not Pa, matching
    ``calculix.run_fea``'s own material unit convention (its schema's own
    warning: mixing Pa into a mm-based mesh silently understates stiffness
    by 1e6), so a tier-0 estimate and a tier-2 FEA run for the same part
    are never silently inconsistent in units.
    """
    if length_mm <= 0 or width_mm <= 0 or height_mm <= 0:
        raise ValueError("cantilever_tip_deflection_mm: length/width/height must be positive")
    if load_n < 0:
        raise ValueError("cantilever_tip_deflection_mm: load_n must be non-negative")
    if youngs_modulus_mpa <= 0:
        raise ValueError("cantilever_tip_deflection_mm: youngs_modulus_mpa must be positive")
    moment_of_inertia_mm4 = width_mm * height_mm**3 / 12.0
    return (load_n * length_mm**3) / (3.0 * youngs_modulus_mpa * moment_of_inertia_mm4)


def evaluate_tip_deflection_tier0(
    *,
    length_mm: float,
    width_mm: float,
    height_mm: float,
    load_n: float,
    youngs_modulus_mpa: float,
    limit_mm: float | None = None,
    band_fraction: float = 0.2,
    escalation_k: float = 1.0,
) -> Tier0DeflectionResult:
    """Tier-0 estimate + the escalation decision.

    Escalate when ``|margin| < k * band`` (spec §30) -- ``margin`` is
    ``limit - value`` (positive = within budget, negative = already over
    it; either way, close to zero means the tier-0 estimate alone isn't
    trustworthy enough to call it, so escalate to a higher-fidelity tier).
    With no ``limit_mm`` supplied there's nothing to escalate against --
    returns the raw estimate with ``escalate=False``.
    """
    value_mm = cantilever_tip_deflection_mm(
        length_mm=length_mm,
        width_mm=width_mm,
        height_mm=height_mm,
        load_n=load_n,
        youngs_modulus_mpa=youngs_modulus_mpa,
    )
    if limit_mm is None:
        return Tier0DeflectionResult(value_mm=value_mm, band_mm=0.0, escalate=False)
    band_mm = band_fraction * limit_mm
    margin_mm = limit_mm - value_mm
    escalate = abs(margin_mm) < escalation_k * band_mm
    return Tier0DeflectionResult(
        value_mm=value_mm,
        band_mm=band_mm,
        limit_mm=limit_mm,
        margin_mm=margin_mm,
        escalate=escalate,
    )
