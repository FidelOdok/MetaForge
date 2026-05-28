"""Unit tests for the KB object-storage abstraction (MET-476)."""

from __future__ import annotations

import pytest

from digital_twin.storage import (
    DEFAULT_BUCKET_NAME,
    InMemoryKBStorage,
    KBObjectStorage,
    KBPrefix,
    KBStorageError,
)
from digital_twin.storage.kb_storage import _PREFIX_MARKER, object_key


def test_kb_prefix_covers_met_476_layout():
    # The four prefixes MET-476 standardises on, exactly.
    assert {p.value for p in KBPrefix} == {
        "datasheets",
        "extracted-properties",
        "design-decisions",
        "bom-specs",
    }


def test_default_bucket_name():
    assert DEFAULT_BUCKET_NAME == "metaforge-kb"


def test_object_key_composes_prefix_and_name():
    assert object_key(KBPrefix.DATASHEETS, "ESP32.pdf") == "datasheets/ESP32.pdf"


def test_object_key_rejects_empty_and_traversal():
    with pytest.raises(ValueError, match="non-empty"):
        object_key(KBPrefix.DATASHEETS, "")
    with pytest.raises(ValueError, match="invalid"):
        object_key(KBPrefix.DATASHEETS, "../escape")
    with pytest.raises(ValueError, match="invalid"):
        object_key(KBPrefix.DATASHEETS, "a/../b")


def test_in_memory_storage_implements_protocol():
    assert isinstance(InMemoryKBStorage(), KBObjectStorage)


@pytest.mark.asyncio
async def test_initialize_creates_prefix_markers_idempotently():
    store = InMemoryKBStorage()
    await store.initialize()
    await store.initialize()  # second call is a no-op
    for prefix in KBPrefix:
        assert await store.exists(prefix, _PREFIX_MARKER)


@pytest.mark.asyncio
async def test_put_get_round_trip():
    store = InMemoryKBStorage()
    await store.initialize()
    key = await store.put_object(KBPrefix.DATASHEETS, "esp32.pdf", b"hello world")
    assert key == "datasheets/esp32.pdf"
    assert await store.get_object(KBPrefix.DATASHEETS, "esp32.pdf") == b"hello world"
    assert await store.exists(KBPrefix.DATASHEETS, "esp32.pdf")


@pytest.mark.asyncio
async def test_put_rejects_non_bytes():
    store = InMemoryKBStorage()
    await store.initialize()
    with pytest.raises(TypeError):
        await store.put_object(KBPrefix.DATASHEETS, "x", "not bytes")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_missing_raises_storage_error():
    store = InMemoryKBStorage()
    await store.initialize()
    with pytest.raises(KBStorageError, match="not found"):
        await store.get_object(KBPrefix.DATASHEETS, "missing.pdf")


@pytest.mark.asyncio
async def test_list_objects_excludes_prefix_marker_and_other_prefixes():
    store = InMemoryKBStorage()
    await store.initialize()
    await store.put_object(KBPrefix.DATASHEETS, "a.pdf", b"a")
    await store.put_object(KBPrefix.DATASHEETS, "b.pdf", b"bb")
    await store.put_object(KBPrefix.BOM_SPECS, "c.yaml", b"c")

    listings = await store.list_objects(KBPrefix.DATASHEETS)
    keys = [item.key for item in listings]
    assert keys == ["datasheets/a.pdf", "datasheets/b.pdf"]
    # The .keep marker that initialize() wrote is hidden from listings.
    assert not any(item.key.endswith(f"/{_PREFIX_MARKER}") for item in listings)
    # Other prefixes are not leaked.
    assert "bom-specs/c.yaml" not in keys


@pytest.mark.asyncio
async def test_delete_object_returns_existed_flag():
    store = InMemoryKBStorage()
    await store.initialize()
    await store.put_object(KBPrefix.BOM_SPECS, "bom.yaml", b"data")
    assert await store.delete_object(KBPrefix.BOM_SPECS, "bom.yaml") is True
    assert await store.delete_object(KBPrefix.BOM_SPECS, "bom.yaml") is False
    assert not await store.exists(KBPrefix.BOM_SPECS, "bom.yaml")
