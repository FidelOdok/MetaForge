"""The cross-domain impact walker (FORGE-793; FRS XDM-002..007, CHG-001, CHG-004).

Given a change to one item (``KEY`` from ``@old`` to ``@new``, and the changed
parameters when they are known), walk the project's interface edges
(:mod:`twin_core.impact.interfaces`) and report:

* **what is affected**, per domain, directly (one edge away) or indirectly
  (further), each with a plain rationale ("mounting hole positions changed;
  the PCB's mounting depends on them") and a proposed action;
* **which results are out of date**: records pinned to the item's older
  revisions (the FORGE-527 pins, which :mod:`record_pins` already marks), and
  records of a directly affected item whose check read the changed item
  across the interface (a firmware build reads the pin map; a DRC run reads
  the enclosure's keep-outs). The second kind is marked ``STALE`` here, tied
  to the input revision it was valid against. Nothing is deleted or relabelled
  a pass; an old result stays browsable as history;
* **which reruns to propose** (``kicad.run_drc``, ``firmware.build``,
  ``calculix.run_fea``, ...), each flagged consequential or not. Nothing is
  run here: the gateway decides which may run unattended under the project's
  approval policy and asks a person for the rest (XDM-007);
* **what is not affected** when asked (XDM-006): every other item, and every
  edge whose coupled parameters the change did not touch.

The walk is bounded (``max_depth``, at most :data:`MAX_DEPTH_CAP`) and
detects cycles. It builds on the existing engines rather than beside them:
record pins (``record_pins.pinned_records``) for what a revision's results
are, and :meth:`StalenessEngine.preview_impact` for the requirement and
engineering-entity pins FORGE-59 already tracks.

Only a confirmed edge (a person's, a tool's, or a confirmed inference) marks
results stale or proposes reruns. A proposed edge is reported as a possible
impact with a "confirm or remove this link" action, so an unconfirmed guess
never invalidates anything.

:func:`on_item_head_moved` runs this for every head move (from
``record_pins.on_head_moved``) and records the outcome as a ``change_impact``
work product. Two seams let the gateway add what twin_core may not import:
:func:`set_impact_hooks` (the approval policy and the operation event) and
:func:`set_parameter_extractor` (parameter-level diffs it can read from
blobs, such as a pin map's pins).
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer
from twin_core.consistency.validation_status import ValidationStatus, verdict_of_result
from twin_core.impact.interfaces import list_interfaces
from twin_core.models.enums import EdgeType, WorkProductType
from twin_core.models.interface import (
    InterfaceEdge,
    InterfaceStatus,
    InterfaceType,
    domain_for_item_type,
    is_record_ref,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.impact.cross_domain")

DEFAULT_MAX_DEPTH = 4
MAX_DEPTH_CAP = 8
#: Where a ``change_impact`` node keeps its result.
IMPACT_META_KEY = "impact"

#: What re-runs a check of each kind.
CHECK_TOOLS: dict[str, str] = {
    "drc": "kicad.run_drc",
    "erc": "kicad.run_erc",
    "firmware_build": "firmware.build",
    "firmware_static": "firmware.static_check",
    "firmware_test": "firmware.run_emulated",
    "fea": "calculix.run_fea",
    "thermal": "calculix.run_thermal",
}
#: Checks that only read. The gateway confirms each against the tool
#: annotations before letting one run unattended.
READ_ONLY_CHECKS = frozenset({"drc", "erc", "firmware_static"})
#: Checks whose rerun changes firmware or board outputs: a person decides.
CONSEQUENTIAL_CHECKS = frozenset({"firmware_build", "firmware_test"})

#: Which of a directly affected item's results an edge type invalidates.
RELEVANT_CHECKS: dict[InterfaceType, frozenset[str]] = {
    InterfaceType.MOUNTS_TO: frozenset({"drc", "clearance"}),
    InterfaceType.CLEARANCE_TO: frozenset({"drc", "clearance"}),
    InterfaceType.CONNECTS_VIA: frozenset({"drc", "clearance"}),
    InterfaceType.PIN_MAPS_TO: frozenset({"firmware_build", "firmware_static", "firmware_test"}),
    InterfaceType.SIMULATION_INPUT_OF: frozenset({"fea", "thermal"}),
    InterfaceType.FEATURE_OF: frozenset(),
}

#: The re-check to propose for a directly affected item that has no result
#: to re-run yet, by edge type and the item's domain.
DEFAULT_CHECK: dict[tuple[InterfaceType, str], str] = {
    (InterfaceType.MOUNTS_TO, "electronics"): "drc",
    (InterfaceType.CLEARANCE_TO, "electronics"): "drc",
    (InterfaceType.CONNECTS_VIA, "electronics"): "drc",
    (InterfaceType.PIN_MAPS_TO, "firmware"): "firmware_build",
}

#: What crosses each kind of interface, for the rationale.
_COUPLING_WORDS: dict[InterfaceType, str] = {
    InterfaceType.MOUNTS_TO: "mounting hole positions",
    InterfaceType.CLEARANCE_TO: "the outline and keep-out envelope",
    InterfaceType.CONNECTS_VIA: "the connector position",
    InterfaceType.PIN_MAPS_TO: "the pin assignment",
    InterfaceType.SIMULATION_INPUT_OF: "the analysed geometry, material or load",
    InterfaceType.FEATURE_OF: "the sketch it was built from",
}
#: What depends on it, on the affected side.
_DEPENDENT_WORDS: dict[InterfaceType, str] = {
    InterfaceType.MOUNTS_TO: "mounting depends on them",
    InterfaceType.CLEARANCE_TO: "clearance depends on it",
    InterfaceType.CONNECTS_VIA: "cut-out or cable must follow it",
    InterfaceType.PIN_MAPS_TO: "modules and drivers use those pins",
    InterfaceType.SIMULATION_INPUT_OF: "was computed from them",
    InterfaceType.FEATURE_OF: "was made from that sketch",
}
#: The proposed action per edge type when no tool re-check applies.
_REVIEW_ACTIONS: dict[InterfaceType, tuple[str, str]] = {
    InterfaceType.MOUNTS_TO: ("review_mounting", "Review the mounting"),
    InterfaceType.CLEARANCE_TO: ("review_clearance", "Review the clearance"),
    InterfaceType.CONNECTS_VIA: ("review_clearance", "Review the connector fit"),
    InterfaceType.PIN_MAPS_TO: ("review_firmware", "Review the firmware pin use"),
    InterfaceType.SIMULATION_INPUT_OF: ("review_simulation", "Review the simulation inputs"),
    InterfaceType.FEATURE_OF: ("review_feature", "Regenerate the feature from the sketch"),
}

#: Metadata keys that describe bookkeeping, not the design.
_BOOKKEEPING_KEYS = frozenset(
    {
        "item_key",
        "item_revision",
        "item_type",
        "change_reason",
        "run_id",
        "change_set",
        "revision_author",
        "staleness",
        "staleness_reason",
        "stale_for",
        "superseded_by",
        "depends_on_items",
        "approved",
        "approved_at",
        "approved_by",
        "content_sha256",
        "minio_object_key",
        "design_files",
        "image_files",
        "preview_files",
        "files",
        "file_count",
        "total_bytes",
        "source_path",
        "original_filename",
        "source_work_product_ids",
        "source_inferred",
        "store_error",
        "preview_error",
        "flow_slot_key",
    }
)

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-twin-impact")
    return _metrics


def count_impact(event: str, kind: str = "none") -> None:
    """Count one impact event.

    ``previewed``, ``recorded``, ``skipped`` (a head move that reached
    nothing), ``failed``, ``result_invalidated``, ``rerun_proposed``.
    """
    try:
        _collector().record_cross_domain_impact(event, kind)
    except Exception:  # noqa: BLE001 - metrics never break a write  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------


class ChangeSpec(BaseModel):
    """The change being analysed."""

    item_key: str
    old_revision: int | None = None
    new_revision: int | None = None
    #: Names of what changed (``mounting_holes``, pin ``PA5``). ``None`` means
    #: unknown, which never narrows the walk.
    changed_parameters: list[str] | None = None

    @property
    def old_ref(self) -> str:
        return f"{self.item_key}@{self.old_revision}" if self.old_revision else self.item_key

    @property
    def new_ref(self) -> str:
        return f"{self.item_key}@{self.new_revision}" if self.new_revision else self.item_key


class Coupling(BaseModel):
    """One interface edge by which the change reaches an entity."""

    edge_id: str
    edge_type: InterfaceType
    edge_status: InterfaceStatus
    rationale: str


class ImpactedEntity(BaseModel):
    """One item or result the change reaches."""

    ref: str
    label: str = ""
    domain: str = "general"
    item_type: str = ""
    #: The entity's current head revision (``None`` for a result record).
    revision: int | None = None
    depth: int
    direct: bool
    edge_id: str
    edge_type: InterfaceType
    edge_status: InterfaceStatus
    #: The refs from the changed item to this one.
    via: list[str] = Field(default_factory=list)
    rationale: str
    proposed_action: str
    #: The coupled parameters the change touched, when both are known.
    parameters: list[str] = Field(default_factory=list)
    #: Firmware files or other parts of the item the edge names.
    modules: list[str] = Field(default_factory=list)
    #: False when the path runs through an unconfirmed (proposed) edge.
    certain: bool = True
    #: Every edge from the same parent that reaches it (the first is ``edge_id``).
    couplings: list[Coupling] = Field(default_factory=list)


class StaleResult(BaseModel):
    """A result the change makes out of date. It stays browsable as history."""

    record_id: str
    record_type: str
    check_kind: str
    name: str
    #: The input revision it was valid against (``PIN-MAIN@1``).
    pinned_ref: str
    #: What that input is now (``PIN-MAIN@2``).
    current_ref: str | None = None
    #: The item the result belongs to (its own pins), for a cross-domain one.
    belongs_to: str | None = None
    input_revisions: list[str] = Field(default_factory=list)
    #: What the result said before (``PASS`` stays recorded as what it was).
    previous_status: ValidationStatus = ValidationStatus.NOT_EVALUATED
    status: ValidationStatus = ValidationStatus.STALE
    reason: str
    tool: str | None = None
    #: True once the record carries ``staleness: stale`` (a preview: False).
    marked: bool = False


RerunStatus = Literal["proposed", "queued", "approved", "rejected"]
Disposition = Literal["requires_approval", "may_run_unattended"]


class ProposedRerun(BaseModel):
    """A re-check the change calls for. Proposed, never run here."""

    id: str
    tool: str
    check_kind: str
    target_ref: str
    target_domain: str = "general"
    label: str
    reason: str
    #: A firmware or PCB edit, a producing tool, or a release-bound target.
    consequential: bool = True
    disposition: Disposition = "requires_approval"
    #: Why the disposition is what it is (the policy's words).
    disposition_reason: str = ""
    status: RerunStatus = "proposed"
    #: The stale result this would replace, when there is one.
    record_id: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    decision_reason: str | None = None


class ReviewAction(BaseModel):
    """Something a person should look at that no tool re-checks."""

    kind: str
    target_ref: str
    label: str
    reason: str


class NotAffected(BaseModel):
    ref: str
    label: str = ""
    domain: str = "general"
    reason: str


class GraphNode(BaseModel):
    ref: str
    label: str
    domain: str
    role: Literal["changed", "impacted", "possible", "unaffected", "record"]


class GraphLink(BaseModel):
    source: str
    target: str
    type: InterfaceType
    status: InterfaceStatus
    impacted: bool


class ImpactGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    links: list[GraphLink] = Field(default_factory=list)


class ImpactResult(BaseModel):
    """What one change does across domains (CHG-001's impact graph and effects)."""

    id: str | None = None
    project_id: str
    #: True for a preview: nothing was written and nothing marked.
    preview: bool
    change: ChangeSpec
    source_domain: str = "general"
    source_label: str = ""
    impacted: list[ImpactedEntity] = Field(default_factory=list)
    by_domain: dict[str, list[str]] = Field(default_factory=dict)
    stale_results: list[StaleResult] = Field(default_factory=list)
    proposed_reruns: list[ProposedRerun] = Field(default_factory=list)
    review_actions: list[ReviewAction] = Field(default_factory=list)
    not_affected: list[NotAffected] = Field(default_factory=list)
    #: Each cycle met, as the refs around it.
    cycles: list[list[str]] = Field(default_factory=list)
    depth_limited: bool = False
    max_depth: int = DEFAULT_MAX_DEPTH
    #: The plain sentence Create shows (UXS-015).
    summary: str = ""
    next_action: str = ""
    graph: ImpactGraph = Field(default_factory=ImpactGraph)
    #: The project's approval policy as the gateway read it (CHG-001).
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    operation_id: str | None = None
    created_at: str | None = None

    def transaction(self) -> dict[str, Any]:
        """The CHG-001 change-transaction view of this impact."""
        return {
            "operation": "preview" if self.preview else "head_moved",
            "operation_id": self.operation_id,
            "source_revision": self.change.old_ref,
            "target_revision": self.change.new_ref,
            "targets": [e.ref for e in self.impacted],
            "proposed_effects": {
                "stale_results": [s.record_id for s in self.stale_results],
                "reruns": [r.id for r in self.proposed_reruns],
                "reviews": [a.kind + ":" + a.target_ref for a in self.review_actions],
            },
            "impact_graph": self.graph.model_dump(mode="json"),
            "approval_policy": self.approval_policy,
        }

    def to_view(self) -> dict[str, Any]:
        out = self.model_dump(mode="json")
        out["transaction"] = self.transaction()
        return out


# ---------------------------------------------------------------------------
# Seams the gateway installs
# ---------------------------------------------------------------------------

#: ``(twin, item, old_node, new_node) -> changed parameter names | None``.
ParameterExtractor = Callable[[Any, Any, Any, Any], Awaitable[list[str] | None]]
#: Refines a result before it is recorded (dispositions from the approval policy).
ImpactRefiner = Callable[[Any, ImpactResult], Awaitable[ImpactResult]]
#: Told about a recorded result (publishes the operation event).
ImpactRecorded = Callable[[Any, ImpactResult], Awaitable[None]]

#: Told about every head move before the walk (proposes new links from its data).
HeadMovedHook = Callable[[Any, Any], Awaitable[None]]

_parameter_extractor: ParameterExtractor | None = None
_refiner: ImpactRefiner | None = None
_recorded: ImpactRecorded | None = None
_head_moved: HeadMovedHook | None = None


def set_parameter_extractor(extractor: ParameterExtractor | None) -> None:
    """Install what reads a parameter-level diff the twin cannot (a pin map's pins)."""
    global _parameter_extractor  # noqa: PLW0603
    _parameter_extractor = extractor


def set_impact_hooks(
    *,
    refine: ImpactRefiner | None = None,
    recorded: ImpactRecorded | None = None,
    head_moved: HeadMovedHook | None = None,
) -> None:
    """Install the gateway's policy refiner, recorded-impact listener and head-move hook."""
    global _refiner, _recorded, _head_moved  # noqa: PLW0603
    _refiner = refine
    _recorded = recorded
    _head_moved = head_moved


def impact_refiner() -> ImpactRefiner | None:
    return _refiner


# ---------------------------------------------------------------------------
# Reading records
# ---------------------------------------------------------------------------


def _meta(node: Any) -> dict[str, Any]:
    return dict(getattr(node, "metadata", None) or {})


def _record_type(node: Any) -> str:
    entity_type = getattr(node, "entity_type", None)
    if isinstance(entity_type, str) and entity_type:
        return entity_type
    wp_type = getattr(node, "type", None)
    return str(getattr(wp_type, "value", wp_type) or "")


def _name(node: Any) -> str:
    for attr in ("name", "statement", "title"):
        value = getattr(node, attr, None)
        if isinstance(value, str) and value:
            return value
    return str(getattr(node, "id", ""))


def producer_tool(meta: dict[str, Any]) -> str:
    producer = meta.get("producer")
    if isinstance(producer, dict) and producer.get("tool"):
        return str(producer["tool"])
    return str(meta.get("source_tool") or meta.get("tool") or "")


def classify_check(node: Any) -> str | None:
    """The kind of check a record is (``drc``, ``firmware_build``, ``fea``, ...).

    ``None`` for a record that is not a check. A design decision is
    ``decision``: it goes stale like any pinned record but no tool re-runs it.
    """
    record_type = _record_type(node)
    meta = _meta(node)
    if record_type == "simulation_result":
        analysis = str(meta.get("analysis_type") or meta.get("load_case") or "").lower()
        return "thermal" if "thermal" in analysis else "fea"
    if record_type == "design_decision":
        return "decision"
    if record_type != "evidence":
        return None
    tool = producer_tool(meta).lower()
    if "drc" in tool:
        return "drc"
    if "erc" in tool:
        return "erc"
    if "firmware" in tool or "platformio" in tool:
        if "static" in tool or "lint" in tool:
            return "firmware_static"
        if "emul" in tool or "test" in tool or "hil" in tool:
            return "firmware_test"
        return "firmware_build"
    if "thermal" in tool:
        return "thermal"
    if "calculix" in tool or "fea" in tool:
        return "fea"
    if any(word in tool for word in ("clearance", "interference", "collision", "fit")):
        return "clearance"
    return "validation"


def _previous_status(node: Any) -> ValidationStatus:
    meta = _meta(node)
    result = meta.get("result") if isinstance(meta.get("result"), dict) else meta
    return verdict_of_result(result)


def _pins_of(node: Any) -> list[str]:
    from twin_core.consistency.record_pins import record_pins

    return [str(p.get("item_ref")) for p in record_pins(_meta(node)) if p.get("item_ref")]


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def _norm(names: Iterable[str]) -> set[str]:
    return {str(n).strip().lower() for n in names if str(n).strip()}


def _touches(edge: InterfaceEdge, changed: list[str] | None) -> tuple[bool, list[str]]:
    """Whether a change with these parameters travels along ``edge``.

    Unknown parameters, or an edge that names none, always travel.
    """
    if changed is None or not edge.coupled_parameters:
        return True, []
    hit = sorted(_norm(changed) & _norm(edge.coupled_parameters))
    return bool(hit), hit


def _rationale(edge: InterfaceEdge, source_label: str, target_label: str, direct: bool) -> str:
    coupling = _COUPLING_WORDS[edge.type]
    dependent = _DEPENDENT_WORDS[edge.type]
    if direct:
        return f"{source_label}: {coupling} changed; {target_label} {dependent}"
    return (
        f"if {source_label} changes to follow, {coupling} may change too; "
        f"{target_label} {dependent}"
    )


def _phrase(entity: ImpactedEntity) -> str:
    if entity.edge_type in (
        InterfaceType.MOUNTS_TO,
        InterfaceType.CLEARANCE_TO,
        InterfaceType.CONNECTS_VIA,
    ):
        return {"electronics": "the PCB fit", "mechanical": "the enclosure fit"}.get(
            entity.domain, f"the fit of {entity.label or entity.ref}"
        )
    if entity.edge_type is InterfaceType.PIN_MAPS_TO:
        return "the firmware"
    if entity.edge_type is InterfaceType.SIMULATION_INPUT_OF:
        return "the simulation results"
    return "what was built from this sketch"


def _summary(result: ImpactResult) -> tuple[str, str]:
    """UXS-015: one plain sentence and one next action."""
    phrases: list[str] = []
    for entity in sorted(result.impacted, key=lambda e: (e.depth, not e.certain)):
        phrase = _phrase(entity)
        if phrase not in phrases:
            phrases.append(phrase)
    if not phrases and result.stale_results:
        phrases.append("results recorded for the previous revision")
    if not phrases:
        return ("This change does not affect anything else MetaForge tracks.", "No action needed.")
    shown = phrases[:2]
    rest = len(phrases) - len(shown)
    text = " and ".join(shown) + (f" and {rest} more" if rest else "")
    pending = [r for r in result.proposed_reruns if r.status == "proposed"]
    if any(r.disposition == "requires_approval" for r in pending):
        action = "Review the proposed re-checks."
    elif result.review_actions:
        action = result.review_actions[0].label + "."
    elif any(not e.certain for e in result.impacted):
        action = "Confirm or remove the suggested link."
    elif pending:
        action = "The re-checks can run without a decision."
    else:
        action = "No action needed."
    return (f"This may affect {text}.", action)


async def _release_bound(twin: Any, node_id: Any) -> bool:
    """True when the revision is in a baseline (a release-bound artifact)."""
    if node_id is None:
        return False
    try:
        edges = await twin.graph.get_edges(
            UUID(str(node_id)), direction="outgoing", edge_type=EdgeType.INCLUDED_IN_BASELINE
        )
    except Exception:  # noqa: BLE001 - unknown is treated as release-bound below
        return True
    return bool(edges)


async def _stale_from_own_pins(
    twin: Any, item: Any, change: ChangeSpec, *, preview: bool
) -> list[tuple[Any, StaleResult]]:
    """Records pinned to the changed item's older revisions (FORGE-527 pins)."""
    from twin_core.consistency.record_pins import VALID_STATUSES, pinned_records, record_staleness
    from twin_core.items import item_history

    new_rev = change.new_revision or item.head_revision
    out: list[tuple[Any, StaleResult]] = []
    seen: set[str] = set()
    for rev in await item_history(twin, item):
        if rev.revision >= new_rev:
            continue
        for edge, node in await pinned_records(twin, rev.node_id):
            if str(node.id) in seen:
                continue
            meta = _meta(node)
            status = record_staleness(meta)
            pinned = int((edge.metadata or {}).get("revision") or rev.revision)
            if preview:
                if status not in VALID_STATUSES:
                    continue
            else:
                moved = (meta.get("stale_for") or {}).get(item.key) or {}
                if status != "stale" or int(moved.get("current") or 0) != new_rev:
                    continue
            seen.add(str(node.id))
            check = classify_check(node) or "record"
            out.append(
                (
                    node,
                    StaleResult(
                        record_id=str(node.id),
                        record_type=_record_type(node),
                        check_kind=check,
                        name=_name(node),
                        pinned_ref=f"{item.key}@{pinned}",
                        current_ref=f"{item.key}@{new_rev}",
                        belongs_to=f"{item.key}@{pinned}",
                        input_revisions=_pins_of(node),
                        previous_status=_previous_status(node),
                        reason=f"it was for {item.key}@{pinned}, and {item.key} is now @{new_rev}",
                        tool=CHECK_TOOLS.get(check),
                        marked=not preview,
                    ),
                )
            )
    return out


async def analyse_change(
    twin: Any,
    project_id: UUID | str,
    change: ChangeSpec,
    *,
    preview: bool = True,
    max_depth: int = DEFAULT_MAX_DEPTH,
    include_unaffected: bool = False,
) -> ImpactResult:
    """Walk the interface edges from ``change`` and report its impact.

    ``preview=True`` writes nothing: results that would go stale are listed
    with ``marked: false``. ``preview=False`` is the recorded walk after a
    head moved: a directly affected item's relevant results are marked
    ``STALE`` here (results pinned to the changed item itself were already
    marked by ``record_pins``). Raises ``UnknownItemError`` for an unknown key.
    """
    from twin_core.consistency.record_pins import (
        VALID_STATUSES,
        mark_stale_for_input,
        pinned_records,
        record_staleness,
    )
    from twin_core.items import UnknownItemError, find_item, list_items, parse_item_ref

    pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    depth_cap = max(1, min(int(max_depth), MAX_DEPTH_CAP))
    key, _ = parse_item_ref(change.item_key)
    item = await find_item(twin, key, pid)
    if item is None or item.head_node_id is None:
        raise UnknownItemError(f"no item {key} with a current revision in this project")
    change = change.model_copy(
        update={
            "item_key": item.key,
            "old_revision": change.old_revision or item.head_revision,
            "new_revision": change.new_revision
            or (item.head_revision + 1 if preview else item.head_revision),
        }
    )
    with tracer.start_as_current_span("twin.impact.analyse") as span:
        span.set_attribute("impact.item_key", item.key)
        span.set_attribute("impact.preview", preview)
        span.set_attribute("impact.max_depth", depth_cap)
        items = {i.key: i for i in await list_items(twin, project_id=pid)}
        edges = [
            e for e in await list_interfaces(twin, pid) if e.status is not InterfaceStatus.REMOVED
        ]
        adjacency: dict[str, list[tuple[str, InterfaceEdge]]] = {}
        for edge in edges:
            adjacency.setdefault(edge.source.ref, []).append((edge.target.ref, edge))
            if edge.symmetric:
                adjacency.setdefault(edge.target.ref, []).append((edge.source.ref, edge))

        result = ImpactResult(
            project_id=str(pid),
            preview=preview,
            change=change,
            source_domain=domain_for_item_type(item.item_type),
            source_label=item.name or item.key,
            max_depth=depth_cap,
        )
        labels: dict[str, tuple[str, str]] = {
            k: (i.name or k, domain_for_item_type(i.item_type)) for k, i in items.items()
        }
        for edge in edges:
            for end in (edge.source, edge.target):
                labels.setdefault(end.ref, (end.label or end.ref, end.domain))

        # Results pinned to the changed item's older revisions. A result
        # record reached through a derived edge counts only when it is one of
        # these: a record of a newer run, or one already superseded, is not
        # what this change affects.
        stale: dict[str, StaleResult] = {}
        for _node, view in await _stale_from_own_pins(twin, item, change, preview=preview):
            stale[view.record_id] = view
        own_stale = {f"record:{rid}" for rid in stale}

        # Breadth first, so every entity is reported at its shortest depth.
        parents: dict[str, str | None] = {item.key: None}
        reached: dict[str, ImpactedEntity] = {}
        filtered: dict[str, NotAffected] = {}
        walked: dict[tuple[str, str, str], GraphLink] = {}
        queue: deque[tuple[str, int, str | None, bool]] = deque([(item.key, 0, None, True)])

        def _ancestors(ref: str) -> list[str]:
            chain = [ref]
            while parents.get(chain[-1]) is not None:
                chain.append(str(parents[chain[-1]]))
            return chain

        while queue:
            ref, depth, came_by, certain = queue.popleft()
            for neighbour, edge in adjacency.get(ref, []):
                if edge.id == came_by:
                    continue  # the same symmetric edge, walked back
                if is_record_ref(neighbour) and neighbour not in own_stale:
                    continue
                same_step = reached.get(neighbour)
                if (
                    same_step is not None
                    and parents.get(neighbour) == ref
                    and all(c.edge_id != edge.id for c in same_step.couplings)
                ):
                    # A second interface between the same two items (mounted
                    # and clearance-bound): one entity, both couplings.
                    touches, _hit = _touches(
                        edge, change.changed_parameters if depth == 0 else None
                    )
                    if touches:
                        same_step.couplings.append(
                            Coupling(
                                edge_id=edge.id,
                                edge_type=edge.type,
                                edge_status=edge.status,
                                rationale=_rationale(
                                    edge,
                                    labels.get(ref, (ref, ""))[0],
                                    same_step.label,
                                    same_step.direct,
                                ),
                            )
                        )
                        same_step.certain = same_step.certain or (certain and edge.certain)
                        walked[(edge.source.ref, edge.target.ref, edge.type.value)] = GraphLink(
                            source=edge.source.ref,
                            target=edge.target.ref,
                            type=edge.type,
                            status=edge.status,
                            impacted=True,
                        )
                    continue
                if neighbour in parents:
                    chain = _ancestors(ref)
                    if neighbour in chain:
                        cycle = list(reversed(chain[: chain.index(neighbour) + 1])) + [neighbour]
                        if cycle not in result.cycles:
                            result.cycles.append(cycle)
                    continue
                if depth >= depth_cap:
                    result.depth_limited = True
                    continue
                touches, hit = _touches(edge, change.changed_parameters if depth == 0 else None)
                if not touches:
                    filtered.setdefault(
                        neighbour,
                        NotAffected(
                            ref=neighbour,
                            label=labels.get(neighbour, (neighbour, ""))[0],
                            domain=labels.get(neighbour, ("", "general"))[1],
                            reason=(
                                f"linked by {edge.type.value}, but none of its coupled "
                                f"parameters ({', '.join(edge.coupled_parameters)}) changed"
                            ),
                        ),
                    )
                    continue
                parents[neighbour] = ref
                path_certain = certain and edge.certain
                target_item = items.get(neighbour)
                target_end = edge.target if edge.target.ref == neighbour else edge.source
                src_label = labels.get(ref, (ref, ""))[0]
                dst_label, dst_domain = labels.get(neighbour, (neighbour, target_end.domain))
                direct = depth == 0
                kind, label = _REVIEW_ACTIONS[edge.type]
                if not path_certain:
                    action = "Confirm or remove the suggested link"
                else:
                    check = DEFAULT_CHECK.get((edge.type, dst_domain))
                    action = (
                        f"Re-run {CHECK_TOOLS[check]}"
                        if check and direct
                        else label
                        if direct
                        else f"{label} if {src_label} changes"
                    )
                    if edge.type is InterfaceType.SIMULATION_INPUT_OF and direct:
                        action = "Re-run the simulation on the new revision"
                evidence_modules = edge.evidence.get("modules") if edge.evidence else None
                rationale = _rationale(edge, src_label, dst_label, direct)
                reached[neighbour] = ImpactedEntity(
                    ref=neighbour,
                    label=dst_label,
                    domain=dst_domain,
                    item_type=target_item.item_type if target_item else target_end.item_type,
                    revision=target_item.head_revision if target_item else None,
                    depth=depth + 1,
                    direct=direct,
                    edge_id=edge.id,
                    edge_type=edge.type,
                    edge_status=edge.status,
                    via=list(reversed(_ancestors(ref))) + [neighbour],
                    rationale=rationale,
                    proposed_action=action,
                    parameters=hit,
                    modules=[str(m) for m in evidence_modules]
                    if isinstance(evidence_modules, list)
                    else [],
                    certain=path_certain,
                    couplings=[
                        Coupling(
                            edge_id=edge.id,
                            edge_type=edge.type,
                            edge_status=edge.status,
                            rationale=rationale,
                        )
                    ],
                )
                walked[(edge.source.ref, edge.target.ref, edge.type.value)] = GraphLink(
                    source=edge.source.ref,
                    target=edge.target.ref,
                    type=edge.type,
                    status=edge.status,
                    impacted=True,
                )
                if not is_record_ref(neighbour):
                    queue.append((neighbour, depth + 1, edge.id, path_certain))

        result.impacted = sorted(reached.values(), key=lambda e: (e.depth, e.domain, e.ref))

        # -- results of directly affected items, read across the interface ---
        for entity in result.impacted:
            if not entity.direct or not entity.certain or is_record_ref(entity.ref):
                continue
            target_item = items.get(entity.ref)
            if target_item is None or target_item.head_node_id is None:
                continue
            # Only confirmed couplings invalidate; a proposal marks nothing.
            relevant: dict[str, InterfaceType] = {}
            for coupling in entity.couplings:
                if coupling.edge_status is InterfaceStatus.CONFIRMED:
                    for check_kind in RELEVANT_CHECKS[coupling.edge_type]:
                        relevant.setdefault(check_kind, coupling.edge_type)
            for _edge, node in await pinned_records(twin, target_item.head_node_id):
                if str(node.id) in stale:
                    continue
                check = classify_check(node)
                if check is None or check not in relevant:
                    continue
                meta = _meta(node)
                old_ref = change.old_ref
                reason = (
                    f"it read {old_ref} through {relevant[check].value}, and "
                    f"{item.key} is now @{change.new_revision}"
                )
                own = f"{target_item.key}@{target_item.head_revision}"
                if preview:
                    if record_staleness(meta) not in VALID_STATUSES:
                        continue
                    marked = False
                else:
                    moved = (meta.get("stale_for") or {}).get(item.key) or {}
                    if int(moved.get("current") or 0) == change.new_revision:
                        marked = True  # an earlier walk for this same move marked it
                    else:
                        done = await mark_stale_for_input(
                            twin,
                            node,
                            input_key=item.key,
                            pinned=int(change.old_revision or 0),
                            current=int(change.new_revision or 0),
                            reason=reason,
                        )
                        if done is None:
                            continue
                        marked = True
                        count_impact("result_invalidated", check)
                stale[str(node.id)] = StaleResult(
                    record_id=str(node.id),
                    record_type=_record_type(node),
                    check_kind=check,
                    name=_name(node),
                    pinned_ref=old_ref,
                    current_ref=change.new_ref,
                    belongs_to=own,
                    input_revisions=_pins_of(node),
                    previous_status=_previous_status(node),
                    reason=reason,
                    tool=CHECK_TOOLS.get(check),
                    marked=marked,
                )
        result.stale_results = sorted(stale.values(), key=lambda s: (s.check_kind, s.name))

        # -- reruns and reviews ----------------------------------------------
        reruns: dict[tuple[str, str], ProposedRerun] = {}

        async def _propose(
            check: str, target_ref: str, target_domain: str, reason: str, record_id: str | None
        ) -> None:
            tool = CHECK_TOOLS.get(check)
            if tool is None or (tool, target_ref) in reruns:
                return
            target_item = items.get(target_ref.split("@")[0])
            release_bound = await _release_bound(
                twin, target_item.head_node_id if target_item else None
            )
            # XDM-007: firmware and PCB edits and release-bound targets always
            # wait for a person. A read-only check, or an analysis that edits
            # no design, is left to the approval policy (the gateway decides).
            consequential = (
                release_bound
                or check in CONSEQUENTIAL_CHECKS
                or (target_domain in ("electronics", "firmware") and check not in READ_ONLY_CHECKS)
            )
            reruns[(tool, target_ref)] = ProposedRerun(
                id=f"r{len(reruns) + 1}",
                tool=tool,
                check_kind=check,
                target_ref=target_ref,
                target_domain=target_domain,
                label=f"Re-run {tool} on {target_ref}",
                reason=reason + ("; the target is in a baseline" if release_bound else ""),
                consequential=consequential,
                record_id=record_id,
            )

        for view in result.stale_results:
            target_ref = (view.belongs_to or view.pinned_ref).split("@")[0]
            domain = labels.get(target_ref, (target_ref, "general"))[1]
            if view.record_type == "simulation_result":
                domain = "simulation"
            await _propose(view.check_kind, target_ref, domain, view.reason, view.record_id)
        reviews: dict[tuple[str, str], ReviewAction] = {}
        for entity in result.impacted:
            if not entity.direct:
                continue
            for coupling in entity.couplings:
                if coupling.edge_status is not InterfaceStatus.CONFIRMED:
                    reviews.setdefault(
                        ("confirm_link", f"{entity.ref}:{coupling.edge_type.value}"),
                        ReviewAction(
                            kind="confirm_link",
                            target_ref=entity.ref,
                            label="Confirm or remove the suggested link",
                            reason=f"{coupling.edge_type.value} to {entity.label} was inferred, "
                            "not confirmed; it marks nothing stale until it is",
                        ),
                    )
                    continue
                check = DEFAULT_CHECK.get((coupling.edge_type, entity.domain))
                if check is not None:
                    await _propose(check, entity.ref, entity.domain, coupling.rationale, None)
                if coupling.edge_type in (
                    InterfaceType.MOUNTS_TO,
                    InterfaceType.CLEARANCE_TO,
                    InterfaceType.CONNECTS_VIA,
                    InterfaceType.FEATURE_OF,
                ):
                    kind, label = _REVIEW_ACTIONS[coupling.edge_type]
                    reviews.setdefault(
                        (kind, entity.ref),
                        ReviewAction(
                            kind=kind,
                            target_ref=entity.ref,
                            label=label,
                            reason=coupling.rationale,
                        ),
                    )
        result.proposed_reruns = list(reruns.values())
        result.review_actions = list(reviews.values())

        # -- requirement and entity pins (FORGE-59) ---------------------------
        await _merge_entity_pins(twin, pid, item, change, result)

        # -- by domain, unaffected, graph, summary -----------------------------
        by_domain: dict[str, list[str]] = {}
        for entity in result.impacted:
            by_domain.setdefault(entity.domain, []).append(entity.ref)
        result.by_domain = by_domain
        if include_unaffected:
            out = list(filtered.values())
            for ref, other in items.items():
                if ref == item.key or ref in reached or ref in filtered:
                    continue
                out.append(
                    NotAffected(
                        ref=ref,
                        label=other.name or ref,
                        domain=domain_for_item_type(other.item_type),
                        reason=f"no interface links it to {item.key}",
                    )
                )
            result.not_affected = sorted(out, key=lambda n: n.ref)
        else:
            result.not_affected = sorted(filtered.values(), key=lambda n: n.ref)
        result.graph = _graph(item, result, edges, walked)
        result.summary, result.next_action = _summary(result)
        span.set_attribute("impact.impacted", len(result.impacted))
        span.set_attribute("impact.stale_results", len(result.stale_results))
        span.set_attribute("impact.reruns", len(result.proposed_reruns))
        span.set_attribute("impact.cycles", len(result.cycles))
        return result


async def _merge_entity_pins(
    twin: Any, pid: UUID, item: Any, change: ChangeSpec, result: ImpactResult
) -> None:
    """Add what FORGE-59's ``StalenessEngine.preview_impact`` would mark.

    For an item whose head is an engineering entity (a design value, an
    objective), constraints and entities pinned to it through ``depends_on``
    are reported as review actions. Best effort.
    """
    from twin_core.consistency.staleness import StalenessEngine

    if item.head_node_id is None or not hasattr(twin, "get_engineering_entity"):
        return
    try:
        entity = await twin.get_engineering_entity(item.head_node_id)
        if entity is None:
            return
        markings = await StalenessEngine(twin).preview_impact(
            pid,
            "engineering_entity",
            item.head_node_id,
            projected_revision=int(getattr(entity, "revision", 0)) + 1,
        )
    except Exception as exc:  # noqa: BLE001 - an extra source of impact, never a failure
        logger.warning("impact_entity_pins_failed", item_key=item.key, error=str(exc))
        return
    for marking in markings:
        result.review_actions.append(
            ReviewAction(
                kind="review_requirement",
                target_ref=f"{marking.entity_kind}:{marking.entity_id}",
                label="Review the dependent requirement",
                reason=marking.reason,
            )
        )


def _graph(
    item: Any,
    result: ImpactResult,
    edges: list[InterfaceEdge],
    walked: dict[tuple[str, str, str], GraphLink],
) -> ImpactGraph:
    """Nodes and links for the cross-domain scene: the walked part of the graph."""
    nodes: dict[str, GraphNode] = {
        item.key: GraphNode(
            ref=item.key, label=item.name or item.key, domain=result.source_domain, role="changed"
        )
    }
    for entity in result.impacted:
        role: Literal["impacted", "possible", "record"] = (
            "record" if is_record_ref(entity.ref) else "impacted" if entity.certain else "possible"
        )
        nodes[entity.ref] = GraphNode(
            ref=entity.ref, label=entity.label or entity.ref, domain=entity.domain, role=role
        )
    for skipped in result.not_affected:
        if any(skipped.ref in (e.source.ref, e.target.ref) for e in edges):
            nodes.setdefault(
                skipped.ref,
                GraphNode(
                    ref=skipped.ref,
                    label=skipped.label or skipped.ref,
                    domain=skipped.domain,
                    role="unaffected",
                ),
            )
    links = list(walked.values())
    for edge in edges:
        key = (edge.source.ref, edge.target.ref, edge.type.value)
        if key in walked or edge.source.ref not in nodes or edge.target.ref not in nodes:
            continue
        links.append(
            GraphLink(
                source=edge.source.ref,
                target=edge.target.ref,
                type=edge.type,
                status=edge.status,
                impacted=False,
            )
        )
    return ImpactGraph(nodes=list(nodes.values()), links=links)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def operation_id_for(impact_id: str) -> str:
    return f"job:impact-{impact_id}"


def impact_from_node(node: Any) -> ImpactResult | None:
    raw = _meta(node).get(IMPACT_META_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return ImpactResult.model_validate({**raw, "id": str(node.id)})
    except Exception as exc:  # noqa: BLE001 - one bad record must not hide the rest
        logger.warning("impact_record_unreadable", node_id=str(node.id), error=str(exc))
        return None


async def record_impact(twin: Any, result: ImpactResult) -> ImpactResult:
    """Store a recorded walk as a ``change_impact`` work product."""
    from twin_core.models.work_product import WorkProduct

    node_id = uuid4()
    now = datetime.now(UTC).isoformat()
    stored = result.model_copy(
        update={
            "id": str(node_id),
            "operation_id": operation_id_for(str(node_id)),
            "created_at": now,
        }
    )
    change_ref = stored.change.new_ref
    wp = WorkProduct(
        id=node_id,
        name=f"Impact of {change_ref}",
        type=WorkProductType.CHANGE_IMPACT,
        domain="cross_domain",
        file_path="",
        content_hash=hashlib.sha256(f"{change_ref}|{now}".encode()).hexdigest(),
        format="json",
        metadata={
            IMPACT_META_KEY: stored.model_dump(mode="json", exclude={"id"}),
            "change_ref": change_ref,
            "source_ref": stored.change.old_ref,
        },
        created_by="metaforge",
        project_id=UUID(stored.project_id),
    )
    await twin.create_work_product(wp)
    return stored


async def save_impact(twin: Any, result: ImpactResult) -> None:
    """Write back a recorded impact (a rerun decision, a refined disposition)."""
    if result.id is None:
        raise ValueError("only a recorded impact can be saved")
    node_id = UUID(result.id)
    node = await twin.graph.get_node(node_id)
    if node is None:
        raise KeyError(f"impact {result.id} not found")
    meta = _meta(node)
    meta[IMPACT_META_KEY] = result.model_dump(mode="json", exclude={"id"})
    await twin.graph.update_node(node_id, {"metadata": meta})


async def list_impacts(twin: Any, project_id: UUID | str) -> list[ImpactResult]:
    """The project's recorded impacts, newest first."""
    pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    nodes = await twin.list_work_products(
        work_product_type=WorkProductType.CHANGE_IMPACT, project_id=pid
    )
    out = [r for r in (impact_from_node(n) for n in nodes) if r is not None]
    return sorted(out, key=lambda r: r.created_at or "", reverse=True)


async def get_impact(twin: Any, project_id: UUID | str, ref: str) -> ImpactResult | None:
    """A recorded impact by its id, or the latest one for a revision (``KEY@n``)."""
    pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    try:
        node = await twin.graph.get_node(UUID(ref))
    except ValueError:
        node = None
    if node is not None:
        found = impact_from_node(node)
        return found if found is not None and found.project_id == str(pid) else None
    wanted = ref.strip().upper()
    for impact in await list_impacts(twin, pid):
        if impact.change.new_ref.upper() == wanted or impact.change.item_key == wanted:
            return impact
    return None


# ---------------------------------------------------------------------------
# After a head moved
# ---------------------------------------------------------------------------


async def _prior_revision(twin: Any, item: Any, head_revision: int) -> tuple[int, Any] | None:
    from twin_core.items import item_history

    earlier = [
        r
        for r in await item_history(twin, item)
        if r.revision < head_revision and r.status in ("committed", "approved")
    ]
    if not earlier:
        return None
    prior = max(earlier, key=lambda r: r.revision)
    return prior.revision, prior.node_id


async def changed_parameters(
    twin: Any, item: Any, old_node_id: Any, new_node_id: Any
) -> list[str] | None:
    """What changed between two revisions, or ``None`` when that is not known.

    The installed extractor (a pin map's pins) answers first. Otherwise the
    metadata is compared, but only when the content itself did not change:
    a new CAD file can move a hole that no metadata field records, so a
    content change with no extractor is "unknown", which never narrows the walk.
    """
    old = await twin.graph.get_node(old_node_id) if old_node_id else None
    new = await twin.graph.get_node(new_node_id) if new_node_id else None
    if old is None or new is None:
        return None
    if _parameter_extractor is not None:
        try:
            found = await _parameter_extractor(twin, item, old, new)
        except Exception as exc:  # noqa: BLE001 - unknown is the safe answer
            logger.warning("impact_parameter_extract_failed", item_key=item.key, error=str(exc))
            found = None
        if found is not None:
            return found
    if getattr(old, "content_hash", None) != getattr(new, "content_hash", None):
        return None
    old_meta, new_meta = _meta(old), _meta(new)
    keys = (set(old_meta) | set(new_meta)) - _BOOKKEEPING_KEYS
    return sorted(k for k in keys if old_meta.get(k) != new_meta.get(k))


def has_downstream_impact(result: ImpactResult) -> bool:
    """True when a walk reached something worth recording.

    An affected item, a result made stale, a proposed rerun or a review
    action. A walk with none of these leaves no ``change_impact`` record.
    """
    return bool(
        result.impacted or result.stale_results or result.proposed_reruns or result.review_actions
    )


async def on_item_head_moved(
    twin: Any, item: Any, head_revision: int, head_node_id: Any
) -> ImpactResult | None:
    """Walk, invalidate and record after ``item``'s head moved. Never raises.

    Returns ``None`` when the item is new (no earlier revision), has no
    project, or the change reaches nothing (:func:`has_downstream_impact`):
    then nothing is recorded and no operation is opened.
    """
    project_id = getattr(item, "project_id", None)
    if project_id is None:
        return None
    if _head_moved is not None:
        try:
            await _head_moved(twin, item)
        except Exception as exc:  # noqa: BLE001 - a proposal never blocks the walk
            logger.warning("impact_head_moved_hook_failed", item_key=item.key, error=str(exc))
    try:
        prior = await _prior_revision(twin, item, head_revision)
        if prior is None:
            return None
        old_revision, old_node_id = prior
        params = await changed_parameters(twin, item, old_node_id, head_node_id)
        result = await analyse_change(
            twin,
            project_id,
            ChangeSpec(
                item_key=item.key,
                old_revision=old_revision,
                new_revision=head_revision,
                changed_parameters=params,
            ),
            preview=False,
        )
        if not has_downstream_impact(result):
            # Nothing affected, invalidated or proposed: no record and no
            # operation. A change_impact is history about a change that
            # reached something; an empty one would only be clutter.
            count_impact("skipped", "no_impact")
            logger.info(
                "cross_domain_impact_none",
                project_id=str(project_id),
                change=f"{result.change.old_ref} -> {result.change.new_ref}",
            )
            return None
        if _refiner is not None:
            try:
                result = await _refiner(twin, result)
            except Exception as exc:  # noqa: BLE001 - keep the conservative defaults
                logger.warning("impact_refine_failed", item_key=item.key, error=str(exc))
        result.summary, result.next_action = _summary(result)
        stored = await record_impact(twin, result)
        count_impact("recorded", "head_moved")
        for rerun in stored.proposed_reruns:
            count_impact("rerun_proposed", rerun.disposition)
        logger.info(
            "cross_domain_impact_recorded",
            impact_id=stored.id,
            project_id=str(project_id),
            change=f"{stored.change.old_ref} -> {stored.change.new_ref}",
            impacted=[e.ref for e in stored.impacted],
            stale_results=[s.record_id for s in stored.stale_results],
            reruns=[f"{r.tool}:{r.target_ref}:{r.disposition}" for r in stored.proposed_reruns],
            cycles=len(stored.cycles),
        )
        if _recorded is not None:
            try:
                await _recorded(twin, stored)
            except Exception as exc:  # noqa: BLE001 - the record is saved; the event is best effort
                logger.warning("impact_listener_failed", impact_id=stored.id, error=str(exc))
        return stored
    except Exception as exc:  # noqa: BLE001 - never undo the head move
        count_impact("failed", "head_moved")
        logger.warning(
            "cross_domain_impact_failed",
            item_key=getattr(item, "key", None),
            head_revision=head_revision,
            error=str(exc),
            consequence="results across interfaces may still read as current",
        )
        return None
