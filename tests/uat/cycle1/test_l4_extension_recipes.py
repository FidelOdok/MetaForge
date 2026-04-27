"""UAT-C1-L4 — Extension recipes (MET-314, MET-329, MET-331).

Acceptance bullets validated:

* MET-314: ``docs/architecture/knowledge-ingestion-playbook.md`` exists
  and is cross-linked from the protocol spec.
* MET-329: ``QuoteCapture`` records a quote, exposes price-history with
  trend, links quotes to BOM items.
* MET-331: ``SimulationCapture`` records a run, fingerprint is
  deterministic, similar-run lookup ranks by distance.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from digital_twin.context.quote_capture import QuoteCapture, SupplierQuote
from digital_twin.context.simulation_capture import (
    SimulationCapture,
    SimulationParams,
    SimulationResult,
)
from tests.uat.conftest import REPO_ROOT, assert_validates

pytestmark = [pytest.mark.uat]


# ---------------------------------------------------------------------------
# MET-314 — Knowledge Ingestion Playbook
# ---------------------------------------------------------------------------


def test_met314_ingestion_playbook_exists_and_cross_linked() -> None:
    playbook = REPO_ROOT / "docs" / "architecture" / "knowledge-ingestion-playbook.md"
    assert_validates(
        "MET-314",
        "playbook exists at docs/architecture/knowledge-ingestion-playbook.md",
        playbook.exists() and playbook.stat().st_size > 1000,
        f"path={playbook}",
    )
    spec = (REPO_ROOT / "docs" / "architecture" / "context-engineering.md").read_text()
    assert_validates(
        "MET-314",
        "context-engineering.md cross-links the ingestion playbook",
        "knowledge-ingestion-playbook.md" in spec,
    )


# ---------------------------------------------------------------------------
# MET-329 — Supplier quote capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_met329_quote_capture_records_and_returns_history() -> None:
    cap = QuoteCapture()
    bom_item = uuid4()
    base = datetime.now(UTC)

    for i, price in enumerate([4.20, 4.40, 4.60]):
        await cap.record_quote(
            SupplierQuote(
                mpn="MAX1473EUA+",
                supplier="mouser",
                unit_price=price,
                currency="USD",
                quote_quantity=10,
                pricing_tiers=[{"quantity": 10, "unit_price": price, "currency": "USD"}],
                bom_item_id=bom_item,
                captured_at=base - timedelta(days=30 - i * 10),
            )
        )

    history = cap.price_history("MAX1473EUA+")
    assert_validates(
        "MET-329",
        "price_history returns chronological quotes",
        [q.unit_price for q in history.quotes] == [4.20, 4.40, 4.60],
    )
    trend = history.trend_pct()
    assert_validates(
        "MET-329",
        "trend_pct reports positive trend for rising prices",
        trend is not None and trend > 0,
        f"trend={trend}",
    )
    assert_validates(
        "MET-329",
        "quotes_for_bom_item filters by BOM linkage",
        len(cap.quotes_for_bom_item(bom_item)) == 3,
    )


# ---------------------------------------------------------------------------
# MET-331 — Simulation parameter capture
# ---------------------------------------------------------------------------


def _params(**overrides: object) -> SimulationParams:
    base = {
        "solver": "calculix",
        "simulation_type": "fea",
        "mesh_element_count": 10_000,
        "mesh_element_type": "tet10",
        "materials": ["steel_316"],
        "boundary_conditions": [{"face": "fixed_base", "type": "fixed"}],
        "load_cases": [{"name": "load_1", "magnitude_n": 1000.0}],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return SimulationParams(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_met331_simulation_capture_round_trip_and_similarity() -> None:
    cap = SimulationCapture()
    cad = uuid4()

    await cap.record_run(
        _params(),
        SimulationResult(status="success", duration_seconds=12.5, max_stress=180.5),
        cad_model_id=cad,
    )
    await cap.record_run(
        _params(mesh_element_count=10_500),
        SimulationResult(status="success", duration_seconds=13.0, max_stress=181.0),
        cad_model_id=cad,
    )
    await cap.record_run(
        _params(solver="elmer"),
        SimulationResult(status="success", duration_seconds=20.0),
    )

    similar = cap.find_similar(_params(), top_k=2, cad_model_id=cad)
    assert_validates(
        "MET-331",
        "find_similar returns the requested top_k results",
        len(similar) == 2,
    )
    assert_validates(
        "MET-331",
        "identical params have distance == 0",
        similar[0].distance == 0.0,
        f"distances: {[s.distance for s in similar]}",
    )
    assert_validates(
        "MET-331",
        "elmer/different solver ranks lower than calculix sibling",
        similar[1].distance < 2.0,
        f"distances: {[s.distance for s in similar]}",
    )


def test_met331_fingerprint_is_deterministic() -> None:
    a = _params()
    b = _params()
    assert_validates(
        "MET-331",
        "fingerprint is deterministic for equivalent params",
        a.fingerprint() == b.fingerprint(),
    )
    c = _params(solver="elmer")
    assert_validates(
        "MET-331",
        "fingerprint differs when solver changes",
        a.fingerprint() != c.fingerprint(),
    )
