"""Backfill ``project_id`` on every legacy graph node (MET-442).

Run once per dev / staging database after MET-428 Phase 1 lands. Nodes
created **before** the partitioning column existed carry ``project_id =
None`` and are invisible to scoped reads. This script sets them to a
configured default project so the partitioning filter stops hiding
them.

Usage::

    METAFORGE_DEFAULT_PROJECT_ID=11111111-1111-1111-1111-111111111111 \\
        python -m scripts.migrations.backfill_project_id

Options::

    --graph-engine neo4j     (default) — connect via NEO4J_URI/USER/PASSWORD
    --graph-engine in-memory — useful for tests / dry runs against a fresh Twin
    --dry-run                — count nodes that *would* be updated, but don't write

The script is **idempotent**: it only touches nodes whose ``project_id``
is null. A second run reports zero updates.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from twin_core.graph_engine import GraphEngine


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backfill_project_id",
        description=(
            "Set project_id on every graph node that's missing one "
            "(MET-442). Idempotent — touches only null values."
        ),
    )
    parser.add_argument(
        "--graph-engine",
        choices=("neo4j", "in-memory"),
        default="neo4j",
        help=(
            "Backend to migrate. neo4j (default) uses NEO4J_URI; in-memory "
            "is for tests / dry runs against a fresh Twin."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count nodes that would be updated, but don't write.",
    )
    return parser.parse_args(argv)


def _resolve_default_project_id() -> UUID:
    raw = os.environ.get("METAFORGE_DEFAULT_PROJECT_ID")
    if not raw:
        print(
            "Error: METAFORGE_DEFAULT_PROJECT_ID is required. "
            "Set it to a valid UUID before running the migration.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        return UUID(raw)
    except ValueError:
        print(
            f"Error: METAFORGE_DEFAULT_PROJECT_ID is not a valid UUID: {raw!r}",
            file=sys.stderr,
        )
        sys.exit(1)


async def backfill_project_id(
    graph: GraphEngine,
    default_project_id: UUID,
    *,
    dry_run: bool = False,
) -> int:
    """Set ``project_id`` on every node where it is currently null.

    Returns the number of nodes touched (or that would be touched on
    ``dry_run``). Pure on the GraphEngine abstraction so the same
    code path runs against in-memory and Neo4j.
    """
    nodes = await graph.list_nodes()
    touched = 0
    for node in nodes:
        # Pydantic models report missing fields as None via getattr —
        # the legacy nodes that pre-date MET-428 fall into this bucket.
        if getattr(node, "project_id", None) is not None:
            continue
        touched += 1
        if not dry_run:
            await graph.update_node(node.id, {"project_id": default_project_id})
    return touched


async def _run(args: argparse.Namespace, default_project_id: UUID) -> int:
    if args.graph_engine == "neo4j":
        from twin_core.api import InMemoryTwinAPI

        # The from_env factory picks Neo4j when NEO4J_URI is set. We
        # don't run the full TwinAPI here because the migration only
        # needs the graph layer — but reusing the factory keeps the
        # env-handling consistent with the gateway.
        twin = await InMemoryTwinAPI.create_from_env()
        try:
            return await backfill_project_id(
                twin._graph,  # type: ignore[attr-defined]
                default_project_id,
                dry_run=args.dry_run,
            )
        finally:
            await twin.aclose()

    # in-memory: deterministic, dependency-free. Useful for tests.
    from twin_core.graph_engine import InMemoryGraphEngine

    return await backfill_project_id(
        InMemoryGraphEngine(),
        default_project_id,
        dry_run=args.dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    default_project_id = _resolve_default_project_id()

    touched = asyncio.run(_run(args, default_project_id))

    verb = "Would update" if args.dry_run else "Updated"
    print(
        f"{verb} {touched} node(s) with project_id={default_project_id} "
        f"({args.graph_engine})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
