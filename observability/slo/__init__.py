"""MetaForge SLO/SLI framework -- definitions and error-budget calculation."""

from observability.slo.calculator import (
    calculate_availability,
    calculate_burn_rate,
    calculate_error_budget,
    is_budget_exhausted,
)
from observability.slo.definitions import SLIDefinition, SLODefinition, SLORegistry

__all__ = [
    "SLIDefinition",
    "SLODefinition",
    "SLORegistry",
    "calculate_availability",
    "calculate_burn_rate",
    "calculate_error_budget",
    "is_budget_exhausted",
]
