"""Unit tests for the per-product-type completeness checklist (FORGE-257)."""

from api_gateway.requirement_intelligence.completeness import check_completeness, load_checklist


class TestLoadChecklist:
    def test_loads_generic_checklist(self):
        checklist = load_checklist("generic")
        assert checklist.product_type == "generic"
        ids = {c.id for c in checklist.categories}
        assert {"safety", "mechanical", "power", "environmental", "verification"} <= ids

    def test_loads_robotic_arm_checklist(self):
        checklist = load_checklist("robotic_arm")
        assert checklist.product_type == "robotic_arm"
        ids = {c.id for c in checklist.categories}
        assert "payload_and_reach" in ids

    def test_unknown_product_type_falls_back_to_generic(self):
        checklist = load_checklist("underwater_drone_that_does_not_have_a_file")
        assert checklist.product_type == "generic"

    def test_case_and_space_insensitive_lookup(self):
        assert load_checklist("Robotic Arm").product_type == "robotic_arm"

    def test_empty_or_default_falls_back_to_generic(self):
        assert load_checklist("").product_type == "generic"


class TestCheckCompleteness:
    def test_all_categories_covered(self):
        checklist = load_checklist("generic")
        texts = [
            "The system shall include an emergency stop for operator safety.",
            "The frame shall have a mass of at most 5 kg.",
            "The system shall draw at most 2 amps at a supply voltage of 12 V.",
            "The system shall operate at a temperature from -10 C to 40 C.",
            "The requirement shall be verified by inspection.",
        ]
        result = check_completeness(texts, checklist)
        assert set(result.missing) == set()
        assert set(result.covered) == {
            "safety",
            "mechanical",
            "power",
            "environmental",
            "verification",
        }

    def test_missing_categories_are_flagged(self):
        checklist = load_checklist("generic")
        texts = ["The frame shall have a mass of at most 5 kg."]
        result = check_completeness(texts, checklist)
        assert "mechanical" in result.covered
        assert "safety" in result.missing
        assert "power" in result.missing
        assert "environmental" in result.missing
        assert "verification" in result.missing

    def test_empty_requirement_set_flags_everything_missing(self):
        checklist = load_checklist("generic")
        result = check_completeness([], checklist)
        assert result.covered == []
        assert set(result.missing) == {c.id for c in checklist.categories}

    def test_result_carries_the_checklists_product_type(self):
        checklist = load_checklist("robotic_arm")
        result = check_completeness([], checklist)
        assert result.product_type == "robotic_arm"
