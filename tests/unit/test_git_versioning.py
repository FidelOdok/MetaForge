"""Unit tests for GitVersionEngine — the real-git-backed VersionEngine."""

from uuid import uuid4

import pytest

from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models import EdgeType, NodeType, WorkProduct, WorkProductType
from twin_core.versioning import GitVersionEngine, MergeConflict


def _make_work_product(
    name: str = "test",
    content_hash: str = "hash_default",
    domain: str = "mechanical",
) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain=domain,
        file_path=f"models/{name}.step",
        content_hash=content_hash,
        format="step",
        created_by="human",
    )


@pytest.fixture
async def setup(tmp_path):
    """Create a graph + git version engine with a 'main' branch and initial commit."""
    graph = InMemoryGraphEngine()
    veng = GitVersionEngine(graph, tmp_path / "repo")

    art = _make_work_product("root_artifact", content_hash="root_hash")
    await graph.add_node(art)

    await veng.create_branch("main")
    initial = await veng.commit("main", "Initial commit", [art.id], "test-author")

    return graph, veng, art, initial


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


class TestCommit:
    async def test_first_commit_on_branch(self, setup):
        graph, veng, art, initial = setup
        await veng.create_branch("dev")

        new_art = _make_work_product("new_part", content_hash="new_hash")
        await graph.add_node(new_art)

        v = await veng.commit("dev", "Add new part", [new_art.id], "alice")
        assert v.branch_name == "dev"
        assert v.commit_message == "Add new part"
        assert v.author == "alice"
        assert v.parent_id == initial.id
        assert new_art.id in v.work_product_ids
        assert v.git_commit_sha is not None

    async def test_second_commit_links_parent(self, setup):
        graph, veng, art, initial = setup

        a1 = _make_work_product("a1", content_hash="h1")
        a2 = _make_work_product("a2", content_hash="h2")
        await graph.add_node(a1)
        await graph.add_node(a2)

        v1 = await veng.commit("main", "Commit 1", [a1.id], "bob")
        v2 = await veng.commit("main", "Commit 2", [a2.id], "bob")

        assert v2.parent_id == v1.id

    async def test_commit_to_nonexistent_branch_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError, match="does not exist"):
            await veng.commit("ghost", "msg", [], "test")

    async def test_commit_with_nonexistent_artifact_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError, match="not found"):
            await veng.commit("main", "bad", [uuid4()], "test")

    async def test_snapshot_inherits_parent_artifacts(self, setup):
        """A new commit should still contain parent work_products in the tree."""
        graph, veng, art, initial = setup

        a2 = _make_work_product("second", content_hash="second_hash")
        await graph.add_node(a2)
        v2 = await veng.commit("main", "Second", [a2.id], "test")

        d = await veng.diff(initial.id, v2.id)
        change_types = {c.change_type for c in d.changes}
        assert "added" in change_types
        deleted_ids = {c.work_product_id for c in d.changes if c.change_type == "deleted"}
        assert art.id not in deleted_ids

    async def test_paths_gives_stable_file_across_fresh_work_product_ids(self, setup):
        """Regenerating with a fresh id each time still evolves ONE git file.

        Without an explicit `paths`, each work_product_id writes to
        `work_products/<id>` — a fresh id every regeneration (as
        geometry_recorder.py does) would write a new file each time
        instead of evolving one, so a path-scoped `git log` on "this
        part" would only ever show its own single commit, not real history.
        """
        graph, veng, art, initial = setup

        gen1_wp = _make_work_product("bracket_gen1", content_hash="h1")
        await graph.add_node(gen1_wp)
        v1 = await veng.commit(
            "main",
            "author bracket",
            [gen1_wp.id],
            "agent",
            content={gen1_wp.id: b"pad(10)\n"},
            paths={gen1_wp.id: "mechanical/cad_src/bracket.py"},
        )

        gen2_wp = _make_work_product("bracket_gen2", content_hash="h2")
        await graph.add_node(gen2_wp)
        v2 = await veng.commit(
            "main",
            "widen pad",
            [gen2_wp.id],
            "agent",
            content={gen2_wp.id: b"pad(15)\n"},
            paths={gen2_wp.id: "mechanical/cad_src/bracket.py"},
        )

        log = await veng._git(["log", "main", "--format=%H", "--", "mechanical/cad_src/bracket.py"])
        shas = [s for s in log.stdout.splitlines() if s.strip()]
        assert v1.git_commit_sha in shas
        assert v2.git_commit_sha in shas
        assert len(shas) == 2

        d = await veng.diff(v1.id, v2.id)
        change = next(c for c in d.changes if c.change_type == "modified")
        assert change.patch is not None
        assert "-pad(10)" in change.patch
        assert "+pad(15)" in change.patch

    async def test_real_content_is_committed_and_diffable(self, setup):
        """When real bytes are supplied, git diff surfaces an actual text patch."""
        graph, veng, art, initial = setup

        script_wp = _make_work_product("bracket_script", content_hash="ignored")
        await graph.add_node(script_wp)
        v1 = await veng.commit(
            "main",
            "author bracket script",
            [script_wp.id],
            "agent",
            content={script_wp.id: b"pad(10)\n"},
        )

        await graph.update_node(script_wp.id, {"content_hash": "ignored2"})
        v2 = await veng.commit(
            "main",
            "widen pad",
            [script_wp.id],
            "agent",
            content={script_wp.id: b"pad(15)\n"},
        )

        d = await veng.diff(v1.id, v2.id)
        change = next(c for c in d.changes if c.work_product_id == script_wp.id)
        assert change.change_type == "modified"
        assert change.patch is not None
        assert "-pad(10)" in change.patch
        assert "+pad(15)" in change.patch


class TestGetHead:
    async def test_existing_branch(self, setup):
        graph, veng, art, initial = setup
        head = await veng.get_head("main")
        assert head.id == initial.id

    async def test_nonexistent_branch_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError):
            await veng.get_head("nonexistent")


class TestLog:
    async def test_single_commit(self, setup):
        graph, veng, art, initial = setup
        history = await veng.log("main")
        assert len(history) == 1
        assert history[0].id == initial.id

    async def test_multi_commit_history(self, setup):
        graph, veng, art, initial = setup

        a1 = _make_work_product("log1", content_hash="lh1")
        a2 = _make_work_product("log2", content_hash="lh2")
        await graph.add_node(a1)
        await graph.add_node(a2)

        v1 = await veng.commit("main", "Second", [a1.id], "test")
        v2 = await veng.commit("main", "Third", [a2.id], "test")

        history = await veng.log("main")
        assert len(history) == 3
        assert history[0].id == v2.id
        assert history[1].id == v1.id
        assert history[2].id == initial.id

    async def test_limit_parameter(self, setup):
        graph, veng, art, initial = setup

        for i in range(5):
            a = _make_work_product(f"lim_{i}", content_hash=f"limh_{i}")
            await graph.add_node(a)
            await veng.commit("main", f"Commit {i}", [a.id], "test")

        history = await veng.log("main", limit=3)
        assert len(history) == 3

    async def test_log_nonexistent_branch_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError):
            await veng.log("nope")


class TestDiff:
    async def test_added_artifacts(self, setup):
        graph, veng, art, initial = setup

        new_art = _make_work_product("added", content_hash="added_h")
        await graph.add_node(new_art)
        v2 = await veng.commit("main", "Add work_product", [new_art.id], "test")

        d = await veng.diff(initial.id, v2.id)
        added = [c for c in d.changes if c.change_type == "added"]
        assert len(added) == 1
        assert added[0].work_product_id == new_art.id

    async def test_modified_artifacts(self, setup):
        graph, veng, art, initial = setup

        await graph.update_node(art.id, {"content_hash": "modified_hash"})
        v2 = await veng.commit("main", "Modify work_product", [art.id], "test")

        d = await veng.diff(initial.id, v2.id)
        modified = [c for c in d.changes if c.change_type == "modified"]
        assert len(modified) == 1
        assert modified[0].work_product_id == art.id

    async def test_no_changes(self, setup):
        graph, veng, art, initial = setup

        v2 = await veng.commit("main", "Same content", [art.id], "test")

        d = await veng.diff(initial.id, v2.id)
        assert len(d.changes) == 0

    async def test_diff_nonexistent_version_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError):
            await veng.diff(initial.id, uuid4())

    async def test_diff_version_ids_match(self, setup):
        graph, veng, art, initial = setup
        a = _make_work_product("x", content_hash="xh")
        await graph.add_node(a)
        v2 = await veng.commit("main", "x", [a.id], "test")

        d = await veng.diff(initial.id, v2.id)
        assert d.version_a == initial.id
        assert d.version_b == v2.id


class TestMerge:
    async def test_clean_merge_non_overlapping(self, setup):
        graph, veng, art, initial = setup

        await veng.create_branch("feature")

        fa = _make_work_product("feature_art", content_hash="fh")
        await graph.add_node(fa)
        await veng.commit("feature", "Feature work", [fa.id], "dev")

        ma = _make_work_product("main_art", content_hash="mh")
        await graph.add_node(ma)
        await veng.commit("main", "Main work", [ma.id], "lead")

        merge_v = await veng.merge("feature", "main", "Merge feature", "lead")
        assert merge_v.merge_parent_id is not None
        assert merge_v.branch_name == "main"

        head = await veng.get_head("main")
        assert head.id == merge_v.id

    async def test_content_conflict_raises(self, setup):
        """Both branches modify the same work_product's real content differently."""
        graph, veng, art, initial = setup

        conflict_wp = _make_work_product("conflict_script", content_hash="base")
        await graph.add_node(conflict_wp)
        base = await veng.commit(
            "main",
            "base script",
            [conflict_wp.id],
            "lead",
            content={conflict_wp.id: b"pad(10)\n"},
        )
        assert base is not None

        await veng.create_branch("feature")

        await graph.update_node(conflict_wp.id, {"content_hash": "feature_hash"})
        await veng.commit(
            "feature",
            "feature change",
            [conflict_wp.id],
            "dev",
            content={conflict_wp.id: b"pad(20)\n"},
        )

        await graph.update_node(conflict_wp.id, {"content_hash": "main_hash"})
        await veng.commit(
            "main",
            "main change",
            [conflict_wp.id],
            "lead",
            content={conflict_wp.id: b"pad(30)\n"},
        )

        with pytest.raises(MergeConflict) as exc_info:
            await veng.merge("feature", "main", "Merge", "lead")

        assert len(exc_info.value.conflicts) == 1
        assert exc_info.value.conflicts[0].work_product_id == conflict_wp.id

    async def test_merge_creates_version_with_merge_parent(self, setup):
        graph, veng, art, initial = setup

        await veng.create_branch("feat")
        fa = _make_work_product("fa", content_hash="fah")
        await graph.add_node(fa)
        feat_commit = await veng.commit("feat", "Feat", [fa.id], "dev")

        merge_v = await veng.merge("feat", "main", "Merge feat", "lead")
        assert merge_v.merge_parent_id == feat_commit.id
        assert merge_v.parent_id is not None

    async def test_merge_nonexistent_branch_raises(self, setup):
        graph, veng, art, initial = setup
        with pytest.raises(KeyError):
            await veng.merge("ghost", "main", "msg", "test")


class TestEdgeCreation:
    async def test_parent_of_edges(self, setup):
        graph, veng, art, initial = setup

        a = _make_work_product("edge_test", content_hash="eth")
        await graph.add_node(a)
        v2 = await veng.commit("main", "Second", [a.id], "test")

        edges = await graph.get_edges(
            initial.id, direction="outgoing", edge_type=EdgeType.PARENT_OF
        )
        assert len(edges) == 1
        assert edges[0].target_id == v2.id

    async def test_versioned_by_edges(self, setup):
        graph, veng, art, initial = setup

        a = _make_work_product("vby", content_hash="vbyh")
        await graph.add_node(a)
        v2 = await veng.commit("main", "VBy test", [a.id], "test")

        edges = await graph.get_edges(a.id, direction="outgoing", edge_type=EdgeType.VERSIONED_BY)
        assert len(edges) == 1
        assert edges[0].target_id == v2.id

    async def test_merge_creates_two_parent_edges(self, setup):
        graph, veng, art, initial = setup

        await veng.create_branch("feat")
        fa = _make_work_product("mfa", content_hash="mfah")
        await graph.add_node(fa)
        feat_head = await veng.commit("feat", "Feat", [fa.id], "dev")

        main_head = await veng.get_head("main")
        merge_v = await veng.merge("feat", "main", "Merge", "lead")

        edges = await graph.get_edges(
            merge_v.id, direction="incoming", edge_type=EdgeType.PARENT_OF
        )
        parent_ids = {e.source_id for e in edges}
        assert main_head.id in parent_ids
        assert feat_head.id in parent_ids

    async def test_version_nodes_stored_in_graph(self, setup):
        graph, veng, art, initial = setup

        versions = await graph.list_nodes(node_type=NodeType.VERSION)
        assert len(versions) == 1
        assert versions[0].id == initial.id
