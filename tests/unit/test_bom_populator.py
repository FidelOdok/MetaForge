"""Unit tests for the Auto-BOM populator (MET-473)."""

from __future__ import annotations

from typing import Any

import pytest

from digital_twin.knowledge.bom_populator import (
    BomConstraint,
    ConstraintResult,
    _coerce_float,
    evaluate_constraint,
    parse_constraints,
    populate_bom,
    score_candidate,
    to_dict,
)
from digital_twin.knowledge.property_extractor import ExtractedProperty, ExtractionMethod
from digital_twin.knowledge.service import ExtractedProperties

# ---------- _coerce_float ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (3.3, 3.3),
        (3, 3.0),
        ("3.3", 3.3),
        ("3.3 V", 3.3),
        ("-40", -40.0),
        ("+5.0", 5.0),
        ("-40 to +85", -40.0),
        ("3.3-3.6V", 3.3),
        ("", None),
        ("V only", None),
        (None, None),
        (True, None),
        ([1, 2, 3], None),
        (".", None),
    ],
)
def test_coerce_float_handles_typical_datasheet_strings(raw: Any, expected: float | None) -> None:
    assert _coerce_float(raw) == expected


# ---------- evaluate_constraint: numeric ----------


def _result_for(
    op: str,
    required: Any,
    extracted: Any,
    method: str = "verbatim",
    confidence: float = 1.0,
) -> ConstraintResult:
    return evaluate_constraint(
        BomConstraint(property="p", op=op, value=required),  # type: ignore[arg-type]
        extracted_value=extracted,
        extraction_method=method,
        extraction_confidence=confidence,
    )


def test_ge_passes_with_positive_margin():
    r = _result_for(">=", 3.3, 3.6)
    assert r.passed is True
    assert r.margin == pytest.approx((3.6 - 3.3) / 3.3)


def test_ge_fails_below_threshold():
    r = _result_for(">=", 3.3, 3.0)
    assert r.passed is False
    assert r.margin == pytest.approx((3.0 - 3.3) / 3.3)


def test_le_passes_with_headroom():
    r = _result_for("<=", 100.0, 80.0)
    assert r.passed is True
    assert r.margin == pytest.approx(0.2)


def test_le_fails_when_extracted_exceeds_limit():
    r = _result_for("<=", 100.0, 150.0)
    assert r.passed is False
    assert r.margin == pytest.approx(-0.5)


def test_numeric_handles_stringy_extracted():
    r = _result_for(">=", 3.0, "3.3 V")
    assert r.passed is True
    assert r.margin == pytest.approx(0.1)


def test_numeric_with_zero_required_uses_unit_denominator():
    r = _result_for(">=", 0.0, 0.5)
    assert r.passed is True
    assert r.margin == pytest.approx(0.5)


def test_numeric_unparseable_extracted_fails_with_none_margin():
    r = _result_for(">=", 3.3, "abc")
    assert r.passed is False
    assert r.margin is None


# ---------- evaluate_constraint: equality / membership ----------


def test_equality_passes_on_exact_match():
    r = _result_for("==", "QFN-32", "QFN-32")
    assert r.passed is True
    assert r.margin == 1.0


def test_equality_fails_on_mismatch():
    r = _result_for("==", "QFN-32", "LQFP-48")
    assert r.passed is False
    assert r.margin == 0.0


def test_in_passes_when_value_is_in_allowed_set():
    r = _result_for("in", ["QFN-32", "QFN-48"], "QFN-48")
    assert r.passed is True
    assert r.margin == 1.0


def test_in_fails_when_value_outside_set():
    r = _result_for("in", ["QFN-32", "QFN-48"], "LQFP-48")
    assert r.passed is False


def test_in_with_non_list_value_raises():
    with pytest.raises(ValueError):
        _result_for("in", "QFN-32", "QFN-32")


# ---------- evaluate_constraint: not-found ----------


def test_not_found_property_always_fails():
    r = evaluate_constraint(
        BomConstraint(property="p", op=">=", value=3.3),
        extracted_value=None,
        extraction_method="not_found",
        extraction_confidence=0.0,
    )
    assert r.passed is False
    assert r.margin is None


# ---------- score_candidate ----------


def _passing_result(margin: float, confidence: float = 1.0) -> ConstraintResult:
    return ConstraintResult(
        property="p",
        op=">=",
        required_value=1.0,
        extracted_value=1.0 + margin,
        extraction_method="verbatim",
        extraction_confidence=confidence,
        passed=True,
        margin=margin,
    )


def _failing_result() -> ConstraintResult:
    return ConstraintResult(
        property="p",
        op=">=",
        required_value=10.0,
        extracted_value=1.0,
        extraction_method="verbatim",
        extraction_confidence=1.0,
        passed=False,
        margin=-0.9,
    )


def test_score_zero_when_any_constraint_fails():
    assert score_candidate([_passing_result(0.5), _failing_result()]) == 0.0


def test_score_is_weighted_mean_of_margins():
    # Two constraints both passing with margin 0.2 and 0.4, equal confidence
    result = score_candidate([_passing_result(0.2), _passing_result(0.4)])
    assert result == pytest.approx(0.3)


def test_score_saturates_at_one():
    # Massive headroom shouldn't drown out tighter constraints
    result = score_candidate([_passing_result(5.0), _passing_result(0.1)])
    assert result == pytest.approx((1.0 + 0.1) / 2)


def test_score_respects_confidence_weights():
    # Low-confidence high-margin shouldn't beat high-confidence tight-fit
    r_low = _passing_result(margin=0.9, confidence=0.1)
    r_high = _passing_result(margin=0.1, confidence=1.0)
    weighted = (0.9 * 0.1 + 0.1 * 1.0) / (0.1 + 1.0)
    assert score_candidate([r_low, r_high]) == pytest.approx(weighted)


def test_score_empty_returns_zero():
    assert score_candidate([]) == 0.0


# ---------- parse_constraints ----------


def test_parse_constraints_returns_typed_list():
    out = parse_constraints(
        [
            {"property": "supply_voltage", "op": ">=", "value": 3.3},
            {"property": "max_current_ma", "op": "<=", "value": 250, "weight": 2.0},
        ]
    )
    assert len(out) == 2
    assert out[0].property == "supply_voltage"
    assert out[1].weight == 2.0


def test_parse_constraints_rejects_empty():
    with pytest.raises(ValueError, match="at least one"):
        parse_constraints([])


def test_parse_constraints_rejects_bad_op():
    with pytest.raises(ValueError, match="op"):
        parse_constraints([{"property": "p", "op": "!=", "value": 3}])


def test_parse_constraints_requires_property_name():
    with pytest.raises(ValueError, match="property"):
        parse_constraints([{"property": "", "op": ">=", "value": 3}])


def test_parse_constraints_rejects_missing_value_key():
    with pytest.raises(ValueError, match="value"):
        parse_constraints([{"property": "p", "op": ">="}])


def test_parse_constraints_rejects_negative_weight():
    with pytest.raises(ValueError, match="weight"):
        parse_constraints([{"property": "p", "op": ">=", "value": 3, "weight": -1}])


# ---------- populate_bom (in-memory KnowledgeService stub) ----------


class _Hit:
    """Tiny stand-in for a SearchHit — mirrors the duck-typed fields."""

    def __init__(self, mpn: str, source_path: str, chunk_index: int):
        self.metadata = {"mpn": mpn}
        self.source_path = source_path
        self.heading = "Electrical Characteristics"
        self.chunk_index = chunk_index
        self.total_chunks = 10
        self.content = ""
        self.knowledge_type = None
        self.source_work_product_id = None


class _StubKnowledgeService:
    """Search + extract_properties stub for end-to-end populate_bom tests."""

    def __init__(self) -> None:
        self._hits_by_query: dict[str, list[_Hit]] = {}
        self._props: dict[str, dict[str, dict[str, Any]]] = {}

    def stub_search(self, query: str, hits: list[_Hit]) -> None:
        self._hits_by_query[query] = hits

    def stub_extract(self, mpn: str, props: dict[str, dict[str, Any]]) -> None:
        self._props[mpn] = props

    async def search(
        self,
        query: str,
        top_k: int,
        knowledge_type: Any = None,
    ) -> list[_Hit]:
        return list(self._hits_by_query.get(query, []))[:top_k]

    async def extract_properties(
        self,
        mpn: str,
        properties: list[str],
        *,
        aliases: dict[str, list[str]] | None = None,
    ) -> ExtractedProperties:
        canned = self._props.get(mpn, {})
        items: list[ExtractedProperty] = []
        for name in properties:
            spec = canned.get(name)
            if spec is None:
                items.append(
                    ExtractedProperty(
                        property_name=name,
                        value=None,
                        unit=None,
                        confidence=0.0,
                        extraction_method=ExtractionMethod.NOT_FOUND,
                        conditions={},
                    )
                )
            else:
                items.append(
                    ExtractedProperty(
                        property_name=name,
                        value=str(spec.get("value")),
                        unit=spec.get("unit"),
                        confidence=float(spec.get("confidence", 1.0)),
                        extraction_method=ExtractionMethod(spec.get("method", "verbatim")),
                        conditions=spec.get("conditions", {}),
                    )
                )
        return ExtractedProperties(
            mpn=mpn,
            mpn_found=mpn in self._props,
            datasheet_revision=None,
            datasheet_published_at=None,
            datasheet_source_path=f"datasheet://{mpn}",
            items=items,
        )


@pytest.mark.asyncio
async def test_populate_bom_ranks_passing_above_failing():
    svc = _StubKnowledgeService()
    svc.stub_search(
        "ble microcontroller",
        [
            _Hit(mpn="nRF52840", source_path="datasheet://nRF52840", chunk_index=12),
            _Hit(mpn="ESP32-WROOM-32", source_path="datasheet://ESP32", chunk_index=3),
        ],
    )
    # nRF52840 passes both constraints with comfortable headroom
    svc.stub_extract(
        "nRF52840",
        {
            "supply_voltage": {"value": 3.3, "unit": "V", "confidence": 1.0},
            "max_current_ma": {"value": 5.5, "confidence": 0.9},
        },
    )
    # ESP32 fails the current constraint
    svc.stub_extract(
        "ESP32-WROOM-32",
        {
            "supply_voltage": {"value": 3.3, "unit": "V", "confidence": 1.0},
            "max_current_ma": {"value": 240, "confidence": 0.8},
        },
    )

    result = await populate_bom(
        svc,  # type: ignore[arg-type]
        search_query="ble microcontroller",
        constraints=[
            BomConstraint(property="supply_voltage", op=">=", value=3.0),
            BomConstraint(property="max_current_ma", op="<=", value=10.0),
        ],
        top_k=5,
    )
    assert result.candidates_evaluated == 2
    assert len(result.suggestions) == 2
    top = result.suggestions[0]
    assert top.mpn == "nRF52840"
    assert top.all_constraints_passed is True
    assert top.score > 0.0
    runner_up = result.suggestions[1]
    assert runner_up.mpn == "ESP32-WROOM-32"
    assert runner_up.all_constraints_passed is False
    assert runner_up.score == 0.0


@pytest.mark.asyncio
async def test_populate_bom_dedupes_by_mpn():
    svc = _StubKnowledgeService()
    # Same MPN surfaced in 3 chunks — should be extracted once and
    # returned once in suggestions.
    svc.stub_search(
        "wifi",
        [
            _Hit(mpn="ESP32-WROOM-32", source_path="datasheet://ESP32", chunk_index=1),
            _Hit(mpn="ESP32-WROOM-32", source_path="datasheet://ESP32", chunk_index=2),
            _Hit(mpn="ESP32-WROOM-32", source_path="datasheet://ESP32", chunk_index=3),
        ],
    )
    svc.stub_extract(
        "ESP32-WROOM-32",
        {"supply_voltage": {"value": 3.3, "confidence": 1.0}},
    )
    result = await populate_bom(
        svc,  # type: ignore[arg-type]
        search_query="wifi",
        constraints=[BomConstraint(property="supply_voltage", op=">=", value=3.0)],
    )
    assert len(result.suggestions) == 1
    assert result.candidates_evaluated == 1


@pytest.mark.asyncio
async def test_populate_bom_skips_hits_without_mpn_metadata():
    svc = _StubKnowledgeService()
    hit_no_mpn = _Hit(mpn="placeholder", source_path="datasheet://x", chunk_index=0)
    hit_no_mpn.metadata = {}  # strip the mpn key
    svc.stub_search("foo", [hit_no_mpn])
    result = await populate_bom(
        svc,  # type: ignore[arg-type]
        search_query="foo",
        constraints=[BomConstraint(property="supply_voltage", op=">=", value=3.0)],
    )
    assert result.candidates_evaluated == 0
    assert result.suggestions == ()
    assert result.total_search_hits == 1


@pytest.mark.asyncio
async def test_populate_bom_top_k_truncates():
    svc = _StubKnowledgeService()
    svc.stub_search(
        "x",
        [_Hit(mpn=f"MPN-{i}", source_path="ds", chunk_index=i) for i in range(10)],
    )
    for i in range(10):
        # Higher i = more headroom = higher score
        svc.stub_extract(
            f"MPN-{i}",
            {"supply_voltage": {"value": 3.0 + 0.1 * i, "confidence": 1.0}},
        )
    result = await populate_bom(
        svc,  # type: ignore[arg-type]
        search_query="x",
        constraints=[BomConstraint(property="supply_voltage", op=">=", value=3.0)],
        top_k=3,
    )
    assert len(result.suggestions) == 3
    # The highest-margin candidates come first
    assert [s.mpn for s in result.suggestions] == ["MPN-9", "MPN-8", "MPN-7"]


@pytest.mark.asyncio
async def test_to_dict_renders_wire_safe_shape():
    svc = _StubKnowledgeService()
    svc.stub_search("x", [_Hit(mpn="A", source_path="ds", chunk_index=0)])
    svc.stub_extract("A", {"v": {"value": 3.6, "confidence": 1.0}})
    result = await populate_bom(
        svc,  # type: ignore[arg-type]
        search_query="x",
        constraints=[BomConstraint(property="v", op=">=", value=3.0)],
    )
    rendered = to_dict(result)
    assert set(rendered) >= {
        "suggestions",
        "candidates_evaluated",
        "total_search_hits",
        "query_time_ms",
    }
    sugg = rendered["suggestions"][0]
    assert sugg["mpn"] == "A"
    assert sugg["all_constraints_passed"] is True
    assert sugg["score"] > 0.0
    cr = sugg["constraint_results"][0]
    assert cr["property"] == "v"
    assert cr["passed"] is True
    assert cr["margin"] == pytest.approx(0.2)
