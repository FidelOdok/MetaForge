"""twin.record_decision — typed design-decision work products (MET-495).

Covers the decision recorder (markdown render → blob → validated WorkProduct →
project link) and the twin adapter handler that calls it.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import pytest

from api_gateway.twin import decision_recorder as dr
from api_gateway.twin.decision_recorder import make_decision_recorder, render_decision_markdown
from tool_registry.tools.twin.adapter import TwinServer
from twin_core.api import InMemoryTwinAPI
from twin_core.models.enums import WorkProductType


class _FakeProjectBackend:
    def __init__(self) -> None:
        self.links: list[tuple[str, str, str, str]] = []

    async def link_work_product(self, project_id: str, wp_id: str, name: str, wp_type: str) -> None:
        self.links.append((project_id, wp_id, name, wp_type))


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
        return f"work-products/{node_id}/{filename}"

    monkeypatch.setattr("digital_twin.storage.work_product_blobs.store_work_product_blob", _store)
    return calls


def test_render_markdown_has_rationale_and_alternatives() -> None:
    md = render_decision_markdown(
        "Slot over hole",
        "Slots clear PETG stringing",
        [{"option": "round hole", "reason_rejected": "clogged"}],
        supersedes="abc",
    )
    assert "# Slot over hole" in md
    assert "Slots clear PETG stringing" in md
    assert "round hole" in md and "clogged" in md
    assert "Supersedes" in md


class TestRecorder:
    async def test_creates_valid_design_decision(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        record = make_decision_recorder(twin, None)

        result = await record(
            title="Slot remodel",
            rationale="slots beat holes",
            alternatives=[{"option": "hole", "reason_rejected": "clog"}],
        )

        node_id = result["node_id"]
        assert result["content_hash"]
        wp = await twin.get_work_product(UUID(node_id))
        assert wp is not None
        assert wp.type == WorkProductType.DESIGN_DECISION
        assert wp.format == "md"
        assert wp.content_hash == result["content_hash"]
        assert wp.metadata["minio_object_key"].endswith(".md")
        assert wp.metadata["original_filename"] == "slot-remodel.md"
        # content_hash matches the rendered markdown
        expected = render_decision_markdown(
            "Slot remodel",
            "slots beat holes",
            [{"option": "hole", "reason_rejected": "clog"}],
            None,
        )
        assert wp.content_hash == hashlib.sha256(expected.encode()).hexdigest()

    async def test_links_project_only_when_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        pid = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"

        twin = InMemoryTwinAPI.create()
        be = _FakeProjectBackend()
        record = make_decision_recorder(twin, be)
        r = await record(title="D1", rationale="r", project_id=pid)
        assert r["project_linked"] is True
        assert be.links and be.links[0][0] == pid

        twin2 = InMemoryTwinAPI.create()
        be2 = _FakeProjectBackend()
        record2 = make_decision_recorder(twin2, be2)
        r2 = await record2(title="D2", rationale="r")
        assert r2["project_linked"] is False
        assert be2.links == []

    async def test_identical_decision_is_deduplicated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MET-506: recording the same decision twice returns the same node."""
        _patch_blob(monkeypatch)
        pid = "f8240b2a-9e01-4b16-83eb-b24cfcd4a04f"
        twin = InMemoryTwinAPI.create()
        record = make_decision_recorder(twin, None)

        first = await record(title="Slot remodel", rationale="slots beat holes", project_id=pid)
        second = await record(title="Slot remodel", rationale="slots beat holes", project_id=pid)

        assert first["deduplicated"] is False
        assert second["deduplicated"] is True
        assert second["node_id"] == first["node_id"]
        # Only one node exists for that project.
        decisions = await twin.list_work_products(
            work_product_type=WorkProductType.DESIGN_DECISION, project_id=UUID(pid)
        )
        assert len([d for d in decisions if d.content_hash == first["content_hash"]]) == 1

    async def test_supersedes_bypasses_dedup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A superseding record is deliberate — never deduplicated."""
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        record = make_decision_recorder(twin, None)
        first = await record(title="D", rationale="r")
        again = await record(title="D", rationale="r", supersedes=first["node_id"])
        assert again["deduplicated"] is False
        assert again["node_id"] != first["node_id"]

    async def test_blob_failure_degrades_gracefully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch, fail=True)
        twin = InMemoryTwinAPI.create()
        record = make_decision_recorder(twin, None)
        r = await record(title="D", rationale="r")
        # Node still created, just no minio key.
        assert r["minio_object_key"] is None
        wp = await twin.get_work_product(UUID(r["node_id"]))
        assert wp is not None
        assert wp.content_hash == r["content_hash"]
        assert "minio_object_key" not in wp.metadata


class TestAdapterHandler:
    async def test_record_decision_tool_registered_and_calls_recorder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(
            twin=twin, allow_mutations=True, decision_recorder=make_decision_recorder(twin, None)
        )
        assert "twin.record_decision" in server.tool_ids
        out = await server.record_decision({"title": "T", "rationale": "because"})
        assert out["node_id"]

    def test_record_decision_absent_without_recorder(self) -> None:
        server = TwinServer(twin=InMemoryTwinAPI.create())
        assert "twin.record_decision" not in server.tool_ids

    async def test_handler_validates_required_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_blob(monkeypatch)
        twin = InMemoryTwinAPI.create()
        server = TwinServer(twin=twin, decision_recorder=make_decision_recorder(twin, None))
        with pytest.raises(ValueError, match="title"):
            await server.record_decision({"rationale": "r"})
        with pytest.raises(ValueError, match="rationale"):
            await server.record_decision({"title": "t"})


def test_design_decision_enum_parses() -> None:
    assert WorkProductType("design_decision") is WorkProductType.DESIGN_DECISION
    # blob_store re-export still works (backward compat)
    from api_gateway.twin.blob_store import store_work_product_blob  # noqa: F401

    assert dr.render_decision_markdown("a", "b", None, None).startswith("# a")
