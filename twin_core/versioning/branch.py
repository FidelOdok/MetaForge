"""Version engine — ABC and in-memory implementation for Git-like branching."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections import deque
from uuid import UUID

from twin_core.graph_engine import GraphEngine
from twin_core.models.base import EdgeBase
from twin_core.models.enums import EdgeType
from twin_core.models.version import Version, VersionDiff
from twin_core.versioning.diff import compute_diff
from twin_core.versioning.merge import MergeConflict, detect_conflicts, perform_merge


class VersionEngine(ABC):
    """Abstract interface for Git-like versioning of the work_product graph.

    Implementations manage branches, commits, merges, diffs, and history.
    """

    @abstractmethod
    async def create_branch(self, name: str, from_version: UUID | None = None) -> str:
        """Create a new branch, optionally forking from a specific version.

        If from_version is None, forks from the HEAD of "main".

        Returns:
            The branch name.

        Raises:
            ValueError: If branch name already exists.
            KeyError: If from_version doesn't exist.
        """
        ...

    @abstractmethod
    async def commit(
        self,
        branch: str,
        message: str,
        work_product_ids: list[UUID],
        author: str,
    ) -> Version:
        """Create a new version on the given branch.

        Captures a snapshot of all tracked work_products, overlaying changes
        from the provided work_product_ids.

        Returns:
            The newly created Version node.

        Raises:
            KeyError: If branch doesn't exist or an work_product_id is not in the graph.
        """
        ...

    @abstractmethod
    async def merge(
        self,
        source_branch: str,
        target_branch: str,
        message: str,
        author: str,
    ) -> Version:
        """Merge source_branch into target_branch.

        Uses three-way merge with common ancestor detection.

        Returns:
            The merge commit Version node.

        Raises:
            KeyError: If either branch doesn't exist.
            MergeConflict: If conflicting changes are detected.
        """
        ...

    @abstractmethod
    async def diff(self, version_a: UUID, version_b: UUID) -> VersionDiff:
        """Compute the diff between two versions.

        Returns:
            VersionDiff with added/modified/deleted work_products.

        Raises:
            KeyError: If either version doesn't exist.
        """
        ...

    @abstractmethod
    async def log(self, branch: str, limit: int = 50) -> list[Version]:
        """Return commit history for a branch, newest first.

        Args:
            branch: Branch name.
            limit: Maximum number of versions to return.

        Returns:
            List of Version nodes, ordered newest to oldest.

        Raises:
            KeyError: If branch doesn't exist.
        """
        ...

    @abstractmethod
    async def get_head(self, branch: str) -> Version:
        """Get the HEAD version of a branch.

        Returns:
            The Version node at the branch HEAD.

        Raises:
            KeyError: If branch doesn't exist.
        """
        ...


class InMemoryVersionEngine(VersionEngine):
    """In-memory implementation of VersionEngine backed by a GraphEngine.

    Stores Version nodes and PARENT_OF/VERSIONED_BY edges in the graph.
    Maintains internal dicts for branch heads and snapshots.
    """

    def __init__(self, graph: GraphEngine) -> None:
        self._graph = graph
        self._branches: dict[str, UUID] = {}
        self._snapshots: dict[UUID, dict[UUID, str]] = {}

    @staticmethod
    def _compute_snapshot_hash(snapshot: dict[UUID, str]) -> str:
        """Compute a deterministic SHA-256 hash for a snapshot."""
        items = sorted((str(k), v) for k, v in snapshot.items())
        payload = json.dumps(items, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    async def create_branch(self, name: str, from_version: UUID | None = None) -> str:
        if name in self._branches:
            raise ValueError(f"Branch '{name}' already exists")

        if from_version is not None:
            node = await self._graph.get_node(from_version)
            if node is None:
                raise KeyError(f"Version {from_version} not found")
            self._branches[name] = from_version
            self._snapshots.setdefault(from_version, {})
        elif "main" in self._branches:
            head_id = self._branches["main"]
            self._branches[name] = head_id
        else:
            # No main branch yet — create an empty branch
            self._branches[name] = None  # type: ignore[assignment]

        return name

    async def commit(
        self,
        branch: str,
        message: str,
        work_product_ids: list[UUID],
        author: str,
    ) -> Version:
        if branch not in self._branches:
            raise KeyError(f"Branch '{branch}' does not exist")

        parent_id = self._branches[branch]

        # Inherit parent snapshot or start fresh
        if parent_id is not None:
            snapshot = dict(self._snapshots.get(parent_id, {}))
        else:
            snapshot = {}

        # Overlay changes from the provided work_product IDs
        for aid in work_product_ids:
            node = await self._graph.get_node(aid)
            if node is None:
                raise KeyError(f"WorkProduct {aid} not found in graph")
            snapshot[aid] = node.content_hash  # type: ignore[union-attr]

        snapshot_hash = self._compute_snapshot_hash(snapshot)

        version = Version(
            branch_name=branch,
            parent_id=parent_id,
            commit_message=message,
            snapshot_hash=snapshot_hash,
            author=author,
            work_product_ids=work_product_ids,
        )

        await self._graph.add_node(version)

        # Create PARENT_OF edge: parent → child
        if parent_id is not None:
            await self._graph.add_edge(
                EdgeBase(
                    source_id=parent_id,
                    target_id=version.id,
                    edge_type=EdgeType.PARENT_OF,
                )
            )

        # Create VERSIONED_BY edges: work_product → version
        for aid in work_product_ids:
            await self._graph.add_edge(
                EdgeBase(
                    source_id=aid,
                    target_id=version.id,
                    edge_type=EdgeType.VERSIONED_BY,
                )
            )

        # Update branch head and store snapshot
        self._branches[branch] = version.id
        self._snapshots[version.id] = snapshot

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

        source_head = self._branches[source_branch]
        target_head = self._branches[target_branch]

        ancestor_id = await self._find_common_ancestor(source_head, target_head)

        ancestor_snap = self._snapshots.get(ancestor_id, {}) if ancestor_id else {}
        source_snap = self._snapshots.get(source_head, {})
        target_snap = self._snapshots.get(target_head, {})

        # detect_conflicts raises nothing — it returns a list
        conflicts = detect_conflicts(ancestor_snap, source_snap, target_snap)
        if conflicts:
            raise MergeConflict(conflicts)

        merged_snap = perform_merge(ancestor_snap, source_snap, target_snap)
        snapshot_hash = self._compute_snapshot_hash(merged_snap)

        # Determine which work_products changed in the merge
        changed_ids = []
        for aid in set(merged_snap) | set(target_snap):
            if merged_snap.get(aid) != target_snap.get(aid):
                changed_ids.append(aid)

        version = Version(
            branch_name=target_branch,
            parent_id=target_head,
            merge_parent_id=source_head,
            commit_message=message,
            snapshot_hash=snapshot_hash,
            author=author,
            work_product_ids=changed_ids,
        )

        await self._graph.add_node(version)

        # PARENT_OF from target head
        await self._graph.add_edge(
            EdgeBase(
                source_id=target_head,
                target_id=version.id,
                edge_type=EdgeType.PARENT_OF,
            )
        )
        # PARENT_OF from source head (merge parent)
        await self._graph.add_edge(
            EdgeBase(
                source_id=source_head,
                target_id=version.id,
                edge_type=EdgeType.PARENT_OF,
            )
        )

        self._branches[target_branch] = version.id
        self._snapshots[version.id] = merged_snap

        return version

    async def diff(self, version_a: UUID, version_b: UUID) -> VersionDiff:
        node_a = await self._graph.get_node(version_a)
        if node_a is None:
            raise KeyError(f"Version {version_a} not found")
        node_b = await self._graph.get_node(version_b)
        if node_b is None:
            raise KeyError(f"Version {version_b} not found")

        snap_a = self._snapshots.get(version_a, {})
        snap_b = self._snapshots.get(version_b, {})

        return compute_diff(snap_a, snap_b, version_a, version_b)

    async def log(self, branch: str, limit: int = 50) -> list[Version]:
        if branch not in self._branches:
            raise KeyError(f"Branch '{branch}' does not exist")

        head_id = self._branches[branch]
        if head_id is None:
            return []

        history: list[Version] = []
        current_id: UUID | None = head_id

        while current_id is not None and len(history) < limit:
            node = await self._graph.get_node(current_id)
            if node is None:
                break
            history.append(node)  # type: ignore[arg-type]
            current_id = node.parent_id  # type: ignore[union-attr]

        return history

    async def get_head(self, branch: str) -> Version:
        if branch not in self._branches:
            raise KeyError(f"Branch '{branch}' does not exist")

        head_id = self._branches[branch]
        if head_id is None:
            raise KeyError(f"Branch '{branch}' has no commits")

        node = await self._graph.get_node(head_id)
        return node  # type: ignore[return-value]

    async def _find_common_ancestor(self, id_a: UUID, id_b: UUID) -> UUID | None:
        """Find the common ancestor of two versions using BFS up PARENT_OF edges."""
        ancestors_a: set[UUID] = set()
        ancestors_b: set[UUID] = set()

        queue_a: deque[UUID] = deque([id_a])
        queue_b: deque[UUID] = deque([id_b])

        # Interleaved BFS from both sides
        while queue_a or queue_b:
            if queue_a:
                current = queue_a.popleft()
                if current in ancestors_b:
                    return current
                ancestors_a.add(current)
                node = await self._graph.get_node(current)
                if node is not None:
                    parent_id = getattr(node, "parent_id", None)
                    if parent_id is not None and parent_id not in ancestors_a:
                        queue_a.append(parent_id)
                    merge_parent_id = getattr(node, "merge_parent_id", None)
                    if merge_parent_id is not None and merge_parent_id not in ancestors_a:
                        queue_a.append(merge_parent_id)

            if queue_b:
                current = queue_b.popleft()
                if current in ancestors_a:
                    return current
                ancestors_b.add(current)
                node = await self._graph.get_node(current)
                if node is not None:
                    parent_id = getattr(node, "parent_id", None)
                    if parent_id is not None and parent_id not in ancestors_b:
                        queue_b.append(parent_id)
                    merge_parent_id = getattr(node, "merge_parent_id", None)
                    if merge_parent_id is not None and merge_parent_id not in ancestors_b:
                        queue_b.append(merge_parent_id)

        return None
