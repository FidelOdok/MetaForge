"""R8.2: an assembly part built on unaccepted estimates is a fit gap, not a pass."""

from __future__ import annotations

from typing import Any

import pytest

from api_gateway.library import estimate_gaps
from tests.unit.test_library_estimates import _register, _sourced
from twin_core.dfa.checks import AssemblyReport, check_estimates
from twin_core.library.catalog import SqliteCatalog


def _report(check: Any) -> AssemblyReport:
    return AssemblyReport(assembly_ref="ASM@1", part_refs=["A@1", "B@1"], checks=[check])


def test_an_unaccepted_part_is_a_medium_finding_that_blocks() -> None:
    check = check_estimates(["A@1", "B@1"], {"B@1": "breakout: its estimates are proposed"})
    assert check.status == "failed"
    (finding,) = check.findings
    assert finding.severity == "medium" and finding.parts == ["B@1"]
    assert _report(check).outcome() == "issues_found"


def test_no_gaps_pass_and_an_unread_library_is_incomplete() -> None:
    assert _report(check_estimates(["A@1"], {})).outcome() == "passed"
    unread = check_estimates(["A@1"], None)
    assert unread.status == "not_assessed" and unread.required
    assert _report(unread).outcome() == "incomplete"


def test_parts_are_found_by_their_model_work_product(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = SqliteCatalog()
    model = {"step_work_product_id": "wp-guessed"}
    _register(catalog, geometry=model)  # estimates, proposed
    _register(
        catalog,
        name="sourced",
        specs={"length_mm": _sourced(25.4), "width_mm": _sourced(17.78)},
        geometry={"step_work_product_id": "wp-sourced"},
    )
    import api_gateway.library.catalog_routes as routes

    monkeypatch.setattr(routes, "get_catalog", lambda: catalog)
    gaps = estimate_gaps.gaps_by_work_product(["wp-guessed", "wp-sourced", "wp-other"])
    assert list(gaps) == ["wp-guessed"] and "proposed" in gaps["wp-guessed"]
