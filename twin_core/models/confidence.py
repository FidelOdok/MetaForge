"""Confidence — a model's self-reported certainty in a value it produced
(FORGE-51, spec section 25 Authority Model).

Kept as its own field, entirely separate from ``authority``
(twin_core.models.enums.AuthorityState): a 0.96-confidence model inference
is still just ``authority=proposed`` until a human actually reviews/
approves/baselines it. Nothing in this codebase reads ``confidence`` to set
``authority`` -- conflating them is exactly the failure mode the spec calls
out ("a high-confidence model prediction shall NOT imply approval").
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Confidence(BaseModel):
    """How sure the producer was, and on what basis."""

    value: float = Field(ge=0.0, le=1.0)
    basis: str
