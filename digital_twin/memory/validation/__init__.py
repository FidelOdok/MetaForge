"""Design revalidation after spec change (MET-455 Phase 3).

When a datasheet/spec revision invalidates the experiences (and the
insights synthesized from them) that a design relied on, the designs
themselves may now violate constraints. This package re-runs the
constraint engine against the affected designs and reports which ones
have started failing so they can be escalated for engineer review.
"""

from digital_twin.memory.validation.design_revalidator import (
    DesignRevalidationResult,
    DesignRevalidator,
    DesignViolation,
)

__all__ = [
    "DesignRevalidationResult",
    "DesignRevalidator",
    "DesignViolation",
]
