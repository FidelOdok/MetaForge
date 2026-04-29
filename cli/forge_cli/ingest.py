"""``forge ingest`` — markdown + PDF ingestion (MET-336).

Wraps ``ForgeClient.ingest_document`` so users can populate the L1
knowledge layer from existing design docs in one shot. Heading-aware
chunking, dedup, and citation metadata are LightRAG's responsibility
through the ``KnowledgeService`` Protocol; this module is purely a
file-system walker + HTTP poster.

Behaviour summary::

    forge ingest README.md                    # single-file ingest
    forge ingest docs/                        # recursive .md + .pdf
    forge ingest docs/ --no-recursive         # only top-level files
    forge ingest README.md --type session     # override knowledge type
    forge ingest docs/ --dry-run              # list files; no HTTP calls
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from cli.forge_cli.client import ForgeClient

# Extensions the L1 layer can ingest. PDF is handled by ``raganything``
# via the ``KnowledgeService`` provider (no client-side parsing needed).
SUPPORTED_EXTENSIONS = frozenset({".md", ".markdown", ".txt", ".pdf"})

# Default knowledge_type when neither --type nor a path-derived
# inference applies.
DEFAULT_KNOWLEDGE_TYPE = "session"

# Path-segment hints. First match wins. Lower-cased before compare.
_PATH_TYPE_HINTS: list[tuple[str, str]] = [
    ("decisions", "design_decision"),
    ("decision", "design_decision"),
    ("adr", "design_decision"),
    ("constraints", "constraint"),
    ("constraint", "constraint"),
    ("failures", "failure"),
    ("failure", "failure"),
    ("components", "component"),
    ("component", "component"),
    ("datasheets", "component"),
]


def _infer_knowledge_type(path: Path) -> str:
    """Infer ``knowledge_type`` from a file path's directory components."""
    parts = [p.lower() for p in path.parts]
    for needle, kt in _PATH_TYPE_HINTS:
        if any(needle in p for p in parts):
            return kt
    return DEFAULT_KNOWLEDGE_TYPE


def _read_file_content(path: Path) -> str:
    """Read file content as UTF-8 text.

    For PDFs we still pass the raw bytes (decoded via latin-1 so the
    payload survives a JSON round-trip) — the gateway hands them to
    ``raganything`` which knows how to parse them. For markdown /
    text we use ``utf-8-sig`` so a leading BOM is silently stripped
    (Windows-defaults paste in a lot of files), and ``errors="replace"``
    so a stray non-UTF-8 byte yields a U+FFFD replacement character
    rather than crashing the whole batch (MET-400).

    The "replace, don't reject" choice for invalid UTF-8 mirrors how
    most ingestion pipelines (Pandoc, ripgrep --no-utf8-strict)
    behave for adopter friendliness — a few replacement characters
    are harmless to RAG retrieval, whereas refusing the file blocks
    the user. Recorded in ``docs/architecture/ai-memory.md``.
    """
    if path.suffix.lower() == ".pdf":
        # Use latin-1 so every byte maps to a unique codepoint and the
        # payload is JSON-encodable. Provider parses bytes back out.
        return path.read_bytes().decode("latin-1")
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _discover_files(target: Path, recursive: bool) -> list[Path]:
    """Walk *target* and return supported files.

    Single-file paths are always included if their extension is
    supported. Directories are walked depth-first; with ``recursive``
    False, only the immediate children are considered.
    """
    if target.is_file():
        if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file extension: {target.suffix!r}. "
                f"Supported: {sorted(SUPPORTED_EXTENSIONS)}"
            )
        return [target]
    if not target.is_dir():
        raise FileNotFoundError(f"Path does not exist: {target}")

    iterator = target.rglob("*") if recursive else target.iterdir()
    return sorted(p for p in iterator if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)


def ingest_path(
    target: Path,
    *,
    client: ForgeClient,
    knowledge_type: str | None = None,
    recursive: bool = True,
    dry_run: bool = False,
    source_work_product_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    request_timeout: float = 300.0,
) -> dict[str, Any]:
    """Ingest one file or every supported file under a directory.

    Returns a dict shaped::

        {
            "ingested": [{"path": str, "chunks_indexed": int, ...}, ...],
            "skipped":  [{"path": str, "reason": str}, ...],
            "failed":   [{"path": str, "error": str}, ...],
            "total":    int,
            "dry_run":  bool,
        }
    """
    target = target.expanduser().resolve()
    files = _discover_files(target, recursive=recursive)

    ingested: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for path in files:
        kt = knowledge_type or _infer_knowledge_type(path)
        if dry_run:
            ingested.append(
                {
                    "path": str(path),
                    "knowledge_type": kt,
                    "chunks_indexed": 0,
                    "dry_run": True,
                }
            )
            continue

        try:
            content = _read_file_content(path)
        except Exception as exc:  # pragma: no cover — IO edge cases
            failed.append({"path": str(path), "error": f"read failed: {exc}"})
            continue

        if not content.strip():
            skipped.append({"path": str(path), "reason": "empty file"})
            continue

        try:
            response = client.ingest_document(
                content=content,
                source_path=str(path),
                knowledge_type=kt,
                source_work_product_id=source_work_product_id,
                metadata=metadata,
                timeout=request_timeout,
            )
        except Exception as exc:
            failed.append({"path": str(path), "error": str(exc)})
            continue

        ingested.append(
            {
                "path": str(path),
                "knowledge_type": kt,
                "chunks_indexed": response.get("chunksIndexed", 0),
                "entry_ids": response.get("entryIds", []),
            }
        )

    return {
        "ingested": ingested,
        "skipped": skipped,
        "failed": failed,
        "total": len(files),
        "dry_run": dry_run,
    }


def handle_ingest(args: Any, client: ForgeClient) -> dict[str, Any]:
    """argparse handler for ``forge ingest``."""
    target = Path(args.path)
    if not target.exists() and not args.dry_run:
        # Surface a clean error before HTTP setup.
        print(f"Error: path does not exist: {target}", file=sys.stderr)
        sys.exit(1)

    metadata: dict[str, Any] | None = None
    if getattr(args, "metadata", None):
        import json

        try:
            metadata = json.loads(args.metadata)
        except json.JSONDecodeError as exc:
            print(f"Error: invalid JSON in --metadata: {exc}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(metadata, dict):
            print("Error: --metadata must be a JSON object", file=sys.stderr)
            sys.exit(1)

    result = ingest_path(
        target,
        client=client,
        knowledge_type=getattr(args, "knowledge_type", None),
        recursive=not getattr(args, "no_recursive", False),
        dry_run=getattr(args, "dry_run", False),
        source_work_product_id=getattr(args, "work_product", None),
        metadata=metadata,
        request_timeout=float(
            os.environ.get("METAFORGE_INGEST_TIMEOUT", str(getattr(args, "timeout", 300.0)))
        ),
    )
    return result
