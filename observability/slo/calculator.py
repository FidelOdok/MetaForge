"""Error budget calculation utilities for the SLO/SLI framework.

All functions are pure arithmetic helpers that operate on counts and
:class:`SLODefinition` instances -- they do **not** query Prometheus.
"""

from __future__ import annotations

from observability.slo.definitions import SLODefinition


def calculate_error_budget(slo: SLODefinition, window_days: int | None = None) -> float:
    """Return the total error budget in **minutes** for the given *window_days*.

    If *window_days* is ``None`` the SLO's own ``window_days`` is used.

    Formula::

        budget_minutes = window_days * 24 * 60 * (1 - target / 100)
    """
    days = window_days if window_days is not None else slo.window_days
    return days * 24.0 * 60.0 * (1.0 - slo.target / 100.0)


def calculate_burn_rate(
    slo: SLODefinition,
    error_count: int,
    total_count: int,
    window_hours: float,
) -> float:
    """Return the burn-rate multiplier for the observed error ratio.

    A burn rate of 1.0 means budget is being consumed at exactly the
    expected pace.  14.4x is a common fast-burn alerting threshold.

    Formula::

        observed_error_rate = error_count / total_count
        allowed_error_rate  = 1 - target / 100
        burn_rate           = observed_error_rate / allowed_error_rate

    *window_hours* is accepted for API completeness but does not affect
    the ratio calculation directly (callers may use it for windowed
    aggregation upstream).
    """
    if total_count == 0:
        return 0.0
    observed_error_rate = error_count / total_count
    allowed_error_rate = 1.0 - slo.target / 100.0
    if allowed_error_rate == 0.0:
        # target is 100% -- any error is infinite burn
        return float("inf") if error_count > 0 else 0.0
    return observed_error_rate / allowed_error_rate


def is_budget_exhausted(
    slo: SLODefinition,
    error_count: int,
    total_count: int,
) -> bool:
    """Return ``True`` if the observed error ratio has exhausted the budget.

    The budget is exhausted when the error fraction exceeds the allowed
    fraction ``(1 - target / 100)``.
    """
    if total_count == 0:
        return False
    observed_error_rate = error_count / total_count
    allowed_error_rate = 1.0 - slo.target / 100.0
    return observed_error_rate > allowed_error_rate


def calculate_availability(good_count: int, total_count: int) -> float:
    """Return availability as a percentage (0.0 -- 100.0).

    If *total_count* is zero the function returns ``100.0`` (no
    observations means no errors).
    """
    if total_count == 0:
        return 100.0
    return (good_count / total_count) * 100.0
