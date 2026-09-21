"""Unit tests for RequirementLinter and build_quality_record (FORGE-55)."""

from api_gateway.requirement_intelligence.linter import LintCategory, RequirementLinter
from api_gateway.requirement_intelligence.quality import build_quality_record


class TestDocWorkedExample:
    """The exact example from the spec/ticket -- section 31."""

    def test_reproduces_every_category_in_the_docs_example(self):
        text = "The robot should preferably operate very quietly for a long time."
        findings = RequirementLinter().lint(text)
        categories = {f.category for f in findings}
        assert categories == {
            LintCategory.WEAK_MODAL,
            LintCategory.WEAK_WORD,
            LintCategory.AMBIGUOUS,
            LintCategory.COMPOUND,
            LintCategory.MISSING_THRESHOLD,
        }

    def test_ambiguous_catches_both_quietly_and_long_time(self):
        text = "The robot should preferably operate very quietly for a long time."
        findings = RequirementLinter().lint(text)
        ambiguous_details = {f.detail for f in findings if f.category == LintCategory.AMBIGUOUS}
        assert ambiguous_details == {"quietly", "long time"}

    def test_compound_names_the_two_domains(self):
        text = "The robot should preferably operate very quietly for a long time."
        findings = RequirementLinter().lint(text)
        compound = [f for f in findings if f.category == LintCategory.COMPOUND]
        assert len(compound) == 1
        assert compound[0].detail == "acoustics + endurance"


class TestWellFormedRequirement:
    def test_clean_requirement_has_no_findings(self):
        text = "The system shall operate at a noise level below 45 dB when idle."
        findings = RequirementLinter().lint(text)
        assert findings == []


class TestWeakModalAndWord:
    def test_shall_is_not_flagged(self):
        findings = RequirementLinter().lint("The system shall weigh less than 3 kg.")
        assert not any(f.category == LintCategory.WEAK_MODAL for f in findings)

    def test_should_is_flagged(self):
        findings = RequirementLinter().lint("The system should weigh less than 3 kg.")
        assert any(f.category == LintCategory.WEAK_MODAL and f.detail == "should" for f in findings)


class TestMissingThresholdAndUnit:
    def test_no_number_at_all_is_missing_threshold(self):
        findings = RequirementLinter().lint("The system shall be lightweight.")
        assert any(f.category == LintCategory.MISSING_THRESHOLD for f in findings)

    def test_number_without_unit_is_missing_unit(self):
        findings = RequirementLinter().lint("The system shall weigh less than 3.")
        categories = {f.category for f in findings}
        assert LintCategory.MISSING_UNIT in categories
        assert LintCategory.MISSING_THRESHOLD not in categories

    def test_number_with_unit_triggers_neither(self):
        findings = RequirementLinter().lint("The system shall weigh less than 3 kg.")
        categories = {f.category for f in findings}
        assert LintCategory.MISSING_UNIT not in categories
        assert LintCategory.MISSING_THRESHOLD not in categories


class TestUndefinedPronoun:
    def test_bare_it_is_flagged(self):
        findings = RequirementLinter().lint("It shall not exceed 3 kg.")
        assert any(f.category == LintCategory.UNDEFINED_PRONOUN for f in findings)

    def test_this_system_is_not_flagged(self):
        """ "this system" -- a demonstrative adjective with a noun, not a bare pronoun."""
        findings = RequirementLinter().lint("This system shall not exceed 3 kg.")
        assert not any(f.category == LintCategory.UNDEFINED_PRONOUN for f in findings)


class TestMissingCondition:
    def test_off_by_default(self):
        findings = RequirementLinter().lint("The system shall weigh less than 3 kg.")
        assert not any(f.category == LintCategory.MISSING_CONDITION for f in findings)

    def test_flags_when_opted_in_and_absent(self):
        findings = RequirementLinter().lint(
            "The alarm shall sound within 2 seconds.", expects_condition=True
        )
        assert any(f.category == LintCategory.MISSING_CONDITION for f in findings)

    def test_does_not_flag_when_opted_in_and_present(self):
        findings = RequirementLinter().lint(
            "When the battery drops below 10%, the alarm shall sound within 2 seconds.",
            expects_condition=True,
        )
        assert not any(f.category == LintCategory.MISSING_CONDITION for f in findings)


class TestUnverifiableLanguage:
    def test_flags_subjective_words(self):
        findings = RequirementLinter().lint("The interface shall feel intuitive.")
        categories = {f.category for f in findings}
        assert LintCategory.UNVERIFIABLE in categories


class TestImplementationSpecific:
    def test_flags_named_technology(self):
        findings = RequirementLinter().lint(
            "The device shall pair using Bluetooth within 5 seconds."
        )
        assert any(f.category == LintCategory.IMPLEMENTATION_SPECIFIC for f in findings)

    def test_clean_requirement_does_not_flag(self):
        findings = RequirementLinter().lint("The device shall pair within 5 seconds.")
        assert not any(f.category == LintCategory.IMPLEMENTATION_SPECIFIC for f in findings)


class TestFindDuplicates:
    def test_exact_duplicate_is_flagged(self):
        text = "The system shall weigh less than 3 kg."
        findings = RequirementLinter().find_duplicates(text, [text])
        assert len(findings) == 1
        assert findings[0].category == LintCategory.DUPLICATE

    def test_near_duplicate_is_flagged(self):
        text = "The system shall weigh less than 3 kg."
        near = "The system shall weigh less than 3kg."
        findings = RequirementLinter().find_duplicates(text, [near])
        assert len(findings) == 1

    def test_distinct_requirement_is_not_flagged(self):
        text = "The system shall weigh less than 3 kg."
        other = "The system shall pair with a phone within 5 seconds."
        findings = RequirementLinter().find_duplicates(text, [other])
        assert findings == []

    def test_empty_corpus_entries_are_ignored(self):
        text = "The system shall weigh less than 3 kg."
        findings = RequirementLinter().find_duplicates(text, ["", "   "])
        assert findings == []


class TestBuildQualityRecord:
    def test_clean_requirement_passes_everything(self):
        findings = RequirementLinter().lint("The system shall weigh less than 3 kg.")
        quality = build_quality_record(findings)
        assert quality.clarity == "pass"
        assert quality.atomicity == "pass"
        assert quality.quantified == "pass"
        assert quality.verification_ready == "pass"
        assert quality.traceability is None

    def test_ambiguous_requirement_fails_clarity_and_verification_ready(self):
        findings = RequirementLinter().lint("The system shall be lightweight and quiet.")
        quality = build_quality_record(findings)
        assert quality.clarity == "fail"
        assert quality.verification_ready == "fail"

    def test_missing_threshold_fails_quantified(self):
        findings = RequirementLinter().lint("The system shall be robust.")
        quality = build_quality_record(findings)
        assert quality.quantified == "fail"

    def test_has_parent_sets_traceability(self):
        findings = RequirementLinter().lint("The system shall weigh less than 3 kg.")
        quality_with = build_quality_record(findings, has_parent=True)
        quality_without = build_quality_record(findings, has_parent=False)
        assert quality_with.traceability == "pass"
        assert quality_without.traceability == "fail"

    def test_no_single_score_field_exists(self):
        """Spec section 32: diagnostics, never a single score."""
        findings = RequirementLinter().lint("The system shall be robust and reliable.")
        quality = build_quality_record(findings)
        assert not hasattr(quality, "score")
