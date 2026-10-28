# MetaForge Phased Delivery Roadmap

> **Version**: 0.1 (Phase 0 — Spec & Design)
> **Status**: Draft
> **Last Updated**: 2026-03-02
> **Depends on**: [`architecture.md`](architecture.md), [`twin_schema.md`](twin_schema.md), [`skill_spec.md`](skill_spec.md), [`mcp_spec.md`](mcp_spec.md)
> **Referenced by**: [`governance.md`](governance.md)

## 1. Phase Overview

MetaForge is delivered in four phases, each building on the previous:

| Phase | Version | Timeline | Agents | Disciplines | Key Milestone |
|-------|---------|----------|--------|-------------|---------------|
| **Phase 0** | — | Month 0-1 | 0 | 0 | Specs finalized, architecture frozen |
| **Phase 1** | v0.1-0.3 | Month 2-7 | 6-7 | 6-7 | Mechanical vertical end-to-end |
| **Phase 2** | v0.4-0.6 | Month 8-13 | 19 | 19 | KiCad write, IDE extensions |
| **Phase 3** | v0.7-1.0 | Month 14-18 | 25 | 25 | Full 25-discipline coverage |

---

## 2. Phase 0 — Spec & Design (Month 0-1)

### Objective

Finalize all specification documents before development begins. No code is written in Phase 0.

### Deliverables

| Deliverable | Document | Status |
|-------------|----------|--------|
| System architecture | [`architecture.md`](architecture.md) | In Progress |
| Digital Twin schema | [`twin_schema.md`](twin_schema.md) | In Progress |
| Skill system spec | [`skill_spec.md`](skill_spec.md) | In Progress |
| MCP protocol spec | [`mcp_spec.md`](mcp_spec.md) | In Progress |
| Delivery roadmap | [`roadmap.md`](roadmap.md) (this document) | In Progress |
| Governance model | [`governance.md`](governance.md) | In Progress |

### Exit Criteria

- All 6 spec documents reviewed and approved.
- Architecture diagram finalized.
- ADR-001 (PydanticAI + Temporal) ratified.
- Development environment setup documented.
- CI/CD pipeline configured.

---

## 3. Phase 1 — MVP (v0.1-0.3, Month 2-7)

### Objective

Deliver the first end-to-end vertical: **Mechanical Agent**. A user can submit a CAD model, run FEA stress analysis, validate against constraints, and see results committed to the Digital Twin.

### Timeline: 6 months total

- **Months 2-5** (3-4 months): Core development
- **Months 6-7** (1-2 months): Testing, documentation, stabilization

### Scope

| What's Included | What's Excluded |
|-----------------|-----------------|
| 6-7 specialist agents (1:1 ratio to disciplines) | Industrial Design, Prototyping agents |
| Digital Twin with Neo4j graph engine | Multi-user collaboration |
| Skill system with registry and loader | IDE extensions |
| MCP protocol with 4 tool adapters | KiCad write capabilities |
| Gateway Service (FastAPI) | Production deployment tooling |
| Orchestrator (Temporal) | Auto-scaling |
| CLI (`forge` binary) | Web dashboard |
| Assistant Mode + Autonomous Mode | Advanced approval workflows |

### Phase 1 Agents and Disciplines

| # | Discipline | Agent | Key Skills | Epic |
|---|-----------|-------|------------|------|
| 1 | Mechanical Engineering | Mechanical Agent | validate_stress, check_tolerances, export_mesh | - |
| 2 | Electronics Engineering | Electronics Agent | run_erc, run_drc, export_bom | - |
| 3 | Embedded Software/Firmware | Firmware Agent | validate_pinmap, generate_scaffold, check_memory_budget | — |
| 4 | Simulation & Validation | Simulation Agent | run_thermal, run_circuit_sim, compare_results | — |
| 5 | Systems Engineering | Systems Agent | check_interfaces, validate_power_budget, resolve_dependencies | — |
| 6 | Testing & Reliability | Testing Agent | generate_test_plan, validate_test_coverage, create_bringup_checklist | — |

**Note**: The exact count is 6-7 agents depending on whether Product Definition is included as a standalone agent or handled by the Orchestrator in Phase 1.

### Phase 1 Tool Adapters

| Adapter | Capabilities | Read/Write |
|---------|-------------|------------|
| CalculiX | Stress analysis, thermal analysis, mesh validation | Read-only |
| FreeCAD | Mesh export, STEP/STL conversion, measurement | Read-only |
| KiCad | ERC, DRC, BOM export, Gerber export | **Read-only** |
| SPICE | DC/AC analysis, transient simulation | Read-only |

### Phase 1 Milestones

| Version | Milestone | Key Deliverables |
|---------|-----------|-----------------|
| v0.1 | Foundation | Twin Core, Skill Registry, MCP Client, Gateway shell |
| v0.2 | First Vertical | Mechanical Agent end-to-end (CAD → FEA → Twin) |
| v0.3 | Expansion | Remaining agents, CLI polish, test coverage ≥ 80% |

### Linear Epics (Phase 1)

| Epic | Scope |
|------|-------|
| Digital Twin Core | Graph engine, versioning, constraints, Twin API |
| Skill System | Registry, loader, schema validator, MCP bridge |
| MCP Infrastructure | Client, wire protocol, tool registry, adapters |
| Mechanical Agent | Stress validation, meshing, tolerances (first vertical) |

---

## 4. Phase 2 — Expansion (v0.4-0.6, Month 8-13)

### Objective

Expand to 19 agents covering 19 disciplines. Add KiCad write capabilities, IDE extensions, and multi-domain constraint validation.

### New in Phase 2

| Category | Additions |
|----------|----------|
| Agents | 12 new agents (19 total) |
| Tools | KiCad write (schematic generation, PCB auto-routing) |
| Interface | VS Code extension, KiCad plugin, FreeCAD plugin |
| Twin | Cross-domain constraint engine, advanced versioning |
| Workflow | Approval workflow UI, multi-agent coordination |

### Phase 2 New Agents

| # | Discipline | Layer | Rationale for Phase 2 |
|---|-----------|-------|-----------------------|
| 7 | Product Definition | Layer 1 | PRD parsing, requirements traceability |
| 8 | Industrial Design | Layer 1 | Form factor decisions, EVT/DVT gates |
| 9 | Prototyping & Fabrication | Layer 1 | Prototype planning, fab house selection |
| 10 | Manufacturing & Supply Chain | Layer 1 | DFM checks, supplier qualification |
| 11 | Certification & Compliance | Layer 1 | Regulatory pre-screening, test planning |
| 12 | Lifecycle Support | Layer 1 | Maintenance planning, spare parts |
| 13 | Product Management | Layer 2 | Feature prioritization, market alignment |
| 14 | Cost Engineering | Layer 2 | BOM costing, should-cost analysis |
| 15 | Supplier Management | Layer 2 | Supplier evaluation, alternate sourcing |
| 16 | Operations Planning | Layer 2 | Production planning, capacity modeling |
| 17 | Quality Management | Layer 2 | Quality gates, inspection planning |
| 18 | UX/Ergonomics | Layer 3 | Usability assessment, ergonomic checks |

### Linear Epics (Phase 2)

| Epic | Scope |
|------|-------|
| Electronics Agent | ERC, DRC, power budget, KiCad write adapter |
| Assistant Layer | IDE extensions, approval workflow, CLI enhancements |

---

## 5. Phase 3 — Full Coverage (v0.7-1.0, Month 14-18)

### Objective

Complete the 25-discipline framework. All Layer 3 and Layer 4 disciplines are covered.

### Phase 3 New Agents

| # | Discipline | Layer |
|---|-----------|-------|
| 19 | Field Engineering | Layer 3 |
| 20 | Safety Engineering | Layer 3 |
| 21 | Reliability Engineering | Layer 3 |
| 22 | Regulatory Compliance | Layer 4 |
| 23 | After-Sales Support | Layer 4 |
| 24 | Product Telemetry | Layer 4 |
| 25 | Sustainability | Layer 4 |

### Phase 3 Additions

- Multi-user collaboration
- Production deployment tooling
- Web dashboard
- Advanced analytics and reporting
- Field data integration (telemetry)
- Sustainability tracking and reporting

---

## 6. 25-Discipline Framework

Complete taxonomy of engineering disciplines covered by MetaForge, organized by layer:

### Layer 1: Core Engineering (12 disciplines)

| # | Discipline | Phase | Agent | Key Focus |
|---|-----------|-------|-------|-----------|
| 1 | Product Definition | Phase 2 | Product Definition Agent | PRD parsing, requirements |
| 2 | Industrial Design | Phase 2 | Industrial Design Agent | Form factor, aesthetics |
| 3 | Mechanical Engineering | Phase 1 | Mechanical Agent | Stress, tolerances, mesh |
| 4 | Electronics Engineering | Phase 1 | Electronics Agent | ERC, DRC, BOM |
| 5 | Embedded Software/Firmware | Phase 1 | Firmware Agent | Pinmap, scaffolding |
| 6 | Systems Engineering | Phase 1 | Systems Agent | Interfaces, power budget |
| 7 | Simulation & Validation | Phase 1 | Simulation Agent | FEA, SPICE |
| 8 | Prototyping & Fabrication | Phase 2 | Prototyping Agent | Fab planning, EVT/DVT |
| 9 | Testing & Reliability | Phase 1 | Testing Agent | Test plans, coverage |
| 10 | Manufacturing & Supply Chain | Phase 2 | Manufacturing Agent | DFM, supplier qual |
| 11 | Certification & Compliance | Phase 2 | Compliance Agent | Regulatory, standards |
| 12 | Lifecycle Support | Phase 2 | Lifecycle Agent | Maintenance, spares |

### Layer 2: Productization & Business (5 disciplines)

| # | Discipline | Phase | Agent | Key Focus |
|---|-----------|-------|-------|-----------|
| 13 | Product Management | Phase 2 | Product Mgmt Agent | Feature priority |
| 14 | Cost Engineering | Phase 2 | Cost Agent | BOM costing |
| 15 | Supplier Management | Phase 2 | Supplier Agent | Vendor evaluation |
| 16 | Operations Planning | Phase 2 | Operations Agent | Production planning |
| 17 | Quality Management | Phase 2 | Quality Agent | Quality gates |

### Layer 3: Deployment & Field Reality (4 disciplines)

| # | Discipline | Phase | Agent | Key Focus |
|---|-----------|-------|-------|-----------|
| 18 | Field Engineering | Phase 3 | Field Agent | Installation, commissioning |
| 19 | Safety Engineering | Phase 3 | Safety Agent | Hazard analysis, FMEA |
| 20 | UX/Ergonomics | Phase 2 | UX Agent | Usability, ergonomics |
| 21 | Reliability Engineering | Phase 3 | Reliability Agent | MTBF, failure analysis |

### Layer 4: Scale & Sustainability (4 disciplines)

| # | Discipline | Phase | Agent | Key Focus |
|---|-----------|-------|-------|-----------|
| 22 | Regulatory Compliance | Phase 3 | Regulatory Agent | Certification, filings |
| 23 | After-Sales Support | Phase 3 | After-Sales Agent | Warranty, returns |
| 24 | Product Telemetry | Phase 3 | Telemetry Agent | Field data, analytics |
| 25 | Sustainability | Phase 3 | Sustainability Agent | Carbon footprint, recycling |

---

## 7. Cost Estimates

### Phase 1 (MVP): ~$470K

| Category | Cost | Details |
|----------|------|---------|
| Engineering | $400K | 4 engineers x 6 months |
| Infrastructure | $50K | Neo4j, Temporal, CI/CD, Docker registry |
| LLM API costs | $15K | Development and testing usage |
| Miscellaneous | $5K | Tools, licenses |

### Phase 2 (Expansion): +$360K

| Category | Cost | Details |
|----------|------|---------|
| Engineering | $300K | 3 engineers x 6 months |
| Infrastructure | $50K | Scaling, monitoring, IDE extension hosting |
| LLM API costs | $10K | Expanded testing |

### Phase 3 (Full Coverage): +$540K

| Category | Cost | Details |
|----------|------|---------|
| Engineering | $300K | 3 engineers x 6 months |
| Infrastructure | $180K | Production deployment, multi-tenant, SLA monitoring |
| LLM API costs | $50K | Full coverage testing, field validation |
| Miscellaneous | $10K | Certifications, compliance |

### Total: ~$1.37M over 18 months

---

## 8. Success Metrics

### Phase 1

| Metric | Target |
|--------|--------|
| Mechanical vertical completes end-to-end | CAD → FEA → Constraint check → Twin commit |
| Test coverage | ≥ 80% across core modules |
| Skill execution success rate | ≥ 95% on valid inputs |
| Tool adapter availability | ≥ 99% during development |
| CLI usability | `forge setup` → first validation in < 5 minutes |

### Phase 2

| Metric | Target |
|--------|--------|
| 19 agents operational | All pass integration tests |
| KiCad schematic generation | Valid schematics from PRD |
| IDE extension adoption | VS Code extension installable from marketplace |
| Cross-domain constraint evaluation | < 10 seconds for typical design |
| Agent coordination | 3+ agents collaborating on a single workflow |

### Phase 3

| Metric | Target |
|--------|--------|
| 25 agents operational | All pass integration tests |
| End-to-end product design | PRD → manufacturing files in < 1 hour (simple designs) |
| Field data integration | Telemetry from deployed devices feeds Twin |
| Multi-user support | 5+ concurrent users on same project |
| Documentation coverage | 100% of public APIs documented |

---

## 9. Risk Register

| Risk | Impact | Probability | Mitigation |
|------|--------|------------|------------|
| LLM quality degrades for domain tasks | High | Medium | Skill system is deterministic — LLM only selects skills, doesn't execute logic. Multi-provider support via PydanticAI. |
| CalculiX/FreeCAD container performance | Medium | Medium | Warm container pool, timeout tuning, async execution. |
| Neo4j scaling limits for large designs | Medium | Low | Index strategy, query optimization, sharding plan for Phase 3. |
| Temporal complexity for simple workflows | Low | Medium | Simple workflows bypass Temporal and run inline. |
| KiCad write adapter reliability | High | Medium | Extensive validation, snapshot/rollback, human review gate. |
| Team ramp-up on PydanticAI + Temporal | Medium | Medium | ADR-001 includes spike/POC. Training budget allocated. |
| Scope creep in Phase 1 | High | High | Strict 6-7 agent limit. No Phase 2 features in Phase 1. Weekly scope reviews. |
| LLM API cost overruns | Medium | Medium | Token budgets per skill, caching, local model fallback investigation. |
| Docker security vulnerabilities | High | Low | Base image scanning, no network access, read-only mounts, regular updates. |
