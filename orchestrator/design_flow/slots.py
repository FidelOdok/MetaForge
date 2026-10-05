"""Deliverable slots: the items a phase writes (FORGE-524).

FORGE-523 gave every definition write an item, but the item was still found
by name. The model names the part, so the model decided identity: four runs
of the shelf project left sixteen cad_models for four parts, because each run
called them something slightly different.

A slot fixes the identity before the run starts. Each deliverable a phase
declares gets one, carrying the item key its writes land on:

* **Declared slots** come from the flow: a template's ``slots`` list, or a
  tailoring's ``declare_items`` operation ("two brackets" is two slots).
* **Default slots** cover a deliverable no declared slot covers, but only
  for types a phase normally writes one of (:data:`SINGLETON_TYPES`): one per
  type, named after the phase (the requirements phase's constraint set is
  ``CS-REQUIREMENTS``). A part or a stakeholder need gets no default slot:
  a phase usually writes several, and one shared slot would turn four parts
  into four revisions of one item. Those are declared by name or left to
  FORGE-523's name resolution.

Keys are FORGE-523 item keys (``derive_key(type, name)``, e.g.
``CAD-LEFT-BRACKET``). The project part of an item's identity is the item's
project scope, not a prefix in the string: an item key is already unique per
project, a key cannot contain ``/`` (it sits in a URL path), and a project's
name can change between versions while its id cannot. The same deliverable in
the same project therefore always gets the same key, in every version.

:func:`bind_slots` fills in the keys of the *declared* slots when a version
is saved, so they are frozen with it. Default slots are never stored: they are
a pure function of the frozen phase, so :func:`effective_slots` derives them
at run time, and a flow that declares nothing hashes exactly as it did before
slots existed (a version approved earlier still verifies, and a new version
of the same content gets the same hash). :func:`match_slot` decides which
slot a write belongs to.

Pure functions, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

import structlog

from orchestrator.design_flow.spec import DeliverableSlot, FlowDefinition, Phase

logger = structlog.get_logger(__name__)

__all__ = [
    "SINGLETON_TYPES",
    "SlotLike",
    "SlotMatch",
    "bind_slots",
    "declared_slots",
    "default_slots",
    "definition_deliverables",
    "effective_slots",
    "is_derived",
    "match_slot",
    "slot_key",
    "slots_brief",
]

#: Words that say nothing about which part a name means. Dropped before names
#: are compared, so "Left Bracket v2 (final)" still reads as "left bracket".
_NOISE = frozenset(
    {"a", "an", "the", "of", "part", "model", "rev", "final", "new", "updated", "copy", "draft"}
)
_VERSION_TOKEN = re.compile(r"^(v|r|rev)?\d+$")

#: Definition types a phase writes one of, so they get a default slot.
SINGLETON_TYPES = frozenset({"intent", "constraint_set", "bom", "assembly"})


class SlotLike(Protocol):
    item_type: str
    name: str
    item_key: str


def _is_definition(item_type: str) -> bool:
    from twin_core.items.registry import is_definition

    return is_definition(item_type)


def slot_key(item_type: str, name: str) -> str:
    """The item key a slot of ``item_type`` named ``name`` binds to."""
    from twin_core.items.service import derive_key

    return derive_key(item_type, name)


def definition_deliverables(phase: Phase) -> list[str]:
    """The phase's deliverable types that are versioned definitions, in order."""
    types = dict.fromkeys([*phase.required_deliverables, *phase.expected_artifacts])
    return [t for t in types if _is_definition(t)]


def default_slots(phase: Phase) -> tuple[DeliverableSlot, ...]:
    """One slot per singleton definition type the declared slots do not cover."""
    covered = {s.item_type for s in phase.slots}
    return tuple(
        DeliverableSlot(item_type=t, name=phase.id, item_key=slot_key(t, phase.id))
        for t in definition_deliverables(phase)
        if t not in covered and t in SINGLETON_TYPES
    )


def declared_slots(phase: Phase) -> tuple[DeliverableSlot, ...]:
    """The phase's declared slots, each with its key, duplicates dropped."""
    declared: list[DeliverableSlot] = []
    seen: set[str] = set()
    for slot in phase.slots:
        key = slot.item_key or slot_key(slot.item_type, slot.name)
        if key in seen:
            continue
        seen.add(key)
        declared.append(slot if slot.item_key == key else replace(slot, item_key=key))
    return tuple(declared)


def effective_slots(phase: Phase) -> tuple[DeliverableSlot, ...]:
    """Declared slots with their keys, then the derived default ones. Deterministic."""
    declared = declared_slots(phase)
    seen = {s.item_key for s in declared}
    return (*declared, *(s for s in default_slots(phase) if s.item_key not in seen))


def is_derived(phase: Phase, slot: DeliverableSlot) -> bool:
    """True for a default slot: derived at run time, not stored in the version."""
    return all((s.item_type, s.name) != (slot.item_type, slot.name) for s in phase.slots)


def bind_slots(definition: FlowDefinition) -> FlowDefinition:
    """``definition`` with every *declared* slot keyed.

    Called when a version is saved, so declared slots and their keys are part
    of its frozen, hashed content. Default slots are not added (see the module
    docstring). Idempotent: binding a bound flow changes nothing.
    """
    phases = tuple(replace(p, slots=declared_slots(p)) for p in definition.phases)
    bound = replace(definition, phases=phases)
    logger.info(
        "flow_slots_bound",
        flow=definition.id,
        slots=sum(len(p.slots) for p in phases),
    )
    return bound


def _tokens(name: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return frozenset(w for w in words if w not in _NOISE and not _VERSION_TOKEN.match(w))


@dataclass(frozen=True)
class SlotMatch:
    """Which slot a write belongs to, and how that was decided.

    ``how`` is ``name`` (same name or same key), ``fuzzy`` (the names share
    the most meaningful words), ``only`` (the one slot of that type),
    ``ambiguous`` (several slots, the name picks none) or ``none`` (no slot of
    that type).
    """

    slot: Any | None
    how: str


def match_slot(slots: Sequence[SlotLike], item_type: str, name: str) -> SlotMatch:
    """The slot a write of ``item_type`` named ``name`` lands on.

    Exact type beats same family (a cad_model write prefers a cad_model slot
    to an assembly one). Within the candidates: an exact name or key match,
    then the unique best word overlap, then the only candidate. Several
    candidates and no name match is ``ambiguous``: guessing between two
    brackets would put one bracket's geometry on the other's item.
    """
    from twin_core.items.registry import family_of

    family = family_of(item_type)
    exact = [s for s in slots if s.item_type == item_type]
    candidates = exact or [s for s in slots if s.item_type in family]
    if not candidates:
        return SlotMatch(None, "none")
    key = slot_key(item_type, name)
    for slot in candidates:
        if slot.name.strip().lower() == (name or "").strip().lower() or slot.item_key == key:
            return SlotMatch(slot, "name")
    words = _tokens(name)
    if words:
        scored = []
        for slot in candidates:
            theirs = _tokens(slot.name)
            if theirs:
                scored.append((len(words & theirs) / len(words | theirs), slot))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            return SlotMatch(scored[0][1], "fuzzy")
    if len(candidates) == 1:
        return SlotMatch(candidates[0], "only")
    return SlotMatch(None, "ambiguous")


def slots_brief(slots: Sequence[SlotLike]) -> str:
    """The phase-brief block telling the agent its item keys. Empty for none."""
    if not slots:
        return ""
    lines = "\n".join(f"  - {s.item_key}: {s.item_type} '{s.name}'" for s in slots)
    return (
        "Items this phase writes (each write of one of these lands on its item as the "
        "next revision, whatever you name it; you do not need to pass item_key):\n"
        f"{lines}\n"
        "A write that matches none of these is recorded as a NEW, undeclared item and "
        "listed for the gate reviewer. Revise the items above rather than creating new "
        "ones; only add a new part when the design really needs one, and say why in "
        "your summary.\n"
    )
