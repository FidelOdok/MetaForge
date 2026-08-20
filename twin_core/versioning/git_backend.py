"""Git-backed VersionEngine — real git commits/branches/merges/diffs.

``InMemoryVersionEngine`` (see ``branch.py``) reimplements a commit DAG,
branch pointers, and three-way merge inside Neo4j. That duplicates what
git already does correctly, and it can never produce a meaningful diff
for CAD work products: content hashes only ever say "changed" or "not
changed," never *what* changed.

``GitVersionEngine`` implements the same ``VersionEngine`` protocol but
delegates branch/commit/merge/diff/log to a real git repository. Each
work product is materialized as a file under ``work_products/<id>`` in
the repo's working tree — either its actual content (when the caller
has real bytes, e.g. a CadQuery/FreeCAD generation script) or, for
work products MetaForge only knows by content hash (e.g. imported
vendor STEP files with no source-of-truth script), the hash string
itself. Either way, every commit is a real git commit object, `git
diff` gives real output, and `git merge` performs a real three-way
merge — non-overlapping changes auto-resolve instead of an all-or-
nothing content-hash conflict.

Only the ``git`` CLI is used (via ``subprocess``) — no GitPython or
other dependency.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path
from uuid import UUID

from twin_core.graph_engine import GraphEngine
from twin_core.models.base import EdgeBase
from twin_core.models.enums import EdgeType, NodeType
from twin_core.models.version import Version, VersionDiff, WorkProductChange
from twin_core.versioning.branch import VersionEngine
from twin_core.versioning.merge import ConflictDetail, MergeConflict


class GitCommandError(RuntimeError):
    """A git subprocess invocation failed."""


class GitVersionEngine(VersionEngine):
    """VersionEngine backed by a real git repository.

    Args:
        graph: The GraphEngine backing WorkProduct/Version nodes (git
            never stores WorkProduct metadata itself — only file content
            for the purpose of diffing).
        repo_path: Directory to hold the git repository. Created (and
            ``git init``-ed) if it doesn't already exist.
    """

    def __init__(self, graph: GraphEngine, repo_path: str | Path) -> None:
        self._graph = graph
        self._repo = Path(repo_path)
        self._repo.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._branches: dict[str, UUID | None] = {}
        self._sha_to_version: dict[str, UUID] = {}
        if not (self._repo / ".git").is_dir():
            self._git_sync(["init", "--quiet"])

    # --- git plumbing -----------------------------------------------

    def _git_sync(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-C", str(self._repo), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise GitCommandError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result

    async def _git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(self._git_sync, args, check)

    async def _has_head(self) -> bool:
        result = await self._git(["rev-parse", "--verify", "HEAD"], check=False)
        return result.returncode == 0

    def _wp_path(self, work_product_id: UUID) -> Path:
        return self._repo / "work_products" / str(work_product_id)

    @staticmethod
    def _content_hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    async def _version_for_sha(self, sha: str) -> UUID | None:
        if sha in self._sha_to_version:
            return self._sha_to_version[sha]
        # Cold cache (e.g. after a process restart) — recover by scanning
        # Version nodes for a matching git_commit_sha.
        versions = await self._graph.list_nodes(node_type=NodeType.VERSION)
        for node in versions:
            node_sha = getattr(node, "git_commit_sha", None)
            if node_sha:
                self._sha_to_version[node_sha] = node.id
        return self._sha_to_version.get(sha)

    # --- VersionEngine protocol ---------------------------------------

    async def create_branch(self, name: str, from_version: UUID | None = None) -> str:
        if name in self._branches:
            raise ValueError(f"Branch '{name}' already exists")

        if from_version is not None:
            node = await self._graph.get_node(from_version)
            if node is None:
                raise KeyError(f"Version {from_version} not found")
            sha = getattr(node, "git_commit_sha", None)
            self._branches[name] = from_version
            if sha:
                await self._git(["branch", name, sha])
        elif "main" in self._branches:
            head_id = self._branches["main"]
            self._branches[name] = head_id
            if head_id is not None:
                head_node = await self._graph.get_node(head_id)
                sha = getattr(head_node, "git_commit_sha", None) if head_node else None
                if sha:
                    await self._git(["branch", name, sha])
        else:
            self._branches[name] = None

        return name

    async def commit(
        self,
        branch: str,
        message: str,
        work_product_ids: list[UUID],
        author: str,
        content: dict[UUID, bytes] | None = None,
    ) -> Version:
        content = content or {}

        async with self._lock:
            if branch not in self._branches:
                raise KeyError(f"Branch '{branch}' does not exist")

            parent_id = self._branches[branch]
            parent_sha: str | None = None
            if parent_id is not None:
                parent_node = await self._graph.get_node(parent_id)
                parent_sha = getattr(parent_node, "git_commit_sha", None) if parent_node else None

            if parent_sha is not None:
                await self._git(["checkout", branch])
            elif await self._has_head():
                # Other branches already have history, but this one is a
                # fresh lineage — start a true orphan so it doesn't
                # inherit unrelated commits/files.
                await self._git(["checkout", "--orphan", branch])
                await self._git(["rm", "-rf", "--cached", "."], check=False)
                shutil.rmtree(self._repo / "work_products", ignore_errors=True)
            else:
                # Completely empty repo — point the unborn HEAD at this
                # branch name before the first-ever commit lands.
                await self._git(["symbolic-ref", "HEAD", f"refs/heads/{branch}"])

            for wp_id in work_product_ids:
                node = await self._graph.get_node(wp_id)
                if node is None:
                    raise KeyError(f"WorkProduct {wp_id} not found in graph")
                data = content.get(wp_id)
                if data is None:
                    data = getattr(node, "content_hash", "").encode()
                path = self._wp_path(wp_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

            await self._git(["add", "-A"])
            author_email = f"{author.replace(' ', '_')}@metaforge.local"
            await self._git(
                [
                    "-c",
                    f"user.name={author}",
                    "-c",
                    f"user.email={author_email}",
                    "commit",
                    "--allow-empty",
                    "-m",
                    message,
                ]
            )
            sha = (await self._git(["rev-parse", "HEAD"])).stdout.strip()
            tree_sha = (await self._git(["rev-parse", "HEAD^{tree}"])).stdout.strip()

            version = Version(
                branch_name=branch,
                parent_id=parent_id,
                commit_message=message,
                snapshot_hash=tree_sha,
                author=author,
                work_product_ids=work_product_ids,
                git_commit_sha=sha,
            )
            await self._graph.add_node(version)

            if parent_id is not None:
                await self._graph.add_edge(
                    EdgeBase(
                        source_id=parent_id, target_id=version.id, edge_type=EdgeType.PARENT_OF
                    )
                )
            for wp_id in work_product_ids:
                await self._graph.add_edge(
                    EdgeBase(source_id=wp_id, target_id=version.id, edge_type=EdgeType.VERSIONED_BY)
                )

            self._branches[branch] = version.id
            self._sha_to_version[sha] = version.id
            return version

    async def merge(
        self,
        source_branch: str,
        target_branch: str,
        message: str,
        author: str,
    ) -> Version:
        if source_branch not in self._branches:
            raise KeyError(f"Branch '{source_branch}' does not exist")
        if target_branch not in self._branches:
            raise KeyError(f"Branch '{target_branch}' does not exist")

        async with self._lock:
            source_head = self._branches[source_branch]
            target_head = self._branches[target_branch]

            await self._git(["checkout", target_branch])
            author_email = f"{author.replace(' ', '_')}@metaforge.local"
            result = await self._git(
                [
                    "-c",
                    f"user.name={author}",
                    "-c",
                    f"user.email={author_email}",
                    "merge",
                    "--no-ff",
                    "-m",
                    message,
                    source_branch,
                ],
                check=False,
            )

            if result.returncode != 0:
                conflicts = await self._collect_conflicts()
                await self._git(["merge", "--abort"], check=False)
                raise MergeConflict(conflicts)

            sha = (await self._git(["rev-parse", "HEAD"])).stdout.strip()
            tree_sha = (await self._git(["rev-parse", "HEAD^{tree}"])).stdout.strip()

            changed_ids: list[UUID] = []
            if target_head is not None:
                target_node = await self._graph.get_node(target_head)
                target_sha = getattr(target_node, "git_commit_sha", None) if target_node else None
                if target_sha:
                    name_status = await self._git(
                        ["diff", "--name-only", target_sha, sha, "--", "work_products/"]
                    )
                    for line in name_status.stdout.splitlines():
                        if line.strip():
                            changed_ids.append(UUID(Path(line).name))

            version = Version(
                branch_name=target_branch,
                parent_id=target_head,
                merge_parent_id=source_head,
                commit_message=message,
                snapshot_hash=tree_sha,
                author=author,
                work_product_ids=changed_ids,
                git_commit_sha=sha,
            )
            await self._graph.add_node(version)

            if target_head is not None:
                await self._graph.add_edge(
                    EdgeBase(
                        source_id=target_head, target_id=version.id, edge_type=EdgeType.PARENT_OF
                    )
                )
            if source_head is not None:
                await self._graph.add_edge(
                    EdgeBase(
                        source_id=source_head, target_id=version.id, edge_type=EdgeType.PARENT_OF
                    )
                )

            self._branches[target_branch] = version.id
            self._sha_to_version[sha] = version.id
            return version

    async def _collect_conflicts(self) -> list[ConflictDetail]:
        status = await self._git(["status", "--porcelain=1"], check=False)
        conflicts: list[ConflictDetail] = []
        for line in status.stdout.splitlines():
            code, _, path = line.partition(" ")
            code = code.strip()
            if code not in {"UU", "AA", "DU", "UD", "AU", "UA"}:
                continue
            path = path.strip()
            try:
                work_product_id = UUID(Path(path).name)
            except ValueError:
                continue
            conflict_type = "structural" if code in {"DU", "UD", "AU", "UA"} else "content"
            conflicts.append(
                ConflictDetail(
                    work_product_id=work_product_id,
                    conflict_type=conflict_type,
                    source_hash=await self._blob_hash_at(":2:" + path),
                    target_hash=await self._blob_hash_at(":3:" + path),
                )
            )
        return conflicts

    async def _blob_hash_at(self, ref: str) -> str | None:
        result = await self._git(["show", ref], check=False)
        if result.returncode != 0:
            return None
        return self._content_hash(result.stdout.encode())

    async def diff(self, version_a: UUID, version_b: UUID) -> VersionDiff:
        node_a = await self._graph.get_node(version_a)
        if node_a is None:
            raise KeyError(f"Version {version_a} not found")
        node_b = await self._graph.get_node(version_b)
        if node_b is None:
            raise KeyError(f"Version {version_b} not found")

        sha_a = getattr(node_a, "git_commit_sha", None)
        sha_b = getattr(node_b, "git_commit_sha", None)

        changes: list[WorkProductChange] = []
        if sha_a and sha_b:
            name_status = await self._git(
                ["diff", "--name-status", sha_a, sha_b, "--", "work_products/"]
            )
            for line in name_status.stdout.splitlines():
                if not line.strip():
                    continue
                status, _, path = line.partition("\t")
                work_product_id = UUID(Path(path).name)

                old_content = await self._show(sha_a, path)
                new_content = await self._show(sha_b, path)
                patch_result = await self._git(["diff", sha_a, sha_b, "--", path])

                if status.startswith("A"):
                    changes.append(
                        WorkProductChange(
                            work_product_id=work_product_id,
                            change_type="added",
                            new_content_hash=self._content_hash(new_content or b""),
                            patch=patch_result.stdout or None,
                        )
                    )
                elif status.startswith("D"):
                    changes.append(
                        WorkProductChange(
                            work_product_id=work_product_id,
                            change_type="deleted",
                            old_content_hash=self._content_hash(old_content or b""),
                            patch=patch_result.stdout or None,
                        )
                    )
                else:
                    changes.append(
                        WorkProductChange(
                            work_product_id=work_product_id,
                            change_type="modified",
                            old_content_hash=self._content_hash(old_content or b""),
                            new_content_hash=self._content_hash(new_content or b""),
                            patch=patch_result.stdout or None,
                        )
                    )

        return VersionDiff(version_a=version_a, version_b=version_b, changes=changes)

    async def _show(self, sha: str, path: str) -> bytes | None:
        result = await self._git(["show", f"{sha}:{path}"], check=False)
        if result.returncode != 0:
            return None
        return result.stdout.encode()

    async def log(self, branch: str, limit: int = 50) -> list[Version]:
        if branch not in self._branches:
            raise KeyError(f"Branch '{branch}' does not exist")

        head_id = self._branches[branch]
        if head_id is None:
            return []

        result = await self._git(["log", branch, f"--max-count={limit}", "--format=%H"])
        history: list[Version] = []
        for sha in result.stdout.splitlines():
            sha = sha.strip()
            if not sha:
                continue
            version_id = await self._version_for_sha(sha)
            if version_id is None:
                continue
            node = await self._graph.get_node(version_id)
            if node is not None:
                history.append(node)  # type: ignore[arg-type]
        return history

    async def get_head(self, branch: str) -> Version:
        if branch not in self._branches:
            raise KeyError(f"Branch '{branch}' does not exist")

        head_id = self._branches[branch]
        if head_id is None:
            raise KeyError(f"Branch '{branch}' has no commits")

        node = await self._graph.get_node(head_id)
        if node is None:
            raise KeyError(f"Version {head_id} not found")
        return node  # type: ignore[return-value]
