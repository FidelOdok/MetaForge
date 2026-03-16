---
name: full-validation
description: Creates a project and runs full multi-agent validation flow
requires: [gateway, agents]
timeout: 180
tags: [e2e, multi-agent]
---

## Steps

1. [skill: create-project] name="E2E Full Validation"
2. [assert] $project.id exists
3. [skill: run-agent-action] action="full_validation" target="drone flight controller board" project_id="$project.id"
4. [assert] status == "completed"
5. [assert] steps contains agent_code="MECH"
6. [assert] steps contains agent_code="EE"
7. [skill: inspect-twin-node] node="$project.id"
8. [assert] properties exists
9. [skill: check-sessions]
10. [assert] status == "completed"
11. [skill: chat-with-agent] message="Give me a summary of the full validation results"
12. [assert] response_text contains "validation"
13. [screenshot] "full-validation-complete"

## Cleanup
- Delete project "$project.id"
