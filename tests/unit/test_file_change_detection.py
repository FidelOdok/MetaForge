"""Tests for the file change detection pipeline and drift reconciler (MET-204).

All tests are fully mocked — no real filesystem operations needed.
"""

from __future__ import annotations

import hashlib
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from digital_twin.assistant.adapters.base import GraphMutation, MutationType
from digital_twin.assistant.adapters.firmware_adapter import FirmwareAdapter
from digital_twin.assistant.adapters.freecad_adapter import FreecadAdapter
from digital_twin.assistant.adapters.kicad_adapter import KicadAdapter
from digital_twin.assistant.reconciler import (
    DriftDirection,
    DriftReconciler,
    DriftResult,
    StateLink,
)
from digital_twin.assistant.watcher import (
    ChangeType,
    FileChangeEvent,
    FileWatcher,
)

# ---------------------------------------------------------------------------
# FileWatcher tests
# ---------------------------------------------------------------------------


class TestFileWatcher:
    """Tests for the FileWatcher class."""

    def test_default_extensions(self) -> None:
        watcher = FileWatcher(watch_dirs=["/tmp/test"])
        assert ".kicad_sch" in watcher._extensions
        assert ".FCStd" in watcher._extensions
        assert ".c" in watcher._extensions
        assert ".py" in watcher._extensions

    def test_custom_extensions(self) -> None:
        watcher = FileWatcher(
            watch_dirs=["/tmp/test"],
            extensions={".txt", ".json"},
        )
        assert watcher._extensions == {".txt", ".json"}

    def test_extension_filter(self) -> None:
        watcher = FileWatcher(
            watch_dirs=["/tmp/test"],
            extensions={".kicad_sch", ".py"},
        )
        assert watcher._matches_extension(Path("/foo/bar.kicad_sch")) is True
        assert watcher._matches_extension(Path("/foo/bar.py")) is True
        assert watcher._matches_extension(Path("/foo/bar.txt")) is False
        assert watcher._matches_extension(Path("/foo/bar.doc")) is False

    def test_debounce_logic(self) -> None:
        watcher = FileWatcher(watch_dirs=["/tmp/test"], debounce_ms=500)

        # First event for a path is never debounced
        assert watcher._is_debounced("/foo/bar.py") is False

        # Simulate a recent event
        import time

        watcher._last_event_ns["/foo/bar.py"] = time.monotonic_ns()
        assert watcher._is_debounced("/foo/bar.py") is True

        # Old event should not be debounced
        watcher._last_event_ns["/foo/old.py"] = time.monotonic_ns() - 1_000_000_000
        assert watcher._is_debounced("/foo/old.py") is False

    def test_map_change_type(self) -> None:
        assert FileWatcher._map_change_type(1) == ChangeType.CREATED
        assert FileWatcher._map_change_type(2) == ChangeType.MODIFIED
        assert FileWatcher._map_change_type(3) == ChangeType.DELETED

    async def test_compute_hash(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        result = await FileWatcher._compute_hash(test_file)
        assert result == expected

    async def test_compute_hash_missing_file(self) -> None:
        result = await FileWatcher._compute_hash(Path("/nonexistent/file.txt"))
        assert result == ""

    def test_on_change_registers_callback(self) -> None:
        watcher = FileWatcher(watch_dirs=["/tmp/test"])
        cb = AsyncMock()
        watcher.on_change(cb)
        assert len(watcher._callbacks) == 1

    async def test_start_without_watchfiles_raises(self) -> None:
        watcher = FileWatcher(watch_dirs=["/tmp/test"])
        with patch("digital_twin.assistant.watcher.HAS_WATCHFILES", False):
            with pytest.raises(RuntimeError, match="watchfiles is required"):
                await watcher.start()

    async def test_dispatch_calls_callbacks(self) -> None:
        watcher = FileWatcher(watch_dirs=["/tmp/test"])
        cb1 = AsyncMock()
        cb2 = AsyncMock()
        watcher.on_change(cb1)
        watcher.on_change(cb2)

        event = FileChangeEvent(
            path="/tmp/test.py",
            change_type=ChangeType.MODIFIED,
            file_hash="abc123",
        )
        await watcher._dispatch(event)

        cb1.assert_called_once_with(event)
        cb2.assert_called_once_with(event)

    async def test_dispatch_tolerates_callback_error(self) -> None:
        watcher = FileWatcher(watch_dirs=["/tmp/test"])
        failing_cb = AsyncMock(side_effect=ValueError("boom"))
        ok_cb = AsyncMock()
        watcher.on_change(failing_cb)
        watcher.on_change(ok_cb)

        event = FileChangeEvent(
            path="/tmp/test.py",
            change_type=ChangeType.MODIFIED,
            file_hash="abc123",
        )
        await watcher._dispatch(event)

        # Second callback should still be called despite first failing
        ok_cb.assert_called_once_with(event)

    async def test_debounce_collapses_rapid_events(self) -> None:
        """Simulate rapid changes to the same path — only 1 should be emitted."""
        watcher = FileWatcher(watch_dirs=["/tmp/test"], debounce_ms=500)
        cb = AsyncMock()
        watcher.on_change(cb)

        # First call processes the change
        with patch.object(watcher, "_compute_hash", return_value="hash1"):
            await watcher._process_changes({(2, "/tmp/test/main.py")})

        assert cb.call_count == 1

        # Immediately try the same path again — should be debounced
        with patch.object(watcher, "_compute_hash", return_value="hash2"):
            await watcher._process_changes({(2, "/tmp/test/main.py")})

        # Still only 1 call because the second was debounced
        assert cb.call_count == 1

        # Try 3 more times rapidly — all should be debounced
        for i in range(3):
            with patch.object(watcher, "_compute_hash", return_value=f"hash{i + 3}"):
                await watcher._process_changes({(2, "/tmp/test/main.py")})

        # Total: still only 1 event emitted from 5 rapid changes
        assert cb.call_count == 1


# ---------------------------------------------------------------------------
# FileChangeEvent model tests
# ---------------------------------------------------------------------------


class TestFileChangeEvent:
    def test_create_event(self) -> None:
        event = FileChangeEvent(
            path="/tmp/test.kicad_sch",
            change_type=ChangeType.CREATED,
            file_hash="abc123def456",
        )
        assert event.path == "/tmp/test.kicad_sch"
        assert event.change_type == ChangeType.CREATED
        assert event.file_hash == "abc123def456"
        assert isinstance(event.timestamp, datetime)

    def test_default_hash_is_empty(self) -> None:
        event = FileChangeEvent(
            path="/tmp/test.py",
            change_type=ChangeType.MODIFIED,
        )
        assert event.file_hash == ""


# ---------------------------------------------------------------------------
# KicadAdapter tests
# ---------------------------------------------------------------------------


class TestKicadAdapter:
    def setup_method(self) -> None:
        self.adapter = KicadAdapter()

    def test_supported_extensions(self) -> None:
        assert self.adapter.supported_extensions == {".kicad_sch", ".kicad_pcb"}

    async def test_parse_deleted_file(self) -> None:
        event = FileChangeEvent(
            path="/project/board.kicad_sch",
            change_type=ChangeType.DELETED,
            file_hash="",
        )
        mutations = await self.adapter.parse_change(event)
        assert len(mutations) == 1
        assert mutations[0].mutation_type == MutationType.NODE_DELETED
        assert mutations[0].node_type == "kicad_file"

    async def test_parse_schematic_components(self, tmp_path: Path) -> None:
        sch_content = """(kicad_sch (version 20230121) (generator eeschema)
  (symbol (lib_id "Device:R") (at 100 50 0)
    (property "Reference" "R1" (at 0 0 0))
    (property "Value" "10k" (at 0 0 0))
    (property "Footprint" "Resistor_SMD:R_0805" (at 0 0 0))
  )
  (symbol (lib_id "Device:C") (at 150 50 0)
    (property "Reference" "C1" (at 0 0 0))
    (property "Value" "100nF" (at 0 0 0))
    (property "Footprint" "Capacitor_SMD:C_0805" (at 0 0 0))
  )
)"""
        sch_file = tmp_path / "test.kicad_sch"
        sch_file.write_text(sch_content)

        event = FileChangeEvent(
            path=str(sch_file),
            change_type=ChangeType.MODIFIED,
            file_hash="somehash",
        )
        mutations = await self.adapter.parse_change(event)

        # Should extract R1 and C1
        assert len(mutations) >= 2
        refs = {m.properties.get("reference") for m in mutations}
        assert "R1" in refs
        assert "C1" in refs

    async def test_parse_pcb(self, tmp_path: Path) -> None:
        pcb_content = """(kicad_pcb (version 20230121)
  (segment (start 100 50) (end 150 50) (width 0.25) (layer "F.Cu"))
  (segment (start 150 50) (end 200 50) (width 0.5) (layer "F.Cu"))
  (via (at 120 60) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu"))
  (via (at 180 60) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu"))
  (gr_rect (start 0 0) (end 100 80) (layer "Edge.Cuts"))
)"""
        pcb_file = tmp_path / "test.kicad_pcb"
        pcb_file.write_text(pcb_content)

        event = FileChangeEvent(
            path=str(pcb_file),
            change_type=ChangeType.MODIFIED,
            file_hash="somehash",
        )
        mutations = await self.adapter.parse_change(event)
        assert len(mutations) == 1
        props = mutations[0].properties
        assert props["via_count"] == 2
        assert "0.25" in props["track_widths_mm"]
        assert "0.5" in props["track_widths_mm"]
        assert props["width_mm"] == 100.0
        assert props["height_mm"] == 80.0

    async def test_parse_failure_returns_empty(self) -> None:
        event = FileChangeEvent(
            path="/nonexistent/file.kicad_sch",
            change_type=ChangeType.MODIFIED,
            file_hash="somehash",
        )
        mutations = await self.adapter.parse_change(event)
        assert mutations == []


# ---------------------------------------------------------------------------
# FreecadAdapter tests
# ---------------------------------------------------------------------------


class TestFreecadAdapter:
    def setup_method(self) -> None:
        self.adapter = FreecadAdapter()

    def test_supported_extensions(self) -> None:
        assert self.adapter.supported_extensions == {".FCStd", ".step", ".stp"}

    async def test_parse_deleted_file(self) -> None:
        event = FileChangeEvent(
            path="/project/part.FCStd",
            change_type=ChangeType.DELETED,
            file_hash="",
        )
        mutations = await self.adapter.parse_change(event)
        assert len(mutations) == 1
        assert mutations[0].mutation_type == MutationType.NODE_DELETED

    async def test_parse_fcstd_zip(self, tmp_path: Path) -> None:
        """Create a minimal FCStd (ZIP with Document.xml) and parse it."""
        doc_xml = """<?xml version="1.0" encoding="utf-8"?>
<Document>
  <ObjectData>
    <Object name="Body">
      <Properties>
        <Property name="Label"><String value="MainBody"/></Property>
        <Property name="Length"><Float value="50.0"/></Property>
      </Properties>
    </Object>
    <Object name="Pad001">
      <Properties>
        <Property name="Label"><String value="TopPad"/></Property>
      </Properties>
    </Object>
  </ObjectData>
</Document>"""
        fcstd_file = tmp_path / "test.FCStd"
        with zipfile.ZipFile(fcstd_file, "w") as zf:
            zf.writestr("Document.xml", doc_xml)

        event = FileChangeEvent(
            path=str(fcstd_file),
            change_type=ChangeType.MODIFIED,
            file_hash="somehash",
        )
        mutations = await self.adapter.parse_change(event)
        assert len(mutations) >= 2

        names = {m.properties.get("name") for m in mutations}
        assert "Body" in names
        assert "Pad001" in names

    async def test_parse_corrupted_zip(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.FCStd"
        bad_file.write_bytes(b"not a zip file at all")

        event = FileChangeEvent(
            path=str(bad_file),
            change_type=ChangeType.MODIFIED,
            file_hash="somehash",
        )
        mutations = await self.adapter.parse_change(event)
        assert mutations == []

    async def test_parse_step_file(self, tmp_path: Path) -> None:
        step_content = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('FreeCAD Model'),'2;1');
ENDSEC;
DATA;
#1=IFCPROJECT('abc');
#2=IFCSITE('def');
#3=IFCBUILDING('ghi');
ENDSEC;
END-ISO-10303-21;"""
        step_file = tmp_path / "model.step"
        step_file.write_text(step_content)

        event = FileChangeEvent(
            path=str(step_file),
            change_type=ChangeType.MODIFIED,
            file_hash="somehash",
        )
        mutations = await self.adapter.parse_change(event)
        assert len(mutations) == 1
        assert mutations[0].node_type == "step_model"
        assert mutations[0].properties["entity_count"] == 3
        assert mutations[0].properties["file_size_bytes"] > 0


# ---------------------------------------------------------------------------
# FirmwareAdapter tests
# ---------------------------------------------------------------------------


class TestFirmwareAdapter:
    def setup_method(self) -> None:
        self.adapter = FirmwareAdapter()

    def test_supported_extensions(self) -> None:
        assert ".c" in self.adapter.supported_extensions
        assert ".h" in self.adapter.supported_extensions
        assert ".py" in self.adapter.supported_extensions

    async def test_parse_deleted_file(self) -> None:
        event = FileChangeEvent(
            path="/project/main.c",
            change_type=ChangeType.DELETED,
            file_hash="",
        )
        mutations = await self.adapter.parse_change(event)
        assert len(mutations) == 1
        assert mutations[0].mutation_type == MutationType.NODE_DELETED

    async def test_parse_c_file(self, tmp_path: Path) -> None:
        c_content = """#include <stdio.h>
#include "config.h"

#define MAX_RETRIES 5
#define BUFFER_SIZE 1024

void init_hardware(void) {
    // initialization
}

int main(int argc, char* argv[]) {
    init_hardware();
    return 0;
}
"""
        c_file = tmp_path / "main.c"
        c_file.write_text(c_content)

        event = FileChangeEvent(
            path=str(c_file),
            change_type=ChangeType.MODIFIED,
            file_hash="somehash",
        )
        mutations = await self.adapter.parse_change(event)

        # Should find functions, includes, and defines
        func_mutations = [m for m in mutations if m.node_type == "function"]
        func_names = {m.properties["name"] for m in func_mutations}
        assert "init_hardware" in func_names
        assert "main" in func_names

        include_mutations = [m for m in mutations if m.node_id.endswith("::includes")]
        assert len(include_mutations) == 1
        includes = include_mutations[0].properties["includes"]
        assert "stdio.h" in includes
        assert "config.h" in includes

        define_mutations = [m for m in mutations if m.node_id.endswith("::defines")]
        assert len(define_mutations) == 1
        defines = define_mutations[0].properties["defines"]
        assert defines["MAX_RETRIES"] == "5"
        assert defines["BUFFER_SIZE"] == "1024"

    async def test_parse_python_file(self, tmp_path: Path) -> None:
        py_content = """def setup():
    pass

async def process_data(data):
    return data
"""
        py_file = tmp_path / "app.py"
        py_file.write_text(py_content)

        event = FileChangeEvent(
            path=str(py_file),
            change_type=ChangeType.MODIFIED,
            file_hash="somehash",
        )
        mutations = await self.adapter.parse_change(event)
        func_names = {m.properties["name"] for m in mutations if m.node_type == "function"}
        assert "setup" in func_names
        assert "process_data" in func_names

    async def test_parse_read_failure(self) -> None:
        event = FileChangeEvent(
            path="/nonexistent/main.c",
            change_type=ChangeType.MODIFIED,
            file_hash="somehash",
        )
        mutations = await self.adapter.parse_change(event)
        assert mutations == []


# ---------------------------------------------------------------------------
# DriftDirection enum tests
# ---------------------------------------------------------------------------


class TestDriftDirection:
    def test_values(self) -> None:
        assert DriftDirection.FILE_NEWER == "file_newer"
        assert DriftDirection.GRAPH_NEWER == "graph_newer"
        assert DriftDirection.FILE_MISSING == "file_missing"
        assert DriftDirection.IN_SYNC == "in_sync"

    def test_all_values_exist(self) -> None:
        assert len(DriftDirection) == 4


# ---------------------------------------------------------------------------
# DriftReconciler tests
# ---------------------------------------------------------------------------


class TestDriftReconciler:
    def setup_method(self) -> None:
        self.twin = AsyncMock()
        self.event_bus = AsyncMock()
        self.reconciler = DriftReconciler(
            twin=self.twin,
            event_bus=self.event_bus,
        )

    async def test_register_link(self) -> None:
        node_id = uuid4()
        link = await self.reconciler.register_link(
            file_path="/project/board.kicad_sch",
            node_id=node_id,
            file_hash="abc123",
        )
        assert isinstance(link, StateLink)
        assert link.file_path == "/project/board.kicad_sch"
        assert link.graph_node_id == node_id
        assert link.file_hash == "abc123"
        assert self.reconciler.link_count == 1

    async def test_get_link(self) -> None:
        node_id = uuid4()
        await self.reconciler.register_link("/project/test.py", node_id, "hash1")
        link = self.reconciler.get_link("/project/test.py")
        assert link is not None
        assert link.graph_node_id == node_id

    async def test_get_link_missing(self) -> None:
        assert self.reconciler.get_link("/nonexistent") is None

    async def test_check_drift_no_link(self) -> None:
        result = await self.reconciler.check_drift("/untracked/file.py")
        assert result.direction == DriftDirection.IN_SYNC
        assert "No link registered" in result.details

    async def test_check_drift_file_missing(self) -> None:
        node_id = uuid4()
        await self.reconciler.register_link("/missing/file.py", node_id, "hash1")

        result = await self.reconciler.check_drift("/missing/file.py")
        assert result.direction == DriftDirection.FILE_MISSING

    async def test_check_drift_in_sync(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.py"
        test_file.write_bytes(b"content")
        file_hash = hashlib.sha256(b"content").hexdigest()

        node_id = uuid4()
        await self.reconciler.register_link(str(test_file), node_id, file_hash)

        result = await self.reconciler.check_drift(str(test_file))
        assert result.direction == DriftDirection.IN_SYNC

    async def test_check_drift_file_newer(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.py"
        test_file.write_bytes(b"new content")

        node_id = uuid4()
        await self.reconciler.register_link(str(test_file), node_id, "old_hash")

        result = await self.reconciler.check_drift(str(test_file))
        assert result.direction == DriftDirection.FILE_NEWER

    async def test_reconcile_file_newer(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.py"
        test_file.write_bytes(b"updated content")
        new_hash = hashlib.sha256(b"updated content").hexdigest()

        node_id = uuid4()
        await self.reconciler.register_link(str(test_file), node_id, "old_hash")

        drift = DriftResult(
            file_path=str(test_file),
            direction=DriftDirection.FILE_NEWER,
            details="File changed",
        )
        await self.reconciler.reconcile(drift)

        # Link should now have the new hash
        link = self.reconciler.get_link(str(test_file))
        assert link is not None
        assert link.file_hash == new_hash

        # Drift event should be published
        self.event_bus.publish.assert_called()

    async def test_reconcile_file_missing(self) -> None:
        node_id = uuid4()
        await self.reconciler.register_link("/gone/file.py", node_id, "hash1")
        assert self.reconciler.link_count == 1

        drift = DriftResult(
            file_path="/gone/file.py",
            direction=DriftDirection.FILE_MISSING,
            details="File deleted",
        )
        await self.reconciler.reconcile(drift)

        # Link should be removed
        assert self.reconciler.link_count == 0
        assert self.reconciler.get_link("/gone/file.py") is None

    async def test_reconcile_in_sync_noop(self) -> None:
        drift = DriftResult(
            file_path="/some/file.py",
            direction=DriftDirection.IN_SYNC,
            details="All good",
        )
        await self.reconciler.reconcile(drift)
        # Should not publish any event
        self.event_bus.publish.assert_not_called()

    async def test_full_scan(self, tmp_path: Path) -> None:
        # Register 3 links: 1 in sync, 1 drifted, 1 missing
        file_a = tmp_path / "a.py"
        file_a.write_bytes(b"aaa")
        hash_a = hashlib.sha256(b"aaa").hexdigest()

        file_b = tmp_path / "b.py"
        file_b.write_bytes(b"bbb_modified")

        await self.reconciler.register_link(str(file_a), uuid4(), hash_a)
        await self.reconciler.register_link(str(file_b), uuid4(), "old_hash_b")
        await self.reconciler.register_link("/missing/c.py", uuid4(), "hash_c")

        results = await self.reconciler.run_full_scan()
        assert len(results) == 3

        directions = {r.file_path: r.direction for r in results}
        assert directions[str(file_a)] == DriftDirection.IN_SYNC
        assert directions[str(file_b)] == DriftDirection.FILE_NEWER
        assert directions["/missing/c.py"] == DriftDirection.FILE_MISSING

        # Events should be published for drifted files
        assert self.event_bus.publish.call_count == 2  # FILE_NEWER + FILE_MISSING


# ---------------------------------------------------------------------------
# GraphMutation model tests
# ---------------------------------------------------------------------------


class TestGraphMutation:
    def test_create_mutation(self) -> None:
        m = GraphMutation(
            mutation_type=MutationType.NODE_CREATED,
            node_type="schematic_component",
            node_id="test::R1",
            properties={"reference": "R1", "value": "10k"},
            source_file="/project/test.kicad_sch",
        )
        assert m.mutation_type == MutationType.NODE_CREATED
        assert m.node_type == "schematic_component"
        assert m.properties["reference"] == "R1"

    def test_default_properties_empty(self) -> None:
        m = GraphMutation(
            mutation_type=MutationType.NODE_DELETED,
            node_type="source_file",
            node_id="/project/main.c",
            source_file="/project/main.c",
        )
        assert m.properties == {}


# ---------------------------------------------------------------------------
# StateLink model tests
# ---------------------------------------------------------------------------


class TestStateLink:
    def test_create_state_link(self) -> None:
        node_id = uuid4()
        link = StateLink(
            file_path="/project/test.py",
            file_hash="abc123",
            graph_node_id=node_id,
        )
        assert link.file_path == "/project/test.py"
        assert link.file_hash == "abc123"
        assert link.graph_node_id == node_id
        assert isinstance(link.last_synced, datetime)
