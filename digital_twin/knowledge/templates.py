"""Content templates for structured knowledge types.

Each template converts a context dict into a human-readable text
representation suitable for embedding and semantic search.
"""

from __future__ import annotations

import structlog

from digital_twin.knowledge.store import KnowledgeType
from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("digital_twin.knowledge.templates")


def render_design_decision(context: dict[str, object]) -> str:
    """Render a design decision into a searchable text block.

    Expected context keys:
        - ``title``: Short decision title
        - ``rationale``: Why this decision was made
        - ``alternatives``: Alternatives considered (optional)
        - ``constraints``: Relevant constraints (optional)
        - ``outcome``: Final outcome or chosen option (optional)
    """
    with tracer.start_as_current_span("template.design_decision") as span:
        title = context.get("title", "Untitled Decision")
        rationale = context.get("rationale", "")
        alternatives = context.get("alternatives", "")
        constraints = context.get("constraints", "")
        outcome = context.get("outcome", "")

        parts = [f"Design Decision: {title}"]
        if rationale:
            parts.append(f"Rationale: {rationale}")
        if alternatives:
            parts.append(f"Alternatives Considered: {alternatives}")
        if constraints:
            parts.append(f"Constraints: {constraints}")
        if outcome:
            parts.append(f"Outcome: {outcome}")

        result = "\n".join(parts)
        span.set_attribute("template.output_length", len(result))
        logger.debug("template_rendered", template="design_decision", length=len(result))
        return result


def render_session_summary(context: dict[str, object]) -> str:
    """Render a session summary into a searchable text block.

    Expected context keys:
        - ``session_id``: Identifier for the session
        - ``summary``: High-level summary of the session
        - ``decisions``: Decisions made during the session (optional)
        - ``work_products_modified``: List of modified work_products (optional)
        - ``next_steps``: Planned next steps (optional)
    """
    with tracer.start_as_current_span("template.session_summary") as span:
        session_id = context.get("session_id", "unknown")
        summary = context.get("summary", "")
        decisions = context.get("decisions", "")
        work_products = context.get("work_products_modified", "")
        next_steps = context.get("next_steps", "")

        parts = [f"Session Summary (ID: {session_id})"]
        if summary:
            parts.append(f"Summary: {summary}")
        if decisions:
            parts.append(f"Decisions: {decisions}")
        if work_products:
            parts.append(f"Artifacts Modified: {work_products}")
        if next_steps:
            parts.append(f"Next Steps: {next_steps}")

        result = "\n".join(parts)
        span.set_attribute("template.output_length", len(result))
        logger.debug("template_rendered", template="session_summary", length=len(result))
        return result


def render_component_selection(context: dict[str, object]) -> str:
    """Render a component selection into a searchable text block.

    Expected context keys:
        - ``component``: Component name or part number
        - ``reason``: Why this component was selected
        - ``specifications``: Key specifications (optional)
        - ``alternatives``: Alternative components considered (optional)
        - ``supplier``: Preferred supplier (optional)
    """
    with tracer.start_as_current_span("template.component_selection") as span:
        component = context.get("component", "Unknown Component")
        reason = context.get("reason", "")
        specifications = context.get("specifications", "")
        alternatives = context.get("alternatives", "")
        supplier = context.get("supplier", "")

        parts = [f"Component Selection: {component}"]
        if reason:
            parts.append(f"Reason: {reason}")
        if specifications:
            parts.append(f"Specifications: {specifications}")
        if alternatives:
            parts.append(f"Alternatives: {alternatives}")
        if supplier:
            parts.append(f"Supplier: {supplier}")

        result = "\n".join(parts)
        span.set_attribute("template.output_length", len(result))
        logger.debug("template_rendered", template="component_selection", length=len(result))
        return result


def render_failure_mode(context: dict[str, object]) -> str:
    """Render a failure mode into a searchable text block.

    Expected context keys:
        - ``failure``: Description of the failure mode
        - ``severity``: Severity level (optional)
        - ``cause``: Root cause analysis (optional)
        - ``mitigation``: Mitigation strategy (optional)
    """
    with tracer.start_as_current_span("template.failure_mode") as span:
        failure = context.get("failure", "Unknown Failure")
        severity = context.get("severity", "")
        cause = context.get("cause", "")
        mitigation = context.get("mitigation", "")

        parts = [f"Failure Mode: {failure}"]
        if severity:
            parts.append(f"Severity: {severity}")
        if cause:
            parts.append(f"Root Cause: {cause}")
        if mitigation:
            parts.append(f"Mitigation: {mitigation}")

        result = "\n".join(parts)
        span.set_attribute("template.output_length", len(result))
        logger.debug("template_rendered", template="failure_mode", length=len(result))
        return result


def render_constraint_rationale(context: dict[str, object]) -> str:
    """Render a constraint rationale into a searchable text block.

    Expected context keys:
        - ``constraint``: The constraint definition
        - ``rationale``: Why this constraint exists
        - ``domain``: Engineering domain (optional)
        - ``references``: Standards or references (optional)
    """
    with tracer.start_as_current_span("template.constraint_rationale") as span:
        constraint = context.get("constraint", "Unknown Constraint")
        rationale = context.get("rationale", "")
        domain = context.get("domain", "")
        references = context.get("references", "")

        parts = [f"Constraint: {constraint}"]
        if rationale:
            parts.append(f"Rationale: {rationale}")
        if domain:
            parts.append(f"Domain: {domain}")
        if references:
            parts.append(f"References: {references}")

        result = "\n".join(parts)
        span.set_attribute("template.output_length", len(result))
        logger.debug("template_rendered", template="constraint_rationale", length=len(result))
        return result


# Mapping from KnowledgeType to render function
_TEMPLATE_RENDERERS: dict[KnowledgeType, object] = {
    KnowledgeType.DESIGN_DECISION: render_design_decision,
    KnowledgeType.SESSION: render_session_summary,
    KnowledgeType.COMPONENT: render_component_selection,
    KnowledgeType.FAILURE: render_failure_mode,
    KnowledgeType.CONSTRAINT: render_constraint_rationale,
}


def render_template(knowledge_type: KnowledgeType, context: dict[str, object]) -> str:
    """Render content using the appropriate template for a knowledge type.

    Parameters
    ----------
    knowledge_type:
        The type of knowledge to render.
    context:
        Template context dict with type-specific keys.

    Returns
    -------
    str
        Rendered text suitable for embedding.

    Raises
    ------
    ValueError
        If no template is registered for the given knowledge type.
    """
    with tracer.start_as_current_span("template.render") as span:
        span.set_attribute("template.knowledge_type", str(knowledge_type))
        renderer = _TEMPLATE_RENDERERS.get(knowledge_type)
        if renderer is None:
            msg = f"No template registered for knowledge type: {knowledge_type!r}"
            logger.error("template_not_found", knowledge_type=str(knowledge_type))
            raise ValueError(msg)
        result: str = renderer(context)  # type: ignore[operator]
        span.set_attribute("template.output_length", len(result))
        return result
