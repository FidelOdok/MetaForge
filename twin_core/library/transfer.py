"""The whole library as one file, and back (2026-11-12).

The catalog (processes, materials, components, appearances, ergonomic limits) and the
machine library live in one SQLite file on the gateway's machine. ``export_library`` writes
every current entry, and, when asked, each entry's version history, as one JSON document;
``import_library`` loads such a document into another library.

An import only adds. An entry whose name is already there (the same kind and name for a
catalog entry, the same kind, make and model for a machine model, the same name for a
machine instance) is skipped and reported, never overwritten. An imported entry starts
again at version 1 with ``by`` naming the import; the exported history stays in the file as
the record. A machine instance's ``model_id`` is mapped to the model it now points at.
Nothing secret is carried: a machine connection holds only the name of the environment
variable that holds its credential.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from twin_core.library.catalog import CatalogError, SqliteCatalog
from twin_core.library.machines import MachineLibraryError, SqliteMachineLibrary

logger = structlog.get_logger(__name__)

FORMAT = "metaforge-library"
VERSION = 1
KINDS = ("process", "material", "component", "appearance", "ergonomic_limit")
#: Fields an entry's register call sets itself, dropped before an import registers it.
_OWN = ("id", "version", "updated_at", "kind")


def export_library(
    catalog: SqliteCatalog, machines: SqliteMachineLibrary, *, history: bool = False
) -> dict[str, Any]:
    """Every current catalog entry and machine, optionally with each one's history."""
    entries: dict[str, list[dict[str, Any]]] = {}
    for kind in KINDS:
        rows = []
        for entry in catalog.list(kind):
            row = entry.as_dict()
            if history:
                row["history"] = catalog.history(entry.id)
            rows.append(row)
        entries[kind] = rows
    machine_rows = []
    for machine in machines.list():
        row = machine.as_dict()
        if history:
            row["history"] = machines.history(machine.id)
        machine_rows.append(row)
    counts = {k: len(v) for k, v in entries.items()} | {"machine": len(machine_rows)}
    logger.info("library_exported", counts=counts, history=history)
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": time.time(),
        "counts": counts,
        "entries": entries,
        "machines": machine_rows,
    }


def _fresh(row: dict[str, Any], *, keep_kind: bool = False) -> dict[str, Any]:
    """The row without the fields its register call sets (a machine's kind is its own)."""
    drop = {k for k in _OWN if not (keep_kind and k == "kind")} | {"history"}
    return {k: v for k, v in row.items() if k not in drop}


def import_library(
    document: dict[str, Any],
    catalog: SqliteCatalog,
    machines: SqliteMachineLibrary,
    *,
    by: str,
) -> dict[str, Any]:
    """Add what the document holds and the library lacks; report every entry's outcome."""
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} export (no format field saying so)")
    if document.get("version") != VERSION:
        raise ValueError(f"export version {document.get('version')!r} is not {VERSION}")
    report: dict[str, Any] = {"added": {}, "skipped": {}, "failed": []}

    def tally(outcome: str, kind: str) -> None:
        report[outcome][kind] = report[outcome].get(kind, 0) + 1

    raw_entries = document.get("entries") or {}
    if not isinstance(raw_entries, dict):
        raise ValueError("entries must be an object keyed by kind")
    for kind in KINDS:
        have = {e.name for e in catalog.list(kind)}
        for row in raw_entries.get(kind) or []:
            name = str((row or {}).get("name") or "")
            if name in have:
                tally("skipped", kind)
                continue
            try:
                catalog.register(kind, _fresh(row), by=by)
            except CatalogError as exc:
                report["failed"].append({"kind": kind, "name": name, "reason": str(exc)})
                continue
            have.add(name)
            tally("added", kind)

    rows = [r for r in document.get("machines") or [] if isinstance(r, dict)]
    current = machines.list()
    models = {
        (m.kind, m.make, m.model): m.id for m in current if m.as_dict().get("layer") == "model"
    }
    instances = {m.name for m in current if m.as_dict().get("layer") == "instance"}
    mapped: dict[str, str] = {}
    for row in sorted(rows, key=lambda r: r.get("layer") != "model"):  # models first
        layer = row.get("layer")
        if layer == "model":
            key = (row.get("kind"), row.get("make"), row.get("model"))
            if key in models:
                mapped[str(row.get("id"))] = models[key]
                tally("skipped", "machine")
                continue
        elif str(row.get("name")) in instances:
            tally("skipped", "machine")
            continue
        body = _fresh(row, keep_kind=True)
        if layer == "instance" and body.get("model_id"):
            body["model_id"] = mapped.get(str(body["model_id"]), body["model_id"])
        try:
            made = machines.register(body, by=by)
        except MachineLibraryError as exc:
            label = row.get("name") or f"{row.get('make')} {row.get('model')}"
            report["failed"].append({"kind": "machine", "name": label, "reason": str(exc)})
            continue
        if layer == "model":
            mapped[str(row.get("id"))] = made.id
            models[(row.get("kind"), row.get("make"), row.get("model"))] = made.id
        else:
            instances.add(str(row.get("name")))
        tally("added", "machine")
    logger.info("library_imported", added=report["added"], skipped=report["skipped"],
                failed=len(report["failed"]), by=by)  # fmt: skip
    return report
