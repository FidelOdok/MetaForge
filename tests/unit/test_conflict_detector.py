"""Unit tests for RequirementConflictDetector (FORGE-257)."""

from uuid import uuid4

from api_gateway.requirement_intelligence.conflict_detector import RequirementConflictDetector


def _detect(*texts: str):
    ids = [uuid4() for _ in texts]
    findings = RequirementConflictDetector().detect(list(zip(ids, texts)))
    return ids, findings


class TestIncompatibleBounds:
    def test_upper_below_lower_same_unit_conflicts(self):
        ids, findings = _detect(
            "The enclosure shall weigh at most 2 kg.",
            "The enclosure shall weigh at least 3 kg.",
        )
        assert len(findings) == 1
        assert {findings[0].requirement_a_id, findings[0].requirement_b_id} == {
            str(ids[0]),
            str(ids[1]),
        }
        assert "2kg" in findings[0].detail.replace(" ", "")
        assert "3kg" in findings[0].detail.replace(" ", "")

    def test_order_independent(self):
        # Lower-bound requirement listed first this time.
        _, findings = _detect(
            "The enclosure shall weigh at least 3 kg.",
            "The enclosure shall weigh at most 2 kg.",
        )
        assert len(findings) == 1

    def test_compatible_bounds_do_not_conflict(self):
        _, findings = _detect(
            "The enclosure shall weigh at most 5 kg.",
            "The enclosure shall weigh at least 3 kg.",
        )
        assert findings == []

    def test_different_units_do_not_conflict(self):
        _, findings = _detect(
            "The battery shall last at least 3 hr.",
            "The enclosure shall weigh at most 2 kg.",
        )
        assert findings == []

    def test_written_out_unit_matches_abbreviation(self):
        _, findings = _detect(
            "The enclosure shall weigh at most 2 kilograms.",
            "The enclosure shall weigh at least 3 kg.",
        )
        assert len(findings) == 1

    def test_no_bound_phrasing_means_no_finding(self):
        _, findings = _detect(
            "The enclosure shall be quiet.",
            "The enclosure shall be lightweight.",
        )
        assert findings == []

    def test_three_way_set_flags_only_the_incompatible_pair(self):
        ids, findings = _detect(
            "The arm shall weigh at most 2 kg.",
            "The arm shall weigh at least 3 kg.",
            "The arm shall operate at no less than 10 v.",
        )
        assert len(findings) == 1
        assert str(ids[2]) not in (findings[0].requirement_a_id, findings[0].requirement_b_id)
