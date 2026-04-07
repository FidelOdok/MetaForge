# Testing Strategy

Testing strategy for MetaForge Phase 1 (v0.1–0.3). Covers the test pyramid, naming conventions, tooling, and per-module coverage expectations.

## Testing Taxonomy

MetaForge uses a 12-level testing taxonomy. Each level answers a different question about system correctness.

| Level | Name | Description | Tooling (MetaForge) | Status |
|-------|------|-------------|---------------------|--------|
| 1 | Static Analysis / Linting | Syntax errors, type issues, code smells | `ruff check .`, `mypy --strict` | Covered |
| 2 | Unit Tests | Individual functions in isolation | `pytest tests/unit/` (1607 tests) | Covered |
| 3 | Component Tests | Single module/node in isolation, no cross-module wiring | `pytest tests/unit/` with `InMemoryTwinAPI`, `InMemoryMcpBridge` | Partial |
| 4 | Contract Tests | API/message schema agreements between services | No dedicated tooling yet — Pydantic schemas act as implicit contracts | Gap |
| 5 | Integration Tests | Multiple real components wired together | `pytest tests/integration/` (49 tests) | Partial |
| 6 | Smoke Tests | Does it start and respond at all? | `tests/integration/test_gateway_smoke.py`, manual `uvicorn` check | Covered |
| 7 | Regression Tests | Did new changes break existing behaviour? | Full `pytest` suite run in CI on every PR | Covered (implicit) |
| 8 | E2E / System Tests | Full pipeline start to finish | `pytest tests/e2e/` (98 tests, full vertical per agent) | Covered |
| 9 | Performance / Load Tests | Throughput, latency under load | Not implemented | Gap |
| 10 | Security Tests | Injection, auth, data exposure | Not implemented | Gap |
| 11 | Acceptance Tests (UAT) | Meets requirements, user sign-off | Not implemented | Gap |
| 12 | Chaos / Resilience Tests | Behaviour when things fail | Not implemented | Gap |

**Component vs. Unit distinction:** Tests under `tests/unit/` use `InMemoryTwinAPI` and `InMemoryMcpBridge` as in-memory doubles, not mocks. Any test that wires a real `McpClient` or real `TwinAPI` to an agent is classified as Component (Level 3). Any test that wires two or more real modules is Integration (Level 5).

**Regression (Level 7) is implicit:** MetaForge does not maintain a separate regression test directory. The full pytest suite acts as the regression gate on every PR via CI.

## Tooling

| Tool | Purpose |
|------|---------|
| pytest | Test runner (async mode via `pytest-asyncio`, mode=AUTO) |
| pytest-asyncio | Async test support — all agent/skill tests are async |
| pydantic | Schema validation in tests (same models as production) |
| InMemoryTwinAPI | In-memory Digital Twin for deterministic tests |
| InMemoryMcpBridge | In-memory MCP bridge with pre-registered tool responses |
| LoopbackTransport | Full MCP protocol stack without network (server ↔ client) |
| httpx (ASGI) | Gateway HTTP tests via `ASGITransport` (no real network) |
| ruff | Linting |
| mypy | Type checking |

## Directory Structure

```
tests/
├── unit/               # Single-module tests (no cross-module dependencies)
│   ├── test_twin_api.py
│   ├── test_mechanical_agent.py
│   ├── test_electronics_agent.py
│   ├── test_simulation_agent.py
│   ├── test_firmware_agent.py
│   ├── test_orchestrator.py
│   ├── test_skill_registry.py
│   ├── test_mcp_client.py
│   ├── test_mcp_server.py
│   ├── test_gateway_server.py
│   └── ...
├── integration/        # Cross-module wiring tests
│   ├── test_agent_twin_integration.py
│   ├── test_cross_agent_workflow.py
│   ├── test_error_propagation.py
│   ├── test_gateway_smoke.py
│   ├── test_orchestrator_agent_flow.py
│   └── test_workflow_dependencies.py
├── e2e/                # Full-stack vertical tests
│   ├── test_mechanical_e2e.py
│   ├── test_electronics_e2e.py
│   ├── test_simulation_e2e.py
│   ├── test_firmware_e2e.py
│   ├── test_orchestrator_e2e.py
│   └── test_gateway_e2e.py
└── conftest.py         # Shared fixtures (SpySubscriber, etc.)
```

## E2E Test Coverage Map

Each E2E file exercises a complete vertical — from the agent entry point through skills, MCP protocol, and Digital Twin.

### Mechanical Agent (`test_mechanical_e2e.py` — 26 tests)

| Class | Tests | What It Exercises |
|-------|-------|-------------------|
| TestMechanicalAgentE2E | 5 | Agent → MCP → CalculiX → Twin (stress pass/fail, full validation, errors) |
| TestValidateStressSkillE2E | 4 | Skill handler directly (execute, pipeline, preconditions) |
| TestMcpProtocolE2E | 5 | LoopbackTransport → McpClient → McpClientBridge (discovery, invocation, capabilities) |
| TestTwinIntegrationE2E | 2 | WorkProduct lifecycle + branched analysis |
| TestCheckTolerancesE2E | 5 | Tolerance checking: pass/fail/marginal/missing params/stack-up |
| TestGenerateMeshE2E | 5 | Mesh generation: good/bad quality/missing file/unsupported ext/algorithm variants |

### Electronics Agent (`test_electronics_e2e.py` — 19 tests)

| Class | Tests | What It Exercises |
|-------|-------|-------------------|
| TestElectronicsAgentE2E | 10 | Agent → MCP → KiCad (ERC/DRC pass/fail, full validation, errors) |
| TestRunErcSkillE2E | 3 | Skill handler directly (execute, pipeline, preconditions) |
| TestKicadMcpProtocolE2E | 4 | Full KiCad MCP stack (discovery, invocation, capabilities, health) |
| TestElectronicsTwinIntegrationE2E | 2 | WorkProduct lifecycle + branched ERC analysis |

### Simulation Agent (`test_simulation_e2e.py` — 14 tests)

| Class | Tests | What It Exercises |
|-------|-------|-------------------|
| TestSpiceSimulationE2E | 3 | SPICE convergent/non-convergent/missing netlist |
| TestFeaSimulationE2E | 3 | FEA safe/unsafe/missing mesh |
| TestCfdSimulationE2E | 2 | CFD converge/not converge |
| TestFullSimulationE2E | 3 | All three solvers combined, partial failure, no params |
| TestSimulationAgentCommonE2E | 3 | WorkProduct not found, unsupported task, Twin update |

### Firmware Agent (`test_firmware_e2e.py` — 18 tests)

| Class | Tests | What It Exercises |
|-------|-------|-------------------|
| TestGenerateHalE2E | 5 | HAL generation: STM32F4/ESP32/unsupported MCU/missing params |
| TestScaffoldDriverE2E | 4 | Driver scaffolding: SPI/I2C/missing params |
| TestConfigureRtosE2E | 3 | RTOS config: FreeRTOS/missing params |
| TestFullBuildE2E | 3 | Full build: all steps/HAL only/no params |
| TestFirmwareAgentCommonE2E | 3 | WorkProduct not found, unsupported task, Twin update |

### Orchestrator (`test_orchestrator_e2e.py` — 10 tests)

| Class | Tests | What It Exercises |
|-------|-------|-------------------|
| TestSingleStepWorkflowE2E | 1 | Single mechanical step end-to-end |
| TestMultiStepWorkflowE2E | 1 | Parallel MECH + EE steps |
| TestDependencyChainE2E | 1 | Sequential ERC → DRC dependency chain |
| TestWorkflowValidationE2E | 2 | Cyclic dependency detection, topological sort |
| TestEventBusIntegrationE2E | 2 | Event lifecycle, unregistered agent failure |
| TestWorkflowRunLifecycleE2E | 3 | Run creation, cancellation, list by status |

### Gateway (`test_gateway_e2e.py` — 11 tests)

| Class | Tests | What It Exercises |
|-------|-------|-------------------|
| TestHealthEndpointE2E | 2 | GET /health returns 200 with version and timestamp |
| TestAssistantRequestE2E | 3 | POST /v1/assistant/request (stress, ERC, unknown action) |
| TestRunStatusPollingE2E | 2 | GET /v1/assistant/request/{run_id} (poll, 404) |
| TestProposalEndpointsE2E | 2 | GET /v1/assistant/proposals (empty list, 404) |
| TestFullRoundTripE2E | 2 | Submit → poll → verify (stress, full validation) |

## Stubbing Strategy

E2E tests stub **only external solver binaries** — all internal interfaces are real.

| Component | Stubbing Approach |
|-----------|-------------------|
| CalculiX solver | `AsyncMock` on `CalculixServer._execute_solver` returning realistic FEA results |
| KiCad tools | Stubbed `_execute_erc` / `_execute_drc` on `KicadServer` |
| FreeCAD mesh | `InMemoryMcpBridge.register_tool_response("freecad.generate_mesh", ...)` |
| SPICE solver | `InMemoryMcpBridge.register_tool_response("spice.run_simulation", ...)` |
| Firmware skills | No stubs needed — pure computation (no MCP tool calls) |
| Digital Twin | `InMemoryTwinAPI` — real in-memory implementation, not a mock |
| MCP protocol | Two patterns: `LoopbackTransport` (full protocol) or `InMemoryMcpBridge` (simple) |
| HTTP layer | `httpx.ASGITransport` — full FastAPI stack without network |

### When to use LoopbackTransport vs InMemoryMcpBridge

- **LoopbackTransport**: When testing MCP protocol correctness (tool discovery, health checks, capability filtering). Wires a real `McpToolServer` to `McpClient` via in-memory transport.
- **InMemoryMcpBridge**: When testing agent logic and skill execution. Simpler setup — just register tool IDs and their canned responses.

## Conventions

### Naming

- Test files: `test_{module}_e2e.py` for E2E, `test_{module}.py` for unit
- Test classes: `Test{Feature}E2E` for E2E, `Test{Feature}` for unit
- Test methods: `test_{scenario}` — descriptive, no abbreviations
- Fixtures: `stack` (returns dict with twin/agent/work_product/mcp), `gateway_stack` for HTTP tests

### Patterns

1. **Arrange-Act-Assert** in every test
2. **Fixtures return dicts** (not tuples) for readability: `s["agent"]`, `s["work_product"]`
3. **Each E2E test is self-contained** — creates its own Twin, MCP bridge, and agent
4. **Deep-copy shared workflow definitions** in gateway tests to prevent cross-test mutation
5. **Assert both success and structure** — check `result.success`, `result.task_type`, `result.skill_results` shape

### What E2E Tests Must Verify

1. **Happy path**: Correct input → successful result with expected output shape
2. **Failure path**: Bad input / missing params → `success=False` with meaningful error messages
3. **Edge cases**: Unsupported task types, missing work_products, non-convergent solvers
4. **Twin integration**: WorkProduct can be updated with results after agent execution
5. **MCP protocol** (where applicable): Tool discovery, capability filtering, health checks

## Running Tests

```bash
# All tests
pytest

# E2E only
pytest tests/e2e/ -v

# Single vertical
pytest tests/e2e/test_mechanical_e2e.py -v

# Unit only
pytest tests/unit/ -v

# With coverage (when configured)
pytest --cov=. --cov-report=term-missing

# Lint + type check
ruff check .
mypy .
```

## Coverage Gaps (Relative to Taxonomy)

| Level | Name | Gap Detail | Priority |
|-------|------|------------|---------|
| 4 | Contract Tests | No consumer-driven contract tests between gateway↔orchestrator or agent↔twin API. Pydantic schemas provide implicit contracts but are not tested across service boundaries. | Phase 2 |
| 9 | Performance / Load Tests | No throughput or latency-under-load tests. No benchmarking harness. | Phase 2 |
| 10 | Security Tests | No injection, auth bypass, or data exposure tests. Auth module exists but is untested for adversarial inputs. | Phase 2 |
| 11 | Acceptance Tests (UAT) | No formal acceptance criteria tests tied to PRD requirements. E2E tests approximate this but lack traceability. | Phase 2 |
| 12 | Chaos / Resilience Tests | No tests for partial failures, container crashes, or dependency unavailability. Temporal retry logic is untested under failure injection. | Phase 3 |
| — | CLI E2E | CLI is TypeScript; Python E2E tests cannot exercise it directly. | Phase 2 |
| — | WebSocket / SSE gateway | Requires persistent connections; deferred to integration tests. | Phase 2 |
| — | Multi-agent conflict resolution | Phase 2 feature, not yet implemented. | Phase 2 |
| — | FreeCAD full MCP stack | Uses `InMemoryMcpBridge`, not `LoopbackTransport` — no FreeCAD MCP server yet. | Phase 2 |

Gaps without a Level number are infrastructure or phase-scope limitations rather than missing taxonomy levels.
