---
name: smoke-navigation
description: Visits all dashboard pages and verifies they load without errors
requires: [gateway]
timeout: 60
tags: [smoke]
---

## Steps

1. [navigate] /projects
2. [screenshot] "projects-page"
3. [assert] page contains heading
4. [skill: check-api-health]
5. [assert] overall_status == "healthy"
6. [navigate] /assistant
7. [screenshot] "assistant-page"
8. [assert] page contains heading
9. [navigate] /sessions
10. [screenshot] "sessions-page"
11. [assert] page contains heading
12. [navigate] /approvals
13. [screenshot] "approvals-page"
14. [assert] page contains heading
15. [navigate] /bom
16. [screenshot] "bom-page"
17. [assert] page contains heading
18. [navigate] /twin
19. [screenshot] "twin-page"
20. [assert] page contains heading
21. [assert] console has no critical errors
