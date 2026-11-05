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

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

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


# ---------------------------------------------------------------------------
# Interface edges: typed, revision-aware couplings between items (FORGE-793)
# ---------------------------------------------------------------------------
#
# An ``InterfaceQuantity`` above is a number measured across an interface.
# An ``InterfaceEdge`` is the interface itself, as a dependency the impact
# walker can follow: "this enclosure's standoffs carry that board's mounting
# holes", "this firmware module reads those pins". It is distinct from the
# workflow-step DAG (``orchestrator/dependency_engine.py``), which orders
# agent steps; these edges couple product items.
#
# The two ends are item keys (``CAD-ENCLOSURE``, ``PCB-MAIN``) pinned to the
# revision the coupling was stated against, or a result record
# (``record:<uuid>``) that an item feeds. Where the twin already holds the
# dependency, the edge is derived on read rather than stored twice:
# ``simulation_input_of`` from a simulation result's own revision pins,
# ``feature_of`` from the sketch layer's ``DEPENDS_ON`` edges.


class InterfaceType(StrEnum):
    """What kind of coupling an edge states."""

    #: A mechanical part carries a board's mounting holes (or the reverse).
    MOUNTS_TO = "mounts_to"
    #: An enclosure must clear a board's outline or component envelope.
    CLEARANCE_TO = "clearance_to"
    #: A connector meets a cable or an enclosure cut-out.
    CONNECTS_VIA = "connects_via"
    #: An MCU pin or net is used by a firmware module, driver or config.
    PIN_MAPS_TO = "pin_maps_to"
    #: Geometry, a material or a load is an input of a simulation case.
    SIMULATION_INPUT_OF = "simulation_input_of"
    #: A sketch or sketch plane feeds what is built from it.
    FEATURE_OF = "feature_of"
    #: A manufacturing context (its process and material) shaped what was
    #: generated under it. Derived from the ``manufacturing_context_ref`` an
    #: output records; a process change walks it (3D-RC-013).
    MANUFACTURED_BY = "manufactured_by"


#: Couplings where a change on either end affects the other: moving the
#: board's holes breaks the enclosure as surely as moving the standoffs.
SYMMETRIC_TYPES: frozenset[InterfaceType] = frozenset(
    {InterfaceType.MOUNTS_TO, InterfaceType.CLEARANCE_TO, InterfaceType.CONNECTS_VIA}
)


class DeclaredBy(StrEnum):
    """Where an edge came from."""

    #: A person stated it.
    HUMAN = "human"
    #: A tool recorded it as a fact (a simulation's own input pins).
    TOOL = "tool"
    #: MetaForge inferred it from data; a proposal until someone confirms it.
    INFERRED = "inferred"


class InterfaceStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    #: Removed by a person. Kept, never deleted, so history still reads.
    REMOVED = "removed"


#: The domain each item type belongs to, for an edge's domain pair.
ITEM_TYPE_DOMAIN: dict[str, str] = {
    "cad_model": "mechanical",
    "assembly": "mechanical",
    "purchased_model": "mechanical",
    "schematic": "electronics",
    "pcb_layout": "electronics",
    "gerber": "electronics",
    "pick_and_place": "electronics",
    "bom": "electronics",
    "component_selection": "electronics",
    "pinmap": "electronics",
    "firmware_source": "firmware",
    "simulation_result": "simulation",
    "gcode": "manufacturing",
    "sketch_strokes": "sketch",
    "sketch_plane": "sketch",
    "sketch_interpretation": "sketch",
    "parametric_sketch": "sketch",
    "manufacturing_context": "manufacturing",
}

#: Prefix of an edge end that is a result record rather than an item.
RECORD_REF_PREFIX = "record:"


def domain_for_item_type(item_type: str | None) -> str:
    """The engineering domain an item type belongs to, ``general`` when none fits."""
    return ITEM_TYPE_DOMAIN.get(str(item_type or ""), "general")


def is_record_ref(ref: str) -> bool:
    """True for an edge end that names a result record, not an item."""
    return ref.startswith(RECORD_REF_PREFIX)


class InterfaceEnd(BaseModel):
    """One end of an interface edge."""

    #: An item key (``PCB-MAIN``), or ``record:<uuid>`` for a result record.
    ref: str = Field(min_length=1, max_length=200)
    #: The revision the coupling was stated against; ``None`` for a record.
    revision: int | None = None
    domain: str = "general"
    item_type: str = ""
    #: The node the pinned revision (or the record) is.
    node_id: str | None = None
    label: str = ""

    @property
    def pinned_ref(self) -> str:
        return f"{self.ref}@{self.revision}" if self.revision is not None else self.ref


class InterfaceEdge(BaseModel):
    """A typed coupling between two items, pinned to their revisions.

    ``coupled_parameters`` names what crosses the interface when it is known
    (``mounting_holes``, pin ``PA5``): a change whose changed parameters are
    known and miss every coupled parameter does not travel along this edge.
    An inferred edge is a proposal until a person confirms it. The impact
    walker reports what a proposal would affect, but only a confirmed edge
    marks results stale.
    """

    id: str = Field(min_length=1)
    project_id: str = ""
    type: InterfaceType
    source: InterfaceEnd
    target: InterfaceEnd
    declared_by: DeclaredBy
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: InterfaceStatus
    coupled_parameters: list[str] = Field(default_factory=list)
    rationale: str = ""
    #: What an inferred or tool edge was read from (the names it matched, the
    #: counts it compared). Never free model text.
    evidence: dict[str, Any] = Field(default_factory=dict)
    #: Computed on read from links the twin already holds; not stored.
    derived: bool = False
    created_at: str | None = None
    created_by: str = ""
    decided_by: str | None = None
    decided_at: str | None = None
    removed_reason: str | None = None

    @property
    def domain_pair(self) -> tuple[str, str]:
        return (self.source.domain, self.target.domain)

    @property
    def symmetric(self) -> bool:
        return self.type in SYMMETRIC_TYPES

    @property
    def certain(self) -> bool:
        """True when the edge may mark results stale (not a mere proposal)."""
        return self.status is InterfaceStatus.CONFIRMED

    @model_validator(mode="after")
    def _honest_provenance(self) -> InterfaceEdge:
        if self.source.ref == self.target.ref:
            raise ValueError("an interface edge needs two different ends")
        if (
            self.declared_by is DeclaredBy.INFERRED
            and self.status is InterfaceStatus.CONFIRMED
            and not self.decided_by
        ):
            raise ValueError("an inferred edge is confirmed only by someone; name who")
        return self

    def to_view(self) -> dict[str, Any]:
        """The JSON shape the API returns, with the derived fields spelled out."""
        out = self.model_dump(mode="json")
        out["domain_pair"] = list(self.domain_pair)
        out["symmetric"] = self.symmetric
        return out
