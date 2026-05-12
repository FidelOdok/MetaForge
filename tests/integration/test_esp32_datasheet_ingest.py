"""Integration test for the datasheet ingestion pipeline (MET-431).

Drives the full pipeline end-to-end: PDF → parse → ingest →
TwinAPI queries → supersedes / describes edges.

Real ESP32 datasheet ingestion is a developer-driven gesture
(``python scripts/datasheets/fetch_and_extract.py --only ESP32`` then
the snippet below). This test uses the small in-repo
``datasheet_excerpt.pdf`` fixture as a stand-in so CI runs deterministic
and offline — but the code paths exercised are identical to the real
ESP32 run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from digital_twin.datasheets import parse_datasheet_pdf
from twin_core.api import InMemoryTwinAPI
from twin_core.models import Component
from twin_core.models.enums import EdgeType

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "knowledge" / "datasheet_excerpt.pdf"


def _pdfplumber_available() -> bool:
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = [
    pytest.mark.skipif(not _FIXTURE.exists(), reason="datasheet PDF fixture missing"),
    pytest.mark.skipif(
        not _pdfplumber_available(),
        reason="pdfplumber not installed (pip install -e .[knowledge])",
    ),
]


class TestEsp32DatasheetPipeline:
    """End-to-end: parse PDF → ingest → query the Twin."""

    async def test_first_ingest_creates_node(self, tmp_path: Path) -> None:
        twin = InMemoryTwinAPI.create()
        pdf = tmp_path / "esp32-wroom-32e_rev1.pdf"
        shutil.copy(_FIXTURE, pdf)

        ds = parse_datasheet_pdf(
            pdf,
            mpn="ESP32-WROOM-32E",
            manufacturer="Espressif",
            revision="rev1",
        )
        await twin.ingest_datasheet(ds)

        fetched = await twin.get_current_datasheet("ESP32-WROOM-32E")
        assert fetched is not None
        assert fetched.mpn == "ESP32-WROOM-32E"
        assert fetched.manufacturer == "Espressif"
        assert fetched.revision == "rev1"
        assert fetched.page_count >= 1

    async def test_re_ingest_same_bytes_is_idempotent(self, tmp_path: Path) -> None:
        twin = InMemoryTwinAPI.create()
        pdf = tmp_path / "esp32-wroom-32e_rev1.pdf"
        shutil.copy(_FIXTURE, pdf)

        ds_first = parse_datasheet_pdf(
            pdf,
            mpn="ESP32-WROOM-32E",
            manufacturer="Espressif",
            revision="rev1",
        )
        first = await twin.ingest_datasheet(ds_first)

        ds_again = parse_datasheet_pdf(
            pdf,
            mpn="ESP32-WROOM-32E",
            manufacturer="Espressif",
            revision="rev1",  # same bytes, same hash → idempotent
        )
        second = await twin.ingest_datasheet(ds_again)

        assert second.id == first.id

    async def test_new_revision_chains_via_supersedes(self, tmp_path: Path) -> None:
        twin = InMemoryTwinAPI.create()
        pdf_rev1 = tmp_path / "rev1.pdf"
        pdf_rev2 = tmp_path / "rev2.pdf"
        # Make the two files have distinct bytes so file_hash differs.
        shutil.copy(_FIXTURE, pdf_rev1)
        pdf_rev2.write_bytes(_FIXTURE.read_bytes() + b"\n%%distinct revision\n")

        ds_v1 = parse_datasheet_pdf(
            pdf_rev1, mpn="ESP32-WROOM-32E", manufacturer="Espressif", revision="rev1"
        )
        ds_v2 = parse_datasheet_pdf(
            pdf_rev2, mpn="ESP32-WROOM-32E", manufacturer="Espressif", revision="rev2"
        )
        await twin.ingest_datasheet(ds_v1)
        await twin.ingest_datasheet(ds_v2)

        current = await twin.get_current_datasheet("ESP32-WROOM-32E")
        assert current is not None
        assert current.revision == "rev2"

        # Full revision history is recoverable.
        history = await twin.find_datasheets_by_mpn("ESP32-WROOM-32E")
        assert {d.revision for d in history} == {"rev1", "rev2"}

    async def test_describes_edge_to_existing_component(self, tmp_path: Path) -> None:
        """When a Component already exists for the MPN, ingest auto-links."""
        twin = InMemoryTwinAPI.create()
        comp = Component(part_number="ESP32-WROOM-32E", manufacturer="Espressif")
        await twin.add_component(comp)

        pdf = tmp_path / "esp32.pdf"
        shutil.copy(_FIXTURE, pdf)
        ds = parse_datasheet_pdf(
            pdf, mpn="ESP32-WROOM-32E", manufacturer="Espressif", revision="rev1"
        )
        await twin.ingest_datasheet(ds)

        edges = await twin._graph.get_edges(
            ds.id, direction="outgoing", edge_type=EdgeType.DESCRIBES
        )
        assert len(edges) == 1
        assert edges[0].target_id == comp.id
