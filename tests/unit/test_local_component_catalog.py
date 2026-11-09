"""Local structured discovery works without PostgreSQL or an intent LLM."""

from uuid import uuid4

import pytest

from digital_twin.catalog.local_store import SqliteComponentCatalogStore
from digital_twin.catalog.query import CatalogQuery, ComponentCatalogRow, ComponentFilter
from tool_registry.tools.components.adapter import ComponentServer


def row(mpn="module", specs=None):
    return ComponentCatalogRow(
        uuid4(),
        mpn,
        "maker",
        "buck_converter",
        "discrete_part",
        2.0,
        "active",
        "",
        specs or {},
        {},
        1,
    )


@pytest.mark.asyncio
async def test_filtering_persistence_unknown_values_and_identity(tmp_path):
    path = str(tmp_path / "catalog.db")
    store = SqliteComponentCatalogStore(path)
    first = await store.upsert(row(specs={"v_out": 5.0}))
    again = await store.upsert(row(specs={"v_out": 5.0}))
    assert first.id == again.id
    await store.upsert(row("unknown"))
    result = await store.query(
        CatalogQuery(category="buck_converter", filters=[ComponentFilter("v_out", ">=", 4)])
    )
    assert [r.mpn for r in result.rows] == ["module"]
    with pytest.raises(ValueError):
        await store.query(
            CatalogQuery(category="buck_converter", filters=[ComponentFilter("imaginary", "==", 5)])
        )
    await store.close()
    reopened = SqliteComponentCatalogStore(path)
    assert (await reopened.get("module", "maker")).id == first.id
    await reopened.close()


@pytest.mark.asyncio
async def test_only_structured_search_is_exposed_without_llm():
    store = SqliteComponentCatalogStore(":memory:")
    await store.upsert(row())
    server = ComponentServer(search_store=store)
    assert server.tool_ids == ["component.search_parametric"] or set(server.tool_ids) == {
        "component.search_parametric"
    }
    assert len((await server.handle_search_parametric({"category": "buck_converter"}))["rows"]) == 1
    await store.close()


@pytest.mark.asyncio
async def test_numeric_specifications_cannot_be_text_or_nonfinite():
    store = SqliteComponentCatalogStore(":memory:")
    for value in ("5V", float("nan"), True):
        with pytest.raises(ValueError):
            await store.upsert(row(specs={"v_out": value}))
    with pytest.raises(ValueError):
        await store.query(
            CatalogQuery(category="buck_converter", filters=[ComponentFilter("v_out", ">=", "5")])
        )
    await store.close()
