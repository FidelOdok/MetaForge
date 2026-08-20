"""Per-project GitVersionEngine registry (MET-630).

MetaForge "projects" today are pure metadata (a Postgres row + a
``project_id`` attribute stamped on Twin graph nodes) — there is no
per-project filesystem workspace yet. Until that exists, this registry
gives each project its own git repository by keying a subdirectory off
``project_id`` under a single configured root, so independent projects
never share commit history.

Lazily constructs and caches one ``GitVersionEngine`` per project id
(and one for unscoped/legacy work, keyed ``"_unscoped"``).
"""

from __future__ import annotations

import os
from pathlib import Path

from twin_core.graph_engine import GraphEngine
from twin_core.versioning import GitVersionEngine

_UNSCOPED_KEY = "_unscoped"


class GitRepoRegistry:
    """Resolves (and caches) a ``GitVersionEngine`` per MetaForge project."""

    def __init__(self, graph: GraphEngine, root: str | Path) -> None:
        self._graph = graph
        self._root = Path(root)
        self._engines: dict[str, GitVersionEngine] = {}

    def for_project(self, project_id: str | None) -> GitVersionEngine:
        key = project_id or _UNSCOPED_KEY
        engine = self._engines.get(key)
        if engine is None:
            repo_path = self._root / "projects" / key
            engine = GitVersionEngine(self._graph, repo_path)
            self._engines[key] = engine
        return engine

    @classmethod
    def from_env(cls, graph: GraphEngine) -> GitRepoRegistry | None:
        """Build a registry from ``METAFORGE_VERSION_GIT_ROOT``, or None if unset."""
        root = os.environ.get("METAFORGE_VERSION_GIT_ROOT")
        if not root:
            return None
        return cls(graph, root)


# Module-level singleton, set by the server lifespan — mirrors
# get_mcp_bridge()/get_twin()'s pattern so routes can reach the active
# registry without threading it through every call site.
_registry: GitRepoRegistry | None = None


def init_git_registry(registry: GitRepoRegistry | None) -> None:
    global _registry  # noqa: PLW0603
    _registry = registry


def get_git_registry() -> GitRepoRegistry | None:
    return _registry
