"""Unit tests for ``digital_twin.context.quote_capture`` (MET-329)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from digital_twin.context.quote_capture import (
    QuoteCapture,
    SupplierQuote,
    cutoff_since,
    quote_from_adapter_payload,
)
from digital_twin.knowledge.service import IngestResult, SearchHit
from digital_twin.knowledge.types import KnowledgeType


def _quote(
    *,
    mpn: str = "ATSAMD21G18",
    supplier: str = "digikey",
    unit_price: float | None = 3.45,
    captured_at: datetime | None = None,
    bom_item_id: UUID | None = None,
    lifecycle: str = "ACTIVE",
    stock: int | None = 1234,
) -> SupplierQuote:
    return SupplierQuote(
        mpn=mpn,
        supplier=supplier,
        unit_price=unit_price,
        currency="USD",
        quote_quantity=10,
        pricing_tiers=[
            {"quantity": 1, "unit_price": 4.20, "currency": "USD"},
            {"quantity": 10, "unit_price": 3.45, "currency": "USD"},
            {"quantity": 100, "unit_price": 2.80, "currency": "USD"},
        ],
        stock_qty=stock,
        lead_time_days=14,
        minimum_order_qty=1,
        lifecycle_status=lifecycle,
        bom_item_id=bom_item_id,
        captured_at=captured_at or datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


class TestRecord:
    @pytest.mark.asyncio
    async def test_records_quote(self) -> None:
        cap = QuoteCapture()
        q = await cap.record_quote(_quote())
        assert q.mpn == "ATSAMD21G18"
        assert q.unit_price == 3.45
        assert len(cap.all_quotes()) == 1

    @pytest.mark.asyncio
    async def test_no_publish_when_service_absent(self) -> None:
        cap = QuoteCapture()
        await cap.record_quote(_quote())
        assert cap.all_quotes()


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------


class TestPriceHistory:
    @pytest.mark.asyncio
    async def test_chronological_order(self) -> None:
        cap = QuoteCapture()
        now = datetime.now(UTC)
        await cap.record_quote(_quote(unit_price=4.0, captured_at=now - timedelta(days=30)))
        await cap.record_quote(_quote(unit_price=4.5, captured_at=now - timedelta(days=10)))
        await cap.record_quote(_quote(unit_price=5.0, captured_at=now))
        hist = cap.price_history("ATSAMD21G18")
        assert [q.unit_price for q in hist.quotes] == [4.0, 4.5, 5.0]
        assert hist.first_seen_at and hist.last_seen_at
        assert hist.first_seen_at < hist.last_seen_at

    @pytest.mark.asyncio
    async def test_supplier_filter(self) -> None:
        cap = QuoteCapture()
        await cap.record_quote(_quote(supplier="digikey", unit_price=3.45))
        await cap.record_quote(_quote(supplier="mouser", unit_price=3.20))
        digikey = cap.price_history("ATSAMD21G18", supplier="digikey")
        mouser = cap.price_history("ATSAMD21G18", supplier="mouser")
        assert len(digikey.quotes) == 1 and digikey.quotes[0].supplier == "digikey"
        assert len(mouser.quotes) == 1 and mouser.quotes[0].supplier == "mouser"

    @pytest.mark.asyncio
    async def test_since_filter_drops_older_rows(self) -> None:
        cap = QuoteCapture()
        now = datetime.now(UTC)
        await cap.record_quote(_quote(unit_price=2.0, captured_at=now - timedelta(days=200)))
        await cap.record_quote(_quote(unit_price=3.0, captured_at=now - timedelta(days=30)))
        recent = cap.price_history("ATSAMD21G18", since=now - timedelta(days=180))
        assert len(recent.quotes) == 1
        assert recent.quotes[0].unit_price == 3.0

    @pytest.mark.asyncio
    async def test_trend_pct_detects_price_increase(self) -> None:
        cap = QuoteCapture()
        now = datetime.now(UTC)
        await cap.record_quote(_quote(unit_price=2.0, captured_at=now - timedelta(days=60)))
        await cap.record_quote(_quote(unit_price=3.0, captured_at=now))
        trend = cap.price_history("ATSAMD21G18").trend_pct()
        assert trend == pytest.approx(0.5)  # 50% increase

    @pytest.mark.asyncio
    async def test_min_max_unit_price(self) -> None:
        cap = QuoteCapture()
        await cap.record_quote(_quote(unit_price=2.5))
        await cap.record_quote(_quote(unit_price=4.0))
        await cap.record_quote(_quote(unit_price=3.2))
        hist = cap.price_history("ATSAMD21G18")
        assert hist.min_unit_price == 2.5
        assert hist.max_unit_price == 4.0


# ---------------------------------------------------------------------------
# BOM linkage
# ---------------------------------------------------------------------------


class TestBomLinkage:
    @pytest.mark.asyncio
    async def test_quotes_for_bom_item(self) -> None:
        cap = QuoteCapture()
        bom = uuid4()
        other_bom = uuid4()
        await cap.record_quote(_quote(bom_item_id=bom, unit_price=3.0))
        await cap.record_quote(_quote(bom_item_id=other_bom, unit_price=9.0))
        await cap.record_quote(_quote(bom_item_id=bom, unit_price=3.2))
        rows = cap.quotes_for_bom_item(bom)
        assert len(rows) == 2
        assert all(q.bom_item_id == bom for q in rows)


# ---------------------------------------------------------------------------
# Adapter composer
# ---------------------------------------------------------------------------


class TestAdapterComposer:
    def test_picks_quoted_qty_tier(self) -> None:
        # Mimics tool_registry PricingBreak shape via simple objects.
        class _Tier:
            def __init__(self, quantity: int, unit_price: float) -> None:
                self.quantity = quantity
                self.unit_price = unit_price
                self.currency = "USD"

        class _Avail:
            stock_qty = 500
            lead_time_days = 21
            minimum_order_qty = 1

        q = quote_from_adapter_payload(
            mpn="MAX1473EUA+",
            supplier="mouser",
            pricing=[_Tier(1, 5.50), _Tier(10, 4.00), _Tier(100, 2.75)],
            availability=_Avail(),
            quote_quantity=25,  # falls into the qty=10 break
        )
        assert q.unit_price == 4.00
        assert q.stock_qty == 500
        assert q.lead_time_days == 21
        assert len(q.pricing_tiers) == 3

    def test_falls_back_to_cheapest_when_no_qty_match(self) -> None:
        class _Tier:
            def __init__(self, quantity: int, unit_price: float) -> None:
                self.quantity = quantity
                self.unit_price = unit_price
                self.currency = "USD"

        q = quote_from_adapter_payload(
            mpn="X",
            supplier="digikey",
            pricing=[_Tier(100, 1.20), _Tier(1000, 0.85)],
            quote_quantity=10,  # below all tiers
        )
        # Falls back to highest-qty (cheapest) tier.
        assert q.unit_price == 0.85


# ---------------------------------------------------------------------------
# Knowledge integration
# ---------------------------------------------------------------------------


class _FakeKnowledge:
    def __init__(self, fail: bool = False) -> None:
        self.ingested: list[dict[str, Any]] = []
        self.fail = fail

    async def ingest(
        self,
        content: str,
        source_path: str,
        knowledge_type: KnowledgeType,
        source_work_product_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        if self.fail:
            raise RuntimeError("kafka down")
        self.ingested.append(
            {
                "content": content,
                "source_path": source_path,
                "knowledge_type": knowledge_type,
                "source_work_product_id": source_work_product_id,
                "metadata": metadata or {},
            }
        )
        return IngestResult(entry_ids=[uuid4()], chunks_indexed=1, source_path=source_path)

    async def search(self, *args: Any, **kwargs: Any) -> list[SearchHit]:  # pragma: no cover
        return []

    async def delete_by_source(
        self, source_path: str, project_id: UUID | None = None
    ) -> int:  # pragma: no cover
        return 0

    async def health_check(self) -> dict[str, Any]:  # pragma: no cover
        return {"status": "ok"}


class TestKnowledgePush:
    @pytest.mark.asyncio
    async def test_publishes_component_rationale(self) -> None:
        knowledge = _FakeKnowledge()
        cap = QuoteCapture(knowledge_service=knowledge)  # type: ignore[arg-type]
        bom = uuid4()
        q = await cap.record_quote(_quote(bom_item_id=bom))
        assert len(knowledge.ingested) == 1
        entry = knowledge.ingested[0]
        assert entry["knowledge_type"] == KnowledgeType.COMPONENT
        assert entry["source_path"] == f"supplier_quote://{q.id}"
        assert entry["source_work_product_id"] == bom
        assert entry["metadata"]["mpn"] == "ATSAMD21G18"
        assert entry["metadata"]["supplier"] == "digikey"
        assert entry["metadata"]["unit_price"] == 3.45

    @pytest.mark.asyncio
    async def test_publish_failure_does_not_break_record(self) -> None:
        cap = QuoteCapture(knowledge_service=_FakeKnowledge(fail=True))  # type: ignore[arg-type]
        q = await cap.record_quote(_quote())
        assert cap.all_quotes() == [q]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_cutoff_since_returns_past_date() -> None:
    cutoff = cutoff_since(180)
    delta = datetime.now(UTC) - cutoff
    # ~180 days, allow small clock drift.
    assert 179 <= delta.days <= 180
