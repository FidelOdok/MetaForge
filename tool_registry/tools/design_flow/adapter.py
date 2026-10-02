"""Design flows through the harness plugins (FORGE-400).

Flows have been dashboard-only: you could propose, edit, approve and watch one
from the canvas, and an agent in Claude Code or Codex could not see that any
of it existed. This exposes them as MCP tools and resources.

**The rule that shapes the whole surface: the agent has no tool that approves
its own call.** ``flow.propose`` is a held write — it produces a proposal and
an approval id, and stops. There is no ``flow.approve``. An agent that could
both propose a flow and approve it has an approval step in name only, and the
name is worse than nothing because it appears in the audit trail.

Approving happens where a human is: the dashboard queue, or inline
elicitation in clients that support it (FORGE-360). Both route through the
same approval ledger, and the approver's identity comes from that record
rather than from anything the agent said (FORGE-393).

Layering: this takes injected callables the way the twin adapter takes its
recorders. The generator and the version store live in the gateway; nothing
here imports upward.
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import (
    ResourceManifestEntry,
    ResourceNotFoundError,
    ToolManifest,
)
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.design_flow")

__all__ = ["DesignFlowServer"]

_RESOURCE_PREFIX = "metaforge://flow/"


class DesignFlowServer(McpToolServer):
    """Flow catalogue, proposals and run status over MCP."""

    def __init__(
        self,
        *,
        catalogue_reader: Any = None,
        proposer: Any = None,
        run_status_reader: Any = None,
        run_starter: Any = None,
    ) -> None:
        super().__init__(adapter_id="design_flow", version="0.1.0")
        self._catalogue_reader = catalogue_reader
        self._proposer = proposer
        self._run_status_reader = run_status_reader
        self._run_starter = run_starter

        if catalogue_reader is not None:
            self._register_list_flows()
        if proposer is not None:
            self._register_propose()
        if run_status_reader is not None:
            self._register_status()
            self._register_run_resource()
        if run_starter is not None:
            self._register_start()

    # ── tools ────────────────────────────────────────────────────────────

    def _register_list_flows(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="flow.list",
                adapter_id="design_flow",
                name="List design flows",
                description=(
                    "Every launchable design flow, as the gateway will run it: phases, "
                    "gates, required deliverables and disciplines, plus whether each "
                    "flow passes the server-enforced invariants. Read this before "
                    "proposing — a flow tailored from a template you invented is a "
                    "flow that will be refused."
                ),
                capability="design_flow_read",
                input_schema={"type": "object", "properties": {}},
            ),
            handler=self.list_flows,
        )

    def _register_propose(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="flow.propose",
                adapter_id="design_flow",
                name="Propose a tailored design flow",
                description=(
                    "Tailor a flow template to a project's intent and HOLD IT FOR A "
                    "HUMAN. This does not start anything. It returns a proposal, the "
                    "changes made with their rationale, and an approval id; a person "
                    "answers that approval in the dashboard (or inline, if this client "
                    "supports elicitation), and only then can a run start.\n\n"
                    "You cannot approve your own proposal and there is no tool that "
                    "would let you. Report the proposal and the approval id to the "
                    "user and stop; do not poll for an approval you were not given.\n\n"
                    "If manufacturing_context.route, target_maturity or loads_and_use "
                    "is missing, nothing is proposed: the result has status "
                    "'needs_input' and a list of questions. Ask the USER those "
                    "questions -- never answer them yourself -- and call again with "
                    "the answers. 'undecided' (route) and 'unknown' (loads) are "
                    "valid answers.\n\n"
                    "Optional caller-proposed tailoring: if you supply 'operations' "
                    "(and optionally 'template'), the server makes NO model call. It "
                    "applies your operations with the deterministic generator, runs the "
                    "same invariants and holds the same single approval. Operations are "
                    "drop_phase, add_deliverable (value: artifact type), set_disciplines "
                    "(value: list) and set_model (value: provider:model), each on a "
                    "phase of the template with a rationale. Read flow.list for template "
                    "and phase ids. An unknown operation, template or phase, or an "
                    "invariant violation (for example dropping verification with unknown "
                    "loads), is refused with the reason. The required questions still "
                    "come from the server."
                ),
                capability="design_flow_write",
                input_schema={
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "description": "What the product is and what it must do.",
                        },
                        "project_id": {"type": "string"},
                        "requirements": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Known requirements, if any are recorded yet.",
                        },
                        "manufacturing_context": {
                            "type": "object",
                            "description": "What the product can actually be made with.",
                            "properties": {
                                "route": {
                                    "type": "string",
                                    "enum": ["in_house", "vendor", "undecided"],
                                },
                                "processes": {"type": "array", "items": {"type": "string"}},
                                "machines": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": (
                                        "Free-form capability descriptions, e.g. "
                                        "'table saw, 600 mm rip capacity'."
                                    ),
                                },
                                "stock_materials": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "production_quantity": {"type": "integer", "minimum": 1},
                            },
                        },
                        "target_maturity": {
                            "type": "string",
                            "enum": [
                                "concept",
                                "sim_validated",
                                "physically_validated",
                                "released",
                            ],
                        },
                        "loads_and_use": {
                            "type": "string",
                            "description": (
                                "What it carries or endures and how it is used. "
                                "'unknown' is allowed and keeps verification in the flow."
                            ),
                        },
                        "budget": {"type": "string"},
                        "template": {
                            "type": "string",
                            "description": "Template id to tailor (see flow.list). Optional.",
                        },
                        "operations": {
                            "type": "array",
                            "description": (
                                "Caller-proposed tailoring. Supplying it skips the "
                                "server-side generator model."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "op": {
                                        "type": "string",
                                        "enum": [
                                            "drop_phase",
                                            "add_deliverable",
                                            "set_disciplines",
                                            "set_model",
                                        ],
                                    },
                                    "phase": {"type": "string"},
                                    "value": {},
                                    "rationale": {"type": "string"},
                                },
                                "required": ["op", "phase", "rationale"],
                            },
                        },
                        "caller": {
                            "type": "object",
                            "description": "Provenance of the operations.",
                            "properties": {
                                "client": {"type": "string"},
                                "model": {"type": "string"},
                            },
                        },
                    },
                    "required": ["intent"],
                },
            ),
            handler=self.propose,
        )

    def _register_status(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="flow.status",
                adapter_id="design_flow",
                name="Design-run status",
                description=(
                    "Phase-by-phase status of a design run. A phase whose state could "
                    "not be read reports 'unknown', which is NOT the same as pending — "
                    "report it as unknown rather than as 'not started yet'."
                ),
                capability="design_flow_read",
                input_schema={
                    "type": "object",
                    "properties": {"run_id": {"type": "string"}},
                    "required": ["run_id"],
                },
            ),
            handler=self.status,
        )

    def _register_start(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="flow.start_run",
                adapter_id="design_flow",
                name="Start a run on an approved flow",
                description=(
                    "Start a design run from a flow version a human has APPROVED. "
                    "Refused if the version is not approved — which is the normal "
                    "outcome immediately after flow.propose, and means the person has "
                    "not answered yet, not that anything is broken."
                ),
                capability="design_flow_write",
                input_schema={
                    "type": "object",
                    "properties": {
                        "flow_version_id": {"type": "string"},
                        "goal": {"type": "string"},
                        "project_id": {"type": "string"},
                    },
                    "required": ["flow_version_id", "goal"],
                },
            ),
            handler=self.start_run,
        )

    # ── handlers ─────────────────────────────────────────────────────────

    async def list_flows(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("flow.list"):
            result: dict[str, Any] = await self._catalogue_reader()
        return result

    async def propose(self, arguments: dict[str, Any]) -> dict[str, Any]:
        intent = str(arguments.get("intent") or "").strip()
        if not intent:
            raise ValueError("flow.propose: 'intent' is required")
        with tracer.start_as_current_span("flow.propose") as span:
            span.set_attribute("flow.intent_length", len(intent))
            result: dict[str, Any] = await self._proposer(
                intent=intent,
                project_id=arguments.get("project_id"),
                requirements=list(arguments.get("requirements") or []),
                manufacturing_context=arguments.get("manufacturing_context"),
                target_maturity=arguments.get("target_maturity"),
                loads_and_use=arguments.get("loads_and_use"),
                budget=arguments.get("budget"),
                template=arguments.get("template"),
                operations=arguments.get("operations"),
                caller=arguments.get("caller"),
            )
            span.set_attribute("flow.status", str(result.get("status", "proposed")))
        logger.info(
            "flow_proposed_over_mcp",
            status=result.get("status", "proposed"),
            approval_id=result.get("approval_id"),
            version_id=result.get("version_id"),
        )
        return result

    async def status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = str(arguments.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("flow.status: 'run_id' is required")
        with tracer.start_as_current_span("flow.status"):
            result: dict[str, Any] = await self._run_status_reader(run_id)
        return result

    async def start_run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        version_id = str(arguments.get("flow_version_id") or "").strip()
        goal = str(arguments.get("goal") or "").strip()
        if not version_id:
            raise ValueError("flow.start_run: 'flow_version_id' is required")
        if not goal:
            raise ValueError("flow.start_run: 'goal' is required")
        with tracer.start_as_current_span("flow.start_run") as span:
            span.set_attribute("flow.version_id", version_id)
            result: dict[str, Any] = await self._run_starter(
                flow_version_id=version_id,
                goal=goal,
                project_id=arguments.get("project_id"),
            )
        return result

    # ── resources ────────────────────────────────────────────────────────

    def _register_run_resource(self) -> None:
        self.register_resource(
            manifest=ResourceManifestEntry(
                uri_template=f"{_RESOURCE_PREFIX}run/{{run_id}}",
                adapter_id="design_flow",
                name="Design run",
                description=(
                    "A design run's phases, gates and per-phase activity, as markdown. "
                    "Re-read it to follow a run; a phase reading 'unknown' means its "
                    "state could not be read, not that it has not started."
                ),
                mime_type="text/markdown",
            ),
            reader=self._read_run_resource,
            matcher=lambda uri: uri.startswith(f"{_RESOURCE_PREFIX}run/"),
        )

    async def _read_run_resource(self, uri: str) -> list[dict[str, Any]]:
        run_id = uri.removeprefix(f"{_RESOURCE_PREFIX}run/").strip("/")
        if not run_id:
            raise ResourceNotFoundError(uri)
        state = await self._run_status_reader(run_id)
        # A list, not a dict: see the note in metaforge/mcp/health_adapter.py.
        # FORGE-400 shipped this returning a bare dict, so reading a run
        # resource answered with the three key names and no run.
        return [
            {
                "uri": uri,
                "mimeType": "text/markdown",
                "text": render_run_markdown(state),
            }
        ]


def render_run_markdown(state: dict[str, Any]) -> str:
    """A design run as text, for a client with no canvas.

    Deep-linked back to the dashboard rather than described as if the text
    were the whole story: the graph view shows things this cannot (FORGE-396),
    and an agent that never mentions it leaves the user reading a summary when
    a better view was one click away.
    """
    lines: list[str] = [f"# Design run `{state.get('runId', '?')}`", ""]
    lines.append(f"**Status**: {state.get('status', 'unknown')}")
    if state.get("flowVersionId") or state.get("flowContentHash"):
        lines.append(
            f"**Flow**: {state.get('flow') or '?'} "
            f"{state.get('flowVersion') or ''} "
            f"(version {state.get('flowVersionId') or 'n/a'}, "
            f"hash {str(state.get('flowContentHash') or 'n/a')[:12]})"
        )
    if state.get("awaitingGate"):
        lines.append(f"**Waiting on gate**: {state['awaitingGate']}")
    if state.get("error"):
        lines.append(f"**Error**: {state['error']}")
    if not state.get("live", True):
        lines.append("")
        lines.append(
            f"> The engine could not be queried, so phase status below is **unknown**, "
            f"not idle. {state.get('detail', '')}"
        )
    lines.append("")

    lines.append("## Phases")
    lines.append("")
    for phase in state.get("phases", []):
        mark = {
            "passed": "x",
            "running": ">",
            "awaiting_gate": "!",
            "failed": "!",
            "unknown": "?",
        }.get(str(phase.get("status")), " ")
        lines.append(f"- [{mark}] **{phase.get('title')}** — {phase.get('status')}")
        if phase.get("gate"):
            lines.append(f"      gate: {phase['gate']}")
        if phase.get("summary"):
            lines.append(f"      {phase['summary']}")
        if phase.get("artifacts"):
            lines.append(f"      committed: {', '.join(phase['artifacts'])}")
    if not state.get("phases"):
        lines.append("_No phases — this run is not a design flow._")

    events = state.get("events") or []
    if events:
        lines.append("")
        lines.append("## Recent activity")
        lines.append("")
        for event in events[-12:]:
            detail = f" — {event['detail']}" if event.get("detail") else ""
            where = f" ({event['phase']})" if event.get("phase") else ""
            lines.append(f"- `{event.get('at', '')}` {event.get('event')}{where}{detail}")
    return "\n".join(lines)
