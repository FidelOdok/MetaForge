"""Unit tests for RequirementCriticAgent (FORGE-55)."""

from api_gateway.requirement_intelligence import AgentResult, RequirementCriticAgent


class TestRequirementCriticAgent:
    def test_clean_requirement_reports_no_issues(self):
        agent = RequirementCriticAgent()
        result = agent.critique("The system shall weigh less than 3 kg.")
        assert isinstance(result, AgentResult)
        assert result.proposed_patch is None
        assert result.conclusions == ["no issues found"]
        assert result.confidence == 1.0

    def test_flags_each_finding_as_a_conclusion(self):
        agent = RequirementCriticAgent()
        result = agent.critique("The system should preferably be quiet.")
        joined = " ".join(result.conclusions)
        assert "weak_modal: should" in joined
        assert "weak_word: preferably" in joined
        assert "ambiguous: quiet" in joined

    def test_never_proposes_a_patch(self):
        """Diagnoses only -- the Author agent authors, the Critic critiques."""
        agent = RequirementCriticAgent()
        result = agent.critique("It should be robust for a long time.")
        assert result.proposed_patch is None

    def test_evidence_carries_the_quality_record(self):
        agent = RequirementCriticAgent()
        result = agent.critique("The system shall weigh less than 3 kg.", has_parent=True)
        assert any("requirement_quality_record" in e for e in result.evidence)
        assert any("'traceability': 'pass'" in e for e in result.evidence)

    def test_duplicate_check_uses_supplied_corpus(self):
        agent = RequirementCriticAgent()
        text = "The system shall weigh less than 3 kg."
        result = agent.critique(text, corpus=[text])
        assert any("duplicate" in c for c in result.conclusions)

    def test_expects_condition_is_forwarded(self):
        agent = RequirementCriticAgent()
        result = agent.critique("The alarm shall sound within 2 seconds.", expects_condition=True)
        assert any("missing_condition" in c for c in result.conclusions)
