"""Design flows and runs from another process, through the gateway (FORGE-462).

``flow.*`` and ``run.*`` are bound to the gateway's in-process state: the
flow-version store, the approval ledger and the run store are all
process-level. The gateway passes those bindings to
``bootstrap_tool_registry``; the HTTP sidecar never did. The adapters only
register when a binding is supplied, so every external harness plugin (they
all talk to the sidecar) saw none of the six tools. Nothing failed. They were
just never there.

Binding the sidecar to its *own* copies of those stores would have been the
quick fix, and the wrong one. A proposal held in the sidecar's ledger is one
the dashboard cannot show and nobody can answer; a run started there is one
``/v1/runs`` does not list. Same reasoning as ``remote_approvals`` (FORGE-406):
one ledger, one run store, reached over HTTP.

So each binding here calls the gateway route the dashboard already uses, and
returns the same shape as its in-process counterpart in
``api_gateway.design_flows.mcp_bindings`` / ``api_gateway.runs.launcher``.
The parity is asserted by test: a tool that answers differently depending on
which process hosts it is a tool an agent cannot be taught to read.

No ``api_gateway`` import here, on purpose: this is the side of the wire that
must not need the gateway's state to be in-process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("metaforge.mcp.remote_flows")

__all__ = ["RemoteFlowBindings", "RemoteRunLauncher", "build_remote_flow_bindings"]

#: Generating a tailored flow is an LLM call on the gateway side; the default
#: httpx timeout (5s) would turn a slow model into a spurious tool failure.
DEFAULT_TIMEOUT_SECONDS = 120.0

#: Word for word the in-process binding's sentence (``mcp_bindings``), kept
#: here because this side of the wire must not import the gateway.
INTENT_NEXT_STEP = (
    "This is what was understood; nothing was stored or proposed. Ask the user about "
    "every blocking unknown (do not answer it yourself), confirm the success criteria, "
    "then call flow.propose with the answers."
)


class GatewayRefusedError(RuntimeError):
    """The gateway answered, and the answer was no.

    Carries the gateway's own ``detail`` as the message. Those details are
    already written for the reader ("flow version ... is proposed, not
    approved"), and a tool error that says "HTTP 409" instead teaches the
    agent nothing.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or f"gateway returned HTTP {response.status_code}"
    if isinstance(body, dict) and body.get("detail") is not None:
        return str(body["detail"])
    return str(body)


class _Gateway:
    """The handful of requests these bindings make, with one error rule."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None, timeout: float) -> None:
        self._base = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout

    async def request(self, method: str, path: str, *, json: Any = None) -> Any:
        url = f"{self._base}{path}"
        owned = self._client is None
        http = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            try:
                response = await http.request(method, url, json=json)
            except httpx.HTTPError as exc:
                # Unreachable is not "refused": say which, so an operator can
                # tell a gateway that is down from one that said no.
                logger.error("mcp_remote_flow_unreachable", method=method, url=url, error=str(exc))
                raise RuntimeError(
                    f"The MetaForge gateway at {self._base} could not be reached ({exc}). "
                    "Design flows and runs live there; nothing was proposed or started."
                ) from exc
        finally:
            if owned:
                await http.aclose()
        if response.is_error:
            detail = _detail(response)
            logger.warning(
                "mcp_remote_flow_refused",
                method=method,
                path=path,
                status=response.status_code,
                detail=detail,
            )
            raise GatewayRefusedError(response.status_code, detail)
        return response.json()


# ── flow.* ────────────────────────────────────────────────────────────────


def _catalogue(listing: dict[str, Any]) -> dict[str, Any]:
    """``GET /v1/design-flows`` → the ``flow.list`` shape."""
    return {
        "default_flow_id": listing.get("defaultFlowId"),
        "flows": [
            {
                "id": flow["id"],
                "label": flow.get("label"),
                "description": flow.get("description"),
                "version": flow.get("version"),
                "is_default": flow.get("isDefault", False),
                "startable": flow.get("valid", False),
                "violations": flow.get("violations", []),
                "phases": [
                    {
                        "id": p["id"],
                        "title": p.get("title"),
                        "required_deliverables": p.get("requiredDeliverables", []),
                        "gate": (p.get("gate") or {}).get("name"),
                        "disciplines": p.get("disciplines", []),
                        "depends_on": p.get("dependsOn"),
                        "condition": p.get("condition"),
                        "outcome": p.get("outcome", ""),
                    }
                    for p in flow.get("phases", [])
                ],
                "graph": flow.get("graph"),
            }
            for flow in listing.get("flows", [])
        ],
    }


def _needs_input(view: dict[str, Any]) -> dict[str, Any]:
    """``status: "needs_input"`` from the route → the ``flow.propose`` shape.

    Nothing was generated, stored or held (FORGE-463), so there is no
    approval id to report and nothing to wait for.
    """
    return {
        "status": "needs_input",
        "questions": list(view.get("questions", [])),
        "notes": list(view.get("notes", [])),
        # Word for word what the in-process binding says.
        "next_step": (
            "No flow was proposed and nothing is held. Ask the user these "
            "questions -- do not answer them yourself or guess -- then call "
            "flow.propose again with the answers in manufacturing_context, "
            "target_maturity and loads_and_use."
        ),
    }


def _proposal(view: dict[str, Any]) -> dict[str, Any]:
    """``POST /v1/design-flows/propose`` → the ``flow.propose`` shape."""
    if view.get("status") == "needs_input":
        return _needs_input(view)
    approval_id = view["approvalId"]
    return {
        "status": "proposed",
        "version_id": view["versionId"],
        "approval_id": approval_id,
        "base_template_id": view.get("baseTemplateId"),
        "base_version": view.get("baseVersion"),
        "startable": view.get("valid", False),
        "violations": view.get("violations", []),
        "requirements_pending": view.get("requirementsPending", False),
        "assumptions": view.get("assumptions", []),
        "open_questions": list(view.get("openQuestions", [])),
        "proposed_by": view.get("proposedBy"),
        "changes": [
            {
                "op": c.get("op"),
                "phase": c.get("phase"),
                "value": c.get("value"),
                "rationale": c.get("rationale"),
                "basis": c.get("basis", ""),
            }
            for c in view.get("changes", [])
        ],
        "phases": [
            {"id": p["id"], "title": p.get("title"), "gate": (p.get("gate") or {}).get("name")}
            for p in (view.get("flow") or {}).get("phases", [])
        ],
        "intent_model": view.get("intentModel"),
        "capabilities": view.get("capabilities"),
        # Word for word what the in-process binding says. The agent's next
        # move depends on this sentence, so it must not depend on the host.
        "next_step": (
            "This proposal is held for a person. Nothing runs until somebody "
            f"answers approval '{approval_id}' in the dashboard (or inline, if "
            "this client supports elicitation). You cannot approve it yourself and "
            "there is no tool that would let you. Report the changes above and "
            "stop; do not poll."
        ),
    }


@dataclass
class RemoteFlowBindings:
    """The four ``design_flow`` adapter bindings, plus the ``run`` launcher."""

    catalogue_reader: Any
    proposer: Any
    status_reader: Any
    run_starter: Any
    run_launcher: RemoteRunLauncher
    #: FORGE-539: the lifecycle readers.
    intent_compiler: Any = None
    capability_reader: Any = None
    lifecycle_reader: Any = None


class RemoteRunLauncher:
    """``run.start_design_flow`` / ``run.get_status`` against ``/v1/runs``.

    Same duck type as ``api_gateway.runs.launcher.RunLauncher``. One
    difference worth knowing: the in-process launcher drives the run
    directly, while this goes through ``POST /v1/runs`` and therefore through
    the gateway's configured engine (Temporal by default). That is the
    dashboard's path, which is the point.
    """

    def __init__(self, gateway: _Gateway) -> None:
        self._gateway = gateway

    async def start(
        self,
        *,
        goal: str,
        flow: str,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        with tracer.start_as_current_span("remote_flows.run_start") as span:
            span.set_attribute("run.flow", flow)
            try:
                definition = await self._gateway.request("GET", f"/v1/design-flows/{flow}")
            except GatewayRefusedError as exc:
                if exc.status_code == 404:
                    # The in-process launcher raises ValueError here; keep the
                    # same type so the adapter reports it the same way.
                    raise ValueError(exc.detail) from exc
                raise
            request: dict[str, Any] = {"goal": goal, "flow": flow}
            if project_id:
                request["project_id"] = project_id
            if session_id:
                request["session_id"] = session_id
            run = await self._gateway.request(
                "POST", "/v1/runs", json={"request": request, "start": True}
            )
            span.set_attribute("run.id", str(run["id"]))
        logger.info(
            "design_flow_launched_from_tool",
            run_id=run["id"],
            flow=flow,
            project_id=project_id,
            via="gateway",
        )
        return {
            "run_id": run["id"],
            "flow": flow,
            "phases": [p["id"] for p in definition.get("phases", [])],
            "status": run.get("status", "running"),
            "note": (
                "The run pauses at each phase gate for human approval "
                "(POST /v1/runs/{id}/approval or `forge runs approve`)."
            ),
        }

    async def status(self, *, run_id: str) -> dict[str, Any]:
        with tracer.start_as_current_span("remote_flows.run_status") as span:
            span.set_attribute("run.id", run_id)
            run = await self._gateway.request("GET", f"/v1/runs/{run_id}")
        request = run.get("request") or {}
        out: dict[str, Any] = {
            "run_id": run["id"],
            "status": run.get("status"),
            "flow": request.get("flow"),
            "goal": request.get("goal"),
        }
        if run.get("approval_reason"):
            out["awaiting_approval_reason"] = run["approval_reason"]
        if run.get("error"):
            out["error"] = run["error"]
        if run.get("result"):
            out["result"] = run["result"]
        return out


def build_remote_flow_bindings(
    gateway_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> RemoteFlowBindings:
    """Bindings for ``flow.*`` and ``run.*`` that act on the gateway's state."""
    gateway = _Gateway(gateway_url, client, timeout_seconds)

    async def read_catalogue() -> dict[str, Any]:
        with tracer.start_as_current_span("remote_flows.catalogue"):
            listing = await gateway.request("GET", "/v1/design-flows")
        return _catalogue(listing)

    async def propose(
        *,
        intent: str,
        project_id: str | None,
        requirements: list[str],
        manufacturing_context: dict[str, Any] | None = None,
        target_maturity: str | None = None,
        loads_and_use: str | None = None,
        budget: str | None = None,
        template: str | None = None,
        operations: list[dict[str, Any]] | None = None,
        caller: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # FORGE-463's inputs travel as the route spells them. The nested
        # manufacturing_context keys are accepted in either case by the route.
        body: dict[str, Any] = {
            "intent": intent,
            "projectId": project_id,
            "requirements": requirements,
            "manufacturingContext": manufacturing_context,
            "targetMaturity": target_maturity,
            "loadsAndUse": loads_and_use,
            "budget": budget,
            # FORGE-481: caller-proposed tailoring; the route makes no model call.
            "template": template,
            "operations": operations,
            "caller": caller,
        }
        with tracer.start_as_current_span("remote_flows.propose") as span:
            span.set_attribute("flow.intent_length", len(intent))
            try:
                view = await gateway.request("POST", "/v1/design-flows/propose", json=body)
            except GatewayRefusedError as exc:
                if exc.status_code == 422 and exc.detail.startswith("["):
                    # Request validation (an unknown route or maturity), not an
                    # invalid flow: same wording as the in-process binding.
                    raise RuntimeError(f"flow.propose: invalid input: {exc.detail}") from exc
                raise
            status = str(view.get("status") or "proposed")
            span.set_attribute("flow.status", status)
        logger.info(
            "design_flow_proposed_via_gateway",
            status=status,
            approval_id=view.get("approvalId"),
            version_id=view.get("versionId"),
        )
        return _proposal(view)

    async def read_status(run_id: str) -> dict[str, Any]:
        with tracer.start_as_current_span("remote_flows.flow_state") as span:
            span.set_attribute("run.id", run_id)
            state: dict[str, Any] = await gateway.request("GET", f"/v1/runs/{run_id}/flow-state")
        return state

    async def start_run(
        *, flow_version_id: str, goal: str, project_id: str | None
    ) -> dict[str, Any]:
        with tracer.start_as_current_span("remote_flows.start_run") as span:
            span.set_attribute("flow.version_id", flow_version_id)
            try:
                run = await gateway.request(
                    "POST",
                    "/v1/runs",
                    json={
                        "request": {
                            "kind": "design_flow",
                            "flow_version_id": flow_version_id,
                            "goal": goal,
                            "project_id": project_id,
                        },
                        "start": True,
                    },
                )
            except GatewayRefusedError as exc:
                if exc.status_code == 409:
                    # Same wording as the in-process binding: "not approved
                    # yet" is the expected answer right after proposing.
                    raise RuntimeError(
                        f"{exc.detail} This is the expected result until a person answers "
                        "the approval -- it is not a failure."
                    ) from exc
                raise
        logger.info("design_flow_run_started_over_mcp", run_id=run["id"], via="gateway")
        return {
            "run_id": run["id"],
            "status": run["status"],
            "resource": f"metaforge://flow/run/{run['id']}",
        }

    async def compile_intent(
        *,
        intent: str,
        requirements: list[str] | None = None,
        manufacturing_context: dict[str, Any] | None = None,
        target_maturity: str | None = None,
        loads_and_use: str | None = None,
        budget: str | None = None,
        template: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "intent": intent,
            "requirements": requirements or [],
            "manufacturingContext": manufacturing_context,
            "targetMaturity": target_maturity,
            "loadsAndUse": loads_and_use,
            "budget": budget,
            "template": template,
        }
        with tracer.start_as_current_span("remote_flows.compile_intent"):
            view = await gateway.request("POST", "/v1/design-flows/intent", json=body)
        return {
            "intent": view["intent"],
            "missing_inputs": list(view.get("missingInputs", [])),
            "next_step": INTENT_NEXT_STEP,
        }

    async def read_capabilities(
        *, template: str | None = None, version_id: str | None = None, profile: str | None = None
    ) -> dict[str, Any]:
        if bool(template) == bool(version_id):
            raise RuntimeError("flow.capabilities: pass exactly one of template or version_id")
        path = (
            f"/v1/design-flows/versions/{version_id}/capabilities"
            if version_id
            else f"/v1/design-flows/{template}/capabilities"
        )
        if profile:
            path += f"?profile={profile}"
        with tracer.start_as_current_span("remote_flows.capabilities"):
            view = await gateway.request("GET", path)
        return {
            "flow_id": view.get("flowId"),
            "version_id": view.get("versionId"),
            "profile": view.get("profile"),
            **view.get("report", {}),
        }

    async def read_lifecycle(run_id: str) -> dict[str, Any]:
        with tracer.start_as_current_span("remote_flows.lifecycle") as span:
            span.set_attribute("run.id", run_id)
            view = await gateway.request("GET", f"/v1/runs/{run_id}/lifecycle")
        return {
            "run_id": view.get("runId"),
            "live": view.get("live", False),
            "limits": list(view.get("limits", [])),
            **view.get("lifecycle", {}),
            "next_step": view.get("nextStep", ""),
        }

    return RemoteFlowBindings(
        catalogue_reader=read_catalogue,
        proposer=propose,
        status_reader=read_status,
        run_starter=start_run,
        run_launcher=RemoteRunLauncher(gateway),
        intent_compiler=compile_intent,
        capability_reader=read_capabilities,
        lifecycle_reader=read_lifecycle,
    )
