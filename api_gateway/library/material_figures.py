"""Library figures for code that runs outside an MCP tool call (FORGE-446).

Inside a tool call the dispatcher has already attached every material's
library figures. The optimiser, the sensitivity ranker, the design loop and
the evals are also called directly; :func:`with_materials` resolves any
material the current call does not already carry and holds the result for
the block, so ``tool_registry.tools.cadquery.materials`` reads the library
on either path. An unknown material raises ``MaterialResolutionError``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from tool_registry.material_context import current, key, lookup, using
from twin_core.library.material_resolver import resolve_material

__all__ = ["with_materials"]


@asynccontextmanager
async def with_materials(names: Iterable[str | None]) -> AsyncIterator[None]:
    figures = current()
    for name in names:
        if name and lookup(name) is None and key(name) not in figures:
            figures[key(name)] = await resolve_material(name)
    with using(figures):
        yield
