"""Consolidation themes — the buckets the grouper sorts experiences into.

Themes are the semantic axis the synthesizer reasons over: every
experience that lands in a theme bucket gets handed to Claude as one
group, so the synthesized insight stays focused (don't ask Claude to
generalize across mechanical stress and PCB DRC in one call).

The mapping is rule-based for the deterministic Phase-2 cut. A future
iteration can swap in an LLM-based classifier if the rules drift, but
deterministic rules keep replay testing trivial and the unit-test
matrix tight.
"""

from __future__ import annotations

from enum import StrEnum

from digital_twin.memory.models import ExperienceMemory


class ConsolidationTheme(StrEnum):
    """Semantic buckets for experience consolidation."""

    MECHANICAL_VALIDATION = "mechanical_validation"
    POWER_ANALYSIS = "power_analysis"
    CIRCUIT_DESIGN_RULE = "circuit_design_rule"
    COMPONENT_SELECTION = "component_selection"
    FIRMWARE_BUILD = "firmware_build"
    SIMULATION = "simulation"
    COMPLIANCE_CHECK = "compliance_check"
    MISC = "misc"


# Keyword → theme rules applied to the experience's ``task_type`` first,
# then fall back to ``agent_code`` if task_type was empty / ambiguous.
# Ordered: first match wins, so put the most specific patterns up top.
_TASK_KEYWORD_RULES: list[tuple[tuple[str, ...], ConsolidationTheme]] = [
    (("stress", "fea", "tolerance"), ConsolidationTheme.MECHANICAL_VALIDATION),
    (("power", "budget", "rail"), ConsolidationTheme.POWER_ANALYSIS),
    (("erc", "drc", "schematic_check"), ConsolidationTheme.CIRCUIT_DESIGN_RULE),
    (("component", "select", "bom"), ConsolidationTheme.COMPONENT_SELECTION),
    (("firmware", "build", "compile", "flash"), ConsolidationTheme.FIRMWARE_BUILD),
    (("simulation", "spice", "sim"), ConsolidationTheme.SIMULATION),
    (("compliance", "ce", "ukca", "fcc"), ConsolidationTheme.COMPLIANCE_CHECK),
]


# Agent-code fallback when task_type yields no signal. Buckets keyed on
# the canonical agent codes from FRAMEWORK_MAPPING.md.
_AGENT_CODE_FALLBACK: dict[str, ConsolidationTheme] = {
    "mechanical": ConsolidationTheme.MECHANICAL_VALIDATION,
    "electronics": ConsolidationTheme.CIRCUIT_DESIGN_RULE,
    "firmware": ConsolidationTheme.FIRMWARE_BUILD,
    "simulation": ConsolidationTheme.SIMULATION,
    "compliance": ConsolidationTheme.COMPLIANCE_CHECK,
}


def classify_theme(experience: ExperienceMemory) -> ConsolidationTheme:
    """Return the theme an experience belongs to.

    Phase 2 keeps this deterministic and rule-based — fast, replayable,
    and trivial to test. The rules look at ``task_type`` first because
    it carries the most semantic punch; ``agent_code`` is the safety
    net for events where the upstream caller didn't stamp a task_type.
    Unknown shapes land in ``ConsolidationTheme.MISC`` so the synthesizer
    still sees them rather than silently dropping data.
    """
    task = (experience.task_type or "").lower()
    if task:
        for keywords, theme in _TASK_KEYWORD_RULES:
            if any(kw in task for kw in keywords):
                return theme
    agent = (experience.agent_code or "").lower()
    return _AGENT_CODE_FALLBACK.get(agent, ConsolidationTheme.MISC)
