"""Binding the design-flow MCP tools to the gateway (FORGE-400).

The adapter in ``tool_registry`` holds the tool shapes and takes injected
callables; the implementations are here, where the generator, the version
store and the run store live. Same seam as the twin adapter's recorders — the
adapter never imports upward.

Each binding returns plain dictionaries. An MCP tool result is read by a
model, so the shapes carry the same sentences the dashboard shows rather than
a status code the agent has to interpret: the difference between
``"status": "proposed"`` and a line saying nothing runs until a person
answers is whether the agent tells the user the right thing.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "make_catalogue_reader",
    "make_proposer",
    "make_run_starter",
    "make_run_status_reader",
]


def make_catalogue_reader() -> Any:
    """``flow.list`` — every launchable flow, as the gateway will run it."""

    async def read_catalogue() -> dict[str, Any]:
        from api_gateway.design_flows.routes import list_design_flows

        listing = list_design_flows()
        return {
            "default_flow_id": listing.defaultFlowId,
            "flows": [
                {
                    "id": flow.id,
                    "label": flow.label,
                    "description": flow.description,
                    "version": flow.version,
                    "is_default": flow.isDefault,
                    "startable": flow.valid,
                    # Served, not hidden: a flow that cannot be started must
                    # say why, or an agent picks it and gets a refusal it
                    # cannot explain.
                    "violations": flow.violations,
                    "phases": [
                        {
                            "id": p.id,
                            "title": p.title,
                            "required_deliverables": p.requiredDeliverables,
                            "gate": p.gate.name if p.gate else None,
                            "disciplines": p.disciplines,
                        }
                        for p in flow.phases
                    ],
                }
                for flow in listing.flows
            ],
        }

    return read_catalogue


def make_proposer() -> Any:
    """``flow.propose`` — tailor a template and hold it for a human."""

    async def propose(
        *,
        intent: str,
        project_id: str | None,
        requirements: list[str],
        manufacturing_context: dict[str, Any] | None = None,
        target_maturity: str | None = None,
        loads_and_use: str | None = None,
        budget: str | None = None,
    ) -> dict[str, Any]:
        from fastapi import HTTPException, Response
        from pydantic import ValidationError

        from api_gateway.design_flows.routes import (
            FlowNeedsInputView,
            ProposeFlowRequest,
            propose_flow,
        )

        class _NoPrincipal:
            """`propose_flow` takes a Request only to resolve an approver.

            An MCP caller has no HTTP request, and deliberately no way to
            nominate one: the approver is whoever answers the approval, which
            has not happened yet (FORGE-393).
            """

            state = type("S", (), {})()

        try:
            body = ProposeFlowRequest.model_validate(
                {
                    "intent": intent,
                    "projectId": project_id,
                    "requirements": requirements,
                    "manufacturing_context": manufacturing_context,
                    "target_maturity": target_maturity,
                    "loads_and_use": loads_and_use,
                    "budget": budget,
                }
            )
        except ValidationError as exc:
            # An unknown route or maturity value: say which, in plain words.
            raise RuntimeError(f"flow.propose: invalid input: {exc}") from exc

        try:
            view = await propose_flow(
                body,
                _NoPrincipal(),  # type: ignore[arg-type]
                Response(),
            )
        except HTTPException as exc:
            # Surfaced as a tool error with the gateway's own words rather
            # than a bare failure; the detail already says what to do.
            raise RuntimeError(str(exc.detail)) from exc

        if isinstance(view, FlowNeedsInputView):
            return {
                "status": "needs_input",
                "questions": [q.model_dump() for q in view.questions],
                "notes": view.notes,
                # Nothing to approve, so nothing to wait for: the agent's job
                # is to ask, not to fill the answers in itself.
                "next_step": (
                    "No flow was proposed and nothing is held. Ask the user these "
                    "questions -- do not answer them yourself or guess -- then call "
                    "flow.propose again with the answers in manufacturing_context, "
                    "target_maturity and loads_and_use."
                ),
            }

        return {
            "status": "proposed",
            "version_id": view.versionId,
            "approval_id": view.approvalId,
            "base_template_id": view.baseTemplateId,
            "base_version": view.baseVersion,
            "startable": view.valid,
            "violations": view.violations,
            "requirements_pending": view.requirementsPending,
            "assumptions": view.assumptions,
            "open_questions": [q.model_dump() for q in view.openQuestions],
            "changes": [
                {
                    "op": c.op,
                    "phase": c.phase,
                    "value": c.value,
                    "rationale": c.rationale,
                    "basis": c.basis,
                }
                for c in view.changes
            ],
            "phases": [
                {"id": p.id, "title": p.title, "gate": p.gate.name if p.gate else None}
                for p in view.flow.phases
            ],
            # The sentence the agent should repeat, rather than a status code
            # it has to interpret into one.
            "next_step": (
                "This proposal is held for a person. Nothing runs until somebody "
                f"answers approval '{view.approvalId}' in the dashboard (or inline, if "
                "this client supports elicitation). You cannot approve it yourself and "
                "there is no tool that would let you. Report the changes above and "
                "stop; do not poll."
            ),
        }

    return propose


def make_run_status_reader() -> Any:
    """``flow.status`` and the run resource — phase state for one run."""

    async def read_status(run_id: str) -> dict[str, Any]:
        from fastapi import HTTPException

        from api_gateway.runs.routes import get_flow_state

        try:
            state = await get_flow_state(run_id)
        except HTTPException as exc:
            raise RuntimeError(str(exc.detail)) from exc
        return state.model_dump()

    return read_status


def make_run_starter() -> Any:
    """``flow.start_run`` — start a run on an approved flow version."""

    async def start_run(
        *, flow_version_id: str, goal: str, project_id: str | None
    ) -> dict[str, Any]:
        from fastapi import HTTPException

        from api_gateway.runs.routes import create_run
        from api_gateway.runs.schemas import CreateRunRequest

        try:
            run = await create_run(
                CreateRunRequest(
                    request={
                        "kind": "design_flow",
                        "flow_version_id": flow_version_id,
                        "goal": goal,
                        "project_id": project_id,
                    },
                    start=True,
                )
            )
        except HTTPException as exc:
            if exc.status_code == 409:
                # The normal outcome right after proposing. Saying "not
                # approved yet" rather than "conflict" is the difference
                # between the agent waiting and the agent reporting a fault.
                raise RuntimeError(
                    f"{exc.detail} This is the expected result until a person answers "
                    "the approval -- it is not a failure."
                ) from exc
            raise RuntimeError(str(exc.detail)) from exc

        logger.info("design_flow_run_started_over_mcp", run_id=run.id)
        return {
            "run_id": run.id,
            "status": run.status,
            "resource": f"metaforge://flow/run/{run.id}",
        }

    return start_run
