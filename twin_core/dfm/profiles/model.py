"""The shape of a DFM rule profile (DFM-009).

A profile is one process's rules at one version. Every rule says what it
measures, the limit, and **where the number came from** (``source``), and of
which kind that source is:

``machine_limit``
    A capability of the selected machine, read from the machine library at
    evaluation time (its own sourced value). A profile never carries the
    number itself, only which capability to read (``machine_capability``).
``supplier_limit``
    A capability a supplier confirmed through a verified integration. No
    profile seeds one (DFM-014): they are only ever supplied at evaluation.
``heuristic``
    A rule of thumb MetaForge applies to estimated geometry.
``general_guidance``
    Widely published design guidance for the process, not a limit of any
    machine or supplier.

A heuristic or general guidance rule may name a machine capability it
defers to (``machine_override``): when the selected machine records that
capability, the machine's value and source replace the guidance.

Pure: Pydantic and the standard library only.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RuleKind(StrEnum):
    MACHINE_LIMIT = "machine_limit"
    SUPPLIER_LIMIT = "supplier_limit"
    HEURISTIC = "heuristic"
    GENERAL_GUIDANCE = "general_guidance"


#: Kinds that are verified limits rather than guidance. A violation measured
#: on exact or confirmed geometry against one of these is blocking.
LIMIT_KINDS: frozenset[RuleKind] = frozenset({RuleKind.MACHINE_LIMIT, RuleKind.SUPPLIER_LIMIT})

#: How a rule is checked.
#:
#: * ``min`` / ``max``: every measure of ``quantity`` against ``limit`` (or
#:   ``factor`` times the measure of ``relative_to``).
#: * ``envelope``: the part's three extents against the machine's build volume
#:   (sorted, so a part that fits in some orientation fits).
#: * ``overhang``: mesh or B-Rep faces tilted past ``limit`` degrees from the
#:   vertical build axis (the FDM overhang check).
#: * ``support_access``: flagged overhang faces inside an enclosed cavity.
#: * ``requires``: when ``quantity`` is true, ``relative_to`` must be measured
#:   and at least ``limit`` (a hollow resin part needs a drain hole).
CheckKind = Literal["min", "max", "envelope", "overhang", "support_access", "requires"]


class DfmRule(BaseModel):
    """One process rule with its provenance."""

    model_config = {"extra": "forbid"}

    #: ``<profile>.<rule>``, stable across versions.
    id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")
    title: str = Field(min_length=3, max_length=120)
    check: CheckKind
    #: The measured quantity (``wall_thickness``, ``hole_diameter`` ...).
    quantity: str = Field(min_length=2, max_length=60)
    #: Other quantity names that can stand in for ``quantity`` on a ``min``
    #: check (``thinnest_section`` of an estimated block for a wall).
    also_reads: list[str] = Field(default_factory=list)
    unit: Literal["mm", "deg", "ratio", "mm3"] = "mm"
    limit: float | None = None
    #: A relative limit: ``factor`` times the measure of ``relative_to``.
    relative_to: str | None = None
    factor: float | None = None
    kind: RuleKind
    #: Where the number came from, in words a reviewer can follow.
    source: str = Field(min_length=10, max_length=400)
    #: ``machine_limit``: the machine library capability to read.
    machine_capability: str | None = None
    #: Guidance that defers to the machine when it records this capability.
    machine_override: str | None = None
    #: The machine value is multiplied by this (a cutter's diameter halved
    #: is the smallest internal corner radius it cuts).
    machine_override_scale: float = Field(1.0, gt=0)
    #: Empty for every material; else only these families (``metal``,
    #: ``plastic``, ``resin`` ...).
    material_families: list[str] = Field(default_factory=list)
    #: The plain-language problem, with ``{measured}``, ``{limit}``, ``{unit}``.
    message: str = Field(min_length=10, max_length=300)
    #: The suggested fix, in plain words.
    fix: str = Field(min_length=10, max_length=300)
    #: What applying the fix would change: geometry quantities, or context
    #: fields (``selected_process``, ``selected_material``).
    fix_changes: list[str] = Field(default_factory=list)
    #: A short cue for a manufacturability-oriented concept variant (DFM-003).
    variant_cue: str = Field("", max_length=200)

    @model_validator(mode="after")
    def _well_formed(self) -> DfmRule:
        if self.kind is RuleKind.SUPPLIER_LIMIT:
            raise ValueError(
                f"{self.id}: a profile never seeds a supplier limit (DFM-014); "
                "supplier capabilities come only from a verified integration"
            )
        if self.kind is RuleKind.MACHINE_LIMIT:
            if not self.machine_capability:
                raise ValueError(f"{self.id}: a machine limit names the capability it reads")
            if self.limit is not None:
                raise ValueError(
                    f"{self.id}: a machine limit's number comes from the machine library, "
                    "not the profile"
                )
        elif self.check in ("min", "max", "overhang", "requires"):
            if self.limit is None and not (self.relative_to and self.factor):
                raise ValueError(f"{self.id}: give a limit, or relative_to and factor")
        if self.check == "requires" and not self.relative_to:
            raise ValueError(f"{self.id}: a requires check names what is required (relative_to)")
        return self


class DfmProfile(BaseModel):
    """One process's rules at one version."""

    model_config = {"extra": "forbid"}

    id: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9_]+$")
    #: Semantic version; any change to a rule or a number is a new version.
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    process: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=2, max_length=120)
    #: Words that name this process in a manufacturing context.
    aliases: list[str] = Field(min_length=1)
    rules: list[DfmRule] = Field(min_length=1)
    notes: str = Field("", max_length=600)

    @model_validator(mode="after")
    def _rules_belong(self) -> DfmProfile:
        ids = [r.id for r in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.id}: rule ids repeat")
        stray = [r.id for r in self.rules if not r.id.startswith(self.id + ".")]
        if stray:
            raise ValueError(f"{self.id}: rules {', '.join(stray)} belong to another profile")
        return self

    def rule(self, rule_id: str) -> DfmRule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)

    @property
    def ref(self) -> str:
        """``fdm@1.0.0``."""
        return f"{self.id}@{self.version}"


__all__ = ["LIMIT_KINDS", "CheckKind", "DfmProfile", "DfmRule", "RuleKind"]
