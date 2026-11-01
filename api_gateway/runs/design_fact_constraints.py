"""Compare structured requirements to what the current design records (FORGE-698).

A requirement recorded as a ``metric`` + ``operator`` + ``limit`` binding
carries the placeholder expression ``True`` (see ``constraint_recorder``). The
constraint engine no longer counts that placeholder as a pass, and
``geometry_constraints`` / ``analysis_constraints`` cover only cad_models and
their simulation results. So an electronics or firmware requirement such as
``drc_error_count == 0`` or ``board_length_mm <= 40`` was never compared to
anything: the requirement matrix showed every one as ``no_data`` and the gate
counted the placeholders as evaluated.

This module is that comparison. It reads the numbers the current revision of
each design record states about itself (a routed ``pcb_layout``, a
``schematic``, a ``firmware_source``, a ``bom``) and compares the one named by
the requirement's metric to its limit. Every requirement ends in one of three
states: passed, failed (with the numbers), or not evaluated (with the reason).
A requirement that cannot be compared is never passed.

Analysis results (``simulation_result``) are deliberately not read here: their
numbers rest on load cases and assumptions, so they verify a requirement only
through a claim (the requirement matrix) or ``analysis_constraints``.

FORGE-701: a recorded result (``twin.record_evidence``) of a tool that measures
rather than estimates is read too, after the design records. Those tools are
the KiCad checks, the firmware build, static check and emulated boot, and the
power budget, whose total is a sum of the stated loads with its basis on
record; a datasheet rating is read the same way. Each number keeps that basis
(typical figures, a stand-in board, the emulator and its version) in the
detail, so a pass says what it rests on. Any other tool's result still counts
only through a claim that cites it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from api_gateway.runs.geometry_constraints import _OPS

logger = structlog.get_logger(__name__)

#: Work products that are the design itself, read for facts.
DESIGN_RECORD_TYPES: tuple[str, ...] = ("pcb_layout", "schematic", "firmware_source", "bom")

#: The same fact under the names tools and agents use for it.
_ALIASES: dict[str, tuple[str, ...]] = {
    "erc_error_count": ("erc_errors",),
    "erc_warning_count": ("erc_warnings",),
    "drc_error_count": ("drc_errors",),
    "drc_warning_count": ("drc_warnings",),
    "unrouted_count": ("unrouted", "unrouted_connections"),
    "copper_layer_count": ("layer_count", "copper_layers", "layers"),
    "build_error_count": ("build_errors",),
    "build_warning_count": ("build_warnings",),
    # FORGE-701: what firmware.run_emulated and power.check_budget call them.
    "boot_checks_failed": ("checks_failed",),
    "total_supply_current": ("vbus_load_ma", "vbus_load_current", "supply_current_ma"),
    "vbus_load_current": ("vbus_load_ma", "total_supply_current"),
    "rail_3v3_load_current": ("rail_3v3_load_ma",),
    "rail_load_margin_pct": ("rail_3v3_margin_pct",),
}

#: Bookkeeping numbers every record carries; never a design fact.
_BOOKKEEPING = frozenset({"item_revision", "revision", "file_count", "total_bytes"})

#: Tools whose results are measurements of the design (FORGE-701). An entry
#: ending in "." is a family. Anything else, an FEA or CFD run for one, rests
#: on load cases and counts only through a claim.
MEASURING_TOOLS: tuple[str, ...] = (
    "kicad.",
    "firmware.build",
    "firmware.static_check",
    "firmware.run_emulated",
    "power.check_budget",
)

#: A result with one of these verdicts established nothing; its numbers are partial.
_UNESTABLISHED = frozenset({"not_established", "error"})

#: What to run so a design record or tool result states a metric. A metric is
#: matched on the start of its words, so ``flash_used_pct`` is not ERC.
_HOW_TO_CLOSE: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("erc",),
        "run kicad.generate_schematic and record the schematic with twin.record_design_files",
    ),
    (
        ("drc", "unrouted", "board", "copper", "layer"),
        "route with kicad.autoroute_pcb and record the routed board with twin.record_design_files",
    ),
    (
        ("build", "flash", "ram"),
        "build with firmware.build and record the project with twin.record_design_files",
    ),
    (("boot",), "boot with firmware.run_emulated and record its result with twin.record_evidence"),
    (
        ("current", "load", "margin", "supply", "rail", "power"),
        "run power.check_budget and record its result with twin.record_evidence",
    ),
    (
        ("ambient", "temp", "rated"),
        "record the parts' rated range from their datasheets with twin.record_evidence "
        "(evidence_type datasheet)",
    ),
)


@dataclass(frozen=True)
class DesignFact:
    """One number a design record, or a measuring tool's result, states about the design."""

    value: float
    node_id: str
    record: str  # e.g. "pcb_layout 'Routed PCB' (PCB-BOARD@1)"
    #: "design" for a design record, "evidence" for a tool result (FORGE-701).
    origin: str = "design"


@dataclass(frozen=True)
class Measurement:
    """A requirement compared to the design: ``pass``, ``fail`` or ``not_evaluated``."""

    status: str
    detail: str
    node_id: str | None = None


@dataclass
class DesignFactCheck:
    """Outcome of comparing constraints to the design's records, for a gate."""

    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evaluated: int = 0
    not_evaluated: list[str] = field(default_factory=list)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def facts_from_records(
    records: Iterable[tuple[str, str, str, dict[str, Any]]],
) -> dict[str, DesignFact]:
    """``metric -> fact`` from ``(node_id, type, name, metadata)`` design records.

    A metric two records both state is taken from the first one given, so
    callers pass records in order of preference.
    """
    facts: dict[str, DesignFact] = {}
    for node_id, wp_type, name, meta in records:
        ref = meta.get("item_key")
        label = f"{wp_type} '{name}'"
        if ref:
            label += f" ({ref}@{meta.get('item_revision', '?')})"
        for key, raw in meta.items():
            number = _number(raw)
            if number is None or key in _BOOKKEEPING or key in facts:
                continue
            facts[key] = DesignFact(value=number, node_id=node_id, record=label)
    return facts


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def measures_the_design(meta: dict[str, Any]) -> bool:
    """Whether an evidence record is a measuring tool's result or a datasheet rating."""
    if str(meta.get("evidence_type") or "") == "datasheet":
        return True
    tool = _text((meta.get("producer") or {}).get("tool")).split(" ", 1)[0]
    return any(tool.startswith(t) if t.endswith(".") else tool == t for t in MEASURING_TOOLS)


def _evidence_label(node_id: str, meta: dict[str, Any]) -> str:
    """``evidence from <tool> <version>; basis: ...; board: ... [abcd1234]``."""
    producer = meta.get("producer") or {}
    result = meta.get("result") or {}
    inputs = meta.get("inputs") or {}
    head = _text(producer.get("tool")) or str(meta.get("evidence_type") or "a tool")
    if _text(producer.get("version")):
        head += f" {_text(producer.get('version'))}"
    bits = [head]
    for key, source in (
        ("basis", result),
        ("basis", inputs),
        ("basis_accepted_by", inputs),
        ("board", inputs),
    ):
        if _text(source.get(key)):
            bits.append(f"{key.replace('_', ' ')}: {_text(source.get(key))}")
    return f"evidence from {'; '.join(bits)} [{node_id[:8]}]"


def facts_from_evidence(
    entities: Iterable[tuple[str, dict[str, Any]]], *, any_tool: bool = False
) -> dict[str, DesignFact]:
    """``metric -> fact`` from ``(node_id, metadata)`` evidence records (FORGE-701).

    Reads the numbers at the top level of each result. Only a measuring tool's
    result or a datasheet rating, unless ``any_tool`` (the evidence a claim
    cites, which the claim vouches for). A result that established nothing
    (``not_established``) is skipped. The first record given wins, so callers
    pass the newest first.
    """
    facts: dict[str, DesignFact] = {}
    for node_id, meta in entities:
        result = meta.get("result")
        if not isinstance(result, dict) or not (any_tool or measures_the_design(meta)):
            continue
        if str(result.get("verdict") or "").lower() in _UNESTABLISHED:
            continue
        label = _evidence_label(node_id, meta)
        for key, raw in result.items():
            number = _number(raw)
            if number is None or key in _BOOKKEEPING or key in facts:
                continue
            facts[key] = DesignFact(value=number, node_id=node_id, record=label, origin="evidence")
    return facts


def how_to_close(metric: str) -> str:
    """The tool call that would put ``metric`` on record, for a not-evaluated detail."""
    tokens = metric.lower().split("_")
    for words, action in _HOW_TO_CLOSE:
        if any(t.startswith(w) for w in words for t in tokens):
            return f"{action}, with '{metric}' in it"
    return (
        f"record a measuring tool's result stating '{metric}' with twin.record_evidence, "
        "or a twin.record_claim citing evidence that states it"
    )


def _lookup(metric: str, facts: dict[str, DesignFact]) -> tuple[str, DesignFact] | None:
    for name in (metric, *_ALIASES.get(metric, ())):
        if name in facts:
            return name, facts[name]
    for canonical, aliases in _ALIASES.items():
        if metric in aliases and canonical in facts:
            return canonical, facts[canonical]
    return None


def states_metric(constraint: Any, facts: dict[str, DesignFact]) -> bool:
    """Whether a design record states the metric ``constraint`` limits."""
    metric = str(getattr(constraint, "metric", "") or "").strip()
    return bool(metric) and _lookup(metric, facts) is not None


def measure(constraint: Any, facts: dict[str, DesignFact]) -> Measurement | None:
    """Compare one requirement to the design. ``None`` when it has no metric binding."""
    metric = str(getattr(constraint, "metric", "") or "").strip()
    limit = getattr(constraint, "limit", None)
    if not metric or limit is None:
        return None
    op_name = str(getattr(constraint, "operator", "") or "<=")
    compare = _OPS.get(op_name)
    unit = str(getattr(constraint, "unit", "") or "")
    target = f"{op_name} {float(limit):g}{(' ' + unit) if unit else ''}"
    if compare is None:
        return Measurement("not_evaluated", f"operator '{op_name}' is not supported")
    found = _lookup(metric, facts)
    if found is None:
        kinds = ", ".join(DESIGN_RECORD_TYPES)
        return Measurement(
            "not_evaluated",
            f"no current design record ({kinds}) or measuring tool result states "
            f"'{metric}'; to close it, {how_to_close(metric)}",
        )
    name, fact = found
    stated = f"{name} = {fact.value:g} on {fact.record}"
    if compare(fact.value, float(limit)):
        return Measurement("pass", f"{stated}, requirement {target}", fact.node_id)
    return Measurement("fail", f"{stated} breaks requirement {target}", fact.node_id)


def check_design_fact_constraints(
    constraints: Iterable[Any], facts: dict[str, DesignFact], *, skip: Any = None
) -> DesignFactCheck:
    """Gate view of :func:`measure` for every structured constraint ``skip`` leaves."""
    result = DesignFactCheck()
    for constraint in constraints:
        severity = str(getattr(getattr(constraint, "severity", ""), "value", "") or "error")
        if severity == "info" or (skip is not None and skip(constraint)):
            continue
        outcome = measure(constraint, facts)
        if outcome is None:
            continue
        label = str(getattr(constraint, "name", "") or getattr(constraint, "metric", ""))
        if outcome.status == "not_evaluated":
            result.not_evaluated.append(f"{label}: {outcome.detail}")
            continue
        result.evaluated += 1
        if outcome.status == "fail":
            sink = result.violations if severity == "error" else result.warnings
            sink.append(f"{label}: {outcome.detail}")
    return result


async def read_design_facts(twin: Any, project_id: UUID | str) -> dict[str, DesignFact]:
    """Facts from the current (head) revision of each of the project's design records,
    then from the current results of the measuring tools (FORGE-701).

    Empty when the twin has neither (a unit-test double) or nothing is recorded.
    A head or result whose staleness says it is out of date is skipped.
    """
    from twin_core.items.service import list_items, supports_items

    getter = getattr(twin, "get_work_product", None)
    records: list[tuple[str, str, str, dict[str, Any]]] = []
    kinds = DESIGN_RECORD_TYPES if getter is not None and supports_items(twin) else ()
    for wp_type in kinds:
        assert getter is not None
        for item in await list_items(twin, project_id, wp_type):
            if item.head_node_id is None:
                continue
            node = await getter(item.head_node_id)
            if node is None:
                continue
            meta = dict(getattr(node, "metadata", None) or {})
            if not _is_current(meta):
                continue
            meta.setdefault("item_key", item.key)
            meta.setdefault("item_revision", item.head_revision)
            name = str(getattr(node, "name", "") or item.key)
            records.append((str(item.head_node_id), wp_type, name, meta))
    facts = facts_from_records(records)
    # FORGE-701: then what the measuring tools' current results state. A
    # design record wins over a result for the same metric.
    measured = await read_evidence_facts(twin, project_id)
    for key, fact in measured.items():
        facts.setdefault(key, fact)
    logger.info(
        "design_facts_read",
        project_id=str(project_id),
        records=len(records),
        facts=len(facts),
        from_evidence=sum(1 for f in facts.values() if f.origin == "evidence"),
    )
    return facts


def _is_current(meta: dict[str, Any]) -> bool:
    return str(meta.get("staleness") or "current") in ("current", "revalidated")


async def read_evidence_facts(
    twin: Any, project_id: UUID | str | None, *, evidence_ids: Iterable[UUID] | None = None
) -> dict[str, DesignFact]:
    """Facts from current evidence: the project's measuring tool results, newest first.

    With ``evidence_ids`` (what a claim cites) only those records are read,
    from any tool, since the claim vouches for them.
    """
    entities: list[Any] = []
    if evidence_ids is not None:
        getter = getattr(twin, "get_engineering_entity", None)
        if getter is None:
            return {}
        for evidence_id in evidence_ids:
            entity = await getter(evidence_id)
            if entity is not None:
                entities.append(entity)
    else:
        lister = getattr(twin, "list_engineering_entities", None)
        if lister is None or project_id is None:
            return {}
        entities = list(await lister(project_id=UUID(str(project_id)), entity_type="evidence"))
    entities = [
        e
        for e in entities
        if getattr(e, "entity_type", "evidence") == "evidence"
        and _is_current(dict(getattr(e, "metadata", None) or {}))
    ]
    entities.sort(key=lambda e: str(getattr(e, "created_at", "") or ""), reverse=True)
    return facts_from_evidence(
        ((str(e.id), dict(e.metadata or {})) for e in entities),
        any_tool=evidence_ids is not None,
    )
