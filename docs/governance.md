# Contribution & Governance

> **Version**: 0.1 (Phase 0 — Spec & Design)
> **Status**: Draft
> **Last Updated**: 2026-03-02
> **Depends on**: All other spec documents

## 1. Repository Ownership

Each top-level directory has a designated owner responsible for code review and architectural decisions.

| Directory | Owner | Responsibility |
|-----------|-------|---------------|
| `cli/` | CLI Team | CLI commands, TypeScript build, UX |
| `api_gateway/` | Platform Team | FastAPI routes, auth, WebSocket handlers |
| `orchestrator/` | Platform Team | Temporal workflows, agent scheduling |
| `twin_core/` | Platform Team | Neo4j graph engine, versioning, constraints |
| `skill_registry/` | Platform Team | Registry, loader, schema validation |
| `domain_agents/` | Agent Team | Agent implementations, domain skills |
| `mcp_core/` | Platform Team | MCP client, wire protocol |
| `tool_registry/` | Platform Team | Tool catalog, execution engine, adapters |
| `ide_assistants/` | IDE Team | VS Code, KiCad, FreeCAD extensions |
| `tests/` | All Teams | Cross-cutting tests (unit, integration, e2e) |
| `docs/` | All Teams | Specifications, guides |

---

## 2. Branching Strategy

### Branch Naming

| Pattern | Purpose | Example |
|---------|---------|---------|
| `main` | Stable, reviewed code only | — |
| `feat/<issue-id>/<short-desc>` | New features | `feat/123/mechanical-agent` |
| `fix/<issue-id>/<short-desc>` | Bug fixes | `fix/456/twin-merge-conflict` |
| `<issue-id>/<short-desc>` | Tracker issue work | `789/phase-0-specs` |
| `docs/<short-desc>` | Documentation changes | `docs/skill-spec-update` |
| `release/v<version>` | Release preparation | `release/v0.1.0` |

### Rules

1. **Never commit directly to `main`**. All changes go through pull requests.
2. Feature branches are created from `main` and merged back via PR.
3. Branches are deleted after merge.
4. Rebase before merge to keep linear history (no merge commits).

### Pull Request Requirements

| Requirement | Details |
|-------------|---------|
| Reviewers | At least 1 approval from directory owner |
| Tests | All existing tests pass; new code includes tests |
| Lint | `ruff check .` passes (Python) or `eslint` passes (TypeScript) |
| Types | `mypy .` passes (Python, strict mode) or `tsc --noEmit` (TypeScript) |
| Docs | Public API changes include docstring updates |
| Linear | PR description references the tracker issue |

---

## 3. Contributing a New Skill

Follow the complete guide in [`skill_spec.md` Section 12](skill_spec.md#12-writing-a-new-skill). Summary checklist:

### Checklist

- [ ] Linear issue created under the appropriate agent epic
- [ ] Directory created: `domain_agents/<domain>/skills/<skill_name>/`
- [ ] `definition.json` follows schema (see [`skill_spec.md` Section 3](skill_spec.md#3-definitionjson-schema))
- [ ] `schema.py` with Pydantic input/output models, all fields have descriptions
- [ ] `handler.py` subclasses `SkillBase`, implements `execute()`
- [ ] `tests.py` with pytest async tests — happy path, error path, edge cases
- [ ] `SKILL.md` with human-readable documentation
- [ ] All tool access goes through `self.context.mcp.invoke()` (never direct calls)
- [ ] Tests pass: `pytest domain_agents/<domain>/skills/<skill_name>/tests.py`
- [ ] Lint passes: `ruff check domain_agents/<domain>/skills/<skill_name>/`
- [ ] Types pass: `mypy domain_agents/<domain>/skills/<skill_name>/`
- [ ] PR created with "Closes MET-XX" in description

---

## 4. Contributing a New Tool Adapter

Follow the Tool Adapter SDK guide in [`mcp_spec.md` Section 9](mcp_spec.md#9-tool-adapter-sdk).

### Checklist

- [ ] Linear issue created under the MCP Infrastructure epic
- [ ] Directory created: `tool_registry/tools/<adapter_name>/`
- [ ] `server.py` subclasses `McpToolServer`, registers tool manifests
- [ ] `Dockerfile` with tool binary installed, workspace volume, no network
- [ ] `requirements.txt` with Python dependencies
- [ ] `tests/test_server.py` with pytest tests (mock tool binary for unit tests)
- [ ] Tool manifests include accurate `input_schema` and `output_schema`
- [ ] Resource limits set appropriately for the tool
- [ ] Docker image builds: `docker build -t metaforge/adapter-<name>:0.1 .`
- [ ] Integration test: tool responds to `health/check` and `tool/list`
- [ ] PR created with "Closes MET-XX" in description

---

## 5. Contributing a New Agent

### Checklist

- [ ] Linear epic created for the new agent
- [ ] Discipline verified in 25-discipline framework ([`roadmap.md` Section 6](roadmap.md#6-25-discipline-framework))
- [ ] Phase assignment confirmed (don't implement Phase 2+ agents in Phase 1)
- [ ] Directory created: `domain_agents/<domain>/`
- [ ] Agent implementation using PydanticAI `Agent` class
- [ ] System prompt written for the domain
- [ ] At least 3 skills implemented (following skill checklist above)
- [ ] Agent registered with the orchestrator
- [ ] Integration test: agent can execute a skill and update the Twin
- [ ] PR created with "Closes MET-XX" in description

---

## 6. Spec Change Process

Specification documents (`docs/*.md`) are living documents. Changes follow an RFC process:

### Process

1. **Propose**: Create a Linear issue with label `RFC` describing the change and rationale.
2. **Discuss**: Team reviews the issue. Comments and alternatives are discussed.
3. **Decide**: Issue author updates the proposal based on feedback. A team member approves.
4. **Implement**: Author creates a PR with the spec change. PR references the RFC issue.
5. **Merge**: PR is reviewed and merged. RFC issue is closed.

### Rules

- Spec changes that affect multiple documents must update all affected docs in the same PR.
- Breaking changes to the Twin schema, Skill spec, or MCP spec require ADR (Architecture Decision Record).
- ADRs are numbered sequentially (ADR-001, ADR-002, ...) and stored in `docs/adr/`.

---

## 7. Coding Standards

### Python (Platform Core)

| Tool | Configuration | Enforcement |
|------|--------------|-------------|
| **Ruff** | `ruff.toml` in repo root | CI check, pre-commit hook |
| **mypy** | Strict mode (`--strict`) | CI check |
| **Pydantic** | v2 with `model_config = ConfigDict(strict=True)` where appropriate | Runtime validation |
| **pytest** | Async tests with `pytest-asyncio` | CI required |
| **structlog** | Structured JSON logging | Required for all modules |

#### Python Style Rules

- **Type hints**: All function signatures must have type annotations.
- **Docstrings**: All public classes and functions must have docstrings (Google style).
- **Imports**: Use absolute imports. No wildcard imports.
- **Async**: Use `async`/`await` for all I/O operations.
- **Models**: Use Pydantic `BaseModel` for all data structures that cross module boundaries.
- **Errors**: Use domain-specific exception classes, not bare `Exception`.
- **Tests**: Minimum 80% coverage for new code. Use `pytest.mark.asyncio` for async tests.

### Node.js / TypeScript (CLI Only)

| Tool | Configuration | Enforcement |
|------|--------------|-------------|
| **TypeScript** | Strict mode (`strict: true` in `tsconfig.json`) | CI check |
| **ESLint** | `eslint.config.js` in `cli/` | CI check, pre-commit hook |
| **Prettier** | `.prettierrc` in `cli/` | CI check, pre-commit hook |
| **Jest** | Test runner for CLI commands | CI required |

#### TypeScript Style Rules

- **Strict mode**: No `any` types. Explicit return types on exported functions.
- **Naming**: camelCase for variables/functions, PascalCase for types/classes.
- **Imports**: ES module imports. No `require()`.

---

## 8. Linear Workflow

### Issue Lifecycle

```
Backlog → Todo → In Progress → In Review → Done
```

| Status | Meaning |
|--------|---------|
| **Backlog** | Identified but not prioritized |
| **Todo** | Prioritized for current phase |
| **In Progress** | Actively being worked on (branch created) |
| **In Review** | PR created, awaiting review |
| **Done** | PR merged, issue closed |

### Issue Labels

| Label | Purpose |
|-------|---------|
| `Epic` | Parent issue grouping related work |
| `Phase 0` / `Phase 1` / `Phase 2` / `Phase 3` | Phase assignment |
| `Documentation` | Documentation task |
| `RFC` | Specification change proposal |
| `Bug` | Bug report |
| `Enhancement` | Feature improvement |
| `Tech Debt` | Technical debt cleanup |
| `Blocked` | Cannot proceed (add comment explaining why) |

### Epic Structure

| Epic | Scope | Phase |
|------|-------|-------|
| - | Phase 0: Specification documents | Phase 0 |
| - | Digital Twin Core | Phase 1 |
| - | Skill System | Phase 1 |
| - | MCP Infrastructure | Phase 1-2 |
| - | Mechanical Agent (first vertical) | Phase 1 |
| - | Electronics Agent | Phase 2 |
| - | Assistant Layer (IDE extensions) | Phase 2-3 |

---

## 9. Release Process

### Versioning

MetaForge follows [Semantic Versioning](https://semver.org/):

- **v0.x.y**: Pre-1.0 development. Minor bumps may include breaking changes.
- **v1.0.0**: First stable release (end of Phase 3).

### Phase Gates

Each phase version increment requires:

| Gate | Requirement |
|------|------------|
| All epics for the phase are Done | Linear epic status = Done |
| Test coverage ≥ 80% | CI coverage report |
| All P0/P1 bugs resolved | No open Critical/High bugs |
| Spec documents up to date | docs/ matches implementation |
| CHANGELOG.md updated | Summarizes changes since last release |
| Release branch created | `release/v<version>` branch |
| Tag created | `v<version>` tag on merge to main |

### Release Steps

1. Create release branch: `release/v0.1.0` from `main`.
2. Update version numbers in `pyproject.toml` and `cli/package.json`.
3. Update `CHANGELOG.md`.
4. Create PR to `main`.
5. After merge, tag: `git tag v0.1.0`.
6. Push tag: `git push origin v0.1.0`.
7. Create GitHub Release from tag with changelog contents.
