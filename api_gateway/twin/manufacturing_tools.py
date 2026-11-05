"""The manufacturing context, DFM report and release checklist as MCP tools.

What ``twin.get_manufacturing_context``, ``twin.state_manufacturing_context``,
``twin.get_dfm_report`` and ``twin.get_release_checklist`` call (through
:class:`api_gateway.twin.dfm_findings.DfmService`). The REST routes stay the
dashboard's way in; these are an agent's.

**Who may say what** (1A, DFM-002). An agent calling
``twin.state_manufacturing_context`` proposes: its values are assumptions,
which never override a stated value or a decision, and the fields that are
never assumed (target cost, lead time, machine or supplier) are refused. A
value is recorded as ``user_stated`` only when the call was held and a
person approved it: the dispatcher hands the tool that person
(:data:`mcp_core.guardrails.APPROVED_BY_ARG`, set only for the tools in
``APPROVER_RECORDED``), and the model can neither supply nor fake it. A
project policy's automatic approval names no person and is refused for a
statement. ``decision`` comes only from an approved design decision
(``twin.record_decision`` with a ``manufacturing_route``), never from here.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.manufacturing_tools")

#: How a ``state`` call is to be read.
STATEMENT_KINDS: tuple[str, ...] = ("proposal", "user_stated")
#: Approver labels that are not a person (a project policy's automatic approval).
_NOT_A_PERSON_PREFIXES: tuple[str, ...] = ("policy",)


class ManufacturingToolError(ValueError):
    """A call that cannot be honoured; the message says why."""


def _view_dict(view: Any) -> dict[str, Any]:
    out: dict[str, Any] = view.model_dump(mode="json")
    return out


async def get_context(twin: Any, project_id: str, component: str | None = None) -> dict[str, Any]:
    """The context a component (or the product) is made under, with its ref and gaps."""
    from api_gateway.twin.manufacturing_context import effective_context, view_of

    with tracer.start_as_current_span("twin.manufacturing_tools.get_context") as span:
        span.set_attribute("project_id", project_id)
        stored = await effective_context(twin, project_id, component)
        return _view_dict(view_of(project_id, component, stored))


def _person(approved_by: Any) -> str | None:
    """The approving person's label, or ``None`` when no person approved the call."""
    if not isinstance(approved_by, str) or not approved_by.strip():
        return None
    label = approved_by.strip()
    if label.lower().startswith(_NOT_A_PERSON_PREFIXES):
        return None
    return label


def updates_from_tool(
    fields: dict[str, Any],
    *,
    statement: str,
    person: str | None,
    notes: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """``(updates, clears)`` for a tool call, or ``ManufacturingToolError``.

    A proposal is an agent's: assumptions only, no clearing, nothing that is
    never assumed. A ``user_stated`` call needs ``person``.
    """
    from api_gateway.twin.manufacturing_context_routes import normalize_value
    from twin_core.manufacturing import (
        Authority,
        ContextField,
        Provenance,
        normalize_orientation,
    )
    from twin_core.manufacturing.context import CONTEXT_FIELDS, NEVER_ASSUMED

    if statement not in STATEMENT_KINDS:
        raise ManufacturingToolError(
            f"statement is one of {', '.join(STATEMENT_KINDS)}; a decision is recorded with "
            "twin.record_decision (a manufacturing_route), never stated here"
        )
    if statement == "user_stated" and person is None:
        raise ManufacturingToolError(
            "only a person's statement becomes user_stated, and no person approved this "
            "call: send it as a proposal (assumptions), or have the person approve the held "
            "call or state it on the dashboard"
        )
    unknown = sorted(set(fields) - set(CONTEXT_FIELDS))
    if unknown:
        raise ManufacturingToolError(f"unknown manufacturing context fields: {', '.join(unknown)}")
    agent = statement == "proposal"
    source = "mcp_agent" if agent else "mcp_person"
    updates: dict[str, ContextField] = {}
    clears = False
    for name, raw in fields.items():
        try:
            value = normalize_value(name, raw)
            if name == "print_orientation" and value is not None:
                value = normalize_orientation(value)
        except ValueError as exc:
            raise ManufacturingToolError(str(exc)) from exc
        note = str((notes or {}).get(name) or "")[:500]
        prov = Provenance(
            source=source,
            actor=person or "agent",
            ref="approved tool call" if person else None,
        )
        if value is None or value in ("", [], {}):
            if agent:
                raise ManufacturingToolError(f"{name}: clearing a field is a person's statement")
            updates[name] = ContextField(authority=Authority.UNKNOWN, provenance=prov, note=note)
            clears = True
            continue
        if agent and name in NEVER_ASSUMED:
            raise ManufacturingToolError(
                f"{name} is never assumed: only a person or a verified integration states it"
            )
        try:
            updates[name] = ContextField(
                value=value,
                authority=Authority.ASSUMPTION if agent else Authority.USER_STATED,
                provenance=prov,
                note=note,
                # An assumed orientation only holds for the process it was assumed for.
                process_specific=agent and name == "print_orientation",
            )
        except ValueError as exc:
            raise ManufacturingToolError(f"{name}: {exc}") from exc
    return updates, clears


async def state_context(
    twin: Any,
    project_id: str,
    fields: dict[str, Any],
    *,
    component: str | None = None,
    statement: str = "proposal",
    approved_by: Any = None,
    notes: dict[str, Any] | None = None,
    change_reason: str = "",
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Write fields as the context's next revision, under the conflict rule."""
    from api_gateway.twin.manufacturing_context import effective_context, view_of, write_context

    with tracer.start_as_current_span("twin.manufacturing_tools.state_context") as span:
        span.set_attribute("project_id", project_id)
        span.set_attribute("mfc.statement", statement)
        if not fields:
            raise ManufacturingToolError("no fields to state")
        person = _person(approved_by)
        updates, clears = updates_from_tool(fields, statement=statement, person=person, notes=notes)
        source = "mcp_person" if statement == "user_stated" else "mcp_agent"
        outcome = await write_context(
            twin,
            project_id,
            updates,
            source=source,
            actor=person or "agent",
            change_reason=change_reason
            or (
                "manufacturing context stated by a person"
                if statement == "user_stated"
                else "manufacturing context proposed by an agent"
            ),
            component_key=component,
            allow_clear=clears,
            expected_revision=expected_revision,
        )
        stored = outcome.stored or await effective_context(twin, project_id, component)
        view = _view_dict(view_of(project_id, component, stored, outcome=outcome))
        view["statement"] = statement
        view["stated_by"] = person
        logger.info(
            "manufacturing_context_tool_write",
            project_id=project_id,
            component=component,
            statement=statement,
            changed=list(outcome.changed),
            refused=[c.field for c in outcome.refused],
        )
        return view


async def get_report(twin: Any, project_id: str) -> dict[str, Any]:
    """The latest stored DFM report; with none stored, an unstored preview."""
    from api_gateway.twin.dfm_report import DfmReportError, build_report, latest_report

    with tracer.start_as_current_span("twin.manufacturing_tools.get_report") as span:
        span.set_attribute("project_id", project_id)
        try:
            out = await latest_report(twin, project_id)
            return {"stored": True, **out}
        except DfmReportError as exc:
            if exc.status != 404:
                raise
        preview = await build_report(twin, project_id)
        return {
            "stored": False,
            "report_id": None,
            "report": preview,
            "note": (
                "No DFM report is stored for this project yet. This is a preview of what one "
                "would hold now; generate and store one from the DFM report action."
            ),
        }


async def get_checklist(twin: Any, project_id: str) -> dict[str, Any]:
    """The manufacturing release checklist for the current revision, with readiness."""
    from twin_core.dfm.release_checklist import manufacturing_checklist, readiness

    with tracer.start_as_current_span("twin.manufacturing_tools.get_checklist") as span:
        span.set_attribute("project_id", project_id)
        checklist = await manufacturing_checklist(twin, project_id)
        if checklist is None:
            return {"project_id": project_id, "applies": False, "readiness": readiness(None)}
        return {"applies": True, **checklist.as_dict()}


__all__ = [
    "STATEMENT_KINDS",
    "ManufacturingToolError",
    "get_checklist",
    "get_context",
    "get_report",
    "state_context",
    "updates_from_tool",
]
