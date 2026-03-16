# Test Scenario Format

Scenario files define automated test sequences for the MetaForge dashboard tester agent.

## File Convention

- Location: `.claude/test-scenarios/`
- Extension: `.scenario.md`
- Naming: `kebab-case.scenario.md`
- Dynamic scenarios are prefixed with `dynamic-`

## Format

Each file has YAML frontmatter followed by a Steps section and optional Cleanup section.

### Frontmatter

```yaml
---
name: scenario-name
description: One-line description
requires: [gateway]         # Services needed: gateway, agents, neo4j, kafka, temporal
timeout: 120                # Max seconds for entire scenario
tags: [smoke, e2e]          # For filtering: smoke, mechanical, electronics, e2e, multi-agent
---
```

### Step Syntax

| Syntax | Description | Example |
|--------|-------------|---------|
| `[skill: <name>]` | Invoke a test skill with named params | `[skill: create-project] name="My Project"` |
| `[assert]` | Check condition against last skill output | `[assert] status == "completed"` |
| `[navigate]` | Go to URL path (appended to base URL) | `[navigate] /twin` |
| `[screenshot]` | Capture screenshot with label | `[screenshot] "after-validation"` |
| `[wait]` | Pause N seconds | `[wait] 3` |

### Variables

Steps that return data store it under the skill name. Access nested fields with dot notation:

- `$project.id` — project ID from create-project skill
- `$project.name` — project name
- `$action.status` — action result status
- `$action.steps` — action step timeline
- `$twin.properties` — twin node properties
- `$chat.response_text` — chat response content

### Assertions

Assertions check conditions against the current state:

- `status == "completed"` — exact equality
- `response contains "MPa"` — substring match
- `steps contains agent_code="MECH"` — object property match in array
- `properties.stress_results exists` — field existence check
- `page_load_ms < 5000` — numeric comparison

### Available Skills

- `create-project` — params: `name`, `description`
- `run-agent-action` — params: `action`, `target`, `project_id`
- `chat-with-agent` — params: `message`
- `inspect-twin-node` — params: `node`
- `review-approval` — params: `action` (approve/reject), `reason`
- `check-sessions` — no params
- `check-api-health` — no params
- `upload-download-file` — params: `file_path`, `page`
- `test-dark-mode-responsive` — no params
- `measure-performance` — no params

### Cleanup

The `## Cleanup` section runs after all steps regardless of pass/fail. Use it to delete test data.

```markdown
## Cleanup
- Delete project "$project.id"
```
