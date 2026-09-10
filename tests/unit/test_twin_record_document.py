"""twin.record_document — generic text/markdown work products (MET-58X).

Fixes a real gap: the chat agent had no direct way to save a document
(requirements, notes, a spec) and fell back to ``twin.propose_change``, whose
apply-on-approve executor only implements a ``record_decision`` action — any
other diff shape (including one the model invents, e.g. ``create_work_product``)
silently no-ops even after a human approves it (see ``test_proposal_apply.py``).
``document_recorder.py`` already existed (used by the deterministic requirements
run-phase) but was never exposed as an MCP tool the chat agent could call.

Covers the recorder (blob → validated WorkProduct → project link, mirroring
``test_twin_record_decision.py``) and the twin adapter handler that calls it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin.document_recorder import make_document_recorder
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list[tuple[str, str, str, str]] = []

    async def link_work_product(
        self, project_id: str, wp_id: str, name: str, link_type: str
    ) -> None:
        self.links.append((project_id, wp_id, name, link_type))


def _patch_blob(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def _store(
        node_id: str, filename: str, content: bytes, *, content_type: str | None = None
    ) -> str:
        if fail:
            raise RuntimeError("minio down")
        calls["node_id"] = node_id
        calls["filename"] = filename
        calls["content"] = content
        calls["content_type"] = content_type
        return f"work-products/{node_id}/{filename}"

    monkeypatch.setattr("digital_twin.storage.work_product_blobs.store_work_product_blob", _store)
    return calls


class TestRecorder:
    async def test_creates_valid_prd_work_product(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        record = make_document_recorder(twin, None)

        result = await record(
            content="# Requirements\n\nThe widget shall be blue.",
            name="Widget PRD",
            wp_type="prd",
            domain="requirements",
            fmt="md",
            link_type="prd",
            source_tool="twin.record_document",
        )

        node_id = result["node_id"]
        assert result["content_hash"]
        wp = await twin.get_work_product(UUID(node_id))
        assert wp is not None
        assert wp.type == WorkProductType.PRD
        assert wp.domain == "requirements"
        assert wp.format == "md"
        assert wp.metadata["minio_object_key"].endswith(".md")
        assert wp.metadata["original_filename"] == "widget-prd.md"
        assert wp.metadata["authored_by"] == "twin.record_document"

    async def test_links_project_only_when_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        pid = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"

        twin = InMemoryTwinAPI.create()
        be = _FakeProjectBackend()
        record = make_document_recorder(twin, be)
        r = await record(
            content="notes",
            name="D1",
            wp_type="documentation",
            domain="documentation",
            fmt="md",
            link_type="documentation",
            source_tool="twin.record_document",
            project_id=pid,
        )
        assert r["project_linked"] is True
        assert be.links and be.links[0][0] == pid
        assert be.links[0][3] == "documentation"  # link_type passed through

        twin2 = InMemoryTwinAPI.create()
        be2 = _FakeProjectBackend()
        record2 = make_document_recorder(twin2, be2)
        r2 = await record2(
            content="notes",
            name="D2",
            wp_type="documentation",
            domain="documentation",
            fmt="md",
            link_type="documentation",
            source_tool="twin.record_document",
        )
        assert r2["project_linked"] is False
        assert be2.links == []

    async def test_blob_failure_degrades_gracefully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch, fail=True)
        twin = InMemoryTwinAPI.create()
        record = make_document_recorder(twin, None)
        r = await record(
            content="notes",
            name="D",
            wp_type="documentation",
            domain="documentation",
            fmt="md",
            link_type="documentation",
            source_tool="twin.record_document",
        )
        assert r["minio_object_key"] is None
        wp = await twin.get_work_product(UUID(r["node_id"]))
        assert wp is not None
        assert wp.content_hash == r["content_hash"]
        assert "minio_object_key" not in wp.metadata

    async def test_requires_name_and_content(self) -> None:
        twin = InMemoryTwinAPI.create()
        record = make_document_recorder(twin, None)
        with pytest.raises(ValueError, match="name"):
            await record(
                content="x",
                name="",
                wp_type="documentation",
                domain="documentation",
                fmt="md",
                link_type="documentation",
                source_tool="t",
            )
        with pytest.raises(ValueError, match="content"):
            await record(
                content="",
                name="x",
                wp_type="documentation",
                domain="documentation",
                fmt="md",
                link_type="documentation",
                source_tool="t",
            )


class TestAdapterHandler:
    async def test_record_document_tool_registered_and_calls_recorder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(
            twin=twin, allow_mutations=True, document_recorder=make_document_recorder(twin, None)
        )
        assert "twin.record_document" in server.tool_ids
        out = await server.record_document({"name": "N", "content": "body"})
        assert out["node_id"]
        wp = await twin.get_work_product(UUID(out["node_id"]))
        # Defaults to 'documentation' when document_type is omitted.
        assert wp.type == WorkProductType.DOCUMENTATION

    async def test_prd_document_type_sets_requirements_domain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        out = await server.record_document({"name": "N", "content": "body", "document_type": "prd"})
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp.type == WorkProductType.PRD
        assert wp.domain == "requirements"

    def test_record_document_absent_without_recorder(self) -> None:
        server = TwinServer(twin=InMemoryTwinAPI.create())
        assert "twin.record_document" not in server.tool_ids

    async def test_handler_validates_required_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        with pytest.raises(ValueError, match="name"):
            await server.record_document({"content": "c"})
        with pytest.raises(ValueError, match="content"):
            await server.record_document({"name": "n"})

    async def test_handler_rejects_unknown_document_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, document_recorder=make_document_recorder(twin, None))
        with pytest.raises(ValueError, match="document_type"):
            await server.record_document(
                {"name": "n", "content": "c", "document_type": "create_work_product"}
            )
