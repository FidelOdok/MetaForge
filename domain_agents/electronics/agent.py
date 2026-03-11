"""Electronics engineering domain agent.

Orchestrates skill execution for electronics design validation:
ERC checking, DRC checking, and power budget analysis.

Supports two modes:
- **LLM mode**: PydanticAI Agent() with LLM-driven tool selection
- **Hardcoded mode**: Deterministic dispatch by task_type (fallback)
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from domain_agents.base_agent import (
    AgentDependencies,
    AgentResult,
    get_llm_model,
    is_llm_available,
)
from domain_agents.electronics.skills.run_drc.handler import RunDrcHandler
from domain_agents.electronics.skills.run_drc.schema import RunDrcInput
from domain_agents.electronics.skills.run_erc.handler import RunErcHandler
from domain_agents.electronics.skills.run_erc.schema import RunErcInput
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.electronics")


# ---------------------------------------------------------------------------
# Domain-specific result model for PydanticAI structured output
# ---------------------------------------------------------------------------


class ElectronicsResult(AgentResult):
    """Structured output from the electronics agent's PydanticAI run."""

    overall_passed: bool = Field(
        default=True,
        description="Whether all electronics checks passed",
    )
    total_erc_errors: int = Field(
        default=0,
        description="Total ERC errors found",
    )
    total_drc_errors: int = Field(
        default=0,
        description="Total DRC errors found",
    )


# ---------------------------------------------------------------------------
# Backward-compatible request/result models
# ---------------------------------------------------------------------------


class TaskRequest(BaseModel):
    """A request for the electronics agent to perform a task."""

    task_type: str  # "run_erc", "run_drc", "check_power_budget", "full_validation"
    artifact_id: UUID
    parameters: dict[str, Any] = {}
    branch: str = "main"


class TaskResult(BaseModel):
    """Result of an electronics agent task."""

    task_type: str
    artifact_id: UUID
    success: bool
    skill_results: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# PydanticAI agent factory (lazy, created once per process)
# ---------------------------------------------------------------------------

_pydantic_agent: Any | None = None

ELECTRONICS_SYSTEM_PROMPT = """\
You are an expert electronics engineer working within the MetaForge design \
validation platform. You have deep knowledge of PCB design, schematic review, \
power distribution networks, signal integrity, and EMC compliance.

You have access to the following tools:

- **run_erc**: Run Electrical Rules Check on a KiCad schematic file. Checks \
for unconnected pins, missing power flags, and other schematic errors.
- **run_drc**: Run Design Rules Check on a KiCad PCB layout file. Checks for \
clearance violations, unconnected nets, and manufacturing rule violations.
- **check_power_budget**: Analyze power consumption of all components against \
available power supply capacity (not yet implemented).

Given a user request, determine which tools to call and in what order. \
Analyze the results and provide a clear assessment with pass/fail status \
and recommendations for fixing any violations.

Always validate that required parameters are available before calling a tool.
"""


def _get_or_create_pydantic_agent() -> Any:
    """Lazily create the PydanticAI Agent for electronics engineering."""
    global _pydantic_agent
    if _pydantic_agent is not None:
        return _pydantic_agent

    try:
        from pydantic_ai import Agent, RunContext
    except ImportError:
        logger.warning("pydantic_ai_not_installed")
        return None

    model = get_llm_model()
    if model is None:
        return None

    agent = Agent(
        model,
        system_prompt=ELECTRONICS_SYSTEM_PROMPT,
        result_type=ElectronicsResult,
        deps_type=AgentDependencies,
    )

    # -- Tool: run_erc --------------------------------------------------------

    @agent.tool
    async def run_erc(
        ctx: RunContext[AgentDependencies],
        schematic_file: str,
        severity_filter: str = "all",
    ) -> dict[str, Any]:
        """Run Electrical Rules Check on a KiCad schematic.

        Args:
            schematic_file: Path to the KiCad schematic file (.kicad_sch).
            severity_filter: Filter by severity ('all', 'error', 'warning').
        """
        skill_ctx = SkillContext(
            twin=ctx.deps.twin,
            mcp=ctx.deps.mcp_bridge,
            logger=logger,
            session_id=UUID(ctx.deps.session_id),
            branch=ctx.deps.branch,
        )

        skill_input = RunErcInput(
            artifact_id=UUID("00000000-0000-0000-0000-000000000000"),
            schematic_file=schematic_file,
            severity_filter=severity_filter,
        )

        handler = RunErcHandler(skill_ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return {"skill": "run_erc", "success": False, "errors": result.errors}

        output = result.data
        return {
            "skill": "run_erc",
            "success": True,
            "passed": output.passed,
            "total_violations": output.total_violations,
            "total_errors": output.total_errors,
            "total_warnings": output.total_warnings,
            "summary": output.summary,
        }

    # -- Tool: run_drc --------------------------------------------------------

    @agent.tool
    async def run_drc(
        ctx: RunContext[AgentDependencies],
        pcb_file: str,
        severity_filter: str = "all",
    ) -> dict[str, Any]:
        """Run Design Rules Check on a KiCad PCB layout.

        Args:
            pcb_file: Path to the KiCad PCB file (.kicad_pcb).
            severity_filter: Filter by severity ('all', 'error', 'warning').
        """
        skill_ctx = SkillContext(
            twin=ctx.deps.twin,
            mcp=ctx.deps.mcp_bridge,
            logger=logger,
            session_id=UUID(ctx.deps.session_id),
            branch=ctx.deps.branch,
        )

        skill_input = RunDrcInput(
            artifact_id=UUID("00000000-0000-0000-0000-000000000000"),
            pcb_file=pcb_file,
            severity_filter=severity_filter,
        )

        handler = RunDrcHandler(skill_ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return {"skill": "run_drc", "success": False, "errors": result.errors}

        output = result.data
        return {
            "skill": "run_drc",
            "success": True,
            "passed": output.passed,
            "total_violations": output.total_violations,
            "total_errors": output.total_errors,
            "total_warnings": output.total_warnings,
            "summary": output.summary,
        }

    # -- Tool: check_power_budget ---------------------------------------------

    @agent.tool
    async def check_power_budget(
        ctx: RunContext[AgentDependencies],
        components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Analyze power budget for the design.

        Args:
            components: List of components with power ratings
                (each has 'name' and 'power_mw' fields).
        """
        return {
            "skill": "check_power_budget",
            "status": "not_implemented",
            "num_components": len(components),
            "error": "check_power_budget skill is not yet implemented",
        }

    _pydantic_agent = agent
    return agent


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------


class ElectronicsAgent:
    """Electronics engineering domain agent.

    Orchestrates skill execution for electronics design validation:
    ERC checking, DRC checking, power budget analysis.

    Supports two execution modes:
    - PydanticAI mode: LLM-driven tool selection (when METAFORGE_LLM_PROVIDER is set)
    - Hardcoded mode: Deterministic dispatch by task_type (fallback)

    The agent is stateless -- all state lives in the Digital Twin.

    Usage:
        twin = InMemoryTwinAPI.create()
        mcp = InMemoryMcpBridge()
        agent = ElectronicsAgent(twin=twin, mcp=mcp)
        result = await agent.run_task(TaskRequest(
            task_type="run_erc",
            artifact_id=artifact.id,
            parameters={"schematic_file": "eda/kicad/main.kicad_sch"},
        ))
    """

    SUPPORTED_TASKS = {"run_erc", "run_drc", "check_power_budget", "full_validation"}

    def __init__(
        self,
        twin: Any,  # TwinAPI -- avoid circular import at module level
        mcp: McpBridge,
        session_id: UUID | None = None,
    ) -> None:
        self.twin = twin
        self.mcp = mcp
        self.session_id = session_id or uuid4()
        self.logger = logger.bind(agent="electronics", session_id=str(self.session_id))

    async def run_task(self, request: TaskRequest) -> TaskResult:
        """Execute an electronics engineering task.

        If an LLM is configured, attempts PydanticAI-driven execution.
        Falls back to hardcoded dispatch on LLM unavailability or error.
        """
        with tracer.start_as_current_span("agent.execute") as span:
            span.set_attribute("agent.code", "electronics")
            span.set_attribute("session.id", str(self.session_id))
            span.set_attribute("task.type", request.task_type)

            self.logger.info(
                "Running task",
                task_type=request.task_type,
                artifact_id=str(request.artifact_id),
            )

            # Try PydanticAI path if LLM is available
            if is_llm_available() and request.task_type in self.SUPPORTED_TASKS:
                try:
                    result = await self._run_with_llm(request)
                    span.set_attribute("agent.mode", "llm")
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    self.logger.warning(
                        "LLM execution failed, falling back to hardcoded dispatch",
                        error=str(exc),
                    )

            # Hardcoded dispatch (fallback)
            span.set_attribute("agent.mode", "hardcoded")
            return await self._run_hardcoded(request)

    async def _run_with_llm(self, request: TaskRequest) -> TaskResult:
        """Execute a task using PydanticAI agent with LLM reasoning."""
        agent = _get_or_create_pydantic_agent()
        if agent is None:
            raise RuntimeError("PydanticAI agent could not be created")

        # Verify artifact exists first
        artifact = await self.twin.get_artifact(request.artifact_id, branch=request.branch)
        if artifact is None:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=[f"Artifact {request.artifact_id} not found on branch '{request.branch}'"],
            )

        deps = AgentDependencies(
            twin=self.twin,
            mcp_bridge=self.mcp,
            session_id=str(self.session_id),
            branch=request.branch,
        )

        prompt = self._build_prompt(request)

        t0 = time.monotonic()
        result = await agent.run(prompt, deps=deps)
        elapsed = time.monotonic() - t0

        self.logger.info(
            "LLM execution completed",
            task_type=request.task_type,
            elapsed_s=round(elapsed, 3),
        )

        electronics_result: ElectronicsResult = result.data

        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=electronics_result.overall_passed,
            skill_results=electronics_result.tool_calls
            if electronics_result.tool_calls
            else [electronics_result.analysis],
            warnings=(
                electronics_result.recommendations if not electronics_result.overall_passed else []
            ),
        )

    def _build_prompt(self, request: TaskRequest) -> str:
        """Build a natural language prompt from a structured TaskRequest."""
        parts = [f"Perform a '{request.task_type}' task on artifact {request.artifact_id}."]
        if request.parameters:
            parts.append(f"Parameters: {request.parameters}")
        return " ".join(parts)

    # --- Hardcoded dispatch (original implementation) ---

    async def _run_hardcoded(self, request: TaskRequest) -> TaskResult:
        """Original hardcoded dispatch path."""
        if request.task_type not in self.SUPPORTED_TASKS:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=[
                    f"Unsupported task type: {request.task_type}. "
                    f"Supported: {', '.join(sorted(self.SUPPORTED_TASKS))}"
                ],
            )

        # Verify artifact exists
        artifact = await self.twin.get_artifact(request.artifact_id, branch=request.branch)
        if artifact is None:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=[f"Artifact {request.artifact_id} not found on branch '{request.branch}'"],
            )

        # Route to handler
        handler = self._get_handler(request.task_type)
        return await handler(request)

    def _get_handler(
        self, task_type: str
    ) -> Callable[[TaskRequest], Coroutine[Any, Any, TaskResult]]:
        """Return the handler coroutine function for the given task type."""
        handlers: dict[str, Callable[[TaskRequest], Coroutine[Any, Any, TaskResult]]] = {
            "run_erc": self._run_erc,
            "run_drc": self._run_drc,
            "check_power_budget": self._run_check_power_budget,
            "full_validation": self._run_full_validation,
        }
        return handlers[task_type]

    async def _run_erc(self, request: TaskRequest) -> TaskResult:
        """Run Electrical Rules Check on a schematic file."""
        schematic_file: str = request.parameters.get("schematic_file", "")
        if not schematic_file:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: schematic_file"],
            )

        self.logger.info("ERC requested", schematic_file=schematic_file)

        ctx = self._create_skill_context(request.branch)
        skill_input = RunErcInput(
            artifact_id=request.artifact_id,
            schematic_file=schematic_file,
            severity_filter=request.parameters.get("severity_filter", "all"),
        )

        handler = RunErcHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=output.passed,
            skill_results=[
                {
                    "skill": "run_erc",
                    "passed": output.passed,
                    "total_violations": output.total_violations,
                    "total_errors": output.total_errors,
                    "total_warnings": output.total_warnings,
                    "summary": output.summary,
                    "schematic_file": output.schematic_file,
                }
            ],
            warnings=([output.summary] if output.total_warnings > 0 else []),
        )

    async def _run_drc(self, request: TaskRequest) -> TaskResult:
        """Run Design Rules Check on a PCB layout file."""
        pcb_file: str = request.parameters.get("pcb_file", "")
        if not pcb_file:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: pcb_file"],
            )

        self.logger.info("DRC requested", pcb_file=pcb_file)

        ctx = self._create_skill_context(request.branch)
        skill_input = RunDrcInput(
            artifact_id=request.artifact_id,
            pcb_file=pcb_file,
            severity_filter=request.parameters.get("severity_filter", "all"),
        )

        handler = RunDrcHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=result.errors,
            )

        output = result.data
        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=output.passed,
            skill_results=[
                {
                    "skill": "run_drc",
                    "passed": output.passed,
                    "total_violations": output.total_violations,
                    "total_errors": output.total_errors,
                    "total_warnings": output.total_warnings,
                    "summary": output.summary,
                    "pcb_file": output.pcb_file,
                }
            ],
            warnings=([output.summary] if output.total_warnings > 0 else []),
        )

    async def _run_check_power_budget(self, request: TaskRequest) -> TaskResult:
        """Check power budget against component power ratings."""
        components: list[dict[str, Any]] = request.parameters.get("components", [])
        if not components:
            return TaskResult(
                task_type=request.task_type,
                artifact_id=request.artifact_id,
                success=False,
                errors=["Missing required parameter: components"],
            )

        self.logger.info("Power budget check requested", num_components=len(components))

        return TaskResult(
            task_type=request.task_type,
            artifact_id=request.artifact_id,
            success=False,
            errors=["check_power_budget skill is not yet implemented"],
            skill_results=[
                {
                    "skill": "check_power_budget",
                    "status": "not_implemented",
                    "num_components": len(components),
                }
            ],
        )

    async def _run_full_validation(self, request: TaskRequest) -> TaskResult:
        """Run full electronics validation (ERC + DRC + power budget)."""
        all_results: list[dict[str, Any]] = []
        all_errors: list[str] = []
        all_warnings: list[str] = []
        overall_success = True
        checks_run = 0

        # Run ERC if schematic_file is provided
        if request.parameters.get("schematic_file"):
            erc_result = await self._run_erc(request)
            all_results.extend(erc_result.skill_results)
            all_errors.extend(erc_result.errors)
            all_warnings.extend(erc_result.warnings)
            if not erc_result.success:
                overall_success = False
            checks_run += 1

        # Run DRC if pcb_file is provided
        if request.parameters.get("pcb_file"):
            drc_result = await self._run_drc(request)
            all_results.extend(drc_result.skill_results)
            all_errors.extend(drc_result.errors)
            all_warnings.extend(drc_result.warnings)
            if not drc_result.success:
                overall_success = False
            checks_run += 1

        # Run power budget check if components are provided
        if request.parameters.get("components"):
            power_result = await self._run_check_power_budget(request)
            all_results.extend(power_result.skill_results)
            all_errors.extend(power_result.errors)
            all_warnings.extend(power_result.warnings)
            if not power_result.success:
                overall_success = False
            checks_run += 1

        # If no checks were run, report an error
        if checks_run == 0:
            return TaskResult(
                task_type="full_validation",
                artifact_id=request.artifact_id,
                success=False,
                errors=[
                    "No validation checks could be run. "
                    "Provide at least one of: schematic_file, pcb_file, components"
                ],
            )

        return TaskResult(
            task_type="full_validation",
            artifact_id=request.artifact_id,
            success=overall_success,
            skill_results=all_results,
            errors=all_errors,
            warnings=all_warnings,
        )

    def _create_skill_context(self, branch: str = "main") -> SkillContext:
        """Create a SkillContext for skill execution."""
        return SkillContext(
            twin=self.twin,
            mcp=self.mcp,
            logger=self.logger,
            session_id=self.session_id,
            branch=branch,
        )
