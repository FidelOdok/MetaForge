"""Supply chain domain agent.

Orchestrates skill execution for BOM risk scoring and alternate parts finding.

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
from domain_agents.supply_chain.alt_parts import AlternatePartsFinder
from domain_agents.supply_chain.models import BOMRiskReport
from domain_agents.supply_chain.risk_scorer import BOMRiskScorer
from domain_agents.supply_chain.skills.find_alternates.handler import (
    FindAlternatesHandler,
)
from domain_agents.supply_chain.skills.find_alternates.schema import (
    FindAlternatesInput,
)
from domain_agents.supply_chain.skills.score_bom_risk.handler import (
    ScoreBomRiskHandler,
)
from domain_agents.supply_chain.skills.score_bom_risk.schema import (
    BOMItem,
    ScoreBomRiskInput,
)
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.supply_chain")


# ---------------------------------------------------------------------------
# Domain-specific result model for PydanticAI structured output
# ---------------------------------------------------------------------------


class SupplyChainResult(AgentResult):
    """Structured output from the supply chain agent's PydanticAI run."""

    overall_bom_risk: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Overall BOM risk score (0-100)",
    )
    critical_parts_count: int = Field(
        default=0,
        description="Number of critical-risk parts in the BOM",
    )


# ---------------------------------------------------------------------------
# Backward-compatible request/result models
# ---------------------------------------------------------------------------


class TaskRequest(BaseModel):
    """A request for the supply chain agent to perform a task."""

    task_type: str  # "score_bom_risk", "find_alternates"
    parameters: dict[str, Any] = {}
    branch: str = "main"


class TaskResult(BaseModel):
    """Result of a supply chain agent task."""

    task_type: str
    success: bool
    skill_results: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# PydanticAI agent factory (lazy, created once per process)
# ---------------------------------------------------------------------------

_pydantic_agent: Any | None = None

SUPPLY_CHAIN_SYSTEM_PROMPT = """\
You are an expert supply chain engineer working within the MetaForge design \
validation platform. You have deep knowledge of component sourcing, BOM \
risk assessment, distributor analysis, lifecycle management, and supply chain \
risk mitigation strategies.

You have access to the following tools:

- **score_bom_risk**: Score supply chain risk for a BOM. Evaluates single-source \
risk, lead time, lifecycle status, price volatility, stock levels, and compliance \
gaps. Provide a list of BOM items with distributor data.
- **find_alternates**: Find and rank alternate parts for a given MPN. Searches \
for compatible replacements based on package, specs, availability, price, and \
risk reduction. Provide the original MPN, specs, and distributor search results.

Given a user request, determine which tools to call and in what order. \
Analyze the results and provide clear supply chain risk assessments with \
actionable recommendations for risk mitigation.

Always flag critical-risk parts (score > 75) and recommend alternates \
for high-risk components.
"""


def _get_or_create_pydantic_agent() -> Any:
    """Lazily create the PydanticAI Agent for supply chain analysis."""
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
        system_prompt=SUPPLY_CHAIN_SYSTEM_PROMPT,
        result_type=SupplyChainResult,
        deps_type=AgentDependencies,
    )

    # -- Tool: score_bom_risk -------------------------------------------------

    @agent.tool
    async def score_bom_risk(
        ctx: RunContext[AgentDependencies],
        project_id: str,
        bom_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Score supply chain risk for a BOM.

        Args:
            project_id: Project identifier.
            bom_items: List of BOM items, each with mpn, manufacturer,
                quantity, description, and optional distributor_data.
        """
        scorer = BOMRiskScorer()
        parts_data = []
        for item in bom_items:
            part_data = dict(item)
            if "distributor_data" in part_data and part_data["distributor_data"]:
                part_data.update(part_data.pop("distributor_data"))
            parts_data.append(part_data)

        report = scorer.score_bom(parts_data, project_id=project_id)
        return report.model_dump(mode="json")

    # -- Tool: find_alternates ------------------------------------------------

    @agent.tool
    async def find_alternates(
        ctx: RunContext[AgentDependencies],
        mpn: str,
        specs: dict[str, Any],
        distributor_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Find and rank alternate parts for a given MPN.

        Args:
            mpn: Original manufacturer part number.
            specs: Original part specifications.
            distributor_results: Candidate parts from distributor search.
        """
        finder = AlternatePartsFinder()
        result = finder.find_alternates(
            mpn=mpn, specs=specs, distributor_results=distributor_results
        )
        return result.model_dump(mode="json")

    _pydantic_agent = agent
    return agent


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------


class SupplyChainAgent:
    """Supply chain domain agent.

    Orchestrates skill execution for BOM risk scoring and alternate parts.
    The agent is stateless -- all state lives in the Digital Twin.

    Supports two execution modes:
    - PydanticAI mode: LLM-driven tool selection (when METAFORGE_LLM_PROVIDER is set)
    - Hardcoded mode: Deterministic dispatch by task_type (fallback)
    """

    SUPPORTED_TASKS = {"score_bom_risk", "find_alternates"}

    def __init__(
        self,
        twin: Any,  # TwinAPI -- avoid circular import at module level
        mcp: McpBridge,
        session_id: UUID | None = None,
    ) -> None:
        self.twin = twin
        self.mcp = mcp
        self.session_id = session_id or uuid4()
        self.logger = logger.bind(agent="supply_chain", session_id=str(self.session_id))

    async def run_task(self, request: TaskRequest) -> TaskResult:
        """Execute a supply chain task.

        If an LLM is configured, attempts PydanticAI-driven execution.
        Falls back to hardcoded dispatch on LLM unavailability or error.
        """
        with tracer.start_as_current_span("agent.execute") as span:
            span.set_attribute("agent.code", "supply_chain")
            span.set_attribute("session.id", str(self.session_id))
            span.set_attribute("task.type", request.task_type)

            self.logger.info(
                "Running task",
                task_type=request.task_type,
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

        sc_result: SupplyChainResult = result.data

        return TaskResult(
            task_type=request.task_type,
            success=True,
            skill_results=sc_result.tool_calls
            if sc_result.tool_calls
            else [sc_result.analysis],
            warnings=sc_result.recommendations,
        )

    def _build_prompt(self, request: TaskRequest) -> str:
        """Build a natural language prompt from a structured TaskRequest."""
        parts = [f"Perform a '{request.task_type}' task."]
        if request.parameters:
            parts.append(f"Parameters: {request.parameters}")
        return " ".join(parts)

    # --- Hardcoded dispatch (fallback) ---

    async def _run_hardcoded(self, request: TaskRequest) -> TaskResult:
        """Hardcoded dispatch path."""
        if request.task_type not in self.SUPPORTED_TASKS:
            return TaskResult(
                task_type=request.task_type,
                success=False,
                errors=[
                    f"Unsupported task type: {request.task_type}. "
                    f"Supported: {', '.join(sorted(self.SUPPORTED_TASKS))}"
                ],
            )

        handler = self._get_handler(request.task_type)
        return await handler(request)

    def _get_handler(
        self, task_type: str
    ) -> Callable[[TaskRequest], Coroutine[Any, Any, TaskResult]]:
        """Return the handler coroutine function for the given task type."""
        handlers: dict[str, Callable[[TaskRequest], Coroutine[Any, Any, TaskResult]]] = {
            "score_bom_risk": self._run_score_bom_risk,
            "find_alternates": self._run_find_alternates,
        }
        return handlers[task_type]

    async def _run_score_bom_risk(self, request: TaskRequest) -> TaskResult:
        """Run BOM risk scoring using the score_bom_risk skill."""
        ctx = self._create_skill_context(request.branch)

        project_id = request.parameters.get("project_id", "")
        bom_items_raw = request.parameters.get("bom_items", [])

        if not bom_items_raw:
            return TaskResult(
                task_type=request.task_type,
                success=False,
                errors=["Missing required parameter: bom_items"],
            )

        try:
            bom_items = [BOMItem.model_validate(item) for item in bom_items_raw]
        except Exception as exc:
            return TaskResult(
                task_type=request.task_type,
                success=False,
                errors=[f"Invalid bom_items: {exc}"],
            )

        skill_input = ScoreBomRiskInput(
            project_id=project_id,
            bom_items=bom_items,
        )

        handler = ScoreBomRiskHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                success=False,
                errors=result.errors,
            )

        output = result.data
        report = output.report
        warnings = []
        if report.critical_count > 0:
            warnings.append(
                f"{report.critical_count} part(s) have critical supply chain risk"
            )
        if report.high_count > 0:
            warnings.append(
                f"{report.high_count} part(s) have high supply chain risk"
            )

        return TaskResult(
            task_type=request.task_type,
            success=True,
            skill_results=[report.model_dump(mode="json")],
            warnings=warnings,
        )

    async def _run_find_alternates(self, request: TaskRequest) -> TaskResult:
        """Run alternate parts finding using the find_alternates skill."""
        ctx = self._create_skill_context(request.branch)

        mpn = request.parameters.get("mpn", "")
        if not mpn:
            return TaskResult(
                task_type=request.task_type,
                success=False,
                errors=["Missing required parameter: mpn"],
            )

        specs = request.parameters.get("specs", {})
        distributor_results = request.parameters.get("distributor_results", [])

        skill_input = FindAlternatesInput(
            mpn=mpn,
            specs=specs,
            distributor_results=distributor_results,
        )

        handler = FindAlternatesHandler(ctx)
        result = await handler.run(skill_input)

        if not result.success:
            return TaskResult(
                task_type=request.task_type,
                success=False,
                errors=result.errors,
            )

        output = result.data
        return TaskResult(
            task_type=request.task_type,
            success=True,
            skill_results=[output.result.model_dump(mode="json")],
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
