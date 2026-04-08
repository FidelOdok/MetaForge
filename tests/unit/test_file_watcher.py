"""Unit tests for the FileWatcher background polling task (MET-252)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api_gateway.twin.file_link import FileLink, _file_hash, link_store
from api_gateway.twin.file_watcher import FileWatcher

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_link_store():
    """Clear the link_store singleton before and after each test."""
    link_store._links.clear()
    yield
    link_store._links.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watcher_detects_changed_file(tmp_path):
    """Watcher calls sync_linked_file when a file's hash has changed."""
    f = tmp_path / "design.step"
    f.write_bytes(b"original content")

    link = FileLink(
        work_product_id="wp-changed",
        source_path=str(f),
        source_hash="stale_hash_not_matching",
        sync_status="synced",
        watch=True,
    )
    link_store.create(link)

    watcher = FileWatcher()
    mock_twin = object()
    watcher.set_twin(mock_twin)

    with patch(
        "api_gateway.twin.file_watcher.sync_linked_file", new_callable=AsyncMock
    ) as mock_sync:
        await watcher._poll()

    mock_sync.assert_called_once()
    call_args = mock_sync.call_args
    assert call_args[0][0].work_product_id == "wp-changed"
    assert call_args[0][1] is mock_twin

    updated = link_store.get("wp-changed")
    assert updated is not None
    assert updated.sync_status == "changed"


@pytest.mark.asyncio
async def test_watcher_detects_disconnected_file():
    """Watcher marks status as disconnected when the source file no longer exists."""
    link = FileLink(
        work_product_id="wp-gone",
        source_path="/nonexistent/path/file.step",
        source_hash="some_hash",
        sync_status="synced",
        watch=True,
    )
    link_store.create(link)

    watcher = FileWatcher()
    watcher.set_twin(object())

    with patch(
        "api_gateway.twin.file_watcher.sync_linked_file", new_callable=AsyncMock
    ) as mock_sync:
        await watcher._poll()

    mock_sync.assert_not_called()

    updated = link_store.get("wp-gone")
    assert updated is not None
    assert updated.sync_status == "disconnected"


@pytest.mark.asyncio
async def test_watcher_skips_watch_false(tmp_path):
    """Watcher ignores links where watch=False."""
    f = tmp_path / "design.step"
    f.write_bytes(b"some content")

    link = FileLink(
        work_product_id="wp-nowatch",
        source_path=str(f),
        source_hash="stale_hash",
        sync_status="synced",
        watch=False,  # watching disabled
    )
    link_store.create(link)

    watcher = FileWatcher()
    watcher.set_twin(object())

    with patch(
        "api_gateway.twin.file_watcher.sync_linked_file", new_callable=AsyncMock
    ) as mock_sync:
        await watcher._poll()

    mock_sync.assert_not_called()
    # Status should be unchanged
    assert link_store.get("wp-nowatch").sync_status == "synced"


@pytest.mark.asyncio
async def test_watcher_skips_unchanged_file(tmp_path):
    """Watcher does not call sync_linked_file when the hash matches."""
    f = tmp_path / "design.step"
    f.write_bytes(b"stable content")
    correct_hash = _file_hash(str(f))

    link = FileLink(
        work_product_id="wp-synced",
        source_path=str(f),
        source_hash=correct_hash,
        sync_status="synced",
        watch=True,
    )
    link_store.create(link)

    watcher = FileWatcher()
    watcher.set_twin(object())

    with patch(
        "api_gateway.twin.file_watcher.sync_linked_file", new_callable=AsyncMock
    ) as mock_sync:
        await watcher._poll()

    mock_sync.assert_not_called()
    assert link_store.get("wp-synced").sync_status == "synced"


@pytest.mark.asyncio
async def test_watcher_start_stop():
    """FileWatcher starts a background task and cancels it cleanly on stop()."""
    watcher = FileWatcher(interval=60.0)  # long interval — won't fire in test
    await watcher.start()

    assert watcher._task is not None
    assert not watcher._task.done()

    await watcher.stop()

    assert watcher._task.done()


@pytest.mark.asyncio
async def test_watcher_handles_link_error(tmp_path):
    """Watcher continues polling when check_sync_status raises for one link."""
    f = tmp_path / "good.step"
    f.write_bytes(b"good content")
    good_hash = _file_hash(str(f))

    # A link that will cause check_sync_status to raise
    bad_link = FileLink(
        work_product_id="wp-bad",
        source_path="/some/path.step",
        source_hash="hash",
        sync_status="synced",
        watch=True,
    )
    # A healthy link with correct hash — should not trigger sync
    good_link = FileLink(
        work_product_id="wp-good",
        source_path=str(f),
        source_hash=good_hash,
        sync_status="synced",
        watch=True,
    )
    link_store.create(bad_link)
    link_store.create(good_link)

    watcher = FileWatcher()
    watcher.set_twin(object())

    with patch(
        "api_gateway.twin.file_watcher.check_sync_status",
        side_effect=RuntimeError("disk read error"),
    ):
        # Must not raise — watcher catches per-link errors
        await watcher._poll()
