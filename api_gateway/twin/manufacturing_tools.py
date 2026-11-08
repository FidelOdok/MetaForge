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

**Delegation.** When the gateway owner delegates human authority to agents
(``METAFORGE_ALLOW_AGENT_HUMAN_AUTHORITY``) and the MCP request named the
agent and the person it acts for, the dispatcher records a delegated approval
and hands the tool both (:data:`mcp_core.guardrails.DELEGATED_AGENT_ARG`
beside the approver). The agent may then state what only the person could,
the machine above all: recorded ``user_stated`` with the agent as the actor,
the person in ``on_behalf_of`` and route ``delegated``. A named machine the
machine library holds is linked by id, so DFM assesses the part against it.
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


def _delegate(delegated_agent: Any, person: str | None) -> str | None:
    """The agent's name when the dispatcher delegated this call, else ``None``.

    Both the agent and the person are reserved arguments the MCP dispatcher
    sets only when the owner delegates human authority and the request named
    the agent and the person it acts for; a client can supply neither.
    """
    if person is None or not isinstance(delegated_agent, str) or not delegated_agent.strip():
        return None
    return delegated_agent.strip()[:100]


def _machine_label(entry: Any) -> str:
    name = getattr(entry, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return f"{getattr(entry, 'make', '')} {getattr(entry, 'model', '')}".strip()


def _tokens(text: str) -> set[str]:
    return {t for t in "".join(c if c.isalnum() else " " for c in text.lower()).split() if t}


def find_machine(wanted: str) -> Any:
    """The one machine library entry a stated name means, or ``None``.

    An exact label wins. Otherwise the entry whose label words all appear in
    the name, or whose words the name all carries ("Bambu P1S" is the
    "Bambu Lab P1S" model), and only when exactly one entry matches: an
    ambiguous name ("Bambu Lab") links nothing.
    """
    want = _tokens(wanted)
    if not want:
        return None
    try:
        from api_gateway.library.routes import get_machine_library

        entries = list(get_machine_library().list())
    except Exception as exc:  # noqa: BLE001 - an unreadable library links nothing
        logger.debug("manufacturing_tools_machine_library_unavailable", error=str(exc))
        return None
    exact = [e for e in entries if _machine_label(e).lower() == wanted.strip().lower()]
    if len(exact) == 1:
        return exact[0]
    close = [
        e
        for e in entries
        if (label := _tokens(_machine_label(e))) and (label <= want or want <= label)
    ]
    return close[0] if len(close) == 1 else None


def link_machine(profile: dict[str, Any]) -> dict[str, Any]:
    """A stated machine or supplier profile, linked to the machine library when it names one.

    A bare name (text with no machine, machine id or supplier) is read as the
    machine. A machine the library holds gets its ``machine_id`` and its
    library label, so DFM reads that machine's limits; one it does not hold
    keeps the name as stated.
    """
    out = dict(profile)
    if not (out.get("machine") or out.get("machine_id") or out.get("supplier")):
        if out.get("description"):
            out["machine"] = out["description"]
    if out.get("machine") and not out.get("machine_id"):
        found = find_machine(str(out["machine"]))
        if found is not None:
            out["machine_id"] = str(found.id)
            out["machine"] = _machine_label(found)[:300]
    return out


def updates_from_tool(
    fields: dict[str, Any],
    *,
    statement: str,
    person: str | None,
    notes: dict[str, Any] | None = None,
    delegated_agent: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """``(updates, clears)`` for a tool call, or ``ManufacturingToolError``.

    A proposal is an agent's: assumptions only, no clearing, nothing that is
    never assumed. A ``user_stated`` call needs ``person``.

    With ``delegated_agent`` (the owner delegates human authority, and the
    agent named itself and ``person``), what the person could state is stated
    by the agent on their behalf: every field of a ``user_stated`` call, and
    the never-assumed fields of a proposal (the machine or supplier, target
    cost, lead time), which would otherwise be refused. Those are recorded as
    ``user_stated`` with the agent as the actor, ``on_behalf_of`` the person
    and route ``delegated``: never as the person alone, never as assumed.
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
    agent_name = _delegate(delegated_agent, person)
    updates: dict[str, ContextField] = {}
    clears = False
    for name, raw in fields.items():
        try:
            value = normalize_value(name, raw)
            if name == "print_orientation" and value is not None:
                value = normalize_orientation(value)
        except ValueError as exc:
            raise ManufacturingToolError(str(exc)) from exc
        if name == "machine_or_supplier_profile" and isinstance(value, dict) and value:
            value = link_machine(value)
        note = str((notes or {}).get(name) or "")[:500]
        stated_here = statement == "user_stated" or (
            agent_name is not None and name in NEVER_ASSUMED
        )
        if agent_name is not None:
            prov = Provenance(
                source="mcp_delegated" if stated_here else "mcp_agent",
                actor=f"agent:{agent_name}",
                ref="delegated tool call",
                on_behalf_of=person,
                route="delegated",
            )
        else:
            prov = Provenance(
                source="mcp_person" if stated_here else "mcp_agent",
                actor=person or "agent",
                ref="approved tool call" if person else None,
            )
        if value is None or value in ("", [], {}):
            if statement != "user_stated":
                raise ManufacturingToolError(f"{name}: clearing a field is a person's statement")
            updates[name] = ContextField(authority=Authority.UNKNOWN, provenance=prov, note=note)
            clears = True
            continue
        if not stated_here and name in NEVER_ASSUMED:
            raise ManufacturingToolError(
                f"{name} is never assumed: only a person or a verified integration states it"
            )
        try:
            updates[name] = ContextField(
                value=value,
                authority=Authority.USER_STATED if stated_here else Authority.ASSUMPTION,
                provenance=prov,
                note=note,
                # An assumed orientation only holds for the process it was assumed for.
                process_specific=not stated_here and name == "print_orientation",
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
    delegated_agent: Any = None,
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
        agent_name = _delegate(delegated_agent, person)
        span.set_attribute("mfc.delegated", agent_name is not None)
        updates, clears = updates_from_tool(
            fields,
            statement=statement,
            person=person,
            notes=notes,
            delegated_agent=agent_name,
        )
        if agent_name is not None:
            source = "mcp_delegated"
            actor = f"agent:{agent_name}"
            default_reason = f"manufacturing context stated by agent {agent_name} for {person}"
        elif statement == "user_stated":
            source, actor = "mcp_person", person or "agent"
            default_reason = "manufacturing context stated by a person"
        else:
            source, actor = "mcp_agent", person or "agent"
            default_reason = "manufacturing context proposed by an agent"
        outcome = await write_context(
            twin,
            project_id,
            updates,
            source=source,
            actor=actor,
            change_reason=change_reason or default_reason,
            component_key=component,
            allow_clear=clears,
            expected_revision=expected_revision,
        )
        stored = outcome.stored or await effective_context(twin, project_id, component)
        view = _view_dict(view_of(project_id, component, stored, outcome=outcome))
        view["statement"] = statement
        if agent_name is not None:
            # The agent stated it, for the person: never the person alone.
            view["stated_by"] = f"agent:{agent_name}"
            view["on_behalf_of"] = person
            view["route"] = "delegated"
        else:
            view["stated_by"] = person
        logger.info(
            "manufacturing_context_tool_write",
            project_id=project_id,
            component=component,
            statement=statement,
            delegated=agent_name is not None,
            agent=agent_name,
            on_behalf_of=person if agent_name else None,
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
    "find_machine",
    "get_checklist",
    "get_context",
    "get_report",
    "link_machine",
    "state_context",
    "updates_from_tool",
]
