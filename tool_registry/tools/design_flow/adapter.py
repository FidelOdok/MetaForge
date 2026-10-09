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
        intent_compiler: Any = None,
        capability_reader: Any = None,
        lifecycle_reader: Any = None,
        patcher: Any = None,
        gate_reader: Any = None,
        gate_decider: Any = None,
        client_tasks: Any = None,
    ) -> None:
        super().__init__(adapter_id="design_flow", version="0.1.0")
        self._catalogue_reader = catalogue_reader
        self._proposer = proposer
        self._run_status_reader = run_status_reader
        self._run_starter = run_starter
        self._intent_compiler = intent_compiler
        self._capability_reader = capability_reader
        self._lifecycle_reader = lifecycle_reader
        self._patcher = patcher
        self._gate_reader = gate_reader
        self._gate_decider = gate_decider
        self._client_tasks = client_tasks

        if catalogue_reader is not None:
            self._register_list_flows()
        if proposer is not None:
            self._register_propose()
        if run_status_reader is not None:
            self._register_status()
            self._register_run_resource()
        if run_starter is not None:
            self._register_start()
        # FORGE-539: the lifecycle surface. All read-only: none of these
        # proposes, approves or starts anything.
        if intent_compiler is not None:
            self._register_compile_intent()
        if capability_reader is not None:
            self._register_capabilities()
        if lifecycle_reader is not None:
            self._register_lifecycle()
        if patcher is not None:
            self._register_patch()
        # FORGE-582: a gate put to the person in the client's chat. Needs both
        # halves: reading the gate and recording the person's answer.
        if gate_reader is not None and gate_decider is not None:
            self._register_await_gate()
        # FORGE-581: phase tasks for client-mode runs.
        if client_tasks is not None:
            self._register_phase_tasks()

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
                    "Optional caller-proposed tailoring: if you supply 'template' "
                    "and 'operations' together, the server makes NO model call. It "
                    "applies your operations with the deterministic generator, runs the "
                    "same invariants and holds the same single approval. Operations are "
                    "drop_phase, add_deliverable (value: artifact type), set_disciplines "
                    "(value: list), set_model (value: provider:model) and declare_items "
                    "(value: list of {type, name}, e.g. two cad_model brackets; each "
                    "becomes an item with a fixed key), each on a "
                    "phase of the template with a rationale. Read flow.list for template "
                    "and phase ids. An unknown operation, template or phase, or an "
                    "invariant violation (for example dropping verification with unknown "
                    "loads), is refused with the reason. The required questions still "
                    "come from the server.\n\n"
                    "IMPORTANT (FORGE-539): 'template' WITHOUT 'operations' also skips "
                    "the server model and means 'use this template unchanged' -- no "
                    "tailoring at all. To have the server tailor a template you chose, "
                    "do not send 'template'; to keep it unchanged on purpose, send "
                    "'operations': [] and say so. Phases may also be tailored with "
                    "set_dependencies (value: list of phase ids it needs), set_condition "
                    "(value: e.g. 'route == undecided') and set_outcome (value: one "
                    "line).\n\n"
                    "A proposal carries 'intent_model' (what was understood) and "
                    "'capabilities' (whether the tools exist: READY, "
                    "READY_WITH_WARNINGS or BLOCKED, with the gaps). Report blocking "
                    "gaps to the user with the approval id."
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
                                            "declare_items",
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
                        "intelligence": {
                            "type": "string",
                            "enum": ["server", "client"],
                            "description": (
                                "Who does the phase work. 'client': each phase waits for "
                                "you to take it with phase.claim and hand it back with "
                                "phase.submit; MetaForge calls no model. Omit for the "
                                "deployment default."
                            ),
                        },
                    },
                    "required": ["flow_version_id", "goal"],
                },
            ),
            handler=self.start_run,
        )

    def _register_compile_intent(self) -> None:
        context_props = {
            "intent": {"type": "string", "description": "What the user asked for."},
            "requirements": {"type": "array", "items": {"type": "string"}},
            "manufacturing_context": {"type": "object"},
            "target_maturity": {"type": "string"},
            "loads_and_use": {"type": "string"},
            "budget": {"type": "string"},
            "template": {"type": "string", "description": "Template id, to list its deliverables."},
        }
        self.register_tool(
            manifest=ToolManifest(
                tool_id="flow.compile_intent",
                adapter_id="design_flow",
                name="Compile an intent",
                description=(
                    "Turn what the user asked for into a structured intent, BEFORE "
                    "proposing a flow: the goal and what it is about, the immediate "
                    "request versus the objective behind it, constraints sorted by kind, "
                    "measurable success criteria (directed quantities with units), "
                    "preferences, assumptions, and unknowns marked blocking or not. "
                    "Deterministic: no model call, nothing stored, and no value appears "
                    "that the user did not state. Ask the user about every blocking "
                    "unknown; never fill one in yourself."
                ),
                capability="design_flow_read",
                input_schema={
                    "type": "object",
                    "properties": context_props,
                    "required": ["intent"],
                },
            ),
            handler=self.compile_intent,
        )

    def _register_capabilities(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="flow.capabilities",
                adapter_id="design_flow",
                name="Check a flow's tool coverage",
                description=(
                    "Whether a flow can actually be run with the tools that exist and "
                    "answer right now. For every deliverable each phase requires or "
                    "expects: FULL, PARTIAL, UNAVAILABLE or UNKNOWN coverage, and a gap "
                    "register with severity (BLOCKS_STEP, DEGRADES_CONFIDENCE, "
                    "REQUIRES_USER_ACTION such as an adapter that is down or a tool not "
                    "on your profile, REQUIRES_NEW_CAPABILITY) and workarounds. Pass "
                    "exactly one of 'template' or 'version_id'; 'profile' narrows it to "
                    "the tools your connection is served. 'limits' lists what was not "
                    "checked: an unchecked input is not a clean bill of health."
                ),
                capability="design_flow_read",
                input_schema={
                    "type": "object",
                    "properties": {
                        "template": {"type": "string"},
                        "version_id": {"type": "string"},
                        "profile": {"type": "string"},
                    },
                },
            ),
            handler=self.capabilities,
        )

    def _register_lifecycle(self) -> None:
        run_schema = {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        }
        self.register_tool(
            manifest=ToolManifest(
                tool_id="flow.lifecycle",
                adapter_id="design_flow",
                name="Design-run lifecycle",
                description=(
                    "Where a design run stands, as separate answers per phase: "
                    "execution_status (did it run), eligibility (can it run now), "
                    "validity (is its result still current: STALE when an item it "
                    "recorded was superseded, POTENTIALLY_INVALID downstream of that) "
                    "and objective_status (did its gate find the objective met), plus "
                    "capability gaps, the requirement statuses and the completion "
                    "verdict. Read 'next_step' and do what it says."
                ),
                capability="design_flow_read",
                input_schema=run_schema,
            ),
            handler=self.lifecycle,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="flow.verify_completion",
                adapter_id="design_flow",
                name="Verify a run satisfied its intent",
                description=(
                    "Whether a design run actually achieved what was asked: "
                    "COMPLETED_VERIFIED only when every mandatory requirement passes with "
                    "current evidence, no result is stale, every phase's objective was "
                    "met and no blocking gap remains. A run whose phases all finished "
                    "while a requirement still fails is PARTIALLY_COMPLETED, never done. "
                    "Use this before telling the user a design is finished."
                ),
                capability="design_flow_read",
                input_schema=run_schema,
            ),
            handler=self.verify_completion,
        )

    def _register_patch(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="flow.patch",
                adapter_id="design_flow",
                name="Patch a running design flow",
                description=(
                    "Change a RUNNING design flow without starting again, re-running only "
                    "what the change touches. action='propose': pass run_id, "
                    "expected_content_hash (the run's flowContentHash from flow.status, so "
                    "a patch written against an older flow is refused), reason, and "
                    "operations (the flow.propose set) and/or invalidate (phases whose "
                    "results the new information makes wrong, e.g. design after a payload "
                    "change). It returns what will re-run, what is kept, and an approval id "
                    "-- the patch is HELD for a person and nothing changes yet; you cannot "
                    "approve it. action='apply': pass run_id and version_id once a person "
                    "has approved it; refused (expected, not a fault) until then, or if the "
                    "run's flow changed since."
                ),
                capability="design_flow_write",
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["propose", "apply"]},
                        "run_id": {"type": "string"},
                        "expected_content_hash": {"type": "string"},
                        "reason": {"type": "string"},
                        "operations": {"type": "array", "items": {"type": "object"}},
                        "invalidate": {"type": "array", "items": {"type": "string"}},
                        "version_id": {"type": "string"},
                    },
                    "required": ["action", "run_id"],
                },
            ),
            handler=self.patch,
        )

    def _register_await_gate(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="flow.await_gate",
                adapter_id="design_flow",
                name="Wait for a design-run gate and ask the person",
                description=(
                    "Wait until a design run reaches its next gate, then ask the PERSON in "
                    "this chat to decide it (approve, retry, rework or reject). The person "
                    "answers in a prompt this client shows them; you do not answer it and "
                    "cannot decide for them. Only the decisions the gate allows are "
                    "offered: approve is not offered when the gate's checks failed. If "
                    "the person dismisses the prompt, or this client cannot show one, the "
                    "gate stays open for them in the dashboard or `forge approvals`. "
                    "Returns no_gate_yet if none opened within wait_seconds; call again."
                ),
                capability="design_flow_write",
                input_schema={
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "wait_seconds": {
                            "type": "number",
                            "description": "How long to wait for a gate (default 120, max 1800).",
                        },
                        "answer_seconds": {
                            "type": "number",
                            "description": "How long the person has to answer (default 300).",
                        },
                    },
                    "required": ["run_id"],
                },
            ),
            handler=self.await_gate,
        )

    def _register_phase_tasks(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="phase.list_tasks",
                adapter_id="design_flow",
                name="List phases waiting for this client",
                description=(
                    "Phases of client-mode design runs that are waiting for a client to do "
                    "them. Filter by project_id or run_id. Take one with phase.claim."
                ),
                capability="design_flow_read",
                input_schema={
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "run_id": {"type": "string"},
                    },
                },
            ),
            handler=self.list_tasks,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="phase.claim",
                adapter_id="design_flow",
                name="Take a phase task",
                description=(
                    "Take a waiting phase of a client-mode design run and get its brief: "
                    "goal, objective, required deliverables and slots, flow context and "
                    "any retry feedback. Do the work with MetaForge tools, recording each "
                    "required deliverable in the twin under the run's project, then call "
                    "phase.submit."
                ),
                capability="design_flow_write",
                input_schema={
                    "type": "object",
                    "properties": {"task_id": {"type": "string"}},
                    "required": ["task_id"],
                },
            ),
            handler=self.claim_task,
        )
        self.register_tool(
            manifest=ToolManifest(
                tool_id="phase.submit",
                adapter_id="design_flow",
                name="Hand a phase task back",
                description=(
                    "Finish a phase task you claimed: a short summary of what you did and "
                    "the ids of what you recorded. The run's gate then checks the twin, not "
                    "the summary. Afterwards call flow.await_gate so the person can decide "
                    "the gate in this chat."
                ),
                capability="design_flow_write",
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "summary": {"type": "string"},
                        "artifacts": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["task_id", "summary"],
                },
            ),
            handler=self.submit_task,
        )

    # ── handlers ─────────────────────────────────────────────────────────

    async def await_gate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from tool_registry.tools.design_flow.gates import await_gate

        run_id = str(arguments.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("flow.await_gate: 'run_id' is required")
        return await await_gate(
            run_id,
            reader=self._gate_reader,
            decider=self._gate_decider,
            wait_seconds=arguments.get("wait_seconds"),
            answer_seconds=arguments.get("answer_seconds"),
        )

    @staticmethod
    def _client_name() -> str:
        from mcp_core.context import current_context

        actor = current_context().actor_id
        return actor if actor and actor != "system:unattributed" else "unknown"

    async def list_tasks(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("phase.list_tasks"):
            result: dict[str, Any] = await self._client_tasks.list_tasks(
                project_id=arguments.get("project_id") or None,
                run_id=arguments.get("run_id") or None,
            )
        return result

    async def claim_task(self, arguments: dict[str, Any]) -> dict[str, Any]:
        task_id = str(arguments.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("phase.claim: 'task_id' is required")
        with tracer.start_as_current_span("phase.claim") as span:
            span.set_attribute("task.id", task_id)
            result: dict[str, Any] = await self._client_tasks.claim(task_id, self._client_name())
        # FORGE-584: remember which task this session took. Only a claim: the
        # server grants scoped writes for it only when the owner turned that
        # on and the gateway confirms the claim on each call.
        from mcp_core.context import bind_current_session_task

        bound = bind_current_session_task(
            task_id,
            str(result.get("run_id") or ""),
            str(result.get("phase_id") or ""),
            result.get("project_id"),
        )
        logger.info("phase_task_claimed_over_mcp", task_id=task_id, session_bound=bound)
        return {**result, "session_bound": bound}

    async def submit_task(self, arguments: dict[str, Any]) -> dict[str, Any]:
        task_id = str(arguments.get("task_id") or "").strip()
        summary = str(arguments.get("summary") or "").strip()
        if not task_id:
            raise ValueError("phase.submit: 'task_id' is required")
        if not summary:
            raise ValueError("phase.submit: 'summary' is required")
        with tracer.start_as_current_span("phase.submit") as span:
            span.set_attribute("task.id", task_id)
            result: dict[str, Any] = await self._client_tasks.submit(
                task_id,
                self._client_name(),
                summary,
                [str(a) for a in arguments.get("artifacts") or []],
            )
        # FORGE-584: the grant ends with the task.
        from mcp_core.context import clear_session_task, current_context

        ctx = current_context()
        if ctx.session_is_stable:
            clear_session_task(ctx.session_id, task_id)
        logger.info("phase_task_submitted_over_mcp", task_id=task_id)
        return {
            **result,
            "next_step": (
                "Call flow.await_gate with this run_id so the person can decide the gate here."
            ),
        }

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
            extra: dict[str, Any] = {}
            if arguments.get("intelligence"):
                extra["intelligence"] = str(arguments["intelligence"])
            result: dict[str, Any] = await self._run_starter(
                flow_version_id=version_id,
                goal=goal,
                project_id=arguments.get("project_id"),
                **extra,
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

    async def compile_intent(self, arguments: dict[str, Any]) -> dict[str, Any]:
        intent = str(arguments.get("intent") or "").strip()
        if not intent:
            raise ValueError("flow.compile_intent: 'intent' is required")
        with tracer.start_as_current_span("flow.compile_intent"):
            result: dict[str, Any] = await self._intent_compiler(
                intent=intent,
                requirements=list(arguments.get("requirements") or []),
                manufacturing_context=arguments.get("manufacturing_context"),
                target_maturity=arguments.get("target_maturity"),
                loads_and_use=arguments.get("loads_and_use"),
                budget=arguments.get("budget"),
                template=arguments.get("template"),
            )
        return result

    async def capabilities(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("flow.capabilities") as span:
            result: dict[str, Any] = await self._capability_reader(
                template=arguments.get("template") or None,
                version_id=arguments.get("version_id") or None,
                profile=arguments.get("profile") or None,
            )
            span.set_attribute("flow.capability_status", str(result.get("status")))
        return result

    async def lifecycle(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = str(arguments.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("flow.lifecycle: 'run_id' is required")
        with tracer.start_as_current_span("flow.lifecycle"):
            result: dict[str, Any] = await self._lifecycle_reader(run_id)
        return result

    async def patch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "").strip()
        run_id = str(arguments.get("run_id") or "").strip()
        if action not in ("propose", "apply"):
            raise ValueError("flow.patch: 'action' must be 'propose' or 'apply'")
        if not run_id:
            raise ValueError("flow.patch: 'run_id' is required")
        with tracer.start_as_current_span("flow.patch") as span:
            span.set_attribute("flow.patch_action", action)
            if action == "propose":
                if not str(arguments.get("expected_content_hash") or "").strip():
                    raise ValueError("flow.patch: propose needs 'expected_content_hash'")
                if not str(arguments.get("reason") or "").strip():
                    raise ValueError("flow.patch: propose needs a 'reason'")
                result: dict[str, Any] = await self._patcher(
                    action="propose",
                    run_id=run_id,
                    expected_content_hash=str(arguments["expected_content_hash"]),
                    reason=str(arguments["reason"]),
                    operations=list(arguments.get("operations") or []),
                    invalidate=[str(p) for p in arguments.get("invalidate") or []],
                )
            else:
                version_id = str(arguments.get("version_id") or "").strip()
                if not version_id:
                    raise ValueError("flow.patch: apply needs 'version_id'")
                result = await self._patcher(action="apply", run_id=run_id, version_id=version_id)
        logger.info("flow_patch_over_mcp", action=action, run_id=run_id)
        return result

    async def verify_completion(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = str(arguments.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("flow.verify_completion: 'run_id' is required")
        with tracer.start_as_current_span("flow.verify_completion") as span:
            view: dict[str, Any] = await self._lifecycle_reader(run_id)
            completion = view.get("completion") or {}
            span.set_attribute("flow.completion", str(completion.get("classification")))
        logger.info(
            "flow_completion_verified_over_mcp",
            run_id=run_id,
            classification=completion.get("classification"),
        )
        return {
            "run_id": run_id,
            "completion": completion,
            "requirements": view.get("requirements", []),
            "stale_item_keys": view.get("stale_item_keys", []),
            "limits": view.get("limits", []),
            "next_step": view.get("next_step", ""),
        }


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
