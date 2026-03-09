"""Feasibility evaluation for SysML v2 integration with MetaForge.

Assesses the mapping coverage, identifies gaps, and provides effort
estimates for building a production-ready SysML v2 adapter.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.sysml.evaluation")


class MappingCoverage(BaseModel):
    """Coverage analysis for a single node/edge type mapping."""

    metaforge_type: str
    sysml_type: str
    coverage: str  # "full", "partial", "none"
    notes: str = ""


class GapItem(BaseModel):
    """A gap identified in the MetaForge-to-SysML mapping."""

    category: str  # "model", "api", "tooling", "semantics"
    description: str
    severity: str  # "high", "medium", "low"
    mitigation: str = ""


class EffortEstimate(BaseModel):
    """Effort estimate for a component of the SysML v2 adapter."""

    component: str
    description: str
    effort_weeks: float
    complexity: str  # "low", "medium", "high"


class FeasibilityReport(BaseModel):
    """Complete feasibility assessment for SysML v2 integration."""

    summary: str
    overall_feasibility: str  # "high", "medium", "low"
    mapping_coverage: list[MappingCoverage] = Field(default_factory=list)
    gaps: list[GapItem] = Field(default_factory=list)
    effort_estimates: list[EffortEstimate] = Field(default_factory=list)
    total_effort_weeks: float = 0.0
    recommendations: list[str] = Field(default_factory=list)


def evaluate_sysml_feasibility() -> FeasibilityReport:
    """Generate a feasibility report for SysML v2 integration.

    Analyzes the current MetaForge graph schema against the SysML v2
    metamodel to identify coverage, gaps, and estimate integration effort.
    """
    with tracer.start_as_current_span("sysml.evaluate_feasibility"):
        logger.info("starting_sysml_feasibility_evaluation")

        coverage = _assess_mapping_coverage()
        gaps = _identify_gaps()
        estimates = _estimate_effort()
        total = sum(e.effort_weeks for e in estimates)
        recommendations = _generate_recommendations()

        report = FeasibilityReport(
            summary=(
                "SysML v2 integration is feasible with moderate effort. "
                "The core MetaForge node types (Artifact, Constraint, Component, "
                "Relationship) map well to SysML v2 usages (PartUsage, "
                "RequirementUsage, ConstraintUsage, ConnectionUsage). Key gaps "
                "include behavioral modeling, allocation/traceability matrices, "
                "and the full SysML v2 REST API implementation. A production "
                "adapter is estimated at approximately {:.0f} engineering weeks.".format(
                    total
                )
            ),
            overall_feasibility="medium",
            mapping_coverage=coverage,
            gaps=gaps,
            effort_estimates=estimates,
            total_effort_weeks=total,
            recommendations=recommendations,
        )

        logger.info(
            "feasibility_evaluation_complete",
            feasibility=report.overall_feasibility,
            total_effort_weeks=total,
            gap_count=len(gaps),
        )

        return report


def _assess_mapping_coverage() -> list[MappingCoverage]:
    """Assess how well MetaForge types map to SysML v2 elements."""
    return [
        MappingCoverage(
            metaforge_type="Artifact (CAD_MODEL, SCHEMATIC, PCB_LAYOUT, BOM)",
            sysml_type="PartUsage",
            coverage="full",
            notes=(
                "Physical artifact types map cleanly to PartUsage. "
                "Artifact metadata preserved in properties dict."
            ),
        ),
        MappingCoverage(
            metaforge_type="Artifact (PRD, DOCUMENTATION, TEST_PLAN)",
            sysml_type="RequirementUsage",
            coverage="full",
            notes=(
                "Requirement-like artifacts map to RequirementUsage. "
                "Requirement text extracted from metadata."
            ),
        ),
        MappingCoverage(
            metaforge_type="Constraint",
            sysml_type="ConstraintUsage",
            coverage="full",
            notes=(
                "Expression, severity, status, and cross-domain flag "
                "all have direct equivalents."
            ),
        ),
        MappingCoverage(
            metaforge_type="Component",
            sysml_type="PartUsage",
            coverage="partial",
            notes=(
                "Components map to PartUsage with an is_component flag. "
                "SysML v2 lacks native BOM/supply-chain concepts; these "
                "are carried in the properties dict."
            ),
        ),
        MappingCoverage(
            metaforge_type="EdgeBase (all EdgeTypes)",
            sysml_type="ConnectionUsage",
            coverage="partial",
            notes=(
                "All 10 EdgeTypes map to ConnectionUsage with a "
                "connection_kind discriminator. SysML v2 has richer "
                "relationship semantics (allocations, satisfy, verify) "
                "not yet captured."
            ),
        ),
        MappingCoverage(
            metaforge_type="Version",
            sysml_type="(no direct equivalent)",
            coverage="none",
            notes=(
                "SysML v2 API has commits/branches but they model "
                "project-level versioning, not per-artifact versioning "
                "as in MetaForge."
            ),
        ),
        MappingCoverage(
            metaforge_type="SubGraph",
            sysml_type="Package",
            coverage="partial",
            notes=(
                "SubGraph exports map to Package containers. Package "
                "nesting and membership semantics differ."
            ),
        ),
    ]


def _identify_gaps() -> list[GapItem]:
    """Identify gaps in the current mapping prototype."""
    return [
        GapItem(
            category="model",
            description=(
                "SysML v2 behavioral elements (ActionUsage, StateUsage, "
                "FlowConnectionUsage) have no MetaForge equivalent."
            ),
            severity="medium",
            mitigation=(
                "Add behavioral modeling nodes to the Digital Twin graph "
                "schema in a future phase, or carry behavioral data as "
                "opaque metadata."
            ),
        ),
        GapItem(
            category="model",
            description=(
                "SysML v2 allocation and traceability (AllocationUsage, "
                "SatisfyRequirementUsage, VerifyRequirementUsage) are not "
                "mapped."
            ),
            severity="medium",
            mitigation=(
                "Extend EdgeType enum with ALLOCATES, SATISFIES, VERIFIES "
                "to support richer traceability."
            ),
        ),
        GapItem(
            category="api",
            description=(
                "SysML v2 REST API requires project/commit/branch endpoints "
                "for model exchange. Only JSON serialization is prototyped."
            ),
            severity="high",
            mitigation=(
                "Implement a SysML v2 API client using the OpenAPI spec "
                "from the OMG SysML v2 API & Services specification."
            ),
        ),
        GapItem(
            category="api",
            description=(
                "SysML v2 API uses server-sent events (SSE) for real-time "
                "sync. MetaForge uses Kafka for event distribution."
            ),
            severity="low",
            mitigation=(
                "Build an SSE-to-Kafka bridge adapter for real-time "
                "bidirectional sync."
            ),
        ),
        GapItem(
            category="tooling",
            description=(
                "No integration with existing SysML v2 tools (Eclipse "
                "SysON, Cameo/MagicDraw via SysML v2 Pilot)."
            ),
            severity="medium",
            mitigation=(
                "Target SysON (open-source) as the first integration "
                "target. Its REST API aligns with the OMG spec."
            ),
        ),
        GapItem(
            category="semantics",
            description=(
                "MetaForge constraint expressions use domain-specific "
                "syntax; SysML v2 uses OCL-like constraint expressions."
            ),
            severity="low",
            mitigation=(
                "Build a constraint expression translator or carry "
                "MetaForge expressions as opaque strings in SysML metadata."
            ),
        ),
        GapItem(
            category="model",
            description=(
                "InterfaceUsage mapping is defined but has no MetaForge "
                "equivalent beyond generic EdgeBase relationships."
            ),
            severity="low",
            mitigation=(
                "Add an INTERFACE edge type or a dedicated InterfaceNode "
                "to the Digital Twin schema."
            ),
        ),
    ]


def _estimate_effort() -> list[EffortEstimate]:
    """Estimate effort for production SysML v2 adapter components."""
    return [
        EffortEstimate(
            component="SysML v2 API Client",
            description=(
                "HTTP client implementing the SysML v2 REST API spec "
                "(projects, commits, elements CRUD, queries)."
            ),
            effort_weeks=4.0,
            complexity="high",
        ),
        EffortEstimate(
            component="Bidirectional Mapper (production)",
            description=(
                "Extend prototype mapper with full type coverage, "
                "behavioral elements, and allocation/traceability."
            ),
            effort_weeks=3.0,
            complexity="medium",
        ),
        EffortEstimate(
            component="Real-time Sync Engine",
            description=(
                "SSE-to-Kafka bridge for bidirectional real-time sync "
                "between MetaForge and SysML v2 tools."
            ),
            effort_weeks=3.0,
            complexity="high",
        ),
        EffortEstimate(
            component="Constraint Expression Translator",
            description=(
                "Translate MetaForge constraint syntax to/from OCL-like "
                "SysML v2 constraint expressions."
            ),
            effort_weeks=2.0,
            complexity="medium",
        ),
        EffortEstimate(
            component="Schema Extension (Digital Twin)",
            description=(
                "Add behavioral nodes, interface nodes, and new edge "
                "types to the Digital Twin graph schema."
            ),
            effort_weeks=2.0,
            complexity="medium",
        ),
        EffortEstimate(
            component="Integration Tests & Validation",
            description=(
                "End-to-end tests with SysON or a mock SysML v2 API "
                "server. Includes round-trip validation."
            ),
            effort_weeks=2.0,
            complexity="low",
        ),
    ]


def _generate_recommendations() -> list[str]:
    """Generate strategic recommendations for SysML v2 integration."""
    return [
        (
            "Start with export-only (MetaForge -> SysML v2) to validate the "
            "mapping with real MBSE tools before investing in bidirectional sync."
        ),
        (
            "Target Eclipse SysON as the first integration partner since it is "
            "open-source and implements the OMG SysML v2 REST API specification."
        ),
        (
            "Extend the MetaForge EdgeType enum with ALLOCATES, SATISFIES, and "
            "VERIFIES to support SysML v2 traceability relationships natively."
        ),
        (
            "Carry unsupported SysML v2 element types as opaque metadata in the "
            "Digital Twin graph rather than losing data during import."
        ),
        (
            "Implement the SysML v2 API client as an MCP tool adapter, consistent "
            "with MetaForge's architecture where all tool access goes through MCP."
        ),
        (
            "Phase the integration: Phase 2 for export-only, Phase 3 for full "
            "bidirectional sync with behavioral modeling support."
        ),
    ]
