"""Interface contracts: typed, versioned agreements between two or more domains.

An :class:`~twin_core.models.interface.InterfaceEdge` says *that* two items are
coupled ("this enclosure clears that board"). A contract says *what* they agree
on: the coupled parameters, each with its value, unit, tolerance, the domain
that owns it and how far it can be trusted (stated, estimated, assumed or
unknown). It is what both sides build against before either side exists:

* **CAD to EDA**: PCB outline, mounting hole positions and diameter, maximum
  component height, LED position and orientation, the USB-C connector position
  and its cut-out, keep-outs and the cable routing path.
* **EDA to firmware**: the controller part, the pin map (the LED driver's PWM
  pin, the control's input pins), the driver's capabilities and limits, and
  the interface version.
* **CAD and EDA to simulation**: the geometry revision, materials, the LED's
  electrical power and heat estimate, boundary conditions and operating
  assumptions.
* **CAD and EDA to manufacturing**: the artifacts the package must hold (print
  files per printed part, Gerbers, drill, pick-and-place, BOM, assembly
  instructions, the firmware binary) and the rule that makes them compatible.

**No new store.** A contract is a definition item (``IFC-<kind>``) in the
twin's item service, so every parameter change is the next revision and the
revision number is the interface version. Its links name the items that
implement each side (``CAD-BASE``, ``PCB-MAIN``) once they exist; until then a
link names only what will produce it. A check of a contract is an
``interface_contract_check`` record pinned to the contract revision and to
every item revision it read, so the existing record pins mark it stale when
any of them moves, and the impact walker lists it like any other result.

**Agreement is a person's.** A contract stays ``proposed`` until every owning
domain has agreed to its current interface version, and each agreement is a
recorded design decision pinned to the contract revision it read
(``contract_agreement`` on the decision). The interface version moves only
when the coupled parameters or expected artifacts change, so linking an item
(a new revision) keeps the agreements, and changing a value needs new ones.

Pure data: no I/O. The gateway stores and reads it
(``api_gateway.twin.interface_contracts``); the checks are
:mod:`twin_core.contracts.checker`.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from twin_core.models.quantity import is_valid_unit

#: The item type (and work product type) a contract is stored as.
CONTRACT_ITEM_TYPE = "interface_contract"
#: The record type one evaluation of a contract is stored as.
CONTRACT_CHECK_TYPE = "interface_contract_check"
#: Where a contract revision keeps the contract.
CONTRACT_META_KEY = "interface_contract"
#: Where a check record keeps its evaluation.
CHECK_META_KEY = "contract_check"
#: Where a design decision says which contract revision it agrees to.
AGREEMENT_META_KEY = "contract_agreement"
#: Where a contract revision keeps its interface version.
INTERFACE_VERSION_KEY = "interface_version"
#: The producer a check record names (the impact walker's ``check`` kind).
CHECK_PRODUCER = "metaforge.contract_check"
KEY_PREFIX = "IFC"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ContractKind(StrEnum):
    """Which domains a contract couples."""

    CAD_EDA = "cad_eda"
    EDA_FIRMWARE = "eda_firmware"
    SIMULATION = "simulation"
    MANUFACTURING = "manufacturing"


#: The item key each kind is stored under.
CONTRACT_KEYS: dict[ContractKind, str] = {
    ContractKind.CAD_EDA: "IFC-CAD-EDA",
    ContractKind.EDA_FIRMWARE: "IFC-EDA-FIRMWARE",
    ContractKind.SIMULATION: "IFC-SIMULATION",
    ContractKind.MANUFACTURING: "IFC-MANUFACTURING",
}


class ContractStatus(StrEnum):
    """Where a contract stands.

    ``proposed`` and ``agreed`` are its lifecycle (agreement is a person's
    decision per owning domain). ``violated`` and ``stale`` come from its
    latest check and override the lifecycle: an agreed contract whose check
    found a mismatch is violated, and one whose check read revisions that have
    since moved is stale until it is checked again.
    """

    PROPOSED = "proposed"
    AGREED = "agreed"
    VIOLATED = "violated"
    STALE = "stale"


class CheckOutcome(StrEnum):
    """What one check of a contract found."""

    #: The data on both sides exists and agrees within tolerance.
    SATISFIED = "satisfied"
    #: The data exists and disagrees; ``values`` says by how much.
    VIOLATED = "violated"
    #: An input is missing; ``missing`` names it and what would produce it.
    NOT_EVALUATED = "not_evaluated"
    #: An input was made from a revision that has since moved.
    STALE = "stale"


class ValueBasis(StrEnum):
    """How far a coupled parameter's value can be trusted."""

    #: A person or a source document stated it.
    STATED = "stated"
    #: An approximate size or figure, marked as such until a design replaces it.
    ESTIMATED = "estimated"
    #: Proposed by MetaForge as a working assumption (ambient, convection).
    ASSUMED = "assumed"
    #: Not known yet. Never shown as a value.
    UNKNOWN = "unknown"


class CoupledParameter(BaseModel):
    """One value both sides of a contract build against."""

    name: str
    label: str = Field(min_length=1, max_length=200)
    value: Any = None
    unit: str = ""
    #: Plus or minus, in ``unit``. ``None`` when the value is exact or not numeric.
    tolerance: float | None = Field(default=None, ge=0)
    #: The domain whose design decides this value.
    owner: str = Field(min_length=1, max_length=64)
    basis: ValueBasis = ValueBasis.UNKNOWN
    note: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"parameter name {v!r} must be snake_case")
        return v

    @field_validator("unit")
    @classmethod
    def _unit(cls, v: str) -> str:
        if v and not is_valid_unit(v):
            raise ValueError(f"{v!r} is not a recognized unit")
        return v

    @model_validator(mode="after")
    def _value_matches_basis(self) -> CoupledParameter:
        if self.basis is ValueBasis.UNKNOWN:
            self.value = None
        elif self.value is None or self.value == "" or self.value == [] or self.value == {}:
            raise ValueError(
                f"parameter {self.name} is {self.basis.value} but has no value; "
                "use basis unknown for a value nobody knows yet"
            )
        return self

    @property
    def known(self) -> bool:
        return self.basis is not ValueBasis.UNKNOWN


class ContractLink(BaseModel):
    """One side's item, by the role it plays in the contract."""

    #: ``enclosure``, ``pcb``, ``pinmap``, ``firmware``, ``head`` ...
    role: str
    label: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=64)
    #: The item types that may fill this role.
    item_types: list[str] = Field(default_factory=list)
    #: The item that fills it, once it exists. ``None`` until then.
    item_key: str | None = None
    #: The step that would produce the item, for an honest "missing" line.
    produced_by: str = Field(default="", max_length=300)

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"link role {v!r} must be snake_case")
        return v


class ExpectedArtifact(BaseModel):
    """One artifact the manufacturing package must hold, and its compatibility rule."""

    kind: str
    label: str = Field(min_length=1, max_length=200)
    #: Component ids for an artifact made once per part (print files).
    parts: list[str] = Field(default_factory=list)
    #: The item types that count as this artifact.
    item_types: list[str] = Field(default_factory=list)
    #: A record producer that counts as this artifact (``firmware.build``).
    producer_tool: str = ""
    #: A file name fragment the artifact must include (``.drl`` for drill).
    file_hint: str = ""
    produced_by: str = Field(default="", max_length=300)
    #: The plain compatibility rule ("made from the current PCB revision").
    rule: str = Field(default="", max_length=300)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(f"artifact kind {v!r} must be snake_case")
        return v


class ContractAgreement(BaseModel):
    """One owning domain's agreement to one interface version (a recorded decision).

    The interface version moves only when the coupled parameters or expected
    artifacts change; linking an item is a new revision of the contract but
    the same interface version, so it keeps its agreements.
    """

    domain: str
    contract_revision: int = Field(ge=1)
    interface_version: int = Field(ge=1)
    decided_by: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    at: str = ""
    rationale: str = ""


class InterfaceContract(BaseModel):
    """What two (or more) domains agree on, as data."""

    kind: ContractKind
    title: str = Field(min_length=1, max_length=200)
    #: The plain sentence Create shows ("The circuit board must fit inside the base").
    plain: str = Field(min_length=1, max_length=200)
    #: Every domain that must agree before the contract is ``agreed``.
    owning_domains: list[str] = Field(min_length=2)
    parameters: list[CoupledParameter] = Field(default_factory=list)
    links: list[ContractLink] = Field(default_factory=list)
    artifacts: list[ExpectedArtifact] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=1000)
    #: The golden-path example that seeded it, when one did.
    seeded_by: str | None = None

    @model_validator(mode="after")
    def _unique(self) -> InterfaceContract:
        for label, names in (
            ("parameter", [p.name for p in self.parameters]),
            ("link role", [link.role for link in self.links]),
            ("owning domain", list(self.owning_domains)),
        ):
            repeated = sorted({n for n in names if names.count(n) > 1})
            if repeated:
                raise ValueError(f"{label}s must be unique; repeated: {', '.join(repeated)}")
        return self

    def parameter(self, name: str) -> CoupledParameter | None:
        return next((p for p in self.parameters if p.name == name), None)

    def link(self, role: str) -> ContractLink | None:
        return next((link for link in self.links if link.role == role), None)

    def linked_keys(self) -> list[str]:
        return [link.item_key for link in self.links if link.item_key]


class ContractCheck(BaseModel):
    """One check of a contract against the items' current revisions."""

    id: str
    label: str
    #: The same check in plain words, for Create.
    plain: str = ""
    outcome: CheckOutcome
    message: str = ""
    #: The numbers compared (measured, limit, delta), when the check ran.
    values: dict[str, Any] = Field(default_factory=dict)
    #: What was missing, each with the step that would produce it.
    missing: list[str] = Field(default_factory=list)
    #: ``KEY@n`` of every revision the check read.
    inputs: list[str] = Field(default_factory=list)
    #: The parameters the check compared.
    parameters: list[str] = Field(default_factory=list)


class ContractEvaluation(BaseModel):
    """Every check of one contract revision, and what they add up to."""

    contract_key: str
    contract_revision: int
    interface_version: int = 1
    kind: ContractKind
    outcome: CheckOutcome
    checks: list[ContractCheck] = Field(default_factory=list)
    #: ``KEY@n`` of every revision read, the contract's own first.
    inputs: list[str] = Field(default_factory=list)
    summary: str = ""
    evaluated_at: str = ""

    def counts(self) -> dict[str, int]:
        out = {o.value: 0 for o in CheckOutcome}
        for check in self.checks:
            out[check.outcome.value] += 1
        return out
