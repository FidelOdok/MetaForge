"""InterfaceQuantity -- a measurable property of an interface between two
components (FORGE-313, lifecycle step 3, spec section 29 Interface).

Not a new graph node type: ``HierarchyNode``'s own docstring states the
design philosophy explicitly -- a hierarchy node is "a pure organizational
position... never holds design content of its own... reached via edges, not
fields". An Interface follows the same discipline. It already has a real
home: ``twin.commit_system_architecture`` persists ``ArchInterface``
(``domain_agents/shared/skills/define_system_architecture/schema.py``)
entries -- ``{from, to, interface_type, description}`` between two named
components -- as structured (not prose) metadata on a single
``SYSTEM_ARCHITECTURE`` work product. This module adds exactly the piece
that was missing: the quantity itself (a metric, its unit, a limit, who
owns it, a predicted value and any measured ones) as a real, validated
value type embedded in that same ``ArchInterface.quantities`` list -- no
new persistence path, no new node type.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from twin_core.models.quantity import is_valid_unit


class PredictedValue(BaseModel):
    """One tiered prediction for an interface quantity (spec section 29:
    "predicted {value, band, tier, evidence}")."""

    value: float
    band: float | None = None
    tier: str = ""
    # A reference to the Evidence entity (or other record) this prediction
    # came from -- a plain string (uuid or free-form citation), not a typed
    # edge: no MEASURED_BY/PREDICTED_BY writer exists yet to make a real
    # edge meaningful (tracked separately, not guessed at here).
    evidence: str = ""


class MeasuredValue(BaseModel):
    """One real-world measurement of an interface quantity, same shape as
    a predicted value's "did it come true" counterpart."""

    value: float
    source: str = ""
    timestamp: str = ""


class InterfaceQuantity(BaseModel):
    """A measurable property of an interface -- e.g. "upper-arm tip
    deflection" between the upper-arm and shoulder components, limited to
    0.5mm, owned by the mechanical discipline."""

    metric: str = Field(min_length=1)
    unit: str
    limit: float | None = None
    # Comparison operator the limit is checked with -- "<=" (most common,
    # a maximum), ">=" (a minimum), "==" (an exact target). Free string
    # rather than an enum: this mirrors InvariantComparison's own values
    # (twin_core.consistency.models) without importing that module into a
    # base model type (Interface quantities aren't Invariants).
    op: str = "<="
    owner: str = ""
    discipline: str = ""
    predicted: PredictedValue | None = None
    measured: list[MeasuredValue] = Field(default_factory=list)

    @field_validator("unit")
    @classmethod
    def _validate_unit(cls, v: str) -> str:
        if v and not is_valid_unit(v):
            raise ValueError(f"{v!r} is not a recognized unit")
        return v
