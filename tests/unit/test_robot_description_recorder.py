"""Unit tests for make_robot_description_recorder / _updater (MET-740).

Exercises the robot-description persistence facets (multi-blob -> MinIO,
robot_description work product with a structured `assembly` metadata field,
PARENT_OF edges to source parts, project link, and the update/versioning
path) with a fake/real twin + project backend and the MinIO blob store
monkeypatched, so no real storage is required.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from api_gateway.twin.robot_description_recorder import (
    make_robot_description_recorder,
    make_robot_description_updater,
)

_URDF = '<?xml version="1.0"?>\n<robot name="quadruped"><link name="body" /></robot>'
_URDF_V2 = (
    '<?xml version="1.0"?>\n<robot name="quadruped"><link name="body" /><link name="leg" /></robot>'
)
_STL = b"solid mesh\nendsolid mesh\n"

_PARTS = [
    {"node_id": "aaaaaaaa-0000-0000-0000-000000000001", "link_name": "body"},
    {"node_id": "aaaaaaaa-0000-0000-0000-000000000002", "link_name": "leg_fl"},
]
_JOINTS = [
    {
        "name": "hip_fl",
        "type": "revolute",
        "base": "body",
        "follower": "leg_fl",
        "axis": [0, 1, 0],
        "anchor": [80, 50, 0],
    }
]


class _FakeTwin:
    def __init__(self) -> None:
        self.created: list = []
        self.edges: list = []

    async def create_work_product(self, wp):  # type: ignore[no-untyped-def]
        self.created.append(wp)
        return wp

    async def add_edge(self, source_id, target_id, edge_type, metadata=None):  # type: ignore[no-untyped-def]
        self.edges.append((source_id, target_id, edge_type))


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list = []

    async def link_work_product(self, project_id, node_id, name, kind):  # type: ignore[no-untyped-def]
        self.links.append((project_id, node_id, name, kind))


@pytest.fixture()
def patched_blob_store(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {"calls": []}

    def fake_store(node_id: str, filename: str, content: bytes, content_type: str = "") -> str:
        key = f"work-products/{node_id}/{filename}"
        captured["calls"].append(
            {"node_id": node_id, "filename": filename, "content": content, "key": key}
        )
        return key

    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(blobs, "store_work_product_blob", fake_store)
    return captured


class TestRobotDescriptionCommit:
    async def test_persists_robot_description_with_assembly_metadata(
        self, patched_blob_store: dict
    ) -> None:
        from twin_core.models.enums import WorkProductType

        twin = _FakeTwin()
        projects = _FakeProjectBackend()
        commit = make_robot_description_recorder(twin, projects)

        project_id = "11111111-1111-1111-1111-111111111111"
        result = await commit(
            name="Quadruped Robot",
            description_text=_URDF,
            fmt="urdf",
            robot_name="quadruped",
            parts=_PARTS,
            joints=_JOINTS,
            mesh_files={"body.stl": _STL, "leg_fl.stl": _STL},
            source_part_node_ids=[p["node_id"] for p in _PARTS],
            project_id=project_id,
            source_tool="cadquery.export_urdf_assembly",
        )

        assert len(twin.created) == 1
        wp = twin.created[0]
        assert wp.type == WorkProductType.ROBOT_DESCRIPTION
        assert wp.format == "urdf"
        assert wp.metadata["assembly"] == {"parts": _PARTS, "joints": _JOINTS}
        assert wp.metadata["robot_name"] == "quadruped"
        assert wp.metadata["minio_object_key"] == result["minio_object_key"]
        assert wp.file_path == ""

        # Primary blob + both mesh blobs stored.
        filenames = {c["filename"] for c in patched_blob_store["calls"]}
        assert filenames == {"quadruped-robot.urdf", "body.stl", "leg_fl.stl"}
        assert result["mesh_files"] == {
            "body.stl": f"work-products/{result['node_id']}/body.stl",
            "leg_fl.stl": f"work-products/{result['node_id']}/leg_fl.stl",
        }

        # PARENT_OF edges to every source part.
        assert len(twin.edges) == 2
        assert {e[1] for e in twin.edges} == {p["node_id"] for p in _PARTS}

        # Project linked as "robot_description".
        assert projects.links == [
            (project_id, result["node_id"], "Quadruped Robot", "robot_description")
        ]
        assert result["project_linked"] is True

    async def test_degrades_when_blob_store_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import digital_twin.storage.work_product_blobs as blobs

        def boom(*a: object, **k: object) -> str:
            raise RuntimeError("minio down")

        monkeypatch.setattr(blobs, "store_work_product_blob", boom)
        twin = _FakeTwin()
        commit = make_robot_description_recorder(twin, None)
        result = await commit(
            name="Robot",
            description_text=_URDF,
            fmt="urdf",
            robot_name="robot",
            parts=_PARTS,
            joints=_JOINTS,
            mesh_files={"body.stl": _STL},
        )
        assert len(twin.created) == 1
        assert result["minio_object_key"] is None
        assert result["mesh_files"] == {}
        assert result["project_linked"] is False

    async def test_bad_source_part_edge_does_not_block_commit(
        self, patched_blob_store: dict
    ) -> None:
        class _EdgeFailTwin(_FakeTwin):
            async def add_edge(self, source_id, target_id, edge_type, metadata=None):  # type: ignore[no-untyped-def]
                raise RuntimeError("bad node id")

        twin = _EdgeFailTwin()
        commit = make_robot_description_recorder(twin, None)
        result = await commit(
            name="Robot",
            description_text=_URDF,
            fmt="urdf",
            robot_name="robot",
            parts=_PARTS,
            joints=_JOINTS,
            mesh_files={},
            source_part_node_ids=["bad-id"],
        )
        assert len(twin.created) == 1
        assert result["node_id"]

    async def test_missing_name_raises(self) -> None:
        commit = make_robot_description_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="name"):
            await commit(
                name="",
                description_text=_URDF,
                fmt="urdf",
                robot_name="robot",
                parts=[],
                joints=[],
                mesh_files={},
            )

    async def test_empty_description_raises(self) -> None:
        commit = make_robot_description_recorder(_FakeTwin(), None)
        with pytest.raises(ValueError, match="empty"):
            await commit(
                name="Robot",
                description_text="",
                fmt="urdf",
                robot_name="robot",
                parts=[],
                joints=[],
                mesh_files={},
            )


class TestRobotDescriptionUpdate:
    async def test_update_replaces_blob_and_records_version(self, patched_blob_store: dict) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        commit = make_robot_description_recorder(twin, None)
        update = make_robot_description_updater(twin)

        created = await commit(
            name="Quadruped Robot",
            description_text=_URDF,
            fmt="urdf",
            robot_name="quadruped",
            parts=_PARTS,
            joints=_JOINTS,
            mesh_files={"body.stl": _STL},
        )
        node_id = created["node_id"]
        original_hash = created["content_hash"]

        new_joints = _JOINTS + [
            {
                "name": "hip_fr",
                "type": "revolute",
                "base": "body",
                "follower": "leg_fr",
                "axis": [0, 1, 0],
                "anchor": [80, -50, 0],
            }
        ]
        result = await update(
            node_id,
            description_text=_URDF_V2,
            fmt="urdf",
            robot_name="quadruped",
            parts=_PARTS,
            joints=new_joints,
            mesh_files={"leg_fr.stl": _STL},
        )

        assert result["node_id"] == node_id
        assert result["content_hash"] != original_hash

        wp = await twin.get_work_product(UUID(node_id))
        assert wp.content_hash == result["content_hash"]
        assert wp.metadata["assembly"]["joints"] == new_joints
        # Original mesh key preserved, new one merged in — an edit that
        # drops a link's mesh from this export shouldn't orphan the
        # still-referenced blob key from the prior version.
        assert set(wp.metadata["mesh_files"]) == {"body.stl", "leg_fr.stl"}

        # A real version snapshot was recorded, with the NEW content hash
        # (not the pre-update one — /nodes/{id}/iterate's own historical
        # gap this update path deliberately corrects for its own writes).
        revisions = wp.metadata["_revisions"]
        assert len(revisions) == 1
        assert revisions[0]["content_hash"] == result["content_hash"]
        assert revisions[0]["metadata_snapshot"]["assembly"]["joints"] == new_joints

    async def test_update_missing_node_raises(self) -> None:
        from twin_core.api import InMemoryTwinAPI

        twin = InMemoryTwinAPI.create()
        update = make_robot_description_updater(twin)
        with pytest.raises(ValueError, match="not found"):
            await update(
                "11111111-1111-1111-1111-111111111111",
                description_text=_URDF,
                fmt="urdf",
                robot_name="robot",
                parts=[],
                joints=[],
                mesh_files={},
            )
