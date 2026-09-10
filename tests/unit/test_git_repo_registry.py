"""Unit tests for GitRepoRegistry (MET-630)."""

import os

from api_gateway.twin.git_repo_registry import GitRepoRegistry
from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.versioning import GitVersionEngine


class TestGitRepoRegistry:
    def test_same_project_reuses_engine(self, tmp_path) -> None:
        registry = GitRepoRegistry(InMemoryGraphEngine(), tmp_path)
        a = registry.for_project("proj-1")
        b = registry.for_project("proj-1")
        assert a is b

    def test_different_projects_get_separate_repos(self, tmp_path) -> None:
        registry = GitRepoRegistry(InMemoryGraphEngine(), tmp_path)
        a = registry.for_project("proj-1")
        b = registry.for_project("proj-2")
        assert a is not b
        assert isinstance(a, GitVersionEngine)
        assert (tmp_path / "projects" / "proj-1" / ".git").is_dir()
        assert (tmp_path / "projects" / "proj-2" / ".git").is_dir()

    def test_none_project_id_uses_unscoped_repo(self, tmp_path) -> None:
        registry = GitRepoRegistry(InMemoryGraphEngine(), tmp_path)
        registry.for_project(None)
        assert (tmp_path / "projects" / "_unscoped" / ".git").is_dir()

    def test_from_env_without_root_returns_none(self, monkeypatch) -> None:
        monkeypatch.delenv("METAFORGE_VERSION_GIT_ROOT", raising=False)
        assert GitRepoRegistry.from_env(InMemoryGraphEngine()) is None

    def test_from_env_with_root_builds_registry(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("METAFORGE_VERSION_GIT_ROOT", str(tmp_path))
        registry = GitRepoRegistry.from_env(InMemoryGraphEngine())
        assert registry is not None
        engine = registry.for_project("proj-1")
        assert isinstance(engine, GitVersionEngine)
        assert os.path.isdir(tmp_path / "projects" / "proj-1")
