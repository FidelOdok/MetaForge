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
}

#: Bookkeeping numbers every record carries; never a design fact.
_BOOKKEEPING = frozenset({"item_revision", "revision", "file_count", "total_bytes"})


@dataclass(frozen=True)
class DesignFact:
    """One number a design record states about itself."""

    value: float
    node_id: str
    record: str  # e.g. "pcb_layout 'Routed PCB' (PCB-BOARD@1)"


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
            f"no current design record ({kinds}) states '{metric}'; record it on the design "
            "or a claim with evidence",
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
    """Facts from the current (head) revision of each of the project's design records.

    Empty when the twin has no items (a unit-test double) or nothing is recorded.
    A head whose staleness says it is out of date is skipped.
    """
    from twin_core.items.service import list_items, supports_items

    getter = getattr(twin, "get_work_product", None)
    if getter is None or not supports_items(twin):
        return {}
    records: list[tuple[str, str, str, dict[str, Any]]] = []
    for wp_type in DESIGN_RECORD_TYPES:
        for item in await list_items(twin, project_id, wp_type):
            if item.head_node_id is None:
                continue
            node = await getter(item.head_node_id)
            if node is None:
                continue
            meta = dict(getattr(node, "metadata", None) or {})
            if str(meta.get("staleness") or "current") not in ("current", "revalidated"):
                continue
            meta.setdefault("item_key", item.key)
            meta.setdefault("item_revision", item.head_revision)
            name = str(getattr(node, "name", "") or item.key)
            records.append((str(item.head_node_id), wp_type, name, meta))
    facts = facts_from_records(records)
    logger.info(
        "design_facts_read", project_id=str(project_id), records=len(records), facts=len(facts)
    )
    return facts
