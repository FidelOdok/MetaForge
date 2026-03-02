"""Unit tests for the versioning system (branch, commit, merge, diff)."""

from uuid import uuid4

import pytest

from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models import Artifact, ArtifactType, EdgeType, NodeType
from twin_core.versioning import InMemoryVersionEngine, MergeConflict


def _make_artifact(
    name: str = "test",
    content_hash: str = "hash_default",
    domain: str = "mechanical",
) -> Artifact:
    return Artifact(
        name=name,
        type=ArtifactType.CAD_MODEL,
        domain=domain,
        file_path=f"models/{name}.step",
        content_hash=content_hash,
        format="step",
        created_by="human",
    )


@pytest.fixture
async def setup():
    """Create a graph + version engine with a 'main' branch and initial commit."""
    graph = InMemoryGraphEngine()
    veng = InMemoryVersionEngine(graph)

    # Create an initial artifact and commit on main
    art = _make_artifact("root_artifact", content_hash="root_hash")
    await graph.add_node(art)

    await veng.create_branch("main")
    initial = await veng.commit("main", "Initial commit", [art.id], "test-author")

    return graph, veng, art, initial


# --- Branch creation ---


class TestCreateBranch:
    async def test_create_from_main(self, setup):
        graph, veng, art, initial = setup
        name = await veng.create_branch("feature-a")
        assert name == "feature-a"

        head = await veng.get_head("feature-a")
        assert head.id == initial.id

    async def test_create_from_specific_version(self, setup):
        graph, veng, art, initial = setup
        name = await veng.create_branch("feature-b", from_version=initial.id)
        assert name == "feature-b"

        head = await veng.get_head("feature-b")
        assert head.id == initial.id

    async def test_duplicate_name_raises(self, setup):
        graph, veng, art, initial = setup
        await veng.create_branch("dup")
        with pytest.raises(ValueError, match="already exists"):
            await veng.create_branch("dup")

    async def test_create_from_nonexistent_version_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError):
            await veng.create_branch("bad", from_version=uuid4())


# --- Commit ---


class TestCommit:
    async def test_first_commit_on_branch(self, setup):
        graph, veng, art, initial = setup
        await veng.create_branch("dev")

        new_art = _make_artifact("new_part", content_hash="new_hash")
        await graph.add_node(new_art)

        v = await veng.commit("dev", "Add new part", [new_art.id], "alice")
        assert v.branch_name == "dev"
        assert v.commit_message == "Add new part"
        assert v.author == "alice"
        assert v.parent_id == initial.id
        assert new_art.id in v.artifact_ids

    async def test_second_commit_links_parent(self, setup):
        graph, veng, art, initial = setup

        a1 = _make_artifact("a1", content_hash="h1")
        a2 = _make_artifact("a2", content_hash="h2")
        await graph.add_node(a1)
        await graph.add_node(a2)

        v1 = await veng.commit("main", "Commit 1", [a1.id], "bob")
        v2 = await veng.commit("main", "Commit 2", [a2.id], "bob")

        assert v2.parent_id == v1.id

    async def test_multiple_artifacts_in_commit(self, setup):
        graph, veng, art, initial = setup

        arts = []
        for i in range(3):
            a = _make_artifact(f"multi_{i}", content_hash=f"mhash_{i}")
            await graph.add_node(a)
            arts.append(a)

        v = await veng.commit("main", "Multi-artifact commit", [a.id for a in arts], "charlie")
        assert len(v.artifact_ids) == 3

    async def test_snapshot_hash_is_deterministic(self, setup):
        graph, veng, art, initial = setup

        a = _make_artifact("det", content_hash="det_hash")
        await graph.add_node(a)

        v = await veng.commit("main", "Deterministic", [a.id], "test")
        assert len(v.snapshot_hash) == 64  # SHA-256 hex digest

    async def test_commit_to_nonexistent_branch_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError, match="does not exist"):
            await veng.commit("ghost", "msg", [], "test")

    async def test_commit_with_nonexistent_artifact_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError, match="not found"):
            await veng.commit("main", "bad", [uuid4()], "test")

    async def test_snapshot_inherits_parent_artifacts(self, setup):
        """A commit with new artifacts should still contain parent artifacts in snapshot."""
        graph, veng, art, initial = setup

        a2 = _make_artifact("second", content_hash="second_hash")
        await graph.add_node(a2)
        v2 = await veng.commit("main", "Second", [a2.id], "test")

        # Diff from initial to v2 should show a2 as added (root_artifact still present)
        d = await veng.diff(initial.id, v2.id)
        change_types = {c.change_type for c in d.changes}
        assert "added" in change_types
        # Root artifact should NOT be deleted
        deleted_ids = {c.artifact_id for c in d.changes if c.change_type == "deleted"}
        assert art.id not in deleted_ids


# --- get_head ---


class TestGetHead:
    async def test_existing_branch(self, setup):
        graph, veng, art, initial = setup
        head = await veng.get_head("main")
        assert head.id == initial.id

    async def test_nonexistent_branch_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError):
            await veng.get_head("nonexistent")


# --- Log ---


class TestLog:
    async def test_single_commit(self, setup):
        graph, veng, art, initial = setup
        history = await veng.log("main")
        assert len(history) == 1
        assert history[0].id == initial.id

    async def test_multi_commit_history(self, setup):
        graph, veng, art, initial = setup

        a1 = _make_artifact("log1", content_hash="lh1")
        a2 = _make_artifact("log2", content_hash="lh2")
        await graph.add_node(a1)
        await graph.add_node(a2)

        v1 = await veng.commit("main", "Second", [a1.id], "test")
        v2 = await veng.commit("main", "Third", [a2.id], "test")

        history = await veng.log("main")
        assert len(history) == 3
        # Newest first
        assert history[0].id == v2.id
        assert history[1].id == v1.id
        assert history[2].id == initial.id

    async def test_limit_parameter(self, setup):
        graph, veng, art, initial = setup

        for i in range(5):
            a = _make_artifact(f"lim_{i}", content_hash=f"limh_{i}")
            await graph.add_node(a)
            await veng.commit("main", f"Commit {i}", [a.id], "test")

        history = await veng.log("main", limit=3)
        assert len(history) == 3

    async def test_log_nonexistent_branch_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError):
            await veng.log("nope")


# --- Diff ---


class TestDiff:
    async def test_added_artifacts(self, setup):
        graph, veng, art, initial = setup

        new_art = _make_artifact("added", content_hash="added_h")
        await graph.add_node(new_art)
        v2 = await veng.commit("main", "Add artifact", [new_art.id], "test")

        d = await veng.diff(initial.id, v2.id)
        added = [c for c in d.changes if c.change_type == "added"]
        assert len(added) == 1
        assert added[0].artifact_id == new_art.id
        assert added[0].new_content_hash == "added_h"

    async def test_modified_artifacts(self, setup):
        graph, veng, art, initial = setup

        # Update the artifact's content_hash
        await graph.update_node(art.id, {"content_hash": "modified_hash"})
        v2 = await veng.commit("main", "Modify artifact", [art.id], "test")

        d = await veng.diff(initial.id, v2.id)
        modified = [c for c in d.changes if c.change_type == "modified"]
        assert len(modified) == 1
        assert modified[0].artifact_id == art.id
        assert modified[0].old_content_hash == "root_hash"
        assert modified[0].new_content_hash == "modified_hash"

    async def test_no_changes(self, setup):
        graph, veng, art, initial = setup

        # Commit the same artifact with same hash
        v2 = await veng.commit("main", "Same content", [art.id], "test")

        d = await veng.diff(initial.id, v2.id)
        assert len(d.changes) == 0

    async def test_diff_nonexistent_version_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError):
            await veng.diff(initial.id, uuid4())

    async def test_diff_version_ids_match(self, setup):
        graph, veng, art, initial = setup
        a = _make_artifact("x", content_hash="xh")
        await graph.add_node(a)
        v2 = await veng.commit("main", "x", [a.id], "test")

        d = await veng.diff(initial.id, v2.id)
        assert d.version_a == initial.id
        assert d.version_b == v2.id


# --- Merge ---


class TestMerge:
    async def test_clean_merge_non_overlapping(self, setup):
        """Source adds artifact A, target adds artifact B — merge should contain both."""
        graph, veng, art, initial = setup

        await veng.create_branch("feature")

        # Commit on feature
        fa = _make_artifact("feature_art", content_hash="fh")
        await graph.add_node(fa)
        await veng.commit("feature", "Feature work", [fa.id], "dev")

        # Commit on main
        ma = _make_artifact("main_art", content_hash="mh")
        await graph.add_node(ma)
        await veng.commit("main", "Main work", [ma.id], "lead")

        # Merge feature → main
        merge_v = await veng.merge("feature", "main", "Merge feature", "lead")
        assert merge_v.merge_parent_id is not None
        assert merge_v.branch_name == "main"

        # Merged snapshot should have all three artifacts
        snap = veng._snapshots[merge_v.id]
        assert art.id in snap
        assert fa.id in snap
        assert ma.id in snap

    async def test_content_conflict_raises(self, setup):
        """Both branches modify the same artifact differently — should raise MergeConflict."""
        graph, veng, art, initial = setup

        await veng.create_branch("feature")

        # Modify on feature
        await graph.update_node(art.id, {"content_hash": "feature_hash"})
        await veng.commit("feature", "Feature change", [art.id], "dev")

        # Modify on main (different hash)
        await graph.update_node(art.id, {"content_hash": "main_hash"})
        await veng.commit("main", "Main change", [art.id], "lead")

        with pytest.raises(MergeConflict) as exc_info:
            await veng.merge("feature", "main", "Merge", "lead")

        assert len(exc_info.value.conflicts) == 1
        assert exc_info.value.conflicts[0].conflict_type == "content"

    async def test_structural_conflict_raises(self, setup):
        """One branch deletes, other modifies — should raise MergeConflict."""
        graph, veng, art, initial = setup

        # Add an artifact to both branches
        shared = _make_artifact("shared", content_hash="shared_h")
        await graph.add_node(shared)
        await veng.commit("main", "Add shared", [shared.id], "test")

        await veng.create_branch("feature")

        # On feature: modify the shared artifact
        await graph.update_node(shared.id, {"content_hash": "modified_shared"})
        await veng.commit("feature", "Modify shared", [shared.id], "dev")

        # On main: "delete" the shared artifact by committing without it
        # and manually removing from snapshot
        other = _make_artifact("other", content_hash="oh")
        await graph.add_node(other)
        v_main = await veng.commit("main", "Add other", [other.id], "lead")
        # Simulate deletion by removing from snapshot
        del veng._snapshots[v_main.id][shared.id]

        with pytest.raises(MergeConflict) as exc_info:
            await veng.merge("feature", "main", "Merge", "lead")

        conflict_types = {c.conflict_type for c in exc_info.value.conflicts}
        assert "structural" in conflict_types

    async def test_merge_creates_version_with_merge_parent(self, setup):
        graph, veng, art, initial = setup

        await veng.create_branch("feat")
        fa = _make_artifact("fa", content_hash="fah")
        await graph.add_node(fa)
        feat_commit = await veng.commit("feat", "Feat", [fa.id], "dev")

        merge_v = await veng.merge("feat", "main", "Merge feat", "lead")
        assert merge_v.merge_parent_id == feat_commit.id
        assert merge_v.parent_id is not None

    async def test_merge_nonexistent_branch_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError):
            await veng.merge("ghost", "main", "msg", "test")


# --- Edge creation ---


class TestEdgeCreation:
    async def test_parent_of_edges(self, setup):
        """Commits should create PARENT_OF edges between versions."""
        graph, veng, art, initial = setup

        a = _make_artifact("edge_test", content_hash="eth")
        await graph.add_node(a)
        v2 = await veng.commit("main", "Second", [a.id], "test")

        edges = await graph.get_edges(
            initial.id, direction="outgoing", edge_type=EdgeType.PARENT_OF
        )
        assert len(edges) == 1
        assert edges[0].target_id == v2.id

    async def test_versioned_by_edges(self, setup):
        """Commits should create VERSIONED_BY edges from artifacts to versions."""
        graph, veng, art, initial = setup

        a = _make_artifact("vby", content_hash="vbyh")
        await graph.add_node(a)
        v2 = await veng.commit("main", "VBy test", [a.id], "test")

        edges = await graph.get_edges(a.id, direction="outgoing", edge_type=EdgeType.VERSIONED_BY)
        assert len(edges) == 1
        assert edges[0].target_id == v2.id

    async def test_merge_creates_two_parent_edges(self, setup):
        """Merge commits should have PARENT_OF edges from both parents."""
        graph, veng, art, initial = setup

        await veng.create_branch("feat")
        fa = _make_artifact("mfa", content_hash="mfah")
        await graph.add_node(fa)
        feat_head = await veng.commit("feat", "Feat", [fa.id], "dev")

        main_head = await veng.get_head("main")
        merge_v = await veng.merge("feat", "main", "Merge", "lead")

        # Check incoming PARENT_OF edges on merge version
        edges = await graph.get_edges(
            merge_v.id, direction="incoming", edge_type=EdgeType.PARENT_OF
        )
        parent_ids = {e.source_id for e in edges}
        assert main_head.id in parent_ids
        assert feat_head.id in parent_ids

    async def test_version_nodes_stored_in_graph(self, setup):
        """Version nodes should be queryable from the graph engine."""
        graph, veng, art, initial = setup

        versions = await graph.list_nodes(node_type=NodeType.VERSION)
        assert len(versions) == 1
        assert versions[0].id == initial.id
