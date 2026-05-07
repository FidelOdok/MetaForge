"""``forge sources list/show/delete`` — knowledge corpus management (MET-411).

Wraps the ``GET /api/v1/knowledge/sources``, ``GET .../sources/{path}``,
and ``DELETE .../sources/{path}`` endpoints so users can inspect and
prune the L1 knowledge corpus from the command line. The same data is
also exposed through the ``metaforge://knowledge/sources`` MCP resource
(L1-B1) — this CLI is the third surface for the same store.

Behaviour summary::

    forge sources list                              # default columns
    forge sources list --type design_decision       # filter by knowledge_type
    forge sources list --project <uuid> --limit 25  # tenant + page size
    forge sources show uat://decisions/foo.md       # detail (incl. chunks)
    forge sources delete uat://stale.md --yes       # skip confirm prompt
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import structlog

from cli.forge_cli.client import ForgeClient, ForgeClientNotFound
from cli.forge_cli.formatters import format_output

logger = structlog.get_logger(__name__)


# ``forge sources list`` table columns. Matches the MCP resource shape
# (source_path, knowledge_type, fragment_count, indexed_at) so users
# moving between surfaces see the same data layout.
_LIST_COLUMNS = ["knowledge_type", "source_path", "fragment_count", "indexed_at"]

_EMPTY_LIST_MESSAGE = "No sources ingested yet."


def _row_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Map a wire-level ``SourceSummaryResponse`` dict to a CLI table row.

    The gateway response uses camelCase aliases (``sourcePath``,
    ``knowledgeType``, ``fragmentCount``, ``indexedAt``); the CLI surface
    is snake_case so the table reads naturally for shell users.
    """
    return {
        "source_path": summary.get("sourcePath") or summary.get("source_path") or "",
        "knowledge_type": (summary.get("knowledgeType") or summary.get("knowledge_type") or ""),
        "fragment_count": summary.get("fragmentCount", summary.get("fragment_count", 0)),
        "indexed_at": summary.get("indexedAt") or summary.get("indexed_at") or "",
    }


def _detail_from_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Project a gateway detail response onto the CLI snake_case shape."""
    detail = _row_from_summary(payload)
    detail["metadata"] = payload.get("metadata") or {}
    detail["chunks"] = payload.get("chunks") or []
    return detail


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def handle_sources_list(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Handle ``forge sources list``."""
    try:
        response = client.list_sources(
            knowledge_type=getattr(args, "knowledge_type", None),
            project_id=getattr(args, "project_id", None),
            limit=int(getattr(args, "limit", 100)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "forge_sources_list_failed",
            error=str(exc),
        )
        print(f"Error: failed to list sources: {exc}", file=sys.stderr)
        sys.exit(1)

    sources_raw = response.get("sources") or []
    rows = [_row_from_summary(s) for s in sources_raw]
    logger.info("forge_sources_list", count=len(rows))

    if not rows:
        print(_EMPTY_LIST_MESSAGE)
        return None

    fmt = getattr(args, "output_format", "table")
    if fmt == "json":
        # Preserve the raw envelope for machine consumers.
        print(format_output(response, fmt="json"))
        return None
    print(format_output(rows, fmt=fmt, columns=_LIST_COLUMNS))
    return None


def handle_sources_show(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Handle ``forge sources show <id>``."""
    source_id = args.source_id
    try:
        payload = client.get_source(source_id)
    except ForgeClientNotFound as exc:
        logger.info("forge_sources_show_not_found", source_id=source_id)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("forge_sources_show_failed", source_id=source_id, error=str(exc))
        print(f"Error: failed to fetch source: {exc}", file=sys.stderr)
        sys.exit(1)

    logger.info("forge_sources_show", source_id=source_id)
    detail = _detail_from_response(payload)

    fmt = getattr(args, "output_format", "table")
    if fmt == "json":
        print(format_output(payload, fmt="json"))
        return None

    # Detail view: render as a key/value list rather than a table so
    # the metadata + chunks fields don't get squashed into a single
    # cell. JSON dump for nested fields is the most honest projection.
    print(f"source_path:    {detail['source_path']}")
    print(f"knowledge_type: {detail['knowledge_type']}")
    print(f"fragment_count: {detail['fragment_count']}")
    print(f"indexed_at:     {detail['indexed_at']}")
    metadata = detail["metadata"]
    if metadata:
        print("metadata:")
        for key, value in metadata.items():
            print(f"  {key}: {value}")
    else:
        print("metadata:       (none)")
    chunks = detail["chunks"]
    if chunks:
        print(f"chunks:         {len(chunks)}")
        for idx, chunk in enumerate(chunks):
            heading = chunk.get("heading") or ""
            preview = (chunk.get("content") or "")[:120].replace("\n", " ")
            suffix = f" — {heading}" if heading else ""
            print(f"  [{idx}]{suffix}: {preview}")
    else:
        print("chunks:         (none)")
    return None


def handle_sources_delete(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Handle ``forge sources delete <id> [--yes]``."""
    source_id = args.source_id
    if not getattr(args, "yes", False):
        try:
            response = input(f"Delete knowledge source {source_id!r}? [y/N]: ")
        except EOFError:
            response = ""
        if response.strip().lower() not in {"y", "yes"}:
            logger.info("forge_sources_delete_cancelled", source_id=source_id)
            print("Aborted.")
            return None

    try:
        result = client.delete_source(source_id)
    except ForgeClientNotFound as exc:
        logger.info("forge_sources_delete_not_found", source_id=source_id)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("forge_sources_delete_failed", source_id=source_id, error=str(exc))
        print(f"Error: failed to delete source: {exc}", file=sys.stderr)
        sys.exit(1)

    deleted = result.get("deletedChunks", result.get("deleted_chunks", 0))
    logger.info("forge_sources_delete", source_id=source_id, deleted_chunks=int(deleted))
    print(f"Deleted {deleted} chunk(s) for source {source_id!r}.")
    return None


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def register_subparser(subparsers: Any) -> None:
    """Register the ``forge sources`` subcommand group."""
    sources_parser = subparsers.add_parser(
        "sources",
        help="Manage L1 knowledge sources (list / show / delete)",
    )
    sources_sub = sources_parser.add_subparsers(
        dest="sources_command",
        help="Sources subcommands",
    )

    list_parser = sources_sub.add_parser("list", help="List ingested sources")
    list_parser.add_argument(
        "--type",
        dest="knowledge_type",
        default=None,
        help="Filter by knowledge type (design_decision, component, ...)",
    )
    list_parser.add_argument(
        "--project",
        dest="project_id",
        default=None,
        help="Filter by project UUID",
    )
    list_parser.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=100,
        help="Maximum number of sources to return (default: 100)",
    )

    show_parser = sources_sub.add_parser("show", help="Show one source's detail")
    show_parser.add_argument("source_id", help="source_path or id of the source")

    delete_parser = sources_sub.add_parser("delete", help="Delete every chunk for a source")
    delete_parser.add_argument("source_id", help="source_path or id of the source")
    delete_parser.add_argument(
        "--yes",
        action="store_true",
        dest="yes",
        help="Skip the interactive confirmation prompt",
    )


def handle_sources(args: argparse.Namespace, client: ForgeClient) -> Any:
    """Top-level dispatcher for ``forge sources``."""
    cmd = getattr(args, "sources_command", None)
    if cmd == "list":
        return handle_sources_list(args, client)
    if cmd == "show":
        return handle_sources_show(args, client)
    if cmd == "delete":
        return handle_sources_delete(args, client)
    print("Error: specify a sources subcommand (list, show, delete)", file=sys.stderr)
    sys.exit(1)
