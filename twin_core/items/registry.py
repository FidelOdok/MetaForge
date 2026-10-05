"""Twin type registry: versioned definitions vs append-only records (FORGE-522).

Every twin type falls into one of three kinds, and the write paths consult
this registry to decide what a write means:

- **definition**: a thing that is *revised*. It has an :class:`Item` with a
  stable key and exactly one current head. Re-recording it creates revision
  ``n+1`` of the same item, never a free-standing sibling node.
- **record**: a thing that *happened*. Append-only, never revised, never
  given an item. A second design decision is a second decision, not "the
  decision, revision 2".
- **derived**: a generated view of other nodes (``prd``). Not a source of
  truth; it gets neither an item nor record semantics.

A type absent from this table keeps its pre-FORGE-522 behaviour (one node per
write, no item) until it is classified. Classifying it here is the only step
needed to bring it under item identity, provided its write path calls
:mod:`twin_core.items.service`.

The table is mirrored in ``docs/twin_schema.md`` ("Definitions vs records");
``tests/unit/test_item_registry.py`` keeps the two in step.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TwinTypeKind(StrEnum):
    """How a twin type behaves on write."""

    DEFINITION = "definition"
    RECORD = "record"
    DERIVED = "derived"


@dataclass(frozen=True)
class TwinTypeSpec:
    """One registry row."""

    #: Registry name. For a definition this is also ``Item.item_type``.
    name: str
    kind: TwinTypeKind
    #: Where its nodes live: ``work_product:<WorkProductType>``,
    #: ``engineering_entity:<entity_type>``, ``bom_item``, or a non-twin store.
    storage: str
    #: Item key prefix (definitions only), e.g. ``CAD`` -> ``CAD-BRACKET``.
    key_prefix: str | None = None
    #: Definitions whose items may continue one another. A part that gains
    #: parts becomes an assembly without losing its history.
    family: str | None = None
    description: str = ""


_ROWS: tuple[TwinTypeSpec, ...] = (
    # -- definitions -------------------------------------------------------
    TwinTypeSpec(
        "cad_model",
        TwinTypeKind.DEFINITION,
        "work_product:cad_model",
        key_prefix="CAD",
        family="geometry",
        description="A part's geometry (twin.commit_geometry without parts).",
    ),
    TwinTypeSpec(
        "assembly",
        TwinTypeKind.DEFINITION,
        "work_product:cad_model",
        key_prefix="ASM",
        family="geometry",
        description="An assembly's geometry (twin.commit_geometry with parts).",
    ),
    TwinTypeSpec(
        "constraint_set",
        TwinTypeKind.DEFINITION,
        "work_product:constraint_set",
        key_prefix="CS",
        description="A requirement/constraint set (twin.record_constraint_set).",
    ),
    TwinTypeSpec(
        "intent",
        TwinTypeKind.DEFINITION,
        "engineering_entity:intent",
        key_prefix="INT",
        description="The engineering intent statement.",
    ),
    TwinTypeSpec(
        "stakeholder_need",
        TwinTypeKind.DEFINITION,
        "engineering_entity:stakeholder_need",
        key_prefix="NEED",
        description="A stakeholder need.",
    ),
    TwinTypeSpec(
        "objective",
        TwinTypeKind.DEFINITION,
        "engineering_entity:objective",
        key_prefix="OBJ",
        description="An optimisation objective (metric, direction, target).",
    ),
    TwinTypeSpec(
        "bom",
        TwinTypeKind.DEFINITION,
        "work_product:bom",
        key_prefix="BOM",
        description="A bill of materials (electronics BOM recorder).",
    ),
    TwinTypeSpec(
        "component_selection",
        TwinTypeKind.DEFINITION,
        "bom_item",
        key_prefix="CMP",
        description=(
            "The part chosen for one role (twin.record_component_selection); "
            "choosing a different part for the same role is a new revision."
        ),
    ),
    # -- records -----------------------------------------------------------
    TwinTypeSpec(
        "design_decision",
        TwinTypeKind.RECORD,
        "work_product:design_decision",
        description="A recorded design decision (twin.record_decision).",
    ),
    TwinTypeSpec(
        "simulation_result",
        TwinTypeKind.RECORD,
        "work_product:simulation_result",
        description="One simulation run's result.",
    ),
    TwinTypeSpec(
        "evidence",
        TwinTypeKind.RECORD,
        "engineering_entity:evidence",
        description="Evidence pinned to the revisions it was produced against.",
    ),
    TwinTypeSpec(
        "approval",
        TwinTypeKind.RECORD,
        "approvals service",
        description="A gate or tool approval decision.",
    ),
    TwinTypeSpec(
        "session",
        TwinTypeKind.RECORD,
        "agent_sessions (Postgres)",
        description="An agent session and its events.",
    ),
    TwinTypeSpec(
        "run",
        TwinTypeKind.RECORD,
        "runs / design-flow workflow",
        description="One design-flow or chat run.",
    ),
    # -- derived -----------------------------------------------------------
    TwinTypeSpec(
        "prd",
        TwinTypeKind.DERIVED,
        "work_product:prd",
        description="A generated view of intent, needs and requirements.",
    ),
)

TWIN_TYPES: dict[str, TwinTypeSpec] = {row.name: row for row in _ROWS}


def classify(type_name: str) -> TwinTypeSpec | None:
    """The registry row for ``type_name``, or ``None`` when unclassified."""
    return TWIN_TYPES.get(type_name)


def is_definition(type_name: str) -> bool:
    """True when writes of ``type_name`` create item revisions."""
    spec = TWIN_TYPES.get(type_name)
    return spec is not None and spec.kind is TwinTypeKind.DEFINITION


def definition_types() -> list[str]:
    """All definition type names, in registry order."""
    return [r.name for r in _ROWS if r.kind is TwinTypeKind.DEFINITION]


def family_of(type_name: str) -> set[str]:
    """``type_name`` plus every definition sharing its family."""
    spec = TWIN_TYPES.get(type_name)
    if spec is None or spec.family is None:
        return {type_name}
    return {r.name for r in _ROWS if r.family == spec.family}
