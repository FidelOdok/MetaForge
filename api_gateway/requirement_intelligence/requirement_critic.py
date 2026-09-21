"""RequirementCriticAgent (FORGE-55, spec section 26.5 Requirement Critic
Agent).

Every one of the Critic's named responsibilities -- "ambiguity detection;
non-atomic requirement detection; unverifiable wording; missing condition;
missing threshold; mixed preference/requirement; compound requirements" --
is a RequirementLinter category (mixed preference/requirement is
WEAK_MODAL: a "should"/"may" inside what's meant to be a hard requirement).
So this agent makes no LLM call at all -- it runs the deterministic linter
and reports the result in the standard AgentResult envelope, same as every
other engineering agent. Suggested fixes are proposals only, never applied:
this agent's ``proposed_patch`` is always ``None`` -- it diagnoses, it
doesn't author a rewrite (that's the Requirement Author agent's job).
"""

from __future__ import annotations

from api_gateway.requirement_intelligence.linter import RequirementLinter
from api_gateway.requirement_intelligence.models import AgentResult
from api_gateway.requirement_intelligence.quality import build_quality_record


class RequirementCriticAgent:
    """Diagnoses one requirement's text via RequirementLinter."""

    def __init__(self, linter: RequirementLinter | None = None) -> None:
        self._linter = linter or RequirementLinter()

    def critique(
        self,
        text: str,
        *,
        expects_condition: bool = False,
        has_parent: bool | None = None,
        corpus: list[str] | None = None,
    ) -> AgentResult:
        findings = self._linter.lint(text, expects_condition=expects_condition)
        if corpus:
            findings = findings + self._linter.find_duplicates(text, corpus)

        quality = build_quality_record(findings, has_parent=has_parent)

        conclusions = [f"{f.category.value}: {f.detail}" for f in findings]
        if not findings:
            conclusions.append("no issues found")

        # A finding is "unresolved" work for a human/agent to act on --
        # confidence in the DIAGNOSIS itself is always high (the linter is
        # deterministic), never a comment on whether the requirement is good.
        return AgentResult(
            conclusions=conclusions,
            assumptions=[],
            evidence=[f"requirement_quality_record={quality.model_dump()}"],
            proposed_patch=None,
            unresolved=[],
            confidence=1.0,
        )
