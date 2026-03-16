---
name: cad-generation
description: Creates a project, generates a CAD script, and verifies download availability
requires: [gateway, agents]
timeout: 120
tags: [mechanical, e2e]
---

## Steps

1. [skill: create-project] name="E2E CAD Generation"
2. [assert] $project.id exists
3. [skill: run-agent-action] action="generate_cad" target="simple bracket with two mounting holes" project_id="$project.id"
4. [assert] status == "completed"
5. [assert] steps contains agent_code="MECH"
6. [assert] downloads exists
7. [screenshot] "cad-generation-complete"
8. [skill: check-sessions]
9. [assert] status == "completed"

## Cleanup
- Delete project "$project.id"
