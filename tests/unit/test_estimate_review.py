"""R8: estimates are proposed until a person accepts them with twin.confirm_estimate."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_core.guardrails import (
    APPROVED_BY_ARG,
    DELEGATED_AGENT_ARG,
    ApproverArgumentRejectedError,
    HumanAuthorityRequiredError,
    requires_human_authority,
)
from tests.unit.test_library_estimates import _estimate, _register, _sourced
from tool_registry.tools.library.catalog_tools import register_catalog_tools
from twin_core.library.catalog import CatalogError, SqliteCatalog, estimate_gap


def test_a_part_with_estimates_is_proposed_and_a_sourced_one_is_not() -> None:
    catalog = SqliteCatalog()
    guessed = _register(catalog)
    assert guessed.estimate_review["status"] == "proposed"
    assert set(guessed.estimate_review["basis"]) == {"length_mm", "width_mm"}
    assert "not yet accepted by a person" in (estimate_gap(guessed) or "")
    sourced = _register(
        catalog, name="sourced", specs={"length_mm": _sourced(25.4), "width_mm": _sourced(17.78)}
    )
    assert sourced.estimate_review == {} and estimate_gap(sourced) is None


def test_estimated_geometry_is_proposed_too() -> None:
    entry = _register(
        SqliteCatalog(),
        specs={"length_mm": _sourced(25.4), "width_mm": _sourced(17.78)},
        geometry={"generator": "package_template", "params": {}, "fidelity": "photo_estimate"},
    )
    assert entry.estimate_review["status"] == "proposed"
    assert entry.estimate_review["basis"]["geometry"].startswith("photo_estimate:")


def test_only_a_named_person_moves_it_past_proposed() -> None:
    catalog = SqliteCatalog()
    entry = _register(catalog)
    with pytest.raises(CatalogError, match="cannot change estimate_review"):
        catalog.update(entry.id, {"estimate_review": {"status": "accepted"}})
    with pytest.raises(CatalogError, match="names the person"):
        catalog.decide_estimate(entry.id, "accept", decided_by=" ")
    done = catalog.decide_estimate(entry.id, "accept", decided_by="ada", reason="matches calipers")
    assert (
        done.estimate_review["status"] == "accepted" and done.estimate_review["decided_by"] == "ada"
    )
    assert estimate_gap(done) is None
    with pytest.raises(CatalogError, match="no proposed estimates"):
        catalog.decide_estimate(entry.id, "reject", decided_by="ada")


def test_changing_an_estimate_proposes_it_again_and_other_edits_do_not() -> None:
    catalog = SqliteCatalog()
    entry = _register(catalog)
    catalog.decide_estimate(entry.id, "accept", decided_by="ada")
    kept = catalog.update(entry.id, {"notes": "blue board"})
    assert kept.estimate_review["status"] == "accepted"
    moved = catalog.update(entry.id, {"specs": {"width_mm": _estimate(18.1)}})
    assert (
        moved.estimate_review["status"] == "proposed" and "decided_by" not in moved.estimate_review
    )


def test_a_rejected_estimate_is_a_gap_that_says_so() -> None:
    catalog = SqliteCatalog()
    entry = _register(catalog)
    rejected = catalog.decide_estimate(entry.id, "reject", decided_by="ada")
    assert "were rejected" in (estimate_gap(rejected) or "")


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    async def decide_estimate(
        self, ref: str, decision: str, decided_by: str, reason: str
    ) -> dict[str, Any]:
        self.calls.append((ref, decision, decided_by, reason))
        return {"entry": {"name": ref}}


def _tool() -> tuple[Any, _Service]:
    tools: dict[str, Any] = {}
    service = _Service()

    def register(*, manifest: Any, handler: Any) -> None:
        tools[manifest.tool_id] = handler

    register_catalog_tools(register, service)  # type: ignore[arg-type]
    return tools["twin.confirm_estimate"], service


async def test_the_tool_takes_its_person_from_the_approval_only() -> None:
    assert requires_human_authority("twin.confirm_estimate")
    confirm, service = _tool()
    with pytest.raises(HumanAuthorityRequiredError):
        await confirm({"ref": "adafruit-4480", "decision": "accept"})
    with pytest.raises(ApproverArgumentRejectedError):
        await confirm({"ref": "adafruit-4480", "decision": "accept", "approved_by": "me"})
    out = await confirm({"ref": "adafruit-4480", "decision": "accept", APPROVED_BY_ARG: "ada"})
    assert service.calls[-1][:3] == ("adafruit-4480", "accept", "ada")
    assert out["recorded_by"] == "ada"
    await confirm(
        {
            "ref": "adafruit-4480",
            "decision": "reject",
            APPROVED_BY_ARG: "ada",
            DELEGATED_AGENT_ARG: "claude",
        }
    )
    assert "claude" in service.calls[-1][2] and "ada" in service.calls[-1][2]
