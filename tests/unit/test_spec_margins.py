"""A chosen part, held against the requirements already recorded (FORGE-346).

D3 is "component selection with requirement vs spec margins, live
distributor pricing, BOM commit". Selection and commit worked:
``component.search_*`` finds candidates, ``twin.record_component_selection``
persists the chosen one. Nothing compared the part's specs to the
project's requirements -- so a part that violates one could be committed,
and the violation surfaced later at a gate, with nothing linking it back
to the decision to buy that part.

It is only checkable at all because FORGE-259/344 made requirements typed.
"""

from __future__ import annotations

from typing import Any

import pytest

from twin_core.consistency.spec_margins import (
    INCOMPATIBLE_UNITS,
    NO_MATCHING_SPEC,
    REQUIREMENT_NOT_BOUND,
    SPEC_NOT_NUMERIC,
    compare_specs_to_requirements,
)


class _Req:
    """Constraint-shaped, which is all the comparison needs."""

    def __init__(
        self,
        name: str,
        metric: str = "",
        operator: str = "<=",
        limit: float | None = None,
        unit: str = "",
    ) -> None:
        self.name = name
        self.metric = metric
        self.operator = operator
        self.limit = limit
        self.unit = unit


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def test_a_satisfied_requirement_reports_its_headroom() -> None:
    margins, unchecked = compare_specs_to_requirements(
        {"quiescent_current": 40.0},
        [_Req("iq_budget", metric="quiescent_current", operator="<=", limit=50.0, unit="uA")],
    )
    assert unchecked == []
    assert margins[0].satisfied is True
    assert margins[0].margin == 10.0
    assert margins[0].margin_pct == pytest.approx(20.0)


def test_a_violated_requirement_reports_negative_headroom() -> None:
    """Signed, not absolute: the sign is how a reader tells slack from
    overshoot at a glance."""
    [margin], _ = compare_specs_to_requirements(
        {"quiescent_current": 75.0},
        [_Req("iq_budget", metric="quiescent_current", operator="<=", limit=50.0, unit="uA")],
    )
    assert margin.satisfied is False
    assert margin.margin == -25.0


def test_a_lower_bound_counts_headroom_the_other_way() -> None:
    [margin], _ = compare_specs_to_requirements(
        {"input_voltage_max": 36.0},
        [_Req("vin", metric="input_voltage_max", operator=">=", limit=24.0, unit="V")],
    )
    assert margin.satisfied is True
    assert margin.margin == 12.0


def test_an_equality_reports_no_margin() -> None:
    """ "How far over" is not meaningful for ==, and inventing a number
    invites a comparison between parts that does not mean anything."""
    [margin], _ = compare_specs_to_requirements(
        {"pin_count": 48},
        [_Req("pins", metric="pin_count", operator="==", limit=48.0, unit="")],
    )
    assert margin.satisfied is True
    assert margin.margin is None
    assert margin.margin_pct is None


def test_a_zero_limit_does_not_divide() -> None:
    [margin], _ = compare_specs_to_requirements(
        {"leakage": 0.0},
        [_Req("no_leak", metric="leakage", operator="<=", limit=0.0, unit="A")],
    )
    assert margin.margin == 0.0
    assert margin.margin_pct is None


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_a_spec_in_another_unit_is_converted() -> None:
    [margin], _ = compare_specs_to_requirements(
        {"vout": "3300 mV"},
        [_Req("rail", metric="vout", operator="<=", limit=5.0, unit="V")],
    )
    assert margin.spec_value == pytest.approx(3.3)
    assert margin.satisfied is True
    assert margin.unit_assumed is False


def test_a_bare_number_is_compared_but_flagged() -> None:
    """Refusing would leave most catalog rows unchecked. Not flagging
    would let a 3300 that is microfarads read as farads satisfy almost
    anything."""
    [margin], _ = compare_specs_to_requirements(
        {"vout": 3.3},
        [_Req("rail", metric="vout", operator="<=", limit=5.0, unit="V")],
    )
    assert margin.unit_assumed is True


def test_an_unconvertible_unit_is_not_guessed() -> None:
    margins, unchecked = compare_specs_to_requirements(
        {"vout": "3.3 kg"},
        [_Req("rail", metric="vout", operator="<=", limit=5.0, unit="V")],
    )
    assert margins == []
    assert unchecked[0].reason == INCOMPATIBLE_UNITS


# ---------------------------------------------------------------------------
# What could not be checked, and why
# ---------------------------------------------------------------------------


def test_a_requirement_with_no_matching_spec_is_reported() -> None:
    """Not silently skipped: "no margin shown" and "no requirement" look
    identical otherwise."""
    margins, unchecked = compare_specs_to_requirements(
        {"vout": 3.3},
        [_Req("thermal", metric="theta_ja", operator="<=", limit=40.0, unit="K/W")],
    )
    assert margins == []
    assert unchecked[0].reason == NO_MATCHING_SPEC


def test_an_untyped_requirement_is_reported_as_unbound() -> None:
    margins, unchecked = compare_specs_to_requirements({"vout": 3.3}, [_Req("be_efficient")])
    assert unchecked[0].reason == REQUIREMENT_NOT_BOUND


@pytest.mark.parametrize("value", ["see datasheet", None, {"nope": 1}, True])
def test_a_non_numeric_spec_is_reported(value: Any) -> None:
    """`True` included deliberately: bool is an int in Python, and letting
    it through would compare True as 1."""
    margins, unchecked = compare_specs_to_requirements(
        {"vout": value},
        [_Req("rail", metric="vout", operator="<=", limit=5.0, unit="V")],
    )
    assert margins == []
    assert unchecked[0].reason == SPEC_NOT_NUMERIC


def test_no_requirements_gives_neither_margins_nor_complaints() -> None:
    """Distinct from a part that passes everything."""
    assert compare_specs_to_requirements({"vout": 3.3}, []) == ([], [])


def test_a_dict_valued_spec_is_read() -> None:
    [margin], _ = compare_specs_to_requirements(
        {"vout": {"value": 3300, "unit": "mV"}},
        [_Req("rail", metric="vout", operator="<=", limit=5.0, unit="V")],
    )
    assert margin.spec_value == pytest.approx(3.3)


# ---------------------------------------------------------------------------
# It reaches the caller at commit time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_recorder_reports_a_violation_without_refusing_the_commit() -> None:
    """The part is still recorded. Refusing would block the normal case of
    choosing the best available part and then revising the requirement --
    but a silent commit means the violation first surfaces at a gate, with
    nothing linking it back to the decision to buy this part."""
    from api_gateway.twin.component_recorder import _requirement_margins

    class _Twin:
        async def list_constraints(self, project_id: Any = None) -> list[Any]:
            return [_Req("iq", metric="quiescent_current", operator="<=", limit=50.0, unit="uA")]

    margins, unchecked = await _requirement_margins(
        _Twin(), "11111111-1111-1111-1111-111111111111", {"quiescent_current": 75.0}
    )
    assert margins[0].satisfied is False
    assert unchecked == []


@pytest.mark.asyncio
async def test_a_failing_margin_report_never_fails_the_commit() -> None:
    """The commit is what the caller asked for. A report that can break it
    is worse than no report -- the same contract capture and telemetry
    already keep."""
    from api_gateway.twin.component_recorder import _requirement_margins

    class _Broken:
        async def list_constraints(self, project_id: Any = None) -> list[Any]:
            raise RuntimeError("twin unreachable")

    assert await _requirement_margins(
        _Broken(), "11111111-1111-1111-1111-111111111111", {"a": 1}
    ) == (
        [],
        [],
    )


@pytest.mark.asyncio
async def test_an_unscoped_selection_is_not_compared() -> None:
    """With no project there are no requirements to hold it against, and
    guessing which project's apply would be worse than saying nothing."""
    from api_gateway.twin.component_recorder import _requirement_margins

    class _Twin:
        async def list_constraints(self, project_id: Any = None) -> list[Any]:  # pragma: no cover
            raise AssertionError("should not be consulted without a project")

    assert await _requirement_margins(_Twin(), None, {"a": 1}) == ([], [])


@pytest.mark.asyncio
async def test_record_puts_the_margins_in_its_own_result() -> None:
    """Drives the real ``record()``.

    The three tests above call ``_requirement_margins`` directly, and all
    three still passed with the call site deleted from ``record()`` -- so
    they pin the comparison, not the wiring. This one pins the wiring.
    """
    import uuid as _uuid

    from api_gateway.twin.component_recorder import make_component_recorder

    project = str(_uuid.uuid4())

    class _Created:
        def __init__(self) -> None:
            self.id = _uuid.uuid4()

    class _Twin:
        async def list_work_products(self, *a: Any, **k: Any) -> list[Any]:
            return []

        async def create_work_product(self, wp: Any) -> Any:
            return _Created()

        async def add_bom_item(self, item: Any) -> Any:
            return _Created()

        async def add_edge(self, *a: Any, **k: Any) -> None:
            return None

        async def list_constraints(self, project_id: Any = None) -> list[Any]:
            return [
                _Req("iq", metric="quiescent_current", operator="<=", limit=50.0, unit="uA"),
                _Req("vin", metric="input_voltage_max", operator=">=", limit=24.0, unit="V"),
            ]

    record = make_component_recorder(_Twin())
    out = await record(
        mpn="TPS62840",
        manufacturer="TI",
        category="buck_converter",
        purchase_unit="discrete_part",
        project_id=project,
        specs={"quiescent_current": 75.0, "input_voltage_max": 36.0},
    )

    assert out["violates"] == ["iq"]
    by_name = {m["requirement"]: m for m in out["requirement_margins"]}
    assert by_name["iq"]["satisfied"] is False
    assert by_name["vin"]["margin"] == 12.0


@pytest.mark.asyncio
async def test_record_omits_the_keys_when_there_is_nothing_to_compare() -> None:
    import uuid as _uuid

    from api_gateway.twin.component_recorder import make_component_recorder

    class _Created:
        def __init__(self) -> None:
            self.id = _uuid.uuid4()

    class _Twin:
        async def list_work_products(self, *a: Any, **k: Any) -> list[Any]:
            return []

        async def create_work_product(self, wp: Any) -> Any:
            return _Created()

        async def add_bom_item(self, item: Any) -> Any:
            return _Created()

        async def add_edge(self, *a: Any, **k: Any) -> None:
            return None

        async def list_constraints(self, project_id: Any = None) -> list[Any]:
            return []

    record = make_component_recorder(_Twin())
    out = await record(
        mpn="X",
        manufacturer="Y",
        category="z",
        purchase_unit="discrete_part",
        project_id=str(_uuid.uuid4()),
        specs={"a": 1},
    )
    assert "requirement_margins" not in out
    assert "violates" not in out
