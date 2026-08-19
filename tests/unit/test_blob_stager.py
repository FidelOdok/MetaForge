"""twin.stage_work_product_file — reload a committed work product's blob for
inspection once its authoring session is gone (MET-618).

Real gap this closes: an agent whose ``freecad.open_session`` (or any other
authoring session) has expired has no way back to a work product's actual
content — ``freecad.describe_session`` fails, the node's ``file_path`` is
empty (MinIO is the source of truth), and every CAD/FEA/PCB inspection tool
needs a local ``input_file`` path, not a node id. Covers the stager itself
(blob resolve -> shared workspace write) and the twin adapter handler that
exposes it, mirroring ``test_twin_record_document.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from api_gateway.twin.blob_stager import make_blob_stager
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

_CONTENT = b"ISO-10303-21;\nHEADER;\nfake step body\nENDSEC;\n"


async def _make_wp(
    twin: InMemoryTwinAPI, *, minio_key: str | None = "work-products/x/x.step"
) -> WorkProduct:
    now = datetime.now(UTC)
    metadata: dict[str, Any] = {"original_filename": "part.step"}
    if minio_key:
        metadata["minio_object_key"] = minio_key
    wp = WorkProduct(
        id=uuid4(),
        name="Part",
        type=WorkProductType.CAD_MODEL,
        domain="mech",
        file_path="",
        content_hash="deadbeef",
        format="step",
        metadata=metadata,
        created_at=now,
        updated_at=now,
        created_by="freecad.export_model",
    )
    return await twin.create_work_product(wp)


def _patch_fetch(monkeypatch: pytest.MonkeyPatch, content: bytes = _CONTENT) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def _fetch(object_key: str) -> bytes:
        calls["object_key"] = object_key
        return content

    monkeypatch.setattr("api_gateway.twin.blob_store.fetch_work_product_blob", _fetch)
    return calls


class TestBlobStager:
    async def test_stages_blob_onto_workspace(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        calls = _patch_fetch(monkeypatch)
        twin = InMemoryTwinAPI.create()
        wp = await _make_wp(twin)
        stage = make_blob_stager(twin, workspace_dir=tmp_path)

        result = await stage(str(wp.id))

        assert calls["object_key"] == "work-products/x/x.step"
        assert result["node_id"] == str(wp.id)
        assert result["filename"] == "part.step"
        assert result["size_bytes"] == len(_CONTENT)
        assert result["content_hash"] == "deadbeef"
        assert result["format"] == "step"
        staged = tmp_path / "_staged_work_products" / str(wp.id) / "part.step"
        assert staged.read_bytes() == _CONTENT
        assert result["file_path"] == str(staged)

    async def test_restaging_is_idempotent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        _patch_fetch(monkeypatch)
        twin = InMemoryTwinAPI.create()
        wp = await _make_wp(twin)
        stage = make_blob_stager(twin, workspace_dir=tmp_path)

        first = await stage(str(wp.id))
        second = await stage(str(wp.id))

        assert first["file_path"] == second["file_path"]
        staged = tmp_path / "_staged_work_products" / str(wp.id) / "part.step"
        assert staged.read_bytes() == _CONTENT

    async def test_invalid_node_id_raises(self, tmp_path: Any) -> None:
        twin = InMemoryTwinAPI.create()
        stage = make_blob_stager(twin, workspace_dir=tmp_path)
        with pytest.raises(ValueError, match="Invalid node id"):
            await stage("not-a-uuid")

    async def test_missing_node_raises(self, tmp_path: Any) -> None:
        twin = InMemoryTwinAPI.create()
        stage = make_blob_stager(twin, workspace_dir=tmp_path)
        with pytest.raises(ValueError, match="not found"):
            await stage(str(uuid4()))


class TestAdapterHandler:
    async def test_stage_tool_registered_and_calls_stager(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        _patch_fetch(monkeypatch)
        twin = InMemoryTwinAPI.create()
        wp = await _make_wp(twin)
        server = TwinServer(twin=twin, blob_stager=make_blob_stager(twin, workspace_dir=tmp_path))

        assert "twin.stage_work_product_file" in server.tool_ids
        out = await server.stage_work_product_file({"node_id": str(wp.id)})
        assert out["file_path"]
        assert UUID(out["node_id"]) == wp.id

    def test_stage_tool_absent_without_stager(self) -> None:
        server = TwinServer(twin=InMemoryTwinAPI.create())
        assert "twin.stage_work_product_file" not in server.tool_ids

    async def test_handler_requires_node_id(self, tmp_path: Any) -> None:
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, blob_stager=make_blob_stager(twin, workspace_dir=tmp_path))
        with pytest.raises(ValueError, match="node_id"):
            await server.stage_work_product_file({})
