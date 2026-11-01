---
name: design-workflow
description: Design or revise a part in MetaForge. Use when the user asks to design or revise a part.
---

# Design or revise a part

Design a part in the active project.

Read the brief first so the part fits what already exists. Author geometry through the CAD tools, give every part a meaningful name (never `Part_1`), and commit with `twin.commit_geometry`.

A write may be held for approval — that is expected, not an error. Tell the user it is waiting in the dashboard rather than retrying.
