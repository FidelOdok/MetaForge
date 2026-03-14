# Skill System Specification

> **Version**: 0.1 (Phase 0 — Spec & Design)
> **Status**: Draft
> **Last Updated**: 2026-03-02
> **Depends on**: [`architecture.md`](architecture.md), [`twin_schema.md`](twin_schema.md)
> **Referenced by**: [`mcp_spec.md`](mcp_spec.md), [`roadmap.md`](roadmap.md), [`governance.md`](governance.md)

## 1. Overview

A **Skill** is the atomic unit of domain expertise in MetaForge. Every capability an agent has — validating stress results, exporting a BOM, running ERC checks — is implemented as a skill.

Skills are:

- **Deterministic**: Given the same inputs and tool state, a skill produces the same outputs.
- **Schema-validated**: Inputs and outputs are defined as Pydantic models and validated at runtime.
- **Independently testable**: Each skill has its own test suite that runs without the full platform.
- **Tool-mediated**: Skills never call engineering tools directly — all tool access goes through the MCP protocol via the MCP Bridge.

### Relationship to Agents

Agents (PydanticAI) invoke skills as tools. The Skill Registry exposes skills as PydanticAI tool definitions, so agents can discover and call them through the standard PydanticAI tool interface.

```
Agent (PydanticAI)
    │
    ▼
Skill Registry (lookup + validate)
    │
    ▼
Skill Handler (execute logic)
    │
    ▼
MCP Bridge (tool access)
    │
    ▼
Tool Adapter (Docker container)
```

---

## 2. Directory Convention

Every skill lives in a dedicated directory with exactly **5 required files**:

```
domain_agents/<domain>/skills/<skill_name>/
├── definition.json    # Skill metadata and configuration
├── SKILL.md           # Human-readable documentation
├── schema.py          # Pydantic input/output models
├── handler.py         # Execution logic (SkillBase subclass)
└── tests.py           # Skill-specific test suite
```

### File Responsibilities

| File | Purpose | Validated By |
|------|---------|-------------|
| `definition.json` | Machine-readable metadata: name, version, domain, tools needed, phase | Skill Loader (JSON Schema) |
| `SKILL.md` | Human documentation: what the skill does, examples, limitations | Code review |
| `schema.py` | Pydantic models for input and output | Schema Validator (Pydantic) |
| `handler.py` | Execution logic — subclasses `SkillBase` | Unit + integration tests |
| `tests.py` | Pytest test suite for the skill | CI pipeline |

---

## 3. `definition.json` Schema

Every skill must have a `definition.json` that conforms to this schema:

```json
{
  "name": "validate_stress",
  "version": "0.1.0",
  "domain": "mechanical",
  "agent": "mechanical",
  "description": "Validates stress analysis results against design constraints using CalculiX FEA.",
  "phase": 1,
  "tools_required": [
    {
      "tool_id": "calculix.run_fea",
      "capability": "stress_analysis",
      "required": true
    }
  ],
  "input_schema": "schema.ValidateStressInput",
  "output_schema": "schema.ValidateStressOutput",
  "timeout_seconds": 300,
  "retries": 1,
  "idempotent": true,
  "tags": ["fea", "stress", "validation", "mechanical"]
}
```

### Field Definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `str` | Yes | Unique skill identifier (snake_case) |
| `version` | `str` | Yes | Semantic version (`MAJOR.MINOR.PATCH`) |
| `domain` | `str` | Yes | Engineering domain (e.g., `"mechanical"`, `"electronics"`) |
| `agent` | `str` | Yes | Agent that owns this skill |
| `description` | `str` | Yes | One-line description of what the skill does |
| `phase` | `int` | Yes | Minimum phase required (1, 2, or 3) |
| `tools_required` | `list[ToolRef]` | Yes | MCP tools this skill needs (can be empty for pure-logic skills) |
| `input_schema` | `str` | Yes | Dotted path to input Pydantic model in `schema.py` |
| `output_schema` | `str` | Yes | Dotted path to output Pydantic model in `schema.py` |
| `timeout_seconds` | `int` | No | Max execution time (default: 120) |
| `retries` | `int` | No | Number of retry attempts on failure (default: 0) |
| `idempotent` | `bool` | No | Whether the skill is safe to retry (default: `false`) |
| `tags` | `list[str]` | No | Searchable tags for discovery |

### ToolRef Schema

```json
{
  "tool_id": "calculix.run_fea",
  "capability": "stress_analysis",
  "required": true
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tool_id` | `str` | Yes | MCP tool identifier (`<adapter>.<method>`) |
| `capability` | `str` | Yes | Specific capability needed from the tool |
| `required` | `bool` | No | Whether the tool must be available (default: `true`) |

---

## 4. Input/Output Schemas

All skill inputs and outputs are defined as Pydantic models in `schema.py`. This ensures type safety, validation, and documentation.

### Example: `validate_stress` Schemas

```python
"""Input/output schemas for the validate_stress skill."""

from uuid import UUID
from pydantic import BaseModel, Field


class StressConstraint(BaseModel):
    """A constraint on allowable stress values."""
    max_von_mises_mpa: float = Field(..., description="Maximum allowable von Mises stress in MPa")
    safety_factor: float = Field(default=1.5, ge=1.0, description="Required safety factor")
    material: str = Field(..., description="Material name for property lookup")


class ValidateStressInput(BaseModel):
    """Input for stress validation skill."""
    work_product_id: UUID = Field(..., description="ID of the CAD model work_product in the Twin")
    mesh_file_path: str = Field(..., description="Path to the mesh file (e.g., .inp)")
    load_case: str = Field(..., description="Load case identifier")
    constraints: list[StressConstraint] = Field(..., min_length=1, description="Stress constraints to validate against")


class StressResult(BaseModel):
    """Result for a single stress check."""
    region: str = Field(..., description="Region or element set name")
    max_von_mises_mpa: float = Field(..., description="Maximum von Mises stress found")
    allowable_mpa: float = Field(..., description="Allowable stress for this region")
    safety_factor_achieved: float = Field(..., description="Actual safety factor")
    passed: bool = Field(..., description="Whether the constraint is satisfied")


class ValidateStressOutput(BaseModel):
    """Output from stress validation skill."""
    work_product_id: UUID = Field(..., description="ID of the analyzed work_product")
    overall_passed: bool = Field(..., description="Whether all constraints passed")
    results: list[StressResult] = Field(..., description="Per-region stress results")
    max_stress_mpa: float = Field(..., description="Global maximum stress found")
    critical_region: str = Field(..., description="Region with highest stress")
    solver_time_seconds: float = Field(..., description="FEA solver execution time")
    mesh_elements: int = Field(..., description="Number of mesh elements used")
```

### Schema Rules

1. All fields must have a `description` in `Field(...)`.
2. Use appropriate validators (`ge`, `le`, `min_length`, `pattern`, etc.).
3. Input models inherit from `BaseModel` — no defaults for required fields.
4. Output models inherit from `BaseModel` — all fields are populated by the handler.
5. Use `UUID` for work_product/constraint references, not strings.
6. Domain-specific units must be stated in field names or descriptions (e.g., `_mpa`, `_seconds`).

---

## 5. SkillBase Abstract Class

All skill handlers must subclass `SkillBase` and implement the `execute` method.

```python
"""Abstract base class for all MetaForge skills."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from pydantic import BaseModel

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class SkillBase(ABC, Generic[InputT, OutputT]):
    """
    Base class for all skills.

    Subclasses must:
    1. Set input_type and output_type class attributes.
    2. Implement the execute() method.
    """

    input_type: type[InputT]
    output_type: type[OutputT]

    def __init__(self, context: "SkillContext") -> None:
        self.context = context
        self.logger = context.logger.bind(skill=self.__class__.__name__)

    @abstractmethod
    async def execute(self, input_data: InputT) -> OutputT:
        """
        Execute the skill logic.

        Args:
            input_data: Validated input (already passed Pydantic validation).

        Returns:
            Validated output (will be checked against output_type).

        Raises:
            SkillExecutionError: If the skill encounters an unrecoverable error.
        """
        ...

    async def validate_preconditions(self, input_data: InputT) -> list[str]:
        """
        Optional: Check preconditions before execution.

        Returns a list of error messages. Empty list means all preconditions met.
        Override this method to add custom checks (e.g., tool availability).
        """
        return []
```

### SkillContext Interface

The `SkillContext` is injected into every skill and provides access to the Twin, MCP Bridge, and logging.

```python
"""Context provided to every skill during execution."""

import structlog
from uuid import UUID


class SkillContext:
    """
    Dependency-injected context for skill execution.

    Provides access to:
    - Digital Twin API (read/write work_products, constraints)
    - MCP Bridge (invoke external tools)
    - Structured logger (with trace correlation)
    """

    def __init__(
        self,
        twin: "TwinAPI",
        mcp: "McpBridge",
        logger: structlog.BoundLogger,
        session_id: UUID,
        branch: str = "main",
    ) -> None:
        self.twin = twin
        self.mcp = mcp
        self.logger = logger
        self.session_id = session_id
        self.branch = branch
```

### McpBridge Interface

The MCP Bridge is the only way skills can access external tools:

```python
class McpBridge:
    """Bridge from skill execution to MCP tool calls."""

    async def invoke(
        self,
        tool_id: str,
        params: dict,
        timeout: int | None = None,
    ) -> dict:
        """
        Invoke an MCP tool.

        Args:
            tool_id: Tool identifier (e.g., "calculix.run_fea").
            params: Tool parameters (will be JSON-serialized).
            timeout: Override timeout in seconds.

        Returns:
            Tool result as a dictionary.

        Raises:
            McpToolError: If the tool call fails.
            McpTimeoutError: If the tool call exceeds the timeout.
        """
        ...

    async def is_available(self, tool_id: str) -> bool:
        """Check if a tool is available and healthy."""
        ...

    async def list_tools(self, capability: str | None = None) -> list[dict]:
        """List available tools, optionally filtered by capability."""
        ...
```

---

## 6. Skill Lifecycle

Skills progress through a defined lifecycle:

```
DRAFT → REGISTERED → ACTIVE → DEPRECATED
```

| State | Description |
|-------|-------------|
| `DRAFT` | Skill directory exists but is not yet loadable (missing files, failing validation) |
| `REGISTERED` | Skill passes validation and is loaded into the registry, but not yet exposed to agents |
| `ACTIVE` | Skill is available for agent invocation |
| `DEPRECATED` | Skill is still functional but marked for removal. Agents receive a warning when invoking it. |

### State Transitions

| From | To | Trigger |
|------|----|---------|
| `DRAFT` | `REGISTERED` | Skill passes loader validation (all 5 files present, schemas valid) |
| `REGISTERED` | `ACTIVE` | Skill passes integration tests and is promoted |
| `ACTIVE` | `DEPRECATED` | Manual deprecation via registry API |
| `DEPRECATED` | (removed) | Skill directory is deleted |

---

## 7. Skill Registry

The Skill Registry is the central catalog of all available skills.

### Registry API

```python
class SkillRegistry:
    """Central catalog for skill discovery and management."""

    async def discover(self, search_paths: list[str] | None = None) -> int:
        """
        Auto-discover skills by scanning domain_agents/*/skills/ directories.
        Returns the number of newly discovered skills.
        """
        ...

    async def register(self, skill_path: str) -> "SkillRegistration":
        """
        Register a skill from its directory path.
        Validates definition.json, loads schemas, checks handler.
        Raises SkillValidationError if any validation fails.
        """
        ...

    async def get(self, skill_name: str) -> "SkillRegistration | None":
        """Look up a skill by name."""
        ...

    async def list_skills(
        self,
        domain: str | None = None,
        agent: str | None = None,
        phase: int | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
    ) -> list["SkillRegistration"]:
        """Query skills with optional filters."""
        ...

    async def health(self) -> dict:
        """
        Registry health report: total skills, by status, by domain.
        """
        ...

    async def deprecate(self, skill_name: str, reason: str) -> None:
        """Mark a skill as deprecated."""
        ...
```

### SkillRegistration

```python
class SkillRegistration(BaseModel):
    """A registered skill with its metadata and resolved references."""
    name: str
    version: str
    domain: str
    agent: str
    description: str
    phase: int
    status: str  # DRAFT, REGISTERED, ACTIVE, DEPRECATED
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    handler_class: type[SkillBase]
    tools_required: list[dict]
    timeout_seconds: int
    retries: int
    idempotent: bool
    tags: list[str]
    skill_path: str

    class Config:
        arbitrary_types_allowed = True
```

---

## 8. Skill Loader

The Skill Loader dynamically imports skill modules and validates them against the definition.

### Loading Process

```
Read definition.json
        │
        ▼
Validate against SkillDefinition schema
        │
        ▼
Import schema.py module (importlib)
        │
        ▼
Resolve input_schema and output_schema references
        │
        ▼
Import handler.py module
        │
        ▼
Verify handler subclasses SkillBase
        │
        ▼
Verify handler.input_type matches definition
        │
        ▼
Create SkillRegistration
        │
        ▼
Set status to REGISTERED
```

### Error Handling

The loader follows a **fail-soft** strategy: if a single skill fails to load, it is marked as `DRAFT` with an error message, and loading continues for other skills.

```python
class SkillLoadError(Exception):
    """Raised when a skill fails to load."""
    def __init__(self, skill_name: str, reason: str, path: str):
        self.skill_name = skill_name
        self.reason = reason
        self.path = path
        super().__init__(f"Failed to load skill '{skill_name}' at {path}: {reason}")
```

---

## 9. PydanticAI Integration

Skills are exposed to PydanticAI agents as tool definitions. The Skill Registry generates PydanticAI-compatible tool wrappers automatically.

### Tool Registration

```python
from pydanticai import Agent, Tool


def skill_to_tool(registration: SkillRegistration, context: SkillContext) -> Tool:
    """Convert a SkillRegistration into a PydanticAI Tool."""

    async def tool_fn(**kwargs) -> dict:
        # Validate input
        input_data = registration.input_schema(**kwargs)

        # Instantiate handler
        handler = registration.handler_class(context)

        # Check preconditions
        errors = await handler.validate_preconditions(input_data)
        if errors:
            raise SkillPreconditionError(registration.name, errors)

        # Execute
        result = await handler.execute(input_data)

        # Return as dict for PydanticAI
        return result.model_dump()

    return Tool(
        function=tool_fn,
        name=registration.name,
        description=registration.description,
    )
```

### Agent Setup

```python
from pydanticai import Agent

# Create a mechanical engineering agent with its skills
mechanical_agent = Agent(
    model="anthropic:claude-sonnet-4-20250514",
    system_prompt="You are a mechanical engineering specialist...",
    tools=[
        skill_to_tool(registry.get("validate_stress"), context),
        skill_to_tool(registry.get("check_tolerances"), context),
        skill_to_tool(registry.get("export_mesh"), context),
    ],
)
```

---

## 10. Phase 1 Skill Catalog

Phase 1 includes 12 skills across 4 agents (3 skills per agent):

### Mechanical Agent (`domain_agents/mechanical/`)

| Skill | Description | Tools |
|-------|-------------|-------|
| `validate_stress` | Run FEA stress analysis and validate against constraints | `calculix.run_fea` |
| `check_tolerances` | Verify geometric tolerances on CAD models | `freecad.measure` |
| `export_mesh` | Generate FEA-ready mesh from CAD geometry | `freecad.export_mesh` |

### Electronics Agent (`domain_agents/electronics/`)

| Skill | Description | Tools |
|-------|-------------|-------|
| `run_erc` | Execute electrical rules check on schematic | `kicad.run_erc` |
| `run_drc` | Execute design rules check on PCB layout | `kicad.run_drc` |
| `export_bom` | Extract BOM from schematic with component data | `kicad.export_bom` |

### Firmware Agent (`domain_agents/firmware/`)

| Skill | Description | Tools |
|-------|-------------|-------|
| `validate_pinmap` | Check pin assignments against schematic | (pure logic) |
| `generate_scaffold` | Create firmware project structure from pinmap | (pure logic) |
| `check_memory_budget` | Validate firmware fits within MCU memory constraints | (pure logic) |

### Simulation Agent (`domain_agents/simulation/`)

| Skill | Description | Tools |
|-------|-------------|-------|
| `run_thermal` | Thermal analysis using FEA | `calculix.run_thermal` |
| `run_circuit_sim` | SPICE circuit simulation (DC/AC/transient) | `spice.simulate` |
| `compare_results` | Compare simulation results against reference | (pure logic) |

---

## 11. Example: Complete Skill Implementation

### `domain_agents/mechanical/skills/validate_stress/`

**`definition.json`**:

```json
{
  "name": "validate_stress",
  "version": "0.1.0",
  "domain": "mechanical",
  "agent": "mechanical",
  "description": "Validates stress analysis results against design constraints using CalculiX FEA.",
  "phase": 1,
  "tools_required": [
    {
      "tool_id": "calculix.run_fea",
      "capability": "stress_analysis",
      "required": true
    }
  ],
  "input_schema": "schema.ValidateStressInput",
  "output_schema": "schema.ValidateStressOutput",
  "timeout_seconds": 300,
  "retries": 1,
  "idempotent": true,
  "tags": ["fea", "stress", "validation", "mechanical"]
}
```

**`handler.py`**:

```python
"""Handler for the validate_stress skill."""

from skill_registry.skill_base import SkillBase, SkillContext
from .schema import ValidateStressInput, ValidateStressOutput, StressResult


class ValidateStressHandler(SkillBase[ValidateStressInput, ValidateStressOutput]):
    input_type = ValidateStressInput
    output_type = ValidateStressOutput

    async def validate_preconditions(self, input_data: ValidateStressInput) -> list[str]:
        errors = []
        # Check that the mesh file work_product exists in the Twin
        work_product = await self.context.twin.get_work_product(
            input_data.work_product_id, branch=self.context.branch
        )
        if work_product is None:
            errors.append(f"WorkProduct {input_data.work_product_id} not found in Twin")
        # Check that CalculiX is available
        if not await self.context.mcp.is_available("calculix.run_fea"):
            errors.append("CalculiX FEA tool is not available")
        return errors

    async def execute(self, input_data: ValidateStressInput) -> ValidateStressOutput:
        self.logger.info(
            "Running stress validation",
            work_product_id=str(input_data.work_product_id),
            load_case=input_data.load_case,
        )

        # Invoke CalculiX via MCP
        fea_result = await self.context.mcp.invoke(
            "calculix.run_fea",
            {
                "mesh_file": input_data.mesh_file_path,
                "load_case": input_data.load_case,
                "analysis_type": "static_stress",
            },
            timeout=self.context.branch and 300,
        )

        # Process results against constraints
        results = []
        max_stress = 0.0
        critical_region = ""

        for constraint in input_data.constraints:
            region_stress = fea_result.get("max_von_mises", {})
            for region, stress_val in region_stress.items():
                allowable = constraint.max_von_mises_mpa / constraint.safety_factor
                sf_achieved = constraint.max_von_mises_mpa / stress_val if stress_val > 0 else float("inf")
                passed = stress_val <= allowable

                results.append(StressResult(
                    region=region,
                    max_von_mises_mpa=stress_val,
                    allowable_mpa=allowable,
                    safety_factor_achieved=sf_achieved,
                    passed=passed,
                ))

                if stress_val > max_stress:
                    max_stress = stress_val
                    critical_region = region

        overall_passed = all(r.passed for r in results)

        return ValidateStressOutput(
            work_product_id=input_data.work_product_id,
            overall_passed=overall_passed,
            results=results,
            max_stress_mpa=max_stress,
            critical_region=critical_region,
            solver_time_seconds=fea_result.get("solver_time", 0.0),
            mesh_elements=fea_result.get("mesh_elements", 0),
        )
```

**`tests.py`**:

```python
"""Tests for the validate_stress skill."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from .schema import ValidateStressInput, StressConstraint
from .handler import ValidateStressHandler


@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.twin = AsyncMock()
    ctx.mcp = AsyncMock()
    ctx.logger = MagicMock()
    ctx.logger.bind.return_value = ctx.logger
    ctx.session_id = uuid4()
    ctx.branch = "main"
    return ctx


@pytest.fixture
def sample_input():
    return ValidateStressInput(
        work_product_id=uuid4(),
        mesh_file_path="/project/mesh/bracket.inp",
        load_case="static_load_1",
        constraints=[
            StressConstraint(
                max_von_mises_mpa=250.0,
                safety_factor=1.5,
                material="aluminum_6061",
            )
        ],
    )


@pytest.mark.asyncio
async def test_stress_passes(mock_context, sample_input):
    # Arrange
    mock_context.twin.get_work_product.return_value = MagicMock()
    mock_context.mcp.is_available.return_value = True
    mock_context.mcp.invoke.return_value = {
        "max_von_mises": {"bracket_body": 100.0},
        "solver_time": 12.5,
        "mesh_elements": 45000,
    }

    handler = ValidateStressHandler(mock_context)
    result = await handler.execute(sample_input)

    assert result.overall_passed is True
    assert result.max_stress_mpa == 100.0
    assert result.results[0].safety_factor_achieved == 2.5


@pytest.mark.asyncio
async def test_stress_fails(mock_context, sample_input):
    mock_context.twin.get_work_product.return_value = MagicMock()
    mock_context.mcp.is_available.return_value = True
    mock_context.mcp.invoke.return_value = {
        "max_von_mises": {"bracket_body": 200.0},
        "solver_time": 15.0,
        "mesh_elements": 45000,
    }

    handler = ValidateStressHandler(mock_context)
    result = await handler.execute(sample_input)

    # 200 > 250/1.5 = 166.67 → should fail
    assert result.overall_passed is False


@pytest.mark.asyncio
async def test_precondition_missing_artifact(mock_context, sample_input):
    mock_context.twin.get_work_product.return_value = None
    mock_context.mcp.is_available.return_value = True

    handler = ValidateStressHandler(mock_context)
    errors = await handler.validate_preconditions(sample_input)

    assert len(errors) == 1
    assert "not found" in errors[0]
```

---

## 12. Writing a New Skill

Step-by-step guide for contributing a new skill to MetaForge.

### Checklist

- [ ] **1. Create the directory**: `domain_agents/<domain>/skills/<skill_name>/`
- [ ] **2. Write `definition.json`**: Follow the schema in Section 3. Set `phase` to the correct phase.
- [ ] **3. Write `schema.py`**: Define Pydantic input/output models. Add descriptions to all fields.
- [ ] **4. Write `handler.py`**: Subclass `SkillBase`. Implement `execute()`. Use `self.context.mcp.invoke()` for tool access.
- [ ] **5. Write `tests.py`**: Pytest async tests. Mock `SkillContext` (Twin, MCP, logger). Test happy path, error path, and edge cases.
- [ ] **6. Write `SKILL.md`**: Document what the skill does, inputs, outputs, examples, and known limitations.
- [ ] **7. Run tests**: `pytest domain_agents/<domain>/skills/<skill_name>/tests.py`
- [ ] **8. Register**: The Skill Loader auto-discovers on startup, or call `registry.register()` manually.

### Common Patterns

**Pure-logic skill** (no tool calls):

```python
async def execute(self, input_data: MyInput) -> MyOutput:
    # No self.context.mcp calls — all logic is local
    result = compute_something(input_data.value)
    return MyOutput(result=result)
```

**Multi-tool skill** (calls multiple tools sequentially):

```python
async def execute(self, input_data: MyInput) -> MyOutput:
    mesh = await self.context.mcp.invoke("freecad.export_mesh", {"file": input_data.cad_path})
    fea = await self.context.mcp.invoke("calculix.run_fea", {"mesh": mesh["output_path"]})
    return MyOutput(stress=fea["max_stress"])
```

**Skill that reads the Twin**:

```python
async def execute(self, input_data: MyInput) -> MyOutput:
    work_product = await self.context.twin.get_work_product(input_data.work_product_id, branch=self.context.branch)
    constraints = await self.context.twin.get_edges(work_product.id, edge_type="CONSTRAINED_BY")
    # ... process
```
