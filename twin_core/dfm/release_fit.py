"""Release checklist items that make sure the released things fit and match.

Three items the manufacturing release checklist adds to the DFM items in
:mod:`twin_core.dfm.release_checklist`, all required by the release gate:

``eda_checks``
    Every schematic has a passing ERC, and every board a passing DRC, on its
    current (released) revision. The evidence is a ``kicad.run_erc`` or
    ``kicad.run_drc`` result recorded with ``twin.record_evidence`` and pinned
    to that revision (``depends_on: ["PCB-...@n"]``). With no result on the
    revision the item is not assessed; with a failing one it fails, naming the
    violations, unless a person accepted it (a waiver, below).
``assembly_dfa``
    A product with more than one part, or with a board, has an assembly whose
    bodies are the current revisions of every part and board, and a recorded
    ``twin.check_assembly`` of that assembly revision with no open high or
    medium finding (or each one accepted).
``outputs_current``
    Every fabrication output (G-code, Gerbers and drill, position file,
    manufacturing files, a BOM made from a schematic) is pinned to the
    revision it was made from (``source_ref``, ``CAD-...@n``, ``PCB-...@n``,
    ``FW-...@n``), and that revision is the current one. An output made from
    an older revision, or with no source on record, fails. Outputs are never
    waived: make them again.

**Waivers.** A failing ERC/DRC or an assembly finding may be accepted the same
way DFM issues are: by a person, or by an agent the owner delegates release to
acting for a person. ``twin.accept_release_finding`` records it as an approved
``waiver`` entity naming the item (``release_check``), what it accepts
(``targets``), the reason and who accepted it, pinned to the revisions it is
about so a new revision needs a new acceptance.

Reads the twin; never writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.dfm.release_checklist import ChecklistItem

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.dfm.release_fit")

EDA_ITEM = "eda_checks"
ASSEMBLY_ITEM = "assembly_dfa"
OUTPUTS_ITEM = "outputs_current"

FIT_ITEM_LABELS: dict[str, str] = {
    EDA_ITEM: "ERC and DRC pass on the released schematic and board revisions",
    ASSEMBLY_ITEM: "Assembly checked (DFA)",
    OUTPUTS_ITEM: "Fabrication outputs made from the released revisions",
}

#: The items a waiver may accept a failure of.
WAIVABLE_ITEMS: frozenset[str] = frozenset({EDA_ITEM, ASSEMBLY_ITEM})

#: The record type a ``twin.check_assembly`` run is stored as (an engineering entity).
ASSEMBLY_CHECK_TYPE = "assembly_check"
#: Where the record keeps its report.
REPORT_KEY = "assembly_report"
#: Where an assembly revision keeps its layout (bodies, placements, joints).
LAYOUT_KEY = "assembly_layout"

ERC_TOOL = "kicad.run_erc"
DRC_TOOL = "kicad.run_drc"

#: Work product types that are fabrication outputs and must name their source.
OUTPUT_KINDS: tuple[str, ...] = ("gerber", "pick_and_place", "manufacturing_file", "bom", "gcode")
#: Item types an output can be made from.
SOURCE_ITEM_TYPES: frozenset[str] = frozenset(
    {"cad_model", "assembly", "pcb_layout", "schematic", "firmware_source"}
)

_APPROVED = frozenset({"approved", "baselined"})
_VALID = frozenset({"current", "revalidated"})


def _authority(entity: Any) -> str:
    a = getattr(entity, "authority", None)
    return str(getattr(a, "value", a) or "")


def _meta(node: Any) -> dict[str, Any]:
    return dict(getattr(node, "metadata", None) or {})


def _current(meta: dict[str, Any]) -> bool:
    return str(meta.get("staleness") or "current") in _VALID


# ---------------------------------------------------------------------------
# Waivers
# ---------------------------------------------------------------------------


@dataclass
class Waiver:
    id: str
    item: str
    targets: list[str]
    reason: str
    approved_by: str
    on_behalf_of: str = ""
    route: str = "person"

    def words(self) -> str:
        who = (
            f"agent {self.approved_by} on behalf of {self.on_behalf_of}"
            if self.route == "delegated" and self.on_behalf_of
            else self.approved_by
        )
        return f"accepted by {who}: {self.reason[:160]}"


async def accepted_waivers(twin: Any, project_id: UUID) -> dict[str, dict[str, Waiver]]:
    """``item -> target -> waiver`` for the project's approved, current release waivers."""
    try:
        entities = await twin.list_engineering_entities(project_id=project_id, entity_type="waiver")
    except Exception as exc:  # noqa: BLE001 - unreadable waivers accept nothing
        logger.debug("release_waivers_unavailable", error=str(exc))
        return {}
    out: dict[str, dict[str, Waiver]] = {}
    for entity in sorted(entities, key=lambda e: str(getattr(e, "created_at", "") or "")):
        meta = _meta(entity)
        item = str(meta.get("release_check") or "")
        if item not in WAIVABLE_ITEMS or _authority(entity) not in _APPROVED:
            continue
        if not _current(meta):
            continue
        targets = [str(t) for t in meta.get("targets") or [] if t]
        waiver = Waiver(
            id=str(entity.id),
            item=item,
            targets=targets,
            reason=str(meta.get("reason") or getattr(entity, "statement", "") or ""),
            approved_by=str(meta.get("agent") or meta.get("approved_by") or ""),
            on_behalf_of=str(meta.get("on_behalf_of") or ""),
            route=str(meta.get("approval_route") or "person"),
        )
        for target in targets:
            out.setdefault(item, {})[target] = waiver
    return out


# ---------------------------------------------------------------------------
# ERC and DRC on the released revisions
# ---------------------------------------------------------------------------


def _producer_tool(meta: dict[str, Any]) -> str:
    producer = meta.get("producer") or {}
    tool = str(producer.get("tool") or "") if isinstance(producer, dict) else ""
    return tool.split(" ", 1)[0]


def _pins(meta: dict[str, Any]) -> list[tuple[str, int]]:
    out = []
    for p in meta.get("depends_on_items") or []:
        if isinstance(p, dict) and p.get("item_key"):
            out.append((str(p["item_key"]), int(p.get("revision") or 0)))
    return out


def eda_failures(result: dict[str, Any]) -> list[str]:
    """One line per reason a KiCad ERC or DRC result fails; empty when it passes. Pure."""
    reasons: list[str] = []
    errors = result.get("errors")
    unconnected = result.get("unconnected_items")
    parity = result.get("schematic_parity")
    if isinstance(errors, int) and errors > 0:
        reasons.append(f"{errors} error(s)")
    if isinstance(unconnected, int) and unconnected > 0:
        reasons.append(f"{unconnected} unconnected item(s)")
    if isinstance(parity, list) and parity:
        reasons.append(f"{len(parity)} schematic parity issue(s)")
    if result.get("passed") is False and not reasons:
        reasons.append("the check did not pass")
    if result.get("passed") is None and errors is None:
        reasons.append("the result states no verdict (passed or errors)")
    return reasons


def _violation_words(result: dict[str, Any], limit: int = 5) -> str:
    rows = [v for v in result.get("violations") or [] if isinstance(v, dict)]
    errors = [v for v in rows if str(v.get("severity") or "") == "error"] or rows
    words = []
    for v in errors[:limit]:
        kind = str(v.get("type") or v.get("rule") or "violation")
        text = str(v.get("message") or v.get("description") or "").strip()
        words.append(f"{kind}: {text}"[:140] if text else kind)
    more = len(errors) - limit
    return "; ".join(words) + (f"; and {more} more" if more > 0 else "")


@dataclass
class EdaResult:
    """The newest ERC or DRC result on one revision."""

    ref: str
    tool: str
    evidence_id: str
    result: dict[str, Any] = field(default_factory=dict)


async def _eda_evidence(twin: Any, project_id: UUID) -> list[tuple[Any, dict[str, Any]]]:
    """Every ERC and DRC result, newest first, stale ones too (for the hint)."""
    try:
        entities = await twin.list_engineering_entities(
            project_id=project_id, entity_type="evidence"
        )
    except Exception as exc:  # noqa: BLE001 - unreadable evidence is missing evidence
        logger.debug("release_eda_evidence_unavailable", error=str(exc))
        return []
    rows = []
    for e in entities:
        meta = _meta(e)
        if _producer_tool(meta) in (ERC_TOOL, DRC_TOOL):
            rows.append((e, meta))
    # Newest first; records written in the same clock tick keep their order.
    order = {id(r[0]): i for i, r in enumerate(rows)}
    rows.sort(key=lambda r: (_stamp(r[0], r[1]), order[id(r[0])]), reverse=True)
    return rows


def _stamp(entity: Any, meta: dict[str, Any]) -> str:
    return str(meta.get("execution_timestamp") or getattr(entity, "created_at", "") or "")


async def evaluate_eda_item(
    twin: Any, project_id: UUID, waivers: dict[str, dict[str, Waiver]]
) -> ChecklistItem:
    """The ``eda_checks`` item."""
    from twin_core.items import list_items, supports_items

    label = FIT_ITEM_LABELS[EDA_ITEM]
    if not supports_items(twin):
        return ChecklistItem(EDA_ITEM, label, "not_applicable", "no schematic or board")
    heads: list[tuple[Any, str, str]] = []
    for item_type, tool in (("schematic", ERC_TOOL), ("pcb_layout", DRC_TOOL)):
        for item in await list_items(twin, project_id=project_id, item_type=item_type):
            heads.append((item, tool, "ERC" if tool == ERC_TOOL else "DRC"))
    if not heads:
        return ChecklistItem(EDA_ITEM, label, "not_applicable", "no schematic or board")
    evidence = await _eda_evidence(twin, project_id)
    accepted = waivers.get(EDA_ITEM, {})
    blockers: list[str] = []
    steps: list[str] = []
    passed: list[str] = []
    for item, tool, word in heads:
        ref = f"{item.key}@{item.head_revision}"
        on_rev = None
        older = None
        for entity, meta in evidence:
            if _producer_tool(meta) != tool:
                continue
            pins = _pins(meta)
            if (item.key, item.head_revision) in pins and _current(meta):
                on_rev = (entity, meta)
                break
            if older is None and any(k == item.key for k, _r in pins):
                older = next(f"{k}@{r}" for k, r in pins if k == item.key)
        if on_rev is None:
            why = f"{ref}: no {word} result recorded on this revision" + (
                f" (the newest is on {older})" if older else ""
            )
            blockers.append(why)
            target = (
                "kicad.run_erc on the schematic"
                if tool == ERC_TOOL
                else "kicad.run_drc on the board"
            )
            steps.append(
                f"Run {target} ({ref}) and record the result with twin.record_evidence, "
                f"producer {tool}, depends_on {ref}"
            )
            continue
        entity, meta = on_rev
        raw_result = meta.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        failures = eda_failures(result)
        rule_note = " with a custom rule set" if result.get("rule_set_applied") else ""
        if not failures:
            warnings = result.get("warnings")
            passed.append(
                f"{ref} {word} passed{rule_note}"
                + (f" ({warnings} warning(s))" if isinstance(warnings, int) and warnings else "")
            )
            continue
        target = f"{ref}:{word.lower()}"
        waiver = accepted.get(target)
        summary = f"{word} fails{rule_note}: {', '.join(failures)}"
        violations = _violation_words(result)
        if waiver is not None:
            passed.append(f"{ref} {summary} ({waiver.words()})")
            continue
        blockers.append(f"{ref}: {summary}" + (f" ({violations})" if violations else ""))
        steps.append(
            f"Fix the {word} violations on {ref} and record a new result, or have a person "
            f"accept them with twin.accept_release_finding (target {target})"
        )
    if blockers:
        detail = "; ".join(blockers)
        if passed:
            detail += "; passed: " + ", ".join(passed)
        return ChecklistItem(EDA_ITEM, label, "fail", detail, blockers, steps)
    return ChecklistItem(EDA_ITEM, label, "pass", "; ".join(passed))


# ---------------------------------------------------------------------------
# The assembly (DFA)
# ---------------------------------------------------------------------------


@dataclass
class _Part:
    key: str
    ref: str
    name: str
    board: bool = False


def _is_board_body(meta: dict[str, Any]) -> bool:
    """A cad_model that is a board's 3D export, not a made part."""
    from twin_core.dfm.release_checklist import is_board_body

    return is_board_body(meta)


async def product_parts(twin: Any, project_id: UUID) -> tuple[list[_Part], list[_Part]]:
    """``(made parts, boards)`` at their current revisions."""
    from twin_core.items import list_items, supports_items

    if not supports_items(twin):
        return [], []
    parts: list[_Part] = []
    for item in await list_items(twin, project_id=project_id, item_type="cad_model"):
        node = await twin.get_work_product(item.head_node_id) if item.head_node_id else None
        meta = _meta(node)
        if node is None or str(meta.get("model_role") or "") == "analysis" or _is_board_body(meta):
            continue
        parts.append(_Part(item.key, f"{item.key}@{item.head_revision}", item.name))
    boards = [
        _Part(i.key, f"{i.key}@{i.head_revision}", i.name, board=True)
        for i in await list_items(twin, project_id=project_id, item_type="pcb_layout")
    ]
    return parts, boards


def layout_of(meta: dict[str, Any]) -> dict[str, Any]:
    raw = meta.get(LAYOUT_KEY)
    return raw if isinstance(raw, dict) else {}


async def latest_assembly_check(twin: Any, project_id: UUID, assembly_ref: str) -> Any:
    """The newest current ``assembly_check`` record of ``assembly_ref``, or ``None``."""
    try:
        entities = await twin.list_engineering_entities(
            project_id=project_id, entity_type=ASSEMBLY_CHECK_TYPE
        )
    except Exception as exc:  # noqa: BLE001 - unreadable checks are no check
        logger.debug("release_assembly_checks_unavailable", error=str(exc))
        return None
    found = [
        (i, e)
        for i, e in enumerate(entities)
        if _meta(e).get("assembly_ref") == assembly_ref and _current(_meta(e))
    ]
    if not found:
        return None
    # The newest; records written in the same clock tick keep their order.
    return max(found, key=lambda r: (_stamp(r[1], _meta(r[1])), r[0]))[1]


async def evaluate_assembly_item(
    twin: Any, project_id: UUID, waivers: dict[str, dict[str, Waiver]]
) -> ChecklistItem:
    """The ``assembly_dfa`` item."""
    from twin_core.dfa.checks import CHECK_LABELS, AssemblyReport
    from twin_core.items import list_items

    label = FIT_ITEM_LABELS[ASSEMBLY_ITEM]
    parts, boards = await product_parts(twin, project_id)
    if len(parts) + len(boards) < 2:
        return ChecklistItem(
            ASSEMBLY_ITEM,
            label,
            "not_applicable",
            "a single part: nothing to assemble"
            if parts or boards
            else "no parts or boards to assemble",
        )
    assemblies = await list_items(twin, project_id=project_id, item_type="assembly")
    want_parts = {p.key: p for p in parts}
    want_boards = {b.key: b for b in boards}
    if not assemblies:
        names = ", ".join(p.name for p in [*parts, *boards])
        return ChecklistItem(
            ASSEMBLY_ITEM,
            label,
            "fail",
            f"no assembly of the {len(parts) + len(boards)} parts and boards ({names})",
            [
                f"no assembly: {len(parts)} part(s) and {len(boards)} board(s) were never "
                "put together"
            ],
            [
                "Build the assembly with twin.build_assembly: every part and each board's "
                "3D model (kicad.export_3d, committed with twin.commit_geometry) at its "
                "placement, with the joints"
            ],
        )
    best: tuple[int, Any, dict[str, Any]] | None = None
    for asm in assemblies:
        node = await twin.get_work_product(asm.head_node_id) if asm.head_node_id else None
        layout = layout_of(_meta(node))
        keys = {
            str(p.get("item_ref") or "").split("@")[0]
            for p in layout.get("parts") or []
            if isinstance(p, dict)
        }
        boards_in = {
            str(p.get("board_ref") or "").split("@")[0]
            for p in layout.get("parts") or []
            if isinstance(p, dict) and p.get("board_ref")
        }
        score = len(keys & set(want_parts)) + len(boards_in & set(want_boards))
        if best is None or score > best[0]:
            best = (score, asm, layout)
    assert best is not None
    _score, asm, layout = best
    asm_ref = f"{asm.key}@{asm.head_revision}"
    blockers: list[str] = []
    steps: list[str] = []
    held: dict[str, str] = {}
    held_boards: dict[str, str] = {}
    for p in layout.get("parts") or []:
        if not isinstance(p, dict):
            continue
        ref = str(p.get("item_ref") or "")
        if ref:
            held[ref.split("@")[0]] = ref
        if p.get("board_ref"):
            held_boards[str(p["board_ref"]).split("@")[0]] = str(p["board_ref"])
    if not layout:
        blockers.append(
            f"{asm_ref}: no placements on record (it was not built with twin.build_assembly)"
        )
        steps.append("Build the assembly with twin.build_assembly so each body has a placement")
    missing = [p.name for k, p in want_parts.items() if k not in held] + [
        f"board {b.name}" for k, b in want_boards.items() if k not in held_boards
    ]
    if layout and missing:
        blockers.append(f"{asm_ref}: does not hold {', '.join(missing)}")
        steps.append(f"Add {', '.join(missing)} to the assembly with twin.build_assembly")
    stale = [
        f"{held[k]} (now {p.ref})" for k, p in want_parts.items() if k in held and held[k] != p.ref
    ] + [
        f"{held_boards[k]} (now {b.ref})"
        for k, b in want_boards.items()
        if k in held_boards and held_boards[k] != b.ref
    ]
    if stale:
        blockers.append(f"{asm_ref}: holds older revisions: {', '.join(stale)}")
        steps.append("Rebuild the assembly with twin.build_assembly from the current revisions")
    record = await latest_assembly_check(twin, project_id, asm_ref)
    accepted = waivers.get(ASSEMBLY_ITEM, {})
    detail_bits: list[str] = []
    if record is None:
        blockers.append(f"{asm_ref}: not checked")
        steps.append(f"Check the assembly with twin.check_assembly ({asm_ref})")
    else:
        report = AssemblyReport.from_dict(_meta(record).get(REPORT_KEY) or {})
        open_ = report.open_findings(set(accepted))
        for f in open_:
            blockers.append(f"{asm_ref}: {f.severity}: {f.message}")
            steps.append(
                f"Fix it and check again, or have a person accept it with "
                f"twin.accept_release_finding (target {f.id})"
            )
        for c in report.missing_required():
            blockers.append(
                f"{asm_ref}: {CHECK_LABELS.get(c.check, c.check)} not assessed ({c.detail})"
            )
            steps.append(f"Give twin.check_assembly what it needs for {c.check}: {c.detail}")
        kept = [f for f in report.findings() if f.id in accepted]
        detail_bits.append(
            f"checked: {len(report.findings())} finding(s)"
            + (f", {len(kept)} accepted" if kept else "")
        )
        for f in kept:
            detail_bits.append(f"{f.message} ({accepted[f.id].words()})")
    if blockers:
        return ChecklistItem(ASSEMBLY_ITEM, label, "fail", "; ".join(blockers), blockers, steps)
    return ChecklistItem(
        ASSEMBLY_ITEM,
        label,
        "pass",
        f"{asm_ref} holds {len(parts)} part(s) and {len(boards)} board(s) at their current "
        "revisions; " + "; ".join(detail_bits),
    )


# ---------------------------------------------------------------------------
# Fabrication outputs and their sources
# ---------------------------------------------------------------------------


@dataclass
class OutputSource:
    """One fabrication output, what it was made from and whether that is current."""

    kind: str
    name: str
    node_id: str
    #: The output's own ``KEY@n`` when it is an item revision.
    ref: str | None
    #: ``KEY@n`` it was made from, or ``None`` when nothing says.
    source_ref: str | None
    #: The source item's current ``KEY@n``.
    current_ref: str | None
    #: ``current``, ``stale``, ``unpinned``, ``superseded`` or ``not_required``.
    status: str
    created_at: str = ""
    content_hash: str = ""
    format: str = ""

    @property
    def failing(self) -> bool:
        return self.status in ("stale", "unpinned")

    def words(self) -> str:
        label = self.ref or self.name
        if self.status == "stale":
            return (
                f"{label} ({self.kind}) was made from {self.source_ref}; "
                f"the current revision is {self.current_ref}"
            )
        if self.status == "unpinned":
            return f"{label} ({self.kind}) does not say which revision it was made from"
        return f"{label} ({self.kind}) made from {self.source_ref or 'its own revision'}"


async def source_of(twin: Any, meta: dict[str, Any]) -> str | None:
    """``KEY@n`` an output says it was made from: its ``source_ref``, else its sources."""
    from twin_core.items import ItemError, item_for_node, parse_item_ref

    explicit = meta.get("source_ref")
    if isinstance(explicit, str) and explicit.strip():
        try:
            key, rev = parse_item_ref(explicit)
        except ItemError:
            return None
        return f"{key}@{rev}" if rev else key
    for raw in meta.get("source_work_product_ids") or []:
        try:
            found = await item_for_node(twin, UUID(str(raw)))
        except (ValueError, TypeError):
            continue
        except Exception as exc:  # noqa: BLE001 - a lookup that fails names nothing
            logger.debug("release_output_source_lookup_failed", error=str(exc))
            continue
        if found is not None and found[0].item_type in SOURCE_ITEM_TYPES:
            return f"{found[0].key}@{found[1]}"
    return None


async def output_sources(twin: Any, project_id: UUID) -> list[OutputSource]:
    """Every current fabrication output with its source pin, superseded ones marked."""
    from twin_core.items import find_item, item_for_node, list_items, parse_item_ref, supports_items
    from twin_core.models.enums import WorkProductType

    items_ok = supports_items(twin)
    has_schematic = bool(
        items_ok and await list_items(twin, project_id=project_id, item_type="schematic")
    )
    rows: list[OutputSource] = []
    for kind in OUTPUT_KINDS:
        try:
            nodes = await twin.list_work_products(
                project_id=project_id, work_product_type=WorkProductType(kind)
            )
        except Exception as exc:  # noqa: BLE001 - outputs we cannot read are not listed
            logger.debug("release_outputs_unavailable", kind=kind, error=str(exc))
            continue
        newest: dict[str, Any] = {}
        for node in sorted(
            nodes, key=lambda n: n.created_at.isoformat() if getattr(n, "created_at", None) else ""
        ):
            newest[str(getattr(node, "name", "") or node.id)] = node
        for name, node in sorted(newest.items()):
            own = await item_for_node(twin, node.id) if items_ok else None
            if own is not None and own[0].head_node_id not in (None, node.id):
                continue  # an older revision of an output item
            meta = _meta(node)
            source = await source_of(twin, meta)
            current_ref = None
            status = "unpinned"
            if source is not None:
                key, rev = parse_item_ref(source)
                head = await find_item(twin, key, project_id) if items_ok else None
                if head is not None and rev is not None:
                    current_ref = f"{head.key}@{head.head_revision}"
                    status = "current" if rev == head.head_revision else "stale"
                    source = f"{key}@{rev}"
                else:
                    # A bare key, or an item that is not here, pins nothing.
                    status = "unpinned"
            elif kind == "bom" and not has_schematic:
                status = "not_required"
            stamp = getattr(node, "created_at", None)
            rows.append(
                OutputSource(
                    kind=kind,
                    name=name,
                    node_id=str(node.id),
                    ref=f"{own[0].key}@{own[1]}" if own is not None else None,
                    source_ref=source,
                    current_ref=current_ref,
                    status=status,
                    created_at=stamp.isoformat() if stamp is not None else "",
                    content_hash=str(getattr(node, "content_hash", "") or ""),
                    format=str(getattr(node, "format", "") or ""),
                )
            )
    # An output made again from the current revision supersedes the older ones
    # of the same kind made from the same item.
    current_keys = {
        (r.kind, (r.source_ref or "").split("@")[0]) for r in rows if r.status == "current"
    }
    for r in rows:
        if r.status == "stale" and (r.kind, (r.source_ref or "").split("@")[0]) in current_keys:
            r.status = "superseded"
    return rows


def evaluate_outputs_item(rows: list[OutputSource]) -> ChecklistItem:
    """The ``outputs_current`` item from :func:`output_sources`. Pure."""
    label = FIT_ITEM_LABELS[OUTPUTS_ITEM]
    live = [r for r in rows if r.status != "superseded"]
    if not live:
        return ChecklistItem(
            OUTPUTS_ITEM, label, "not_applicable", "no fabrication output recorded"
        )
    failing = [r for r in live if r.failing]
    if failing:
        blockers = [r.words() for r in failing]
        steps = [
            f"Make {r.ref or r.name} again from {r.current_ref} and record it with "
            "twin.record_design_files (source_ref)"
            if r.status == "stale"
            else f"Record {r.ref or r.name} again with twin.record_design_files and its "
            "source_ref (the CAD, PCB or FW revision it was made from)"
            for r in failing
        ]
        return ChecklistItem(OUTPUTS_ITEM, label, "fail", "; ".join(blockers), blockers, steps)
    pinned = [r for r in live if r.status == "current"]
    return ChecklistItem(
        OUTPUTS_ITEM,
        label,
        "pass",
        f"{len(pinned)} output(s) made from the current revisions"
        + (f"; {len(live) - len(pinned)} need no source" if len(live) > len(pinned) else ""),
    )


async def fit_items(twin: Any, project_id: UUID) -> list[ChecklistItem]:
    """The three items, in checklist order."""
    with tracer.start_as_current_span("twin.dfm.release_fit") as span:
        span.set_attribute("project.id", str(project_id))
        waivers = await accepted_waivers(twin, project_id)
        items = [
            await evaluate_eda_item(twin, project_id, waivers),
            await evaluate_assembly_item(twin, project_id, waivers),
            evaluate_outputs_item(await output_sources(twin, project_id)),
        ]
        span.set_attribute(
            "release_fit.failing", ",".join(i.id for i in items if i.status == "fail")
        )
        return items


__all__ = [
    "ASSEMBLY_CHECK_TYPE",
    "ASSEMBLY_ITEM",
    "DRC_TOOL",
    "EDA_ITEM",
    "ERC_TOOL",
    "FIT_ITEM_LABELS",
    "LAYOUT_KEY",
    "OUTPUTS_ITEM",
    "OUTPUT_KINDS",
    "REPORT_KEY",
    "WAIVABLE_ITEMS",
    "OutputSource",
    "Waiver",
    "accepted_waivers",
    "eda_failures",
    "evaluate_assembly_item",
    "evaluate_eda_item",
    "evaluate_outputs_item",
    "fit_items",
    "latest_assembly_check",
    "output_sources",
    "product_parts",
    "source_of",
]
