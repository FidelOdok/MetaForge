---
name: mechanical-stress
description: Creates a project, runs stress validation, checks Twin, and chats with agent
requires: [gateway, agents]
timeout: 120
tags: [mechanical, e2e]
---

## Steps

1. [skill: create-project] name="E2E Stress Test"
2. [assert] $project.id exists
3. [skill: run-agent-action] action="validate_stress" target="bracket" project_id="$project.id"
4. [assert] status == "completed"
5. [assert] steps contains agent_code="MECH"
6. [skill: inspect-twin-node] node="$project.id"
7. [assert] properties.stress_results exists
8. [skill: chat-with-agent] message="Summarize the stress validation results"
9. [assert] response_text contains "stress"
10. [screenshot] "stress-flow-complete"

## Cleanup
- Delete project "$project.id"
