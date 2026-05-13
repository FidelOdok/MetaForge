"""Unit tests for the project_id backfill migration (MET-442)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from scripts.migrations.backfill_project_id import (
    _resolve_default_project_id,
    backfill_project_id,
)
from twin_core.graph_engine import InMemoryGraphEngine
from twin_core.models.component import Component
from twin_core.models.enums import WorkProductType
from twin_core.models.work_product import WorkProduct

_DEFAULT = UUID("11111111-1111-1111-1111-111111111111")


def _wp(name: str = "x", project_id: UUID | None = None) -> WorkProduct:
    return WorkProduct(
        name=name,
        type=WorkProductType.CAD_MODEL,
        domain="mechanical",
        file_path=f"models/{name}.step",
        content_hash="h",
        format="step",
        created_by="human",
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# Env-var resolver
# ---------------------------------------------------------------------------


class TestResolveDefaultProjectId:
    def test_returns_uuid_when_env_set(self, monkeypatch) -> None:
        monkeypatch.setenv("METAFORGE_DEFAULT_PROJECT_ID", str(_DEFAULT))
        assert _resolve_default_project_id() == _DEFAULT

    def test_exits_when_env_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("METAFORGE_DEFAULT_PROJECT_ID", raising=False)
        with pytest.raises(SystemExit) as exc:
            _resolve_default_project_id()
        assert exc.value.code == 1

    def test_exits_on_invalid_uuid(self, monkeypatch) -> None:
        monkeypatch.setenv("METAFORGE_DEFAULT_PROJECT_ID", "not-a-uuid")
        with pytest.raises(SystemExit) as exc:
            _resolve_default_project_id()
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Core backfill logic
# ---------------------------------------------------------------------------


class TestBackfillProjectId:
    async def test_empty_graph_returns_zero(self) -> None:
        graph = InMemoryGraphEngine()
        touched = await backfill_project_id(graph, _DEFAULT)
        assert touched == 0

    async def test_backfills_null_project_id(self) -> None:
        graph = InMemoryGraphEngine()
        legacy = _wp(name="legacy")  # project_id=None
        await graph.add_node(legacy)

        touched = await backfill_project_id(graph, _DEFAULT)
        assert touched == 1

        node = await graph.get_node(legacy.id)
        assert node is not None
        assert getattr(node, "project_id") == _DEFAULT

    async def test_skips_already_scoped_nodes(self) -> None:
        graph = InMemoryGraphEngine()
        other = uuid4()
        already = _wp(name="already", project_id=other)
        await graph.add_node(already)

        touched = await backfill_project_id(graph, _DEFAULT)
        assert touched == 0

        node = await graph.get_node(already.id)
        assert node is not None
        assert getattr(node, "project_id") == other  # untouched

    async def test_dry_run_does_not_write(self) -> None:
        graph = InMemoryGraphEngine()
        legacy = _wp(name="legacy")
        await graph.add_node(legacy)

        touched = await backfill_project_id(graph, _DEFAULT, dry_run=True)
        assert touched == 1

        node = await graph.get_node(legacy.id)
        assert node is not None
        assert getattr(node, "project_id") is None  # not written

    async def test_second_run_is_no_op(self) -> None:
        graph = InMemoryGraphEngine()
        await graph.add_node(_wp(name="a"))
        await graph.add_node(_wp(name="b"))

        first = await backfill_project_id(graph, _DEFAULT)
        second = await backfill_project_id(graph, _DEFAULT)

        assert first == 2
        assert second == 0

    async def test_mixed_node_types_all_handled(self) -> None:
        graph = InMemoryGraphEngine()
        await graph.add_node(_wp(name="wp"))
        await graph.add_node(Component(part_number="STM32", manufacturer="ST"))

        touched = await backfill_project_id(graph, _DEFAULT)
        assert touched == 2
