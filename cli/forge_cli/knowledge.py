"""``forge knowledge`` subcommands (MET-443).

Today: a single ``ingest-datasheet`` command that pipes a manufacturer
PDF through the parser and the Twin API in one shot. Runs in-process
against whatever graph backend ``InMemoryTwinAPI.create_from_env``
selects (Neo4j when ``NEO4J_URI`` is set, in-memory otherwise).

Exit codes:

* 0 — success (either first ingest or idempotent re-ingest)
* 1 — file not found at the given path
* 2 — PDF parser failed (corrupt file, no extractable text, etc.)
* 3 — Twin ingest failed
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Wire ``forge knowledge ingest-datasheet`` into the top-level parser."""
    knowledge = subparsers.add_parser(
        "knowledge",
        help="Knowledge layer commands (datasheet ingest, etc.)",
    )
    knowledge_sub = knowledge.add_subparsers(
        dest="knowledge_command",
        help="Subcommands",
    )

    ingest_ds = knowledge_sub.add_parser(
        "ingest-datasheet",
        help="Parse a manufacturer datasheet PDF and ingest it into the Twin",
    )
    ingest_ds.add_argument(
        "path",
        help="Path to the datasheet PDF on disk.",
    )
    ingest_ds.add_argument(
        "--mpn",
        required=True,
        help="Manufacturer part number this datasheet covers.",
    )
    ingest_ds.add_argument(
        "--manufacturer",
        required=True,
        help="Manufacturer name (e.g. STMicroelectronics, Espressif).",
    )
    ingest_ds.add_argument(
        "--revision",
        required=True,
        help='Datasheet revision string (e.g. "rev9", "v2.4").',
    )
    ingest_ds.add_argument(
        "--source-url",
        dest="source_url",
        default=None,
        help="Optional canonical URL on the manufacturer's site.",
    )


def handle_knowledge(args: argparse.Namespace, _client: Any) -> dict[str, Any] | None:
    """Dispatch the ``knowledge`` subcommand."""
    if args.knowledge_command == "ingest-datasheet":
        return _run_ingest_datasheet(args)
    # Unknown / no subcommand — print help, dispatcher exits 1.
    return {"error": "missing knowledge subcommand", "hint": "see `forge knowledge --help`"}


def _run_ingest_datasheet(args: argparse.Namespace) -> dict[str, Any] | None:
    """Read the PDF, parse, ingest, return a summary dict.

    The TwinAPI is closed (`aclose`) in a `finally` so Neo4j-backed
    runs don't leak a driver when the user Ctrl-C's mid-ingest.
    """
    import sys

    path = Path(args.path)
    if not path.is_file():
        print(f"Error: file not found at {path}", file=sys.stderr)
        sys.exit(1)

    return asyncio.run(_ingest_async(path, args))


async def _ingest_async(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    import sys

    # Lazy imports keep the rest of the CLI fast — pdfplumber + the
    # Twin bootstrap aren't free on cold start.
    from digital_twin.datasheets import (
        PdfDependencyError,
        parse_datasheet_pdf,
    )
    from twin_core.api import InMemoryTwinAPI

    try:
        datasheet = parse_datasheet_pdf(
            path,
            mpn=args.mpn,
            manufacturer=args.manufacturer,
            revision=args.revision,
            source_url=args.source_url,
        )
    except PdfDependencyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        print(f"Error parsing PDF: {exc}", file=sys.stderr)
        sys.exit(2)

    twin = await InMemoryTwinAPI.create_from_env()
    try:
        try:
            persisted = await twin.ingest_datasheet(datasheet)
        except Exception as exc:  # noqa: BLE001
            print(f"Error ingesting datasheet: {exc}", file=sys.stderr)
            sys.exit(3)

        # Was this idempotent (existing file_hash returned)? — same id
        # as the parsed model means a fresh insert; mismatch means a
        # prior ingest returned the existing node.
        idempotent = persisted.id != datasheet.id

        # Detect supersedes link by walking the chain for this MPN.
        # When two or more revisions exist for the MPN, the newly
        # ingested one points at its predecessor.
        history = await twin.find_datasheets_by_mpn(datasheet.mpn)
        superseded_revision = _superseded_revision(persisted, history)

        return _format_summary(persisted, idempotent, superseded_revision)
    finally:
        await twin.aclose()


def _superseded_revision(
    current: Any,
    history: list[Any],
) -> str | None:
    """Return the prior revision label this ingest replaced, if any.

    Used purely for the CLI summary; the in-graph link is owned by
    TwinAPI.ingest_datasheet.
    """
    if len(history) <= 1:
        return None
    # Newer revisions point at older via SUPERSEDES; here the simpler
    # heuristic is "the next-newest by ingested_at that isn't us".
    by_ingested_at = sorted(history, key=lambda d: d.ingested_at)
    for prev in reversed(by_ingested_at):
        if prev.id != current.id:
            return str(prev.revision)
    return None


def _format_summary(
    datasheet: Any,
    idempotent: bool,
    superseded_revision: str | None,
) -> dict[str, Any]:
    return {
        "id": str(datasheet.id) if isinstance(datasheet.id, UUID) else datasheet.id,
        "mpn": datasheet.mpn,
        "manufacturer": datasheet.manufacturer,
        "revision": datasheet.revision,
        "page_count": datasheet.page_count,
        "file_hash": datasheet.file_hash[:12] + "…",
        "status": "already-ingested" if idempotent else "ingested",
        "supersedes": superseded_revision,
    }
