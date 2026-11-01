"""Calibration band model from measurement residuals (FORGE-321, target
lifecycle spec App. B REALISE/LEARN, step 11).

Pure math, no twin/MCP dependency -- mirrors ``twin_core/prediction/
evaluator.py``'s own split from its orchestration layer
(``api_gateway/twin/calibration.py``, which records/queries the residual
history this module turns into a band).

FORGE-315's own docstring named this ticket explicitly: the tier-0
evaluator's error band is "a fixed PRIOR ... calibrated bands from real
measurement residuals are Step 11's own scope". This module is that step,
kept deliberately small: a running mean/stddev of `|predicted - measured|`
per `(metric, tier)`, not full conformal prediction -- the ticket's own
acceptance wording ("the residual narrows the FEA band") only needs a real
dispersion estimate to get tighter as more measurements arrive, not a
coverage-guaranteed interval. A future step could swap this for something
more sophisticated without changing the caller-facing shape
(:class:`CalibratedBand`).
"""

from __future__ import annotations

from pydantic import BaseModel

#: Below this many residuals, there isn't enough history to trust a
#: calibrated band over the existing fixed prior -- callers should keep
#: using their own default (e.g. FORGE-315's ``band_fraction=0.2``)
#: unchanged. Chosen as the smallest count a sample standard deviation
#: means anything at all (n-1 degrees of freedom >= 2).
MIN_SAMPLES_FOR_CALIBRATION = 3

#: Default width multiplier on the residual spread -- matches
#: FORGE-315's own ``escalation_k=1.0`` naming convention (a multiplier on
#: a dispersion measure), not a statistical confidence level.
DEFAULT_K = 2.0


class CalibratedBand(BaseModel):
    """A real, data-derived error band for one ``(metric, tier)`` pair."""

    metric: str
    tier: int
    sample_count: int
    mean_abs_residual: float
    stddev_abs_residual: float
    band: float
    k: float


def compute_calibrated_band(
    residuals: list[float], *, metric: str, tier: int, k: float = DEFAULT_K
) -> CalibratedBand | None:
    """Turn a list of raw residuals (``predicted - measured``, one per
    prior measurement matching this ``(metric, tier)``) into a calibrated
    band, or ``None`` when there isn't enough history yet.

    ``band = k * stddev(|residual|)`` -- a real dispersion estimate that
    narrows as consistent measurements accumulate (the literal acceptance
    wording: "the residual narrows the FEA band"). Consistently identical
    residuals correctly drive this toward a very tight band -- that's the
    data saying the prediction is reliably off by a fixed amount, not a
    degenerate case to paper over.
    """
    if len(residuals) < MIN_SAMPLES_FOR_CALIBRATION:
        return None
    abs_residuals = [abs(r) for r in residuals]
    n = len(abs_residuals)
    mean_abs = sum(abs_residuals) / n
    variance = sum((r - mean_abs) ** 2 for r in abs_residuals) / (n - 1)
    stddev_abs = variance**0.5
    band = k * stddev_abs
    return CalibratedBand(
        metric=metric,
        tier=tier,
        sample_count=n,
        mean_abs_residual=mean_abs,
        stddev_abs_residual=stddev_abs,
        band=band,
        k=k,
    )
