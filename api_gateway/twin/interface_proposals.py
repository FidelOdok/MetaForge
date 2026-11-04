"""Interface edges proposed from data the tools already recorded (FORGE-793).

Every proposal here is read from a real twin record and says what it was read
from; none is invented, and each is stored ``proposed`` (``declared_by:
inferred``) until a person confirms it. A pair with no data behind it gets no
edge, and a link a person removed is never proposed again.

==================  ==========================================================
Edge                Read from
==================  ==========================================================
``mounts_to``       the board revision's ``mounting_holes`` and the enclosure
                    revision's ``mounting_standoffs`` (or ``mounting_holes``):
                    the fields the cross-domain mounting check already reads.
                    Confidence grows with the holes that sit on a standoff.
``clearance_to``    the board's ``dimensions`` or ``board_outline`` and the
                    enclosure's ``dimensions`` (the fit check's fields).
``connects_via``    the board's ``connectors`` and the enclosure's ``cutouts``.
``pin_maps_to``     the pin map's file (JSON or CSV, as ``twin.record_design_files``
                    or the firmware scaffold stored it) against the firmware
                    source files: a firmware item that names a pin
                    (``PA5``), a peripheral (``SPI1``, or its ``hspi1``
                    handle) or a net from the pin map is proposed, with the
                    names it matched and the files they are in.
==================  ==========================================================

``simulation_input_of`` and ``feature_of`` need no proposal: they are read
from the simulation's own pins and the sketch layer's edges
(:mod:`twin_core.impact.interfaces`).

An enclosure is a ``cad_model`` or ``assembly`` item whose head carries
``subtype: enclosure`` or one of the enclosure fields above.

This module also supplies the pin map's parameter diff for a head move
(:func:`pinmap_parameter_diff`), so a pin map change carries the pins it
touched and a firmware module that uses none of them is reported unaffected.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.interface_proposals")

#: Source files read for pin references, and how much of them.
FIRMWARE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".h", ".hpp", ".ino", ".ini", ".rs", ".py"})
MAX_FIRMWARE_FILES = 200
MAX_FILE_BYTES = 512 * 1024

_PIN_RE = re.compile(r"^(P[A-K]\d{1,2}|GPIO\d{1,3}|IO\d{1,3}|D\d{1,2}|A\d{1,2})$")
_PERIPHERAL_RE = re.compile(
    r"^(SPI|I2C|I2S|USART|UART|LPUART|CAN|FDCAN|TIM|ADC|DAC|USB|SDIO|SDMMC|QSPI|OCTOSPI|ETH)\d*$"
)
_NET_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-/]{2,}$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: Net names KiCad generates for an unconnected pin, which say nothing.
_GENERIC_NETS = re.compile(r"^(net-|unconnected-|GND$|VCC$|\+?\d)", re.IGNORECASE)

_ENCLOSURE_FIELDS = ("mounting_standoffs", "mounting_holes", "cutouts")


@dataclass(frozen=True)
class PinEntry:
    """One row of a pin map: a pin, the peripheral and signal it serves, a net."""

    pin: str = ""
    peripheral: str = ""
    signal: str = ""
    net: str = ""
    detail: str = ""

    def names(self) -> set[str]:
        out = set()
        if self.pin and _PIN_RE.match(self.pin.upper()):
            out.add(self.pin.upper())
        if self.peripheral and _PERIPHERAL_RE.match(self.peripheral.upper()):
            out.add(self.peripheral.upper())
        if self.net and _NET_RE.match(self.net) and not _GENERIC_NETS.match(self.net):
            out.add(self.net)
        return out

    @property
    def key(self) -> str:
        return f"{self.peripheral}.{self.signal}" if self.peripheral or self.signal else self.pin


@dataclass
class ProposalOutcome:
    """What a proposal pass did."""

    proposed: list[Any] = field(default_factory=list)
    already: list[Any] = field(default_factory=list)
    #: Plain reasons a pair got no edge (no data, nothing matched).
    skipped: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reading recorded files
# ---------------------------------------------------------------------------


def _fetch(key: str) -> bytes:
    import digital_twin.storage.work_product_blobs as blobs

    return blobs.fetch_work_product_blob(key)


def recorded_files(node: Any, suffixes: frozenset[str] | None = None) -> dict[str, bytes]:
    """The files a revision node recorded, by relative path. Best effort."""
    meta = getattr(node, "metadata", None) or {}
    keys: dict[str, str] = {}
    design = meta.get("design_files")
    if isinstance(design, dict):
        keys.update({str(k): str(v) for k, v in design.items() if v})
    elif meta.get("minio_object_key"):
        name = str(meta.get("original_filename") or f"{getattr(node, 'name', 'file')}")
        keys[name] = str(meta["minio_object_key"])
    out: dict[str, bytes] = {}
    for rel, key in sorted(keys.items()):
        if suffixes is not None and PurePosixPath(rel).suffix.lower() not in suffixes:
            continue
        if len(out) >= MAX_FIRMWARE_FILES:
            break
        try:
            data = _fetch(key)
        except Exception as exc:  # noqa: BLE001 - one unreadable file is reported, not fatal
            logger.warning("interface_proposal_file_unreadable", path=rel, error=str(exc))
            continue
        out[rel] = data[:MAX_FILE_BYTES]
    return out


def _walk_json(value: Any, parent: str, grandparent: str, out: list[PinEntry]) -> None:
    if isinstance(value, dict):
        pin = value.get("pin")
        if isinstance(pin, str) and pin.strip():
            out.append(
                PinEntry(
                    pin=pin.strip(),
                    peripheral=grandparent,
                    signal=parent,
                    net=str(value.get("net") or ""),
                    detail=json.dumps(value, sort_keys=True),
                )
            )
        pins = value.get("pins")
        if isinstance(pins, list):  # kicad.get_pin_mapping: components[].pins[]
            ref = str(value.get("ref") or value.get("reference") or parent)
            for row in pins:
                if isinstance(row, dict):
                    out.append(
                        PinEntry(
                            pin=str(row.get("name") or row.get("number") or ""),
                            peripheral=ref,
                            signal=str(row.get("number") or ""),
                            net=str(row.get("net") or ""),
                            detail=json.dumps(row, sort_keys=True),
                        )
                    )
        for key, child in value.items():
            if key in ("pins",) and isinstance(child, list):
                continue
            _walk_json(child, str(key), parent, out)
    elif isinstance(value, list):
        for child in value:
            _walk_json(child, parent, grandparent, out)


def parse_pinmap(files: dict[str, bytes]) -> list[PinEntry]:
    """Pin entries from a pin map's JSON or CSV files."""
    entries: list[PinEntry] = []
    for rel, data in files.items():
        text = data.decode("utf-8", errors="replace")
        suffix = PurePosixPath(rel).suffix.lower()
        if suffix == ".json" or text.lstrip().startswith(("{", "[")):
            try:
                _walk_json(json.loads(text), "", "", entries)
            except ValueError as exc:
                logger.warning("pinmap_unreadable", path=rel, error=str(exc))
            continue
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            lowered = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
            entries.append(
                PinEntry(
                    pin=lowered.get("pin") or lowered.get("pin_name") or lowered.get("gpio", ""),
                    peripheral=lowered.get("peripheral") or lowered.get("bus", ""),
                    signal=lowered.get("signal") or lowered.get("function", ""),
                    net=lowered.get("net", ""),
                    detail=json.dumps(lowered, sort_keys=True),
                )
            )
    return entries


def pinmap_names(entries: list[PinEntry]) -> set[str]:
    names: set[str] = set()
    for entry in entries:
        names |= entry.names()
    return names


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for ident in _IDENT_RE.findall(text):
        low = ident.lower()
        out.add(low)
        out.update(part for part in low.split("_") if part)
    return out


def firmware_references(names: set[str], files: dict[str, bytes]) -> dict[str, list[str]]:
    """``{name: [files that mention it]}`` for the pin map names a firmware names.

    A peripheral also matches its HAL handle (``hspi1`` for ``SPI1``).
    """
    found: dict[str, list[str]] = {}
    for rel, data in files.items():
        tokens = _tokens(data.decode("utf-8", errors="replace"))
        for name in names:
            low = name.lower()
            if low in tokens or (_PERIPHERAL_RE.match(name) and f"h{low}" in tokens):
                found.setdefault(name, []).append(rel)
    return found


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


async def _heads(twin: Any, pid: UUID, *item_types: str) -> list[tuple[Any, Any]]:
    from twin_core.items import list_items

    out: list[tuple[Any, Any]] = []
    for item_type in item_types:
        for item in await list_items(twin, project_id=pid, item_type=item_type):
            node = await twin.graph.get_node(item.head_node_id) if item.head_node_id else None
            if node is not None:
                out.append((item, node))
    return out


def _is_enclosure(node: Any) -> bool:
    meta = getattr(node, "metadata", None) or {}
    return meta.get("subtype") == "enclosure" or any(meta.get(f) for f in _ENCLOSURE_FIELDS)


def _points(raw: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for point in raw or []:
        if isinstance(point, dict):
            try:
                out.append((float(point.get("x", 0.0)), float(point.get("y", 0.0))))
            except (TypeError, ValueError):
                continue
    return out


def _mount_evidence(board: dict[str, Any], enclosure: dict[str, Any]) -> dict[str, Any] | None:
    holes = _points(board.get("mounting_holes"))
    standoffs = _points(enclosure.get("mounting_standoffs") or enclosure.get("mounting_holes"))
    if not holes or not standoffs:
        return None
    tolerance = float(enclosure.get("mounting_tolerance", 0.5) or 0.5)
    matched = sum(
        1
        for hx, hy in holes
        if min(((hx - sx) ** 2 + (hy - sy) ** 2) ** 0.5 for sx, sy in standoffs) <= tolerance
    )
    return {
        "board_holes": len(holes),
        "enclosure_points": len(standoffs),
        "matched": matched,
        "tolerance_mm": tolerance,
    }


async def _propose(twin: Any, pid: UUID, outcome: ProposalOutcome, **kwargs: Any) -> None:
    from twin_core.impact.interfaces import declare_interface

    edge, created = await declare_interface(
        twin, pid, declared_by="inferred", actor="metaforge:inference", **kwargs
    )
    (outcome.proposed if created else outcome.already).append(edge)


async def _mechanical_to_eda(
    twin: Any, pid: UUID, outcome: ProposalOutcome, only: str | None
) -> None:
    boards = await _heads(twin, pid, "pcb_layout")
    enclosures = [
        (i, n) for i, n in await _heads(twin, pid, "cad_model", "assembly") if _is_enclosure(n)
    ]
    for enc_item, enc_node in enclosures:
        for board_item, board_node in boards:
            if only and only not in (enc_item.key, board_item.key):
                continue
            board = getattr(board_node, "metadata", None) or {}
            enc = getattr(enc_node, "metadata", None) or {}
            mount = _mount_evidence(board, enc)
            if mount is not None:
                share = mount["matched"] / max(1, mount["board_holes"])
                await _propose(
                    twin,
                    pid,
                    outcome,
                    type="mounts_to",
                    source_ref=enc_item.key,
                    target_ref=board_item.key,
                    confidence=round(0.5 + 0.45 * share, 2),
                    coupled_parameters=["mounting_holes", "mounting_standoffs"],
                    rationale=(
                        f"{mount['matched']} of {mount['board_holes']} board mounting holes sit "
                        f"on the enclosure's mounting points (within {mount['tolerance_mm']} mm)"
                    ),
                    evidence={"read": "mounting_holes, mounting_standoffs", **mount},
                )
            else:
                outcome.skipped.append(
                    f"{enc_item.key} and {board_item.key}: no mounting holes or standoffs recorded"
                )
            outline = board.get("dimensions") or board.get("board_outline")
            if outline and enc.get("dimensions"):
                await _propose(
                    twin,
                    pid,
                    outcome,
                    type="clearance_to",
                    source_ref=enc_item.key,
                    target_ref=board_item.key,
                    confidence=0.6,
                    coupled_parameters=[
                        "dimensions",
                        "board_outline",
                        "internal_clearance",
                        "component_max_height",
                    ],
                    rationale="the enclosure's inside must clear the board outline",
                    evidence={
                        "read": "dimensions",
                        "board": outline,
                        "enclosure": enc.get("dimensions"),
                    },
                )
            if board.get("connectors") and enc.get("cutouts"):
                await _propose(
                    twin,
                    pid,
                    outcome,
                    type="connects_via",
                    source_ref=board_item.key,
                    target_ref=enc_item.key,
                    confidence=0.6,
                    coupled_parameters=["connectors", "cutouts"],
                    rationale="the board's connectors meet the enclosure's cut-outs",
                    evidence={
                        "read": "connectors, cutouts",
                        "connectors": len(board.get("connectors") or []),
                        "cutouts": len(enc.get("cutouts") or []),
                    },
                )


async def _pinmap_to_firmware(
    twin: Any, pid: UUID, outcome: ProposalOutcome, only: str | None
) -> None:
    pinmaps = await _heads(twin, pid, "pinmap")
    firmwares = await _heads(twin, pid, "firmware_source")
    if not pinmaps or not firmwares:
        return
    sources = {
        f_item.key: recorded_files(f_node, FIRMWARE_SUFFIXES) for f_item, f_node in firmwares
    }
    for pin_item, pin_node in pinmaps:
        names = pinmap_names(parse_pinmap(recorded_files(pin_node)))
        if not names:
            outcome.skipped.append(f"{pin_item.key}: no pins, peripherals or nets could be read")
            continue
        for fw_item, _fw_node in firmwares:
            if only and only not in (pin_item.key, fw_item.key):
                continue
            refs = firmware_references(names, sources.get(fw_item.key, {}))
            if not refs:
                outcome.skipped.append(
                    f"{pin_item.key} and {fw_item.key}: the firmware names none of its pins"
                )
                continue
            modules = sorted({f for files in refs.values() for f in files})
            await _propose(
                twin,
                pid,
                outcome,
                type="pin_maps_to",
                source_ref=pin_item.key,
                target_ref=fw_item.key,
                confidence=round(min(0.95, 0.4 + 0.1 * len(refs)), 2),
                coupled_parameters=sorted(refs),
                rationale=(
                    f"the firmware names {len(refs)} of the pin map's pins or peripherals "
                    f"in {len(modules)} file{'s' if len(modules) != 1 else ''}"
                ),
                evidence={
                    "read": "pin map and firmware sources",
                    "matched": refs,
                    "modules": modules,
                },
            )


async def propose_interfaces(
    twin: Any, project_id: UUID | str, *, item_key: str | None = None
) -> ProposalOutcome:
    """Propose the interface edges the project's recorded data supports.

    ``item_key`` limits the pass to pairs that include that item. Idempotent:
    a link already stored (or removed by a person) is left as it is.
    """
    pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    outcome = ProposalOutcome()
    with tracer.start_as_current_span("twin.interfaces.propose") as span:
        span.set_attribute("project_id", str(pid))
        if item_key:
            span.set_attribute("item.key", item_key)
        for step in (_mechanical_to_eda, _pinmap_to_firmware):
            try:
                await step(twin, pid, outcome, item_key)
            except Exception as exc:  # noqa: BLE001 - one source must not stop the other
                span.record_exception(exc)
                logger.warning("interface_proposal_failed", step=step.__name__, error=str(exc))
                outcome.skipped.append(f"{step.__name__.strip('_')}: {exc}")
        span.set_attribute("interfaces.proposed", len(outcome.proposed))
        logger.info(
            "interface_edges_proposed",
            project_id=str(pid),
            item_key=item_key,
            proposed=[e.id for e in outcome.proposed],
            already=len(outcome.already),
            skipped=len(outcome.skipped),
        )
        return outcome


# ---------------------------------------------------------------------------
# A pin map's parameter diff for a head move
# ---------------------------------------------------------------------------


def _entry_map(entries: list[PinEntry]) -> dict[str, PinEntry]:
    return {e.key or e.pin: e for e in entries}


def diff_pinmaps(old: list[PinEntry], new: list[PinEntry]) -> list[str]:
    """The pin, peripheral and net names whose rows differ between two pin maps."""
    before, after = _entry_map(old), _entry_map(new)
    changed: set[str] = set()
    for key in set(before) | set(after):
        a, b = before.get(key), after.get(key)
        if a == b:
            continue
        for entry in (a, b):
            if entry is not None:
                changed |= entry.names()
    return sorted(changed)


async def pinmap_parameter_diff(twin: Any, item: Any, old: Any, new: Any) -> list[str] | None:
    """The parameter extractor installed for head moves: pin maps only.

    ``None`` for anything else (unknown, which never narrows the walk), and
    for a pin map whose files cannot be read.
    """
    if getattr(item, "item_type", "") != "pinmap":
        return None
    old_files, new_files = recorded_files(old), recorded_files(new)
    old_entries, new_entries = parse_pinmap(old_files), parse_pinmap(new_files)
    if not old_entries and not new_entries:
        return None
    if _residual(old_files) != _residual(new_files):
        # Something besides the pin rows changed (a clock, a package): every
        # firmware module may depend on it, so the change is not narrowed.
        return None
    diff = diff_pinmaps(old_entries, new_entries)
    if not diff and old_files != new_files:
        return None
    return diff


def _strip_pins(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_pins(v)
            for k, v in value.items()
            if k != "pins" and not (isinstance(v, dict) and "pin" in v)
        }
    if isinstance(value, list):
        return [_strip_pins(v) for v in value]
    return value


def _residual(files: dict[str, bytes]) -> str:
    """A JSON pin map without its pin rows; CSV rows are all pin rows."""
    parts: list[str] = []
    for rel, data in sorted(files.items()):
        text = data.decode("utf-8", errors="replace")
        if PurePosixPath(rel).suffix.lower() == ".json" or text.lstrip().startswith(("{", "[")):
            try:
                parts.append(json.dumps(_strip_pins(json.loads(text)), sort_keys=True))
            except ValueError:
                parts.append(text)
    return "\n".join(parts)
