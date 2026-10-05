"""Migrate a project's legacy nodes into items and revisions (FORGE-529).

Before FORGE-523 the twin versioned single nodes and had no item identity, so
a project that ran a design flow four times holds four copies of every part,
eight constraint sets and a pile of phase-summary decisions, linked by at most
a few hand-made ``SUPERSEDES`` edges. This module folds those nodes into the
item model, safely:

* :meth:`ItemMigration.plan` is a **dry run**. It reads the project and
  returns a :class:`MigrationPlan`: the items it would create or extend, every
  revision with its node id, name, ``created_at``, status and the rule (and
  evidence) that put it there, groupings it is unsure of, the record updates,
  and counts before and after. Nothing is written.
* :meth:`ItemMigration.apply` applies a plan the caller passes back. It
  re-plans first and refuses (:class:`StalePlanError`) unless the fresh plan
  hashes the same, so a plan reviewed against yesterday's twin is never
  applied to today's. It never deletes anything and never edits a definition
  node: revisions are linked by ``REVISION_OF`` / ``HEAD`` / ``SUPERSEDES``
  edges, and only record nodes get metadata flags.

Grouping, per definition type, strongest evidence first:

1. ``existing_item``: a node already a revision of an item stays there, and
   the item is the group's anchor.
2. ``supersedes``: nodes joined by a ``SUPERSEDES`` edge are one item.
3. ``flow_slot``: a node that carries its run and phase (``metadata.run_id``
   / ``change_set`` and ``phase``) lands on the slot key the run's flow
   declared for that phase (FORGE-524), when the caller can look runs up.
4. ``same_name``: the same normalised name (case, punctuation, dimensions and
   version words ignored).
5. ``name_similarity``: names whose meaningful words contain one another
   (``Left PETG Gusset Bracket`` and ``Left Vertical Triangular PETG Gusset
   Bracket - 220 x 120 x 12 mm``) with enough overlap overall (jaccard), never
   across opposite side words (``left`` / ``right``). Geometry is evidence:
   ``metadata.bbox_mm`` (any recorded shape) and ``metadata.volume_mm3``; a
   box more than 2x off on any edge, or a volume more than 10 percent off,
   blocks the merge. A member that joined by name similarity is a weak link:
   another node reaches its group through it only on a full containment.
   A partial match is grouped and flagged; a near miss (blocked by geometry,
   too little overlap, or only through a weak link) is not grouped and both
   sides are flagged for review.
6. ``one_per_project``: the types a project has one of (intent, constraint
   set, prd, bom) are folded into a single item.
7. ``new_item``: anything left is its own item.

A ``cad_model`` with ``metadata.parts``, or with an assembly-like name
(``assembly``, ``assy``, ``asm``) and no parts, is an ``assembly``: never a
revision of a single part. A ``SUPERSEDES`` chain that crosses ``cad_model`` /
``assembly`` is still one item, typed by its existing item or its newest node.

Revisions are ordered by ``created_at``. The newest node whose run was
approved (or simply the newest, when the run is unknown) becomes HEAD; older
ones are approved history; a node from a rejected or failed run becomes a
``rejected`` revision with the run's reason when one is recorded. A node from
a run that is still open is left alone for that run's gate to settle.

Records: a ``design_decision`` titled ``"<Phase> - phase summary"`` (hyphen,
en dash or em dash) is marked ``metadata.run_summary = true``, which keeps it
out of decision lists without deleting it. A ``simulation_result`` whose
analysed geometry resolves to a revision is pinned to it
(``metadata.depends_on_items``, the FORGE-527 shape, plus a ``DEPENDS_ON``
edge with ``kind: revision_pin``) and marked ``stale`` when that revision is
no longer its item's HEAD.

Idempotent: once applied, every definition node is a revision and every
record carries its flag or pin, so a second plan proposes nothing.

``component_selection`` (``BOMItem``) is not migrated: its nodes carry no
``created_at`` to order revisions by. Its write path already gives new
selections items.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from twin_core.consistency.record_pins import (
    PIN_EDGE_KIND,
    PIN_EDGE_KINDS,
    PINS_KEY,
    STALENESS_KEY,
    ItemPin,
    record_pins,
    record_staleness,
)
from twin_core.consistency.staleness import StalenessStatus
from twin_core.items.facts import cad_facts, num
from twin_core.items.registry import family_of
from twin_core.items.service import derive_key, item_for_node, list_items, next_revision
from twin_core.models.enums import EdgeType, NodeType
from twin_core.models.item import Item

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.items.migration")

__all__ = [
    "ItemMigration",
    "MigrationError",
    "MigrationPlan",
    "MigrationResult",
    "PlanTamperedError",
    "ProposedItem",
    "ProposedRevision",
    "RecordAction",
    "RunInfo",
    "StalePlanError",
    "is_run_summary",
    "render_report",
]

#: Definition types the migration groups, in report order.
MIGRATED_TYPES = (
    "intent",
    "stakeholder_need",
    "objective",
    "prd",
    "constraint_set",
    "bom",
    "assembly",
    "cad_model",
)
#: Types a project has one of; all their nodes fold into one item.
ONE_PER_PROJECT = frozenset({"intent", "constraint_set", "prd", "bom"})

#: Rule strength, strongest first. A group's rule is the strongest it used.
RULES = (
    "existing_item",
    "supersedes",
    "flow_slot",
    "same_name",
    "name_similarity",
    "one_per_project",
    "new_item",
)

#: Revision edge statuses the migration writes (FORGE-525 vocabulary).
_EDGE_STATUS = {"approved": "approved", "unknown": "committed", "rejected": "rejected"}

#: Stale and current, in FORGE-527's vocabulary.
STALE = StalenessStatus.STALE.value
CURRENT = StalenessStatus.CURRENT.value

_SUMMARY_RE = re.compile("[-\u2013\u2014]" + r"\s*phase\s+summary\s*$", re.IGNORECASE)
_DIMENSION_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mm|cm|m|in)?(?:\s*[x×]\s*\d+(?:\.\d+)?\s*(?:mm|cm|m|in)?)+"
    r"|\d+(?:\.\d+)?\s*(?:mm|cm|in|deg|kg|g)\b",
    re.IGNORECASE,
)
_NOISE = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "and",
        "with",
        "for",
        "part",
        "model",
        "rev",
        "revision",
        "final",
        "new",
        "updated",
        "copy",
        "draft",
        "v",
        "version",
    }
)
_VERSION_RE = re.compile(r"^(?:v|r|rev)\d+$")
_OPPOSITES = (
    ("left", "right"),
    ("top", "bottom"),
    ("upper", "lower"),
    ("front", "back"),
    ("front", "rear"),
    ("inner", "outer"),
    ("inside", "outside"),
    ("male", "female"),
    ("first", "second"),
)
#: Name containment needed to group by similarity.
SIMILARITY_THRESHOLD = 0.75
#: Jaccard overlap needed too: containment alone lets a short name
#: (``wall_shelf_board``) match any long name that happens to include it.
JACCARD_THRESHOLD = 0.5
#: Bounding boxes whose sorted dimensions are all within this ratio agree.
BBOX_SIMILAR_RATIO = 1.35
#: Any dimension further apart than this ratio means the boxes disagree.
BBOX_DISSIMILAR_RATIO = 2.0
#: Volumes further apart than this ratio (about 10 percent) disagree.
VOLUME_SIMILAR_RATIO = 1.1
#: Name words that mark an assembly when a node carries no ``parts``.
_ASSEMBLY_WORDS = frozenset({"assembly", "assy", "asm"})
_CAD_TYPES = ("assembly", "cad_model")
_EVIDENCE_EDGES = (EdgeType.DERIVES_FROM, EdgeType.PARENT_OF, EdgeType.VALIDATES)

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-twin-items")
    return _metrics


def _count(item_type: str, kind: str, outcome: str = "created", count: int = 1) -> None:
    try:
        _collector().record_twin_item_migration(item_type, kind, outcome, count)
    except Exception:  # noqa: BLE001 - metrics never break a migration  # pragma: no cover
        pass


class MigrationError(ValueError):
    """A migration plan could not be applied. The message is caller-facing."""


class StalePlanError(MigrationError):
    """The twin changed since the plan was made."""


class PlanTamperedError(MigrationError):
    """The plan's content does not match its hash."""


@dataclass(frozen=True)
class RunInfo:
    """What the migration needs to know about one design-flow run.

    ``outcome`` is ``approved`` (its gates approved, the run completed),
    ``rejected`` (rejected, failed, canceled or timed out; ``reason`` says
    why when known), ``open`` (still running or parked at a gate) or
    ``unknown``. ``slots`` maps a phase id to its deliverable slots, each
    ``{"item_type", "name", "item_key"}``.
    """

    run_id: str
    outcome: str = "unknown"
    reason: str | None = None
    slots: dict[str, list[dict[str, str]]] = field(default_factory=dict)


RunLookup = Callable[[str], Awaitable[RunInfo | None]]


# ---------------------------------------------------------------------------
# Plan models (JSON round-trippable: the caller passes the plan back)
# ---------------------------------------------------------------------------


class ProposedRevision(BaseModel):
    """One node as a revision of a proposed item."""

    node_id: str
    name: str
    created_at: str | None = None
    revision: int
    #: ``head``, ``approved`` (history) or ``rejected``.
    status: str
    #: Why it is rejected (the run's reason), when known.
    status_reason: str | None = None
    run_id: str | None = None
    #: How this node joined the item.
    rule: str
    evidence: str
    #: True for a node that is already a revision (shown for context only).
    existing: bool = False


class ProposedItem(BaseModel):
    """An item the migration creates, or an existing item it extends."""

    key: str
    item_type: str
    name: str
    existing_item: bool = False
    #: The strongest rule that formed the group, and every rule it used.
    rule: str
    rules: list[str] = Field(default_factory=list)
    confidence: str = "high"
    review_reasons: list[str] = Field(default_factory=list)
    head_node_id: str | None = None
    head_revision: int = 0
    revisions: list[ProposedRevision] = Field(default_factory=list)

    @property
    def new_revisions(self) -> list[ProposedRevision]:
        return [r for r in self.revisions if not r.existing]


class RecordAction(BaseModel):
    """One record update: a run-summary flag or a revision pin."""

    node_id: str
    name: str
    record_type: str
    #: ``mark_run_summary`` or ``pin``.
    action: str
    item_ref: str | None = None
    pinned_node_id: str | None = None
    item_type: str | None = None
    #: ``current`` or ``stale`` for a pin.
    staleness: str | None = None
    reason: str


class MigrationPlan(BaseModel):
    """A dry-run result. Pass it back unchanged to :meth:`ItemMigration.apply`."""

    project_id: str
    plan_hash: str = ""
    twin_fingerprint: str
    created_at: str
    items: list[ProposedItem] = Field(default_factory=list)
    records: list[RecordAction] = Field(default_factory=list)
    low_confidence: list[dict[str, Any]] = Field(default_factory=list)
    #: Nodes left alone, with why (a run that is still open).
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    counts_before: dict[str, Any] = Field(default_factory=dict)
    counts_after: dict[str, Any] = Field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.items and not self.records

    def content_hash(self) -> str:
        """The hash of what the plan would do, against which twin state."""
        body = self.model_dump(
            mode="json", exclude={"plan_hash", "created_at", "counts_before", "counts_after"}
        )
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()


class MigrationResult(BaseModel):
    """What :meth:`ItemMigration.apply` did."""

    project_id: str
    plan_hash: str
    applied: bool
    items_created: int = 0
    items_extended: int = 0
    revisions_linked: int = 0
    run_summaries_marked: int = 0
    records_pinned: int = 0
    records_stale: int = 0
    failures: list[dict[str, str]] = Field(default_factory=list)
    by_type: dict[str, dict[str, int]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Node reading
# ---------------------------------------------------------------------------


def is_run_summary(name: str | None, metadata: dict[str, Any] | None = None) -> bool:
    """True for a design-flow phase-summary decision (a run summary, not a decision).

    Either already flagged (``metadata.run_summary``) or titled
    ``"<Phase> - phase summary"`` with a hyphen, en dash or em dash.
    """
    if (metadata or {}).get("run_summary"):
        return True
    return bool(_SUMMARY_RE.search((name or "").strip()))


def _value(raw: Any) -> str:
    return str(getattr(raw, "value", raw))


def _definition_type(node: Any) -> str | None:
    """The registry type a legacy node is a revision of, or ``None``."""
    node_type = getattr(node, "node_type", None)
    meta = getattr(node, "metadata", None) or {}
    if node_type == NodeType.WORK_PRODUCT:
        wp_type = _value(getattr(node, "type", ""))
        if wp_type == "cad_model":
            if meta.get("item_type") in {"assembly", "cad_model"}:
                return str(meta["item_type"])
            parts = meta.get("parts")
            if isinstance(parts, list) and parts:
                return "assembly"
            # No parts recorded: an assembly-like name is still an assembly,
            # never a revision of a single part.
            return "assembly" if name_tokens(_name(node)) & _ASSEMBLY_WORDS else "cad_model"
        if wp_type in {"constraint_set", "bom", "prd"}:
            return wp_type
        return None
    if node_type == NodeType.ENGINEERING_ENTITY:
        entity = _value(getattr(node, "entity_type", ""))
        return entity if entity in {"intent", "stakeholder_need", "objective"} else None
    return None


def _record_type(node: Any) -> str | None:
    if getattr(node, "node_type", None) != NodeType.WORK_PRODUCT:
        return None
    wp_type = _value(getattr(node, "type", ""))
    return wp_type if wp_type in {"design_decision", "simulation_result"} else None


def _name(node: Any) -> str:
    for attr in ("name", "title", "statement"):
        value = getattr(node, attr, None)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text if len(text) <= 120 else text[:117] + "..."
    return str(getattr(node, "id", "unnamed"))


def _created(node: Any) -> datetime:
    raw = getattr(node, "created_at", None)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def _iso(node: Any) -> str | None:
    raw = getattr(node, "created_at", None)
    return _created(node).isoformat() if isinstance(raw, datetime) else None


def _run_of(node: Any) -> tuple[str | None, str | None]:
    meta = getattr(node, "metadata", None) or {}
    run = meta.get("run_id") or meta.get("change_set") or meta.get("flow_run_id")
    phase = meta.get("phase") or meta.get("flow_phase")
    return (str(run) if run else None, str(phase) if phase else None)


def name_tokens(name: str) -> frozenset[str]:
    """The meaningful words of a name: no dimensions, version words or filler."""
    text = _DIMENSION_RE.sub(" ", (name or "").lower())
    words = re.findall(r"[a-z]+|\d+", text)
    return frozenset(
        w for w in words if w not in _NOISE and not _VERSION_RE.match(w) and len(w) > 1
    )


def _conflict(a: frozenset[str], b: frozenset[str]) -> str | None:
    for x, y in _OPPOSITES:
        if (x in a and y in b and y not in a) or (y in a and x in b and x not in a):
            return f"'{x}' vs '{y}'"
    return None


_AXIS_KEYS = ("{a}", "{a}_mm", "{a}_length", "{a}length", "{a}_size", "size_{a}", "d{a}")
_AXIS_RANGES = (("{a}min", "{a}max"), ("{a}_min", "{a}_max"), ("min_{a}", "max_{a}"))
_NESTED_KEYS = ("size", "dimensions", "dims", "extent", "size_mm")


def _extent(raw: Any) -> list[float | None]:
    """The three edge lengths of a bbox, in any of the shapes recorders store."""
    if isinstance(raw, list | tuple):
        if len(raw) == 3 and not any(isinstance(v, list | tuple | dict) for v in raw):
            return [num(v) for v in raw]
        if len(raw) == 6:
            lo, hi = [num(v) for v in raw[:3]], [num(v) for v in raw[3:]]
            return [h - x if h is not None and x is not None else None for x, h in zip(lo, hi)]
        if len(raw) == 2 and all(isinstance(v, list | tuple) and len(v) == 3 for v in raw):
            return _extent([*raw[0], *raw[1]])
        return []
    if not isinstance(raw, dict):
        return []
    lower = {str(k).lower(): v for k, v in raw.items()}
    dims: list[float | None] = []
    for axis in ("x", "y", "z"):
        value = next(
            (num(lower[k.format(a=axis)]) for k in _AXIS_KEYS if k.format(a=axis) in lower), None
        )
        for lo_key, hi_key in _AXIS_RANGES:
            lo_k, hi_k = lo_key.format(a=axis), hi_key.format(a=axis)
            if value is None and lo_k in lower and hi_k in lower:
                lo_, hi_ = num(lower[lo_k]), num(lower[hi_k])
                value = hi_ - lo_ if hi_ is not None and lo_ is not None else None
        dims.append(value)
    if any(d is not None for d in dims):
        return dims
    if "min" in lower and "max" in lower:
        lo_raw, hi_raw = lower["min"], lower["max"]
        if isinstance(lo_raw, dict) and isinstance(hi_raw, dict):
            lo_raw = [lo_raw.get(a) for a in ("x", "y", "z")]
            hi_raw = [hi_raw.get(a) for a in ("x", "y", "z")]
        if isinstance(lo_raw, list | tuple) and isinstance(hi_raw, list | tuple):
            return _extent([*lo_raw, *hi_raw])
    for key in _NESTED_KEYS:
        if key in lower:
            return _extent(lower[key])
    return []


def _geometry_props(meta: dict[str, Any]) -> dict[str, Any]:
    features = meta.get("geometry_features")
    props = features.get("properties") if isinstance(features, dict) else None
    return props if isinstance(props, dict) else {}


def _bbox_dims(node: Any) -> list[float] | None:
    """Sorted bbox edge lengths in mm (``metadata.bbox_mm`` and friends), or ``None``."""
    meta = dict(getattr(node, "metadata", None) or {})
    props = _geometry_props(meta)
    candidates = (
        meta.get("bbox_mm"),
        meta.get("bounding_box"),
        meta.get("bbox"),
        cad_facts(meta).get("bbox"),
        props.get("bbox_mm"),
        props.get("bounding_box"),
    )
    for raw in candidates:
        if raw is None:
            continue
        dims = _extent(raw)
        if len(dims) == 3 and all(d is not None and d > 0 for d in dims):
            return sorted(float(d) for d in dims if d is not None)
    return None


def _volume(node: Any) -> float | None:
    """``metadata.volume_mm3`` (or the recorded geometry properties' volume), if positive."""
    meta = dict(getattr(node, "metadata", None) or {})
    props = _geometry_props(meta)
    for raw in (
        meta.get("volume_mm3"),
        meta.get("volume"),
        props.get("volume_mm3"),
        props.get("volume"),
    ):
        value = num(raw)
        if value is not None and value > 0:
            return value
    return None


def _parts(node: Any) -> int:
    parts = (getattr(node, "metadata", None) or {}).get("parts")
    return len(parts) if isinstance(parts, list) else 0


def _bbox_verdict(a: list[float] | None, b: list[float] | None) -> str:
    """``similar``, ``dissimilar``, ``uncertain`` (between the ratios) or ``unknown``."""
    if a is None or b is None:
        return "unknown"
    ratios = [max(x, y) / min(x, y) for x, y in zip(a, b)]
    if all(r <= BBOX_SIMILAR_RATIO for r in ratios):
        return "similar"
    if any(r > BBOX_DISSIMILAR_RATIO for r in ratios):
        return "dissimilar"
    return "uncertain"


def _volume_verdict(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "unknown"
    return "similar" if max(a, b) / min(a, b) <= VOLUME_SIMILAR_RATIO else "dissimilar"


def _geometry_verdict(
    box_a: list[float] | None,
    box_b: list[float] | None,
    vol_a: float | None,
    vol_b: float | None,
) -> str:
    """Bbox and volume together: any clear difference is ``dissimilar``."""
    box, vol = _bbox_verdict(box_a, box_b), _volume_verdict(vol_a, vol_b)
    if "dissimilar" in (box, vol):
        return "dissimilar"
    if box == "uncertain":
        return "uncertain"
    if "similar" in (box, vol):
        return "similar"
    return "unknown"


def _fmt_dims(d: list[float] | None) -> str:
    return "x".join(f"{v:.4g}" for v in d) + " mm" if d else "no bbox"


def _fmt_volume(v: float | None) -> str:
    return f"volume {v:.4g} mm3" if v is not None else "no volume"


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


@dataclass
class _Member:
    node: Any
    item_type: str
    name: str
    tokens: frozenset[str]
    bbox: list[float] | None
    run_id: str | None
    phase: str | None
    volume: float | None = None
    rule: str = "new_item"
    evidence: str = "no other node matched"

    def ref(self) -> _Ref:
        # A member that joined only by name similarity is a weak link: others
        # may reach the group through it only on a strong match.
        return _Ref(self.name, self.tokens, self.bbox, self.volume, self.rule != "name_similarity")


@dataclass(frozen=True)
class _Ref:
    """One name a group can be matched by, with its geometry."""

    name: str
    tokens: frozenset[str]
    bbox: list[float] | None
    volume: float | None
    #: False for a member that joined by name similarity (a weak link).
    core: bool = True


@dataclass(frozen=True)
class _Match:
    """How two names compare, and what the comparison allows."""

    containment: float
    jaccard: float
    geometry: str
    evidence: str
    #: ``merge``, or why not: ``near_miss`` (names overlap too little),
    #: ``weak_link`` (only through a weakly joined member) or ``geometry``.
    decision: str

    @property
    def score(self) -> tuple[float, float]:
        return (self.containment, self.jaccard)


@dataclass
class _Group:
    item_type: str
    members: list[_Member] = field(default_factory=list)
    item: Item | None = None
    #: Names and geometry of an existing item's revisions (for matching only).
    anchor_names: list[_Ref] = field(default_factory=list)
    slot_key: str | None = None
    rules: set[str] = field(default_factory=set)
    review: list[str] = field(default_factory=list)

    def names(self) -> list[_Ref]:
        return [*self.anchor_names, *(m.ref() for m in self.members)]

    def label(self) -> str:
        if self.item is not None:
            return self.item.key
        newest = max(self.members, key=lambda m: (_created(m.node), str(m.node.id)))
        return f"'{newest.name}'"

    def first_created(self) -> datetime:
        times = [_created(m.node) for m in self.members]
        return min(times) if times else datetime.min.replace(tzinfo=UTC)


def _absorb(into: _Group, other: _Group, rule: str, evidence: str) -> None:
    for member in other.members:
        if member.rule == "new_item":
            member.rule, member.evidence = rule, evidence
        into.members.append(member)
    into.anchor_names.extend(other.anchor_names)
    into.rules |= other.rules | {rule}
    into.review.extend(other.review)
    into.item = into.item or other.item
    into.slot_key = into.slot_key or other.slot_key


def _compatible(a: _Group, b: _Group) -> bool:
    if a.item is not None and b.item is not None and a.item.id != b.item.id:
        return False
    return not (a.slot_key and b.slot_key and a.slot_key != b.slot_key)


def _judge(a: _Ref, b: _Ref) -> _Match | None:
    """Compare two names (and their geometry); ``None`` when they do not overlap enough."""
    if not a.tokens or not b.tokens or _conflict(a.tokens, b.tokens):
        return None
    inter = len(a.tokens & b.tokens)
    if inter == 0:
        return None
    containment = inter / min(len(a.tokens), len(b.tokens))
    if containment < SIMILARITY_THRESHOLD:
        return None
    jaccard = inter / len(a.tokens | b.tokens)
    geometry = _geometry_verdict(a.bbox, b.bbox, a.volume, b.volume)
    evidence = (
        f"'{a.name}' ~ '{b.name}': {inter} shared words "
        f"(containment {containment:.2f}, jaccard {jaccard:.2f}); "
        f"bbox {_fmt_dims(a.bbox)} vs {_fmt_dims(b.bbox)}, "
        f"{_fmt_volume(a.volume)} vs {_fmt_volume(b.volume)} ({geometry})"
    )
    if geometry == "dissimilar":
        decision = "geometry"
    elif jaccard < JACCARD_THRESHOLD:
        decision = "near_miss"
    elif not (a.core and b.core) and containment < 1.0:
        decision = "weak_link"
    else:
        decision = "merge"
    return _Match(containment, jaccard, geometry, evidence, decision)


def _similarity(a: _Group, b: _Group) -> tuple[_Match | None, _Match | None]:
    """The best mergeable match between two groups, and the best one that is not."""
    best: _Match | None = None
    blocked: _Match | None = None
    for ref_a in a.names():
        for ref_b in b.names():
            match = _judge(ref_a, ref_b)
            if match is None:
                continue
            if match.decision == "merge":
                if best is None or match.score > best.score:
                    best = match
            elif blocked is None or match.score > blocked.score:
                blocked = match
    return best, blocked


_BLOCKED_REASON = {
    "geometry": "names match but the geometry clearly differs",
    "near_miss": "names overlap but too little to group",
    "weak_link": "names match only through a member that itself joined by name similarity",
}


def _union_by(groups: list[_Group], key_of: Callable[[_Group], str | None], rule: str) -> None:
    """Merge groups that share a key (in place)."""
    by_key: dict[str, _Group] = {}
    for group in list(groups):
        key = key_of(group)
        if not key:
            continue
        if key in by_key and _compatible(by_key[key], group):
            _absorb(by_key[key], group, rule, f"same {rule.replace('_', ' ')} '{key}'")
            groups.remove(group)
        else:
            by_key.setdefault(key, group)


def _match_slot(member: _Member, slots: list[dict[str, str]]) -> tuple[str, str] | None:
    """The slot key a node lands on, and how (same name, best words, the only slot)."""
    family = family_of(member.item_type)
    exact = [s for s in slots if s.get("item_type") == member.item_type]
    candidates = exact or [s for s in slots if s.get("item_type") in family]
    if not candidates:
        return None
    for slot in candidates:
        if str(slot.get("name", "")).strip().lower() == member.name.strip().lower():
            return str(slot["item_key"]), "same name"
    scored = []
    for slot in candidates:
        theirs = name_tokens(str(slot.get("name", "")))
        if theirs and member.tokens and not _conflict(theirs, member.tokens):
            inter = len(theirs & member.tokens)
            scored.append((inter / len(theirs | member.tokens), str(slot["item_key"])))
    scored.sort(reverse=True)
    if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return scored[0][1], f"best word overlap {scored[0][0]:.2f}"
    if len(candidates) == 1:
        return str(candidates[0]["item_key"]), "the only slot of its type"
    return None


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


@dataclass
class _Snapshot:
    """Everything the plan is derived from."""

    definitions: dict[str, list[Any]]
    records: list[Any]
    items: list[Item]
    linked: dict[UUID, tuple[Item, int, str]]
    fingerprint: str
    counts: dict[str, Any]


class ItemMigration:
    """Plan and apply the legacy-node migration for one twin.

    ``run_lookup`` answers what a run's gate decided and which slots its flow
    declared; without it every run is ``unknown`` (newest wins). ``member_ids``
    adds nodes that belong to the project by its membership table but carry
    no ``project_id`` of their own (the dual representation of project scope).
    """

    def __init__(
        self,
        twin: Any,
        *,
        run_lookup: RunLookup | None = None,
        member_ids: Callable[[UUID], Awaitable[Iterable[UUID]]] | None = None,
    ) -> None:
        self.twin = twin
        self.run_lookup = run_lookup
        self.member_ids = member_ids

    # -- reading ------------------------------------------------------------

    async def _project_nodes(self, pid: UUID) -> list[Any]:
        graph = self.twin.graph
        nodes: dict[UUID, Any] = {}
        for node_type in (NodeType.WORK_PRODUCT, NodeType.ENGINEERING_ENTITY):
            for node in await graph.list_nodes(node_type, filters={"project_id": pid}):
                nodes[node.id] = node
        if self.member_ids is not None:
            try:
                extra = list(await self.member_ids(pid))
            except Exception as exc:  # noqa: BLE001 - membership is a best-effort extra
                logger.warning("item_migration_members_failed", project_id=str(pid), error=str(exc))
                extra = []
            for node_id in extra:
                if node_id not in nodes:
                    node = await graph.get_node(node_id)
                    if node is not None:
                        nodes[node_id] = node
        return list(nodes.values())

    async def _snapshot(self, pid: UUID) -> _Snapshot:
        nodes = await self._project_nodes(pid)
        definitions: dict[str, list[Any]] = {t: [] for t in MIGRATED_TYPES}
        records: list[Any] = []
        for node in nodes:
            kind = _definition_type(node)
            if kind is not None:
                definitions[kind].append(node)
            elif _record_type(node) is not None:
                records.append(node)
        items = [i for i in await list_items(self.twin, project_id=pid, include_unheaded=True)]
        linked: dict[UUID, tuple[Item, int, str]] = {}
        for item in items:
            edges = await self.twin.graph.get_edges(
                item.id, direction="incoming", edge_type=EdgeType.REVISION_OF
            )
            for edge in edges:
                meta = edge.metadata or {}
                linked[edge.source_id] = (
                    item,
                    int(meta.get("revision") or 0),
                    str(meta.get("status") or "committed"),
                )
        # A node linked to an item outside this project (or before scoping).
        for kind_nodes in definitions.values():
            for node in kind_nodes:
                if node.id not in linked:
                    found = await item_for_node(self.twin, node.id)
                    if found is not None:
                        linked[node.id] = (found[0], found[1], "committed")
        await self._unify_cad_chains(definitions, linked)
        digest = hashlib.sha256()
        for node in sorted(nodes, key=lambda n: str(n.id)):
            meta = getattr(node, "metadata", None) or {}
            digest.update(
                json.dumps(
                    [
                        str(node.id),
                        _definition_type(node) or _record_type(node),
                        _name(node),
                        _iso(node),
                        str(getattr(node, "updated_at", "")),
                        bool(meta.get("run_summary")),
                        meta.get(PINS_KEY),
                        meta.get(STALENESS_KEY),
                        str(linked[node.id][0].key) if node.id in linked else None,
                    ],
                    default=str,
                ).encode()
            )
        for item in sorted(items, key=lambda i: i.key):
            digest.update(
                f"{item.key}|{item.head_node_id}|{item.head_revision}|{item.last_revision}".encode()
            )
        return _Snapshot(
            definitions=definitions,
            records=records,
            items=items,
            linked=linked,
            fingerprint=digest.hexdigest(),
            counts={},
        )

    async def _unify_cad_chains(
        self, definitions: dict[str, list[Any]], linked: dict[UUID, tuple[Item, int, str]]
    ) -> None:
        """Give a SUPERSEDES chain that crosses ``cad_model`` / ``assembly`` one type.

        A part re-recorded with ``parts`` (or the reverse) is still one item:
        the whole chain takes its existing item's type when it has one, else
        its newest node's type.
        """
        nodes = {n.id: (n, t) for t in _CAD_TYPES for n in definitions[t]}
        parent = {node_id: node_id for node_id in nodes}

        def find(node_id: UUID) -> UUID:
            while parent[node_id] != node_id:
                parent[node_id] = parent[parent[node_id]]
                node_id = parent[node_id]
            return node_id

        for node_id in nodes:
            edges = await self.twin.graph.get_edges(
                node_id, direction="outgoing", edge_type=EdgeType.SUPERSEDES
            )
            for edge in edges:
                if edge.target_id in parent:
                    parent[find(node_id)] = find(edge.target_id)
        chains: dict[UUID, list[UUID]] = {}
        for node_id in nodes:
            chains.setdefault(find(node_id), []).append(node_id)
        for chain in chains.values():
            types = {nodes[node_id][1] for node_id in chain}
            if len(types) < 2:
                continue
            newest = sorted(chain, key=lambda i: (_created(nodes[i][0]), str(i)))
            existing = [
                linked[i][0].item_type
                for i in newest
                if i in linked and linked[i][0].item_type in _CAD_TYPES
            ]
            target = existing[-1] if existing else nodes[newest[-1]][1]
            for node_id in chain:
                node, kind = nodes[node_id]
                if kind != target:
                    definitions[kind].remove(node)
                    definitions[target].append(node)
            logger.info(
                "item_migration_chain_retyped",
                item_type=target,
                nodes=[str(i) for i in newest],
            )

    async def _run(self, run_id: str | None, cache: dict[str, RunInfo | None]) -> RunInfo | None:
        if not run_id or self.run_lookup is None:
            return None
        if run_id not in cache:
            try:
                cache[run_id] = await self.run_lookup(run_id)
            except Exception as exc:  # noqa: BLE001 - an unknown run is "unknown", not fatal
                logger.warning("item_migration_run_lookup_failed", run_id=run_id, error=str(exc))
                cache[run_id] = None
        return cache[run_id]

    # -- planning -----------------------------------------------------------

    async def plan(self, project_id: UUID | str) -> MigrationPlan:
        """The dry run: what :meth:`apply` would do to ``project_id``. Writes nothing."""
        pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
        with tracer.start_as_current_span("twin.items.migration.plan") as span:
            span.set_attribute("project.id", str(pid))
            snap = await self._snapshot(pid)
            runs: dict[str, RunInfo | None] = {}
            proposed: list[ProposedItem] = []
            skipped: list[dict[str, Any]] = []
            taken_keys = {i.key for i in snap.items}
            # node id -> (key, revision, item_type, is_head) after the migration.
            final: dict[str, tuple[str, int, str]] = {}
            heads: dict[str, str] = {}
            for item in snap.items:
                if item.head_node_id is not None:
                    heads[item.key] = str(item.head_node_id)
            for node_id, (item, revision, _status) in snap.linked.items():
                final[str(node_id)] = (item.key, revision, item.item_type)

            for item_type in MIGRATED_TYPES:
                groups = await self._group(item_type, snap, runs, skipped)
                for group in groups:
                    entry = await self._propose(group, item_type, taken_keys, runs)
                    if entry is None:
                        continue
                    taken_keys.add(entry.key)
                    proposed.append(entry)
                    for rev in entry.new_revisions:
                        final[rev.node_id] = (entry.key, rev.revision, entry.item_type)
                    if entry.head_node_id:
                        heads[entry.key] = entry.head_node_id

            records = await self._plan_records(snap, final, heads)
            plan = MigrationPlan(
                project_id=str(pid),
                twin_fingerprint=snap.fingerprint,
                created_at=datetime.now(UTC).isoformat(),
                items=proposed,
                records=records,
                low_confidence=[
                    {"key": p.key, "item_type": p.item_type, "reasons": p.review_reasons}
                    for p in proposed
                    if p.confidence == "low"
                ],
                skipped=skipped,
            )
            plan.counts_before, plan.counts_after = _counts(snap, plan)
            plan.plan_hash = plan.content_hash()
            span.set_attribute("migration.items", len(proposed))
            span.set_attribute("migration.records", len(records))
            span.set_attribute("migration.low_confidence", len(plan.low_confidence))
            logger.info(
                "item_migration_planned",
                project_id=str(pid),
                plan_hash=plan.plan_hash,
                items=len(proposed),
                revisions=sum(len(p.new_revisions) for p in proposed),
                records=len(records),
                low_confidence=len(plan.low_confidence),
                skipped=len(skipped),
            )
            return plan

    async def _group(
        self,
        item_type: str,
        snap: _Snapshot,
        runs: dict[str, RunInfo | None],
        skipped: list[dict[str, Any]],
    ) -> list[_Group]:
        family = family_of(item_type)
        nodes = snap.definitions[item_type]
        by_id: dict[UUID, _Group] = {}
        groups: list[_Group] = []
        # 1. existing items of this type are the anchors.
        anchors: dict[UUID, _Group] = {}
        for item in snap.items:
            if item.item_type != item_type:
                continue
            anchor = _Group(item_type=item_type, item=item, rules={"existing_item"})
            anchors[item.id] = anchor
            groups.append(anchor)
        for node_id, (item, _rev, _status) in snap.linked.items():
            found = anchors.get(item.id)
            if found is None:
                continue
            node = await self.twin.graph.get_node(node_id)
            if node is not None:
                found.anchor_names.append(
                    _Ref(_name(node), name_tokens(_name(node)), _bbox_dims(node), _volume(node))
                )
        for node in sorted(nodes, key=lambda n: (_created(n), str(n.id))):
            if node.id in snap.linked:
                continue
            run_id, phase = _run_of(node)
            run = await self._run(run_id, runs)
            if run is not None and run.outcome == "open":
                skipped.append(
                    {
                        "node_id": str(node.id),
                        "name": _name(node),
                        "reason": f"run {run_id} is still open; its gate decides",
                    }
                )
                continue
            member = _Member(
                node=node,
                item_type=item_type,
                name=_name(node),
                tokens=name_tokens(_name(node)),
                bbox=_bbox_dims(node),
                run_id=run_id,
                phase=phase,
                volume=_volume(node),
            )
            group = _Group(item_type=item_type, members=[member])
            by_id[node.id] = group
            groups.append(group)

        def group_of(node_id: UUID) -> _Group | None:
            for group in groups:
                if any(m.node.id == node_id for m in group.members):
                    return group
            linked = snap.linked.get(node_id)
            if linked is not None and linked[0].item_type in family:
                return anchors.get(linked[0].id)
            return None

        # 2. SUPERSEDES chains.
        for node_id in list(by_id):
            edges = await self.twin.graph.get_edges(
                node_id, direction="outgoing", edge_type=EdgeType.SUPERSEDES
            )
            for edge in edges:
                mine, theirs = group_of(node_id), group_of(edge.target_id)
                if mine is None or theirs is None or mine is theirs:
                    continue
                if not _compatible(mine, theirs):
                    mine.review.append(
                        f"{node_id} supersedes {edge.target_id}, which belongs to another item"
                    )
                    continue
                # Keep the superseded side, so the newer node is the one labelled.
                if mine.item is not None and theirs.item is None:
                    keep, drop = mine, theirs
                else:
                    keep, drop = theirs, mine
                _absorb(keep, drop, "supersedes", f"{node_id} supersedes {edge.target_id}")
                groups.remove(drop)

        # 3. flow slot keys.
        for group in groups:
            for member in group.members:
                run = await self._run(member.run_id, runs)
                if run is None or not member.phase:
                    continue
                match = _match_slot(member, run.slots.get(member.phase, []))
                if match is None:
                    continue
                key, how = match
                if group.slot_key and group.slot_key != key:
                    group.review.append(f"members match two slots ({group.slot_key}, {key})")
                    continue
                group.slot_key = key
                group.rules.add("flow_slot")
                if member.rule in {"new_item", "supersedes"}:
                    member.rule = "flow_slot"
                    member.evidence = (
                        f"run {member.run_id} phase {member.phase} declares slot {key} ({how})"
                    )
        for group in groups:
            if group.item is not None and group.slot_key is None:
                group.slot_key = group.item.key
        _union_by(groups, lambda g: g.slot_key, "flow_slot")

        # 4. same normalised name (or a name that derives an existing item's key).
        def norm(group: _Group) -> str | None:
            if group.item is not None:
                return None
            names = {" ".join(sorted(m.tokens)) for m in group.members if m.tokens}
            return sorted(names)[0] if len(names) == 1 else None

        for anchor in [g for g in groups if g.item is not None]:
            for group in [g for g in groups if g.item is None]:
                if any(derive_key(item_type, m.name) == anchor.item.key for m in group.members):  # type: ignore[union-attr]
                    _absorb(anchor, group, "same_name", f"name derives key {anchor.item.key}")  # type: ignore[union-attr]
                    groups.remove(group)
        _union_by(groups, norm, "same_name")

        # 5. name similarity, best pair first. Geometry (bbox, volume) that
        # clearly differs blocks a merge; so does a short name merely contained
        # in a long one (low jaccard), and a chain through a weakly joined member.
        while True:
            best: tuple[tuple[float, float], _Group, _Group, _Match] | None = None
            ordered = sorted(
                groups, key=lambda g: (g.first_created(), g.item.key if g.item else "")
            )
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    if not _compatible(a, b) or (a.item is not None and b.item is not None):
                        continue
                    pair, _blocked = _similarity(a, b)
                    if pair is not None and (best is None or pair.score > best[0]):
                        best = (pair.score, a, b, pair)
            if best is None:
                break
            _score, a, b, pair = best
            keep, drop = (b, a) if b.item is not None else (a, b)
            _absorb(keep, drop, "name_similarity", pair.evidence)
            groups.remove(drop)
            if pair.containment < 1.0:
                keep.review.append(f"names only partly match: {pair.evidence}")
            if pair.geometry == "uncertain":
                keep.review.append(f"names match but boxes differ somewhat: {pair.evidence}")
        # What was not grouped but came close is flagged on both sides.
        ordered = sorted(groups, key=lambda g: (g.first_created(), g.item.key if g.item else ""))
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                if not (a.members or b.members) or not _compatible(a, b):
                    continue
                _pair, blocked = _similarity(a, b)
                if blocked is None:
                    continue
                reason = _BLOCKED_REASON[blocked.decision]
                a.review.append(f"not grouped with {b.label()}: {reason}: {blocked.evidence}")
                b.review.append(f"not grouped with {a.label()}: {reason}: {blocked.evidence}")

        # 6. one item per project for singleton types.
        if item_type in ONE_PER_PROJECT and len([g for g in groups if g.item is not None]) <= 1:
            populated = [g for g in groups if g.members or g.item is not None]
            if len(populated) > 1:
                ordered = sorted(populated, key=lambda g: (g.item is None, g.first_created()))
                keep = ordered[0]
                for drop in ordered[1:]:
                    _absorb(
                        keep,
                        drop,
                        "one_per_project",
                        f"a project has one {item_type}; folded into one item",
                    )
                    groups.remove(drop)
        return [g for g in groups if g.members]

    async def _propose(
        self,
        group: _Group,
        item_type: str,
        taken_keys: set[str],
        runs: dict[str, RunInfo | None],
    ) -> ProposedItem | None:
        members = sorted(group.members, key=lambda m: (_created(m.node), str(m.node.id)))
        outcomes: list[tuple[_Member, str, str | None]] = []
        for member in members:
            run = await self._run(member.run_id, runs)
            outcome = run.outcome if run is not None else "unknown"
            if outcome not in _EDGE_STATUS:
                outcome = "unknown"
            outcomes.append((member, outcome, run.reason if run is not None else None))
        item = group.item
        # The head candidate: the newest non-rejected node, or the existing head.
        accepted = [(m, o) for m, o, _ in outcomes if o != "rejected"]
        head_member: _Member | None = accepted[-1][0] if accepted else None
        existing_head_time: datetime | None = None
        if item is not None and item.head_node_id is not None:
            head_node = await self.twin.graph.get_node(item.head_node_id)
            existing_head_time = _created(head_node) if head_node is not None else None
            if head_member is not None and existing_head_time is not None:
                if _created(head_member.node) <= existing_head_time:
                    head_member = None

        if item is not None:
            key = item.key
            start = next_revision(item)
        else:
            if group.slot_key:
                key = group.slot_key
            else:
                newest = (head_member or members[-1]).name
                key = derive_key(item_type, newest)
            base, n = key, 2
            while key in taken_keys:
                key = f"{base}-{n}"
                n += 1
            start = 1
        revisions: list[ProposedRevision] = []
        head_revision = item.head_revision if item is not None else 0
        head_node_id = str(item.head_node_id) if item and item.head_node_id else None
        for offset, (member, outcome, reason) in enumerate(outcomes):
            number = start + offset
            if outcome == "rejected":
                status = "rejected"
            elif member is head_member:
                status = "head"
                head_revision, head_node_id = number, str(member.node.id)
            else:
                status = "approved"
            revisions.append(
                ProposedRevision(
                    node_id=str(member.node.id),
                    name=member.name,
                    created_at=_iso(member.node),
                    revision=number,
                    status=status,
                    status_reason=(reason or f"run {member.run_id} was rejected or failed")
                    if status == "rejected"
                    else None,
                    run_id=member.run_id,
                    rule=member.rule,
                    evidence=member.evidence,
                )
            )
        if not revisions:
            return None
        # The first member of a fresh group anchors it; say so instead of "no match".
        if item is None and revisions[0].rule == "new_item" and len(revisions) > 1:
            revisions[0].evidence = "the oldest node of this group"
        if item is not None:
            group.rules.add("existing_item")
        rules = [r for r in RULES if r in group.rules] or ["new_item"]
        review = list(dict.fromkeys(group.review))
        if head_member is None and item is None:
            review.append("every revision is from a rejected run; the item gets no head")
        head_name = (
            head_member.name
            if head_member is not None
            else (item.name if item else members[-1].name)
        )
        return ProposedItem(
            key=key,
            item_type=(head_member.item_type if head_member else item_type),
            name=head_name,
            existing_item=item is not None,
            rule=rules[0],
            rules=rules,
            confidence="low" if review else "high",
            review_reasons=review,
            head_node_id=head_node_id,
            head_revision=head_revision,
            revisions=revisions,
        )

    async def _plan_records(
        self,
        snap: _Snapshot,
        final: dict[str, tuple[str, int, str]],
        heads: dict[str, str],
    ) -> list[RecordAction]:
        actions: list[RecordAction] = []
        for node in sorted(snap.records, key=lambda n: (_created(n), str(n.id))):
            meta = dict(getattr(node, "metadata", None) or {})
            kind = _record_type(node)
            name = _name(node)
            if kind == "design_decision":
                if is_run_summary(name) and not meta.get("run_summary"):
                    actions.append(
                        RecordAction(
                            node_id=str(node.id),
                            name=name,
                            record_type=kind,
                            action="mark_run_summary",
                            reason="titled as a phase summary: a run summary, not a decision",
                        )
                    )
                continue
            geometry = await self._analysed(node, meta, final)
            if geometry is None:
                continue
            key, revision, item_type = final[geometry]
            head = heads.get(key)
            stale = head is not None and head != geometry
            staleness = STALE if stale else CURRENT
            already = any(str(p.get("node_id")) == geometry for p in record_pins(meta))
            if already and not (stale and record_staleness(meta) == CURRENT):
                # Pinned already (at write time, or by an earlier apply) and
                # nothing this plan does changes that: FORGE-527 keeps its
                # staleness up to date. Only a head this plan moves re-marks it.
                continue
            head_rev = next(
                (rev for nid, (k, rev, _t) in final.items() if k == key and nid == head), None
            )
            reason = (
                f"analysed {key}@{revision}; {key} is now @{head_rev}"
                if stale
                else f"analysed {key}@{revision}, the current revision"
            )
            actions.append(
                RecordAction(
                    node_id=str(node.id),
                    name=name,
                    record_type=str(kind),
                    action="pin",
                    item_ref=f"{key}@{revision}",
                    pinned_node_id=geometry,
                    item_type=item_type,
                    staleness=staleness,
                    reason=reason,
                )
            )
        return actions

    async def _analysed(
        self, node: Any, meta: dict[str, Any], final: dict[str, tuple[str, int, str]]
    ) -> str | None:
        """The geometry revision node a simulation result analysed, if it resolves."""
        pin = meta.get("analysed_geometry")
        raw = (pin.get("node_id") if isinstance(pin, dict) else None) or meta.get(
            "analysed_geometry_node_id"
        )
        if raw and str(raw) in final:
            return str(raw)
        for edge_type in _EVIDENCE_EDGES:
            edges = await self.twin.graph.get_edges(
                node.id, direction="outgoing", edge_type=edge_type
            )
            for edge in sorted(edges, key=lambda e: str(e.target_id)):
                target = str(edge.target_id)
                if target in final and final[target][2] in {"cad_model", "assembly"}:
                    return target
        return None

    # -- applying -----------------------------------------------------------

    async def apply(self, plan: MigrationPlan, *, applied_by: str = "") -> MigrationResult:
        """Apply ``plan`` exactly. Refused if it was altered or the twin changed since."""
        with tracer.start_as_current_span("twin.items.migration.apply") as span:
            span.set_attribute("project.id", plan.project_id)
            if plan.content_hash() != plan.plan_hash:
                logger.warning("item_migration_plan_tampered", project_id=plan.project_id)
                raise PlanTamperedError(
                    "this plan's content does not match its plan_hash; pass back the plan "
                    "exactly as the dry run returned it"
                )
            fresh = await self.plan(plan.project_id)
            if fresh.plan_hash != plan.plan_hash:
                span.set_attribute("migration.outcome", "stale")
                logger.warning(
                    "item_migration_plan_stale",
                    project_id=plan.project_id,
                    plan_hash=plan.plan_hash,
                    fresh_hash=fresh.plan_hash,
                )
                raise StalePlanError(
                    "the twin changed since this plan was made (its plan_hash no longer "
                    "matches); run the dry run again and review the new plan"
                )
            result = MigrationResult(
                project_id=plan.project_id, plan_hash=plan.plan_hash, applied=True
            )
            pid = UUID(plan.project_id)
            stamp = {
                "migrated": True,
                "migration": plan.plan_hash[:16],
                "migrated_at": datetime.now(UTC).isoformat(),
                "migrated_by": applied_by or None,
            }
            for entry in plan.items:
                try:
                    await self._apply_item(entry, pid, stamp, applied_by)
                except Exception as exc:  # noqa: BLE001 - one item never stops the rest
                    span.record_exception(exc)
                    logger.error(
                        "item_migration_item_failed",
                        project_id=plan.project_id,
                        item_key=entry.key,
                        error=str(exc),
                    )
                    _count(entry.item_type, "item", "failed")
                    result.failures.append({"key": entry.key, "error": str(exc)})
                    continue
                bucket = result.by_type.setdefault(entry.item_type, {"items": 0, "revisions": 0})
                linked = len(entry.new_revisions)
                bucket["revisions"] += linked
                result.revisions_linked += linked
                if entry.existing_item:
                    result.items_extended += 1
                else:
                    bucket["items"] += 1
                    result.items_created += 1
                    _count(entry.item_type, "item")
                _count(entry.item_type, "revision", count=linked)
            for action in plan.records:
                try:
                    await self._apply_record(action, stamp)
                except Exception as exc:  # noqa: BLE001
                    span.record_exception(exc)
                    logger.error(
                        "item_migration_record_failed", node_id=action.node_id, error=str(exc)
                    )
                    result.failures.append({"node_id": action.node_id, "error": str(exc)})
                    continue
                if action.action == "mark_run_summary":
                    result.run_summaries_marked += 1
                else:
                    result.records_pinned += 1
                    result.records_stale += action.staleness == STALE
            span.set_attribute("migration.items_created", result.items_created)
            span.set_attribute("migration.revisions", result.revisions_linked)
            span.set_attribute("migration.failures", len(result.failures))
            logger.info(
                "item_migration_applied",
                project_id=plan.project_id,
                plan_hash=plan.plan_hash,
                applied_by=applied_by or None,
                items_created=result.items_created,
                items_extended=result.items_extended,
                revisions=result.revisions_linked,
                run_summaries=result.run_summaries_marked,
                pinned=result.records_pinned,
                stale=result.records_stale,
                failures=len(result.failures),
            )
            return result

    async def _apply_item(
        self, entry: ProposedItem, pid: UUID, stamp: dict[str, Any], author: str
    ) -> None:
        twin = self.twin
        now = datetime.now(UTC)
        head_id = UUID(entry.head_node_id) if entry.head_node_id else None
        last = max((r.revision for r in entry.revisions), default=0)
        if entry.existing_item:
            items = await list_items(twin, project_id=pid, include_unheaded=True)
            item = next((i for i in items if i.key == entry.key), None)
            if item is None:
                raise MigrationError(f"item {entry.key} disappeared before apply")
            old_head = item.head_node_id
        else:
            item = Item(
                key=entry.key,
                item_type=entry.item_type,
                name=entry.name,
                project_id=pid,
                head_revision=entry.head_revision,
                head_node_id=head_id,
                last_revision=last,
                created_at=now,
                updated_at=now,
                created_by=author or "item-migration",
            )
            await twin.graph.add_node(item)
            old_head = None
        for rev in entry.new_revisions:
            meta: dict[str, Any] = {
                "revision": rev.revision,
                "adopted": True,
                "status": _EDGE_STATUS["rejected"]
                if rev.status == "rejected"
                else ("committed" if rev.run_id is None else "approved"),
                "run_id": rev.run_id,
                "created_at": rev.created_at,
                "change_reason": f"migrated: {rev.rule} ({rev.evidence})"[:1000],
                **stamp,
            }
            if rev.status == "rejected":
                meta["status_reason"] = rev.status_reason
            await twin.add_edge(UUID(rev.node_id), item.id, EdgeType.REVISION_OF, metadata=meta)
        # SUPERSEDES between consecutive accepted revisions, where missing.
        accepted = [r for r in entry.revisions if r.status != "rejected"]
        for older, newer in zip(accepted, accepted[1:]):
            if older.existing and newer.existing:
                continue
            src, dst = UUID(newer.node_id), UUID(older.node_id)
            edges = await twin.graph.get_edges(
                src, direction="outgoing", edge_type=EdgeType.SUPERSEDES
            )
            if all(e.target_id != dst for e in edges):
                await twin.add_edge(src, dst, EdgeType.SUPERSEDES)
        if entry.existing_item:
            updates: dict[str, Any] = {
                "last_revision": max(item.last_revision, item.head_revision, last),
                "updated_at": now,
            }
            if head_id is not None and head_id != old_head:
                updates.update(
                    {
                        "head_revision": entry.head_revision,
                        "head_node_id": head_id,
                        "name": entry.name,
                        "item_type": entry.item_type,
                    }
                )
                if old_head is not None:
                    await twin.remove_edge(item.id, old_head, EdgeType.HEAD)
                await twin.add_edge(item.id, head_id, EdgeType.HEAD)
            await twin.graph.update_node(item.id, updates)
        elif head_id is not None:
            await twin.add_edge(item.id, head_id, EdgeType.HEAD)
        logger.info(
            "item_migration_item_applied",
            item_key=entry.key,
            item_type=entry.item_type,
            existing=entry.existing_item,
            revisions=len(entry.new_revisions),
            head_revision=entry.head_revision,
            rule=entry.rule,
        )

    async def _apply_record(self, action: RecordAction, stamp: dict[str, Any]) -> None:
        twin = self.twin
        node_id = UUID(action.node_id)
        node = await twin.graph.get_node(node_id)
        if node is None:
            raise MigrationError(f"record {node_id} disappeared before apply")
        meta = dict(getattr(node, "metadata", None) or {})
        if action.action == "mark_run_summary":
            meta.update({"run_summary": True, "record_kind": "run_summary", **stamp})
            await twin.graph.update_node(node_id, {"metadata": meta})
            return
        assert action.item_ref and action.pinned_node_id
        key, _, raw_rev = action.item_ref.rpartition("@")
        pin = ItemPin(key, int(raw_rev), UUID(action.pinned_node_id), action.item_type or "")
        others = [p for p in record_pins(meta) if str(p.get("node_id")) != action.pinned_node_id]
        meta[PINS_KEY] = [*others, pin.to_json()]
        meta[STALENESS_KEY] = action.staleness or CURRENT
        if action.staleness == STALE:
            meta["staleness_reason"] = action.reason
            meta["stale_for"] = pin.ref
        else:
            meta.pop("staleness_reason", None)
            meta.pop("stale_for", None)
        meta["migration_pin"] = stamp["migration"]
        updates: dict[str, Any] = {"metadata": meta}
        # A pin is not new content: keep updated_at, as record_pins does.
        if getattr(node, "updated_at", None) is not None:
            updates["updated_at"] = node.updated_at
        await twin.graph.update_node(node_id, updates)
        edges = await twin.graph.get_edges(
            node_id, direction="outgoing", edge_type=EdgeType.DEPENDS_ON
        )
        if not any(
            e.target_id == pin.node_id and (e.metadata or {}).get("kind") in PIN_EDGE_KINDS
            for e in edges
        ):
            # The same edge record_pins.link_record writes, so FORGE-527's
            # staleness walk finds it when the item's head moves later.
            await twin.add_edge(
                node_id,
                pin.node_id,
                EdgeType.DEPENDS_ON,
                metadata={
                    "kind": PIN_EDGE_KIND,
                    "item_key": pin.item_key,
                    "revision": pin.revision,
                    "item_ref": pin.ref,
                    "migrated": True,
                },
            )


# ---------------------------------------------------------------------------
# Counts and the readable report
# ---------------------------------------------------------------------------


def _counts(snap: _Snapshot, plan: MigrationPlan) -> tuple[dict[str, Any], dict[str, Any]]:
    planned = {r.node_id for p in plan.items for r in p.new_revisions}
    before: dict[str, Any] = {"types": {}, "records": {}}
    after: dict[str, Any] = {"types": {}, "records": {}}
    for item_type in MIGRATED_TYPES:
        nodes = snap.definitions[item_type]
        items = [i for i in snap.items if i.item_type == item_type]
        unlinked = [n for n in nodes if n.id not in snap.linked]
        before["types"][item_type] = {
            "nodes": len(nodes),
            "items": len(items),
            "unlinked": len(unlinked),
        }
        new_items = [p for p in plan.items if p.item_type == item_type and not p.existing_item]
        after["types"][item_type] = {
            "nodes": len(nodes),
            "items": len(items) + len(new_items),
            "unlinked": len([n for n in unlinked if str(n.id) not in planned]),
        }
    decisions = [n for n in snap.records if _record_type(n) == "design_decision"]
    sims = [n for n in snap.records if _record_type(n) == "simulation_result"]
    flagged = [n for n in decisions if (getattr(n, "metadata", None) or {}).get("run_summary")]
    pinned = [n for n in sims if record_pins(getattr(n, "metadata", None))]
    stale = [n for n in sims if record_staleness(getattr(n, "metadata", None)) == STALE]
    marks = sum(1 for r in plan.records if r.action == "mark_run_summary")
    pin_actions = [r for r in plan.records if r.action == "pin"]
    pinned_after = {str(n.id) for n in pinned} | {r.node_id for r in pin_actions}
    stale_after = ({str(n.id) for n in stale} - {r.node_id for r in pin_actions}) | {
        r.node_id for r in pin_actions if r.staleness == STALE
    }
    before["records"] = {
        "design_decisions": len(decisions),
        "decisions_listed": len(decisions) - len(flagged),
        "run_summaries": len(flagged),
        "simulation_results": len(sims),
        "pinned": len(pinned),
        "stale": len(stale),
    }
    after["records"] = {
        "design_decisions": len(decisions),
        "decisions_listed": len(decisions) - len(flagged) - marks,
        "run_summaries": len(flagged) + marks,
        "simulation_results": len(sims),
        "pinned": len(pinned_after),
        "stale": len(stale_after),
    }
    return before, after


def render_report(plan: MigrationPlan) -> str:
    """The plan as a readable table for review."""
    lines = [
        f"Item migration plan for project {plan.project_id}",
        f"plan_hash {plan.plan_hash}",
        "",
    ]
    if plan.empty:
        lines.append("Nothing to migrate: every definition is already an item revision.")
    for entry in plan.items:
        flag = "  [REVIEW]" if entry.confidence == "low" else ""
        verb = "extend" if entry.existing_item else "new"
        lines.append(
            f"{entry.key} ({entry.item_type}, {verb}, rule {entry.rule}, "
            f"head @{entry.head_revision}) '{entry.name}'{flag}"
        )
        for reason in entry.review_reasons:
            lines.append(f"    review: {reason}")
        lines.append(
            f"    {'rev':>4}  {'status':<9} {'created_at':<26} {'node_id':<36}  name / why"
        )
        for rev in entry.revisions:
            lines.append(
                f"    @{rev.revision:<3}  {rev.status:<9} {(rev.created_at or '-')[:26]:<26} "
                f"{rev.node_id:<36}  {rev.name}"
            )
            why = f"{rev.rule}: {rev.evidence}"
            if rev.status_reason:
                why += f"; rejected: {rev.status_reason}"
            lines.append(f"{'':>83}{why}")
        lines.append("")
    if plan.records:
        lines.append("Records")
        for action in plan.records:
            detail = (
                f"pin {action.item_ref} ({action.staleness})"
                if action.action == "pin"
                else "mark run summary"
            )
            lines.append(f"  {action.record_type:<18} {action.node_id}  {detail}: {action.name}")
        lines.append("")
    if plan.skipped:
        lines.append("Skipped")
        for skip in plan.skipped:
            lines.append(f"  {skip['node_id']}  {skip['name']}: {skip['reason']}")
        lines.append("")
    lines.append(
        f"{'type':<18} {'nodes':>6} {'items before':>13} {'items after':>12} {'unlinked after':>15}"
    )
    for item_type, before in plan.counts_before.get("types", {}).items():
        after = plan.counts_after["types"][item_type]
        if not before["nodes"] and not before["items"]:
            continue
        lines.append(
            f"{item_type:<18} {before['nodes']:>6} {before['items']:>13} "
            f"{after['items']:>12} {after['unlinked']:>15}"
        )
    rec_b, rec_a = plan.counts_before.get("records", {}), plan.counts_after.get("records", {})
    if rec_b:
        lines.append(
            f"decisions listed {rec_b['decisions_listed']} -> {rec_a['decisions_listed']}; "
            f"run summaries {rec_b['run_summaries']} -> {rec_a['run_summaries']}; "
            f"simulation results pinned {rec_b['pinned']} -> {rec_a['pinned']}, "
            f"stale {rec_b['stale']} -> {rec_a['stale']}"
        )
    if plan.low_confidence:
        lines.append(f"{len(plan.low_confidence)} grouping(s) flagged for review.")
    return "\n".join(lines)
