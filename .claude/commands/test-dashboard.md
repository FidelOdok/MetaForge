# Test MetaForge Dashboard

Run the `dashboard-tester` agent to test the MetaForge dashboard end-to-end using Playwright MCP browser tools.

## Instructions

Launch the `dashboard-tester` agent (defined in `.claude/agents/dashboard-tester.agent.md`) using the Agent tool.

### Default (no arguments): Run all pre-built scenarios

If the user provides no arguments, use this prompt for the agent:

> Run all test scenarios against the MetaForge dashboard at http://localhost:3000.
> Start with pre-flight checks, then execute scenarios in this order:
> 1. smoke-navigation (layout & navigation smoke test)
> 2. mechanical-stress (stress validation E2E flow)
> 3. cad-generation (CAD generation E2E flow)
> 4. full-validation (multi-agent validation flow)
>
> Read each scenario file from `.claude/test-scenarios/` and execute its steps.
> Report results as a summary table at the end.

### Specific scenario (argument is a scenario name)

If the user provides a known scenario name (e.g., `/test-dashboard mechanical-stress`), use this prompt:

> Run the test scenario "$ARGUMENT" against the MetaForge dashboard at http://localhost:3000.
> Start with pre-flight checks, then read and execute `.claude/test-scenarios/$ARGUMENT.scenario.md`.
> Report results with a step-by-step breakdown.

### Natural language (argument is a description)

If the user provides a natural language description (e.g., `/test-dashboard "test that creating a project works"`), use this prompt:

> Test the MetaForge dashboard at http://localhost:3000 with this request: "$ARGUMENT"
> Start with pre-flight checks, then:
> 1. Explore the relevant dashboard pages to understand current state
> 2. Generate a test scenario from the description
> 3. Save it to `.claude/test-scenarios/dynamic-<name>.scenario.md`
> 4. Execute the generated scenario
> 5. Report results with step-by-step breakdown

### Agent Configuration

When launching the agent, use:
- `subagent_type`: `"dashboard-tester"` — this invokes the custom agent definition from `.claude/agents/dashboard-tester.agent.md`
- The agent has access to all Playwright MCP browser tools plus Read, Grep, and Glob for inspecting source code
