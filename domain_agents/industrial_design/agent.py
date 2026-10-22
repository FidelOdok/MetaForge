"""Industrial design domain agent (FORGE-627, FORGE-633).

The Planner's discipline #2: form factor, ergonomics and DFM-aware
aesthetics. Three deterministic skills:

- ``form_factor_envelope``: the outer envelope a product's contents need;
- ``check_ergonomics``: human-contact features against sourced limits for
  the intended users;
- ``check_cmf_compatibility``: each part's library appearance against its
  material.

None of them carries built-in data. Sizes, limits and appearances arrive
with their sources (the twin, the library, cited datasets), and a missing
one is reported as unknown, never filled in. Rendering a design in its
appearances is ``twin.render_work_product`` (FORGE-630, FORGE-632).

Supports two modes, like the other agents:
- **LLM mode**: PydanticAI Agent() with LLM-driven tool selection
- **Hardcoded mode**: Deterministic dispatch by task_type (fallback)
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID, uuid4

try:
    from pydantic_ai import RunContext
except ImportError:
    RunContext = None  # type: ignore[assignment,misc]

import structlog
from pydantic import BaseModel, ValidationError

from domain_agents.base_agent import (
    AgentDependencies,
    AgentResult,
    get_llm_model,
    is_llm_available,
)
from domain_agents.industrial_design.skills.check_cmf_compatibility import (
    CheckCmfCompatibilityHandler,
    CheckCmfCompatibilityInput,
)
from domain_agents.industrial_design.skills.check_ergonomics import (
    CheckErgonomicsHandler,
    CheckErgonomicsInput,
)
from domain_agents.industrial_design.skills.form_factor_envelope import (
    FormFactorEnvelopeHandler,
    FormFactorEnvelopeInput,
)
from observability.tracing import get_tracer
from skill_registry.mcp_bridge import McpBridge
from skill_registry.skill_base import SkillContext

logger = structlog.get_logger(__name__)
tracer = get_tracer("domain_agents.industrial_design")

#: task type -> (skill handler, its input model).
SKILLS: dict[str, tuple[Any, type[BaseModel]]] = {
    "form_factor_envelope": (FormFactorEnvelopeHandler, FormFactorEnvelopeInput),
    "check_ergonomics": (CheckErgonomicsHandler, CheckErgonomicsInput),
    "check_cmf_compatibility": (CheckCmfCompatibilityHandler, CheckCmfCompatibilityInput),
}


class TaskRequest(BaseModel):
    """A request for the industrial design agent to perform a task."""

    task_type: str  # one of SKILLS
    parameters: dict[str, Any] = {}
    branch: str = "main"


class TaskResult(BaseModel):
    """Result of an industrial design agent task."""

    task_type: str
    success: bool
    skill_results: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []


INDUSTRIAL_DESIGN_SYSTEM_PROMPT = """\
You are an industrial designer working within the MetaForge platform. You \
decide a product's form factor, check that people can use it, and choose \
finishes that suit how its parts are made.

You have access to the following tools:

- **form_factor_envelope**: the smallest outer envelope for the product's \
contents (component boxes, positioned or stacked), with the clearance and the \
wall thickness, checked against a maximum envelope if one is required.
- **check_ergonomics**: the features a person holds, presses, reaches or \
lifts against limits for the intended users. Every limit needs its \
population and a source (a cited anthropometric dataset or ergonomics \
guidance); never supply a figure from memory.
- **check_cmf_compatibility**: each part's chosen library appearance against \
the part's material, the finishing processes needed, and the appearance \
values still to be reviewed.

Report a missing value as unknown. Never invent a size, a limit or a colour.
"""

_pydantic_agent: Any | None = None


def _get_or_create_pydantic_agent() -> Any:
    """Lazily create the PydanticAI Agent for industrial design."""
    global _pydantic_agent
    if _pydantic_agent is not None:
        return _pydantic_agent
    try:
        from pydantic_ai import Agent
    except ImportError:
        logger.warning("pydantic_ai_not_installed")
        return None
    model = get_llm_model()
    if model is None:
        return None

    agent = Agent(
        model,
        system_prompt=INDUSTRIAL_DESIGN_SYSTEM_PROMPT,
        output_type=AgentResult,
        deps_type=AgentDependencies,
    )

    async def _skill(ctx: Any, task_type: str, parameters: dict[str, Any]) -> dict[str, Any]:
        deps: AgentDependencies = ctx.deps
        handler_cls, input_cls = SKILLS[task_type]
        skill_ctx = SkillContext(
            twin=deps.twin,
            mcp=deps.mcp_bridge,
            logger=logger,
            session_id=UUID(deps.session_id),
            branch=deps.branch,
        )
        result = await handler_cls(skill_ctx).run(input_cls.model_validate(parameters))
        if not result.success:
            return {"errors": result.errors}
        output: dict[str, Any] = result.data.model_dump(mode="json")
        return output

    @agent.tool
    async def form_factor_envelope(
        ctx: RunContext[AgentDependencies], parameters: dict[str, Any]
    ) -> dict[str, Any]:
        """The outer envelope for the product's contents.

        Args:
            parameters: components [{name, dims_mm, position_mm?, basis}],
                stack_axis, gap_mm, clearance {value_mm, source_ref},
                wall_thickness {value_mm, source_ref}, max_envelope
                {dims_mm, source_ref} (optional).
        """
        return await _skill(ctx, "form_factor_envelope", parameters)

    @agent.tool
    async def check_ergonomics(
        ctx: RunContext[AgentDependencies], parameters: dict[str, Any]
    ) -> dict[str, Any]:
        """Human-contact features against sourced limits for the users.

        Args:
            parameters: product, user_population, features [{name, kind,
                value, basis}], limits [{kind, min, max, population,
                source_ref}].
        """
        return await _skill(ctx, "check_ergonomics", parameters)

    @agent.tool
    async def check_cmf_compatibility(
        ctx: RunContext[AgentDependencies], parameters: dict[str, Any]
    ) -> dict[str, Any]:
        """Each part's appearance against its material.

        Args:
            parameters: parts [{name, material, appearance}], appearances
                (records from library.get_appearance).
        """
        return await _skill(ctx, "check_cmf_compatibility", parameters)

    _pydantic_agent = agent
    return agent


class IndustrialDesignAgent:
    """Industrial design domain agent. Stateless: all state lives in the twin."""

    SUPPORTED_TASKS = set(SKILLS)

    def __init__(self, twin: Any, mcp: McpBridge, session_id: UUID | None = None) -> None:
        self.twin = twin
        self.mcp = mcp
        self.session_id = session_id or uuid4()
        self.logger = logger.bind(agent="industrial_design", session_id=str(self.session_id))

    async def run_task(self, request: TaskRequest) -> TaskResult:
        """Run a task; LLM-driven when a model is configured, else dispatched directly."""
        with tracer.start_as_current_span("agent.execute") as span:
            span.set_attribute("agent.code", "industrial_design")
            span.set_attribute("session.id", str(self.session_id))
            span.set_attribute("task.type", request.task_type)
            self.logger.info("Running task", task_type=request.task_type)

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
            span.set_attribute("agent.mode", "hardcoded")
            return await self._run_hardcoded(request)

    async def _run_with_llm(self, request: TaskRequest) -> TaskResult:
        agent = _get_or_create_pydantic_agent()
        if agent is None:
            raise RuntimeError("PydanticAI agent could not be created")
        deps = AgentDependencies(
            twin=self.twin,
            mcp_bridge=self.mcp,
            session_id=str(self.session_id),
            branch=request.branch,
        )
        prompt = f"Perform a '{request.task_type}' task. Parameters: {request.parameters}"
        t0 = time.monotonic()
        result = await agent.run(prompt, deps=deps)
        self.logger.info(
            "LLM execution completed",
            task_type=request.task_type,
            elapsed_s=round(time.monotonic() - t0, 3),
        )
        output: AgentResult = result.output
        return TaskResult(
            task_type=request.task_type,
            success=True,
            skill_results=output.tool_calls if output.tool_calls else [output.analysis],
            warnings=output.recommendations,
        )

    async def _run_hardcoded(self, request: TaskRequest) -> TaskResult:
        if request.task_type not in self.SUPPORTED_TASKS:
            return TaskResult(
                task_type=request.task_type,
                success=False,
                errors=[
                    f"Unsupported task type: {request.task_type}. "
                    f"Supported: {', '.join(sorted(self.SUPPORTED_TASKS))}"
                ],
            )
        handler_cls, input_cls = SKILLS[request.task_type]
        try:
            skill_input = input_cls.model_validate(request.parameters)
        except ValidationError as exc:
            return TaskResult(
                task_type=request.task_type,
                success=False,
                errors=[f"Invalid parameters: {exc}"],
            )
        result = await handler_cls(self._create_skill_context(request.branch)).run(skill_input)
        if not result.success:
            return TaskResult(task_type=request.task_type, success=False, errors=result.errors)
        data = result.data.model_dump(mode="json")
        return TaskResult(
            task_type=request.task_type,
            success=True,
            skill_results=[data],
            warnings=_warnings(request.task_type, data),
        )

    def _create_skill_context(self, branch: str = "main") -> SkillContext:
        return SkillContext(
            twin=self.twin,
            mcp=self.mcp,
            logger=self.logger,
            session_id=self.session_id,
            branch=branch,
        )


def _warnings(task_type: str, data: dict[str, Any]) -> list[str]:
    """What a reviewer should not miss in a skill's output."""
    if task_type == "form_factor_envelope":
        out = list(data.get("overlaps") or [])
        if data.get("fits") is False:
            out.append("the envelope exceeds the maximum")
        return out
    out = [str(u) for u in data.get("unknowns") or []]
    out += [str(m) for m in data.get("population_mismatches") or []]
    out += [f"needs review: {r}" for r in data.get("needs_review") or []]
    verdict = data.get("verdict")
    if verdict in {"does_not_fit", "incompatible"}:
        out.insert(0, f"verdict: {verdict}")
    return out
