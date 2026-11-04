"""What the Create journey records about a creation.

Every value says how much it can be trusted: a brief item is confirmed,
assumed or missing; a measurement is user-confirmed, estimated or
unconfirmed; an inspection result is passed, failed, inconclusive or
awaiting review. The Create view shows those words, so an estimate is never
presented as a verified result.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


#: The brief, in the order the Create view asks about it.
BRIEF_KEYS: tuple[str, ...] = (
    "intended_use",
    "size_and_fit",
    "appearance",
    "materials",
    "design_budget",
    "manufacturing_budget",
    "quantity",
    "making_context",
    "success_criteria",
    "inspection_needs",
)

BriefStatus = Literal["confirmed", "assumed", "missing"]
MeasurementStatus = Literal["user_confirmed", "estimated", "unconfirmed"]
Route = Literal["home", "workshop", "external"]
InspectionMethod = Literal["camera", "measurement", "telemetry", "operator", "instrument"]
ResultStatus = Literal["passed", "failed", "inconclusive", "awaiting_review"]
BuildStatus = Literal["preparing", "released", "making", "completed", "failed", "cancelled"]
EvidenceKind = Literal["photo", "measurement", "telemetry", "note"]

#: A build in one of these holds a released revision: it may not be edited,
#: and deleting its creation is refused rather than silently cancelling it.
ACTIVE_BUILD_STATUSES: frozenset[str] = frozenset({"released", "making"})


class BriefItem(BaseModel):
    value: str = Field("", max_length=4000)
    status: BriefStatus = "missing"
    # "user" when a person typed or confirmed it, "agent" when proposed.
    source: Literal["user", "agent"] = "user"
    updated_at: str = Field(default_factory=now_iso)


class Measurement(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=200)
    value: float
    unit: Literal["mm", "cm", "m", "in", "ft"] = "mm"
    status: MeasurementStatus = "unconfirmed"
    # A manufacturing-critical dimension must be user-confirmed before release.
    critical: bool = False
    # Where it came from: typed, marked on a photo or sketch, or read off a model.
    source: Literal["typed", "photo", "sketch", "model", "camera"] = "typed"
    reference_id: str | None = Field(None, description="Work product the mark was made on")
    # For a mark on an image: the known dimension that set the scale.
    scale_reference: str | None = Field(None, max_length=200)
    note: str = Field("", max_length=1000)


class Assumption(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1, max_length=1000)
    status: Literal["open", "confirmed", "revised"] = "open"


#: What a reference is for (FORGE-794, REF-002). Only ``specification`` and
#: ``manufacturing_rule`` are authoritative: their explicit values can be
#: stated facts, and two of them that disagree need a person's review.
ReferenceRole = Literal[
    "inspiration",
    "geometry",
    "functional_demo",
    "specification",
    "manufacturing_rule",
    "verification_evidence",
    "existing_design",
]
REFERENCE_ROLES: tuple[str, ...] = (
    "inspiration",
    "geometry",
    "functional_demo",
    "specification",
    "manufacturing_rule",
    "verification_evidence",
    "existing_design",
)
AUTHORITATIVE_ROLES: frozenset[str] = frozenset({"specification", "manufacturing_rule"})
AssociationKind = Literal["project", "component", "parameter", "plane", "interface", "requirement"]
#: ``stated``: an explicit value in a specification or manufacturing rule.
#: ``estimated``: anything read off an image or a non-authoritative text. Never
#: ``measured``: nothing here measures.
FactBasis = Literal["stated", "estimated"]


class ReferenceAssociation(BaseModel):
    """What a reference is about: the project, a component, a parameter, a plane..."""

    kind: AssociationKind
    id: str = Field(..., min_length=1, max_length=200)
    label: str = Field("", max_length=200)


class FactCitation(BaseModel):
    """Where a fact came from: which reference, which part of it, which version."""

    reference_id: str = Field(..., min_length=1, max_length=200)
    #: ``line 12``, ``whole image`` or a short description of the region.
    region: str = Field(..., min_length=1, max_length=200)
    source_hash: str = Field("", max_length=64)
    excerpt: str = Field("", max_length=300)


class ReferenceFact(BaseModel):
    """A candidate fact extracted from a reference (REF-005). Never measured truth."""

    id: str = Field(..., min_length=1, max_length=64)
    parameter: str = Field(..., min_length=1, max_length=200)
    value: float | None = None
    text: str = Field("", max_length=300)
    unit: str = Field("", max_length=32)
    basis: FactBasis = "estimated"
    #: ``low`` for a stated value; ``high`` for anything read off an image.
    uncertainty: Literal["low", "medium", "high"] = "high"
    citation: FactCitation
    extracted_by: str = Field("", max_length=200)
    extracted_at: str = Field(default_factory=now_iso)


class ReferenceVersion(BaseModel):
    """One change to a reference's file or meaning (REF-006 history)."""

    version: int = Field(..., ge=1)
    source_hash: str = Field("", max_length=64)
    role: ReferenceRole = "inspiration"
    active: bool = True
    protected_features: list[str] = Field(default_factory=list)
    changed_at: str = Field(default_factory=now_iso)
    actor: str = Field("", max_length=200)
    note: str = Field("", max_length=300)


class ReferenceDisclosure(BaseModel):
    """A reference sent to an external provider (REF-007)."""

    provider: str = Field(..., max_length=100)
    model: str = Field("", max_length=200)
    purpose: str = Field("", max_length=100)
    source_hash: str = Field("", max_length=64)
    operation_id: str = Field("", max_length=200)
    sent_at: str = Field(default_factory=now_iso)


class ReferenceRecord(BaseModel):
    """What the Create journey knows about one reference work product (REF-002..007).

    Extends ``Brief.references`` (the ids) rather than replacing it: an id in
    ``references`` with no record here is an ``inspiration`` that is active.
    """

    work_product_id: str = Field(..., min_length=1, max_length=200)
    name: str = Field("", max_length=300)
    role: ReferenceRole = "inspiration"
    associations: list[ReferenceAssociation] = Field(default_factory=list, max_length=50)
    #: Included in the next instruction or generation (REF-004).
    active: bool = True
    protected_features: list[str] = Field(default_factory=list, max_length=50)
    #: sha256 of the reference's file as last seen.
    source_hash: str = Field("", max_length=64)
    mime: str = Field("", max_length=100)
    size: int | None = None
    versions: list[ReferenceVersion] = Field(default_factory=list)
    facts: list[ReferenceFact] = Field(default_factory=list, max_length=500)
    disclosures: list[ReferenceDisclosure] = Field(default_factory=list)

    @property
    def authoritative(self) -> bool:
        return self.role in AUTHORITATIVE_ROLES


class Brief(BaseModel):
    items: dict[str, BriefItem] = Field(default_factory=dict)
    measurements: list[Measurement] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    # Work products (images, sketches, documents, designs) that inform the design.
    references: list[str] = Field(default_factory=list)
    # FORGE-794: role, associations, inclusion, protected features, hash,
    # version history and extracted facts of those references.
    reference_details: list[ReferenceRecord] = Field(default_factory=list)

    def reference(self, work_product_id: str) -> ReferenceRecord | None:
        return next(
            (r for r in self.reference_details if r.work_product_id == work_product_id), None
        )


class RouteChoice(BaseModel):
    route: Route | None = None
    process: str = Field("", max_length=100)
    machine_id: str | None = Field(None, max_length=200)
    material: str = Field("", max_length=200)
    supplier: str = Field("", max_length=200)
    note: str = Field("", max_length=1000)


class InspectionCheck(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    feature: str = Field(..., min_length=1, max_length=300)
    condition: str = Field("", max_length=500, description="Acceptance condition or tolerance")
    evidence: str = Field("", max_length=300, description="What evidence is required")
    method: InspectionMethod = "operator"
    reviewer: Literal["person", "agent"] = "person"
    blocking: bool = True


class Revision(BaseModel):
    """The exact design a build makes: a work product and, when known, its version."""

    work_product_id: str = Field(..., min_length=1, max_length=200)
    version: int | None = None
    label: str = Field("", max_length=200)


class Estimate(BaseModel):
    value: str = Field("", max_length=100)
    basis: str = Field("", max_length=300, description="How the number was reached")


class Evidence(BaseModel):
    id: str
    kind: EvidenceKind
    check_id: str | None = None
    work_product_id: str | None = None
    upload_id: str | None = None
    value: float | None = None
    unit: str | None = None
    note: str = ""
    recorded_by: str = ""
    surface: str = "unknown"
    created_at: str = Field(default_factory=now_iso)


class CheckResult(BaseModel):
    status: ResultStatus = "awaiting_review"
    note: str = ""
    confidence: float | None = Field(None, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    decided_by: str = ""
    surface: str = "unknown"
    updated_at: str = Field(default_factory=now_iso)


class Decision(BaseModel):
    decision: Literal["accept", "reject"]
    reason: str = ""
    limitations: list[str] = Field(default_factory=list)
    approver: str = ""
    approver_verified: bool = False
    surface: str = "unknown"
    decided_at: str = Field(default_factory=now_iso)


class Release(BaseModel):
    approver: str
    approver_verified: bool = False
    surface: str = "unknown"
    released_at: str = Field(default_factory=now_iso)


class Build(BaseModel):
    """One manufacturing attempt of one revision, with its inspection."""

    id: str
    number: int
    revision: Revision
    route: Route
    process: str = ""
    machine_id: str | None = None
    material: str = ""
    settings: dict[str, str] = Field(default_factory=dict)
    estimates: dict[str, Estimate] = Field(default_factory=dict)
    status: BuildStatus = "preparing"
    status_note: str = ""
    # The checks this attempt is inspected against, frozen when it is released.
    checks: list[InspectionCheck] = Field(default_factory=list)
    release: Release | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    results: dict[str, CheckResult] = Field(default_factory=dict)
    acceptance: Decision | None = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class HistoryEntry(BaseModel):
    at: str = Field(default_factory=now_iso)
    actor: str = ""
    surface: str = "unknown"
    change: str
    summary: str = ""


class Creation(BaseModel):
    project_id: str
    # Bumped on every write; a write naming an older one is a conflict.
    version: int = 0
    brief: Brief = Field(default_factory=Brief)
    route: RouteChoice = Field(default_factory=RouteChoice)
    inspection_plan: list[InspectionCheck] = Field(default_factory=list)
    builds: list[Build] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now_iso)

    def build(self, build_id: str) -> Build:
        for b in self.builds:
            if b.id == build_id:
                return b
        raise KeyError(build_id)

    @property
    def active_builds(self) -> list[Build]:
        return [b for b in self.builds if b.status in ACTIVE_BUILD_STATUSES]
