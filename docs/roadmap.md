# MetaForge Roadmap

> **Status:** reviewed 2026-10-28 against the code on `main` and the project's
> open work. Releases are 0.3.x. **No delivery dates are committed.** Where a
> date appears below it is the original plan, labelled as such.
>
> **Depends on:** [`architecture.md`](architecture.md), [`capability-matrix.md`](capability-matrix.md)
> **Referenced by:** [`governance.md`](governance.md)

## 1. How to read this roadmap

This page says what is **released**, what is **in progress**, what is **planned**
and what is **not started**. It is a statement of intent, not a promise.

| Label | Means |
|---|---|
| **Released** | In `main`, tested, and listed in the [capability matrix](capability-matrix.md) |
| **Early** | Released but narrow, or not yet proven on real projects. The matrix says where |
| **In progress** | Being built now. It may change before release |
| **Planned** | Agreed direction, not started. No date |
| **Not started** | In the original plan, with no work behind it yet |

Two rules keep it honest. A feature moves to *Released* only when it is in the
capability matrix. A date is added only when someone has agreed to it.

The detailed plans, specifications and vision live in the separate
`MetaForge-Planner` repository. This page describes what is built and where it is
heading, so it will not match every item in that repository.

## 2. Where MetaForge stands

The first roadmap (written in early 2026) planned four phases over about 18 months.
Development did not follow those phases in order. Several Phase 2 and Phase 3 items
shipped early, and some Phase 1 ideas were replaced by something better.

| Original phase | Original scope | Status today |
|---|---|---|
| **Phase 0**: specifications | Twin schema, skill spec, MCP rules, governance | **Done** |
| **Phase 1**: core platform (v0.1 to 0.3) | Twin graph, MCP tools, skills, mechanical vertical, CLI | **Done**, and well beyond: seven specialist agents and a gated design-flow engine |
| **Phase 2**: expansion | More agents, KiCad write, IDE extensions, approval workflow, container tools | **Mostly done**: KiCad write, approvals and container tools are released. Agents stop at seven. IDE extensions are experimental |
| **Phase 3**: collaboration and scale | Web dashboard, diffing, multi-user, optimisation, composition | **Partly done**: the dashboard, revision compare, an optimisation loop and composable flows are released. Multi-user is the hosted service, outside this repository |
| **Phase 4**: ecosystem | Skill marketplace, tool SDK, remote compute | **Not started**, apart from a plugin marketplace for Claude Code and Codex |

## 3. Released

### The platform

- Gateway (FastAPI), orchestrator on Temporal, a skill registry and an MCP tool
  registry. Every tool call passes the registry, and all tools run in containers.
- Seven specialist agents: mechanical, electronics, firmware, simulation, industrial
  design, compliance and supply chain.
- Signed container images, an image-only host for servers, and credentials that are
  generated rather than shipped with defaults.

### Design flows

- Three versioned flows (`design_v1`, `hardware_v1`, `mech_v1`), built from a shared
  spine and per-discipline fragments, frozen at approval, with a visual editor.
- Review gates with approve, retry, rework and reject, and one approvals surface for
  the dashboard, the CLI and agents.
- Live progress: tool calls and assemblies appear as a run proceeds.
- Per-run token and cost accounting, per-role model routing and prompt caching.

### The digital twin

- Items with numbered revisions, baselines and drafts that become the baseline only
  at a gate; revision compare; requirement, evidence and constraint records.
- A requirements and intent harness: typed requirements, completeness and conflict
  checks, and a completion verdict that treats "no data" as not passed.
- A design loop that proposes, builds, simulates and evaluates against constraints.
- The twin graph on the dashboard, with links to constraints, baselines and parts.

### Engineering tools

- CAD: CadQuery and FreeCAD authoring, STEP and GLB viewing, named parts, a material
  library, and studio renders (Blender).
- Analysis: CalculiX (static, modal, thermal), SPICE, and steady internal flow with
  OpenFOAM (**Early**: turbulence is not validated).
- Electronics: KiCad ERC and DRC, BOM and Gerber export, and generation of new
  schematic, board and routed projects (**Early**: new projects only). Firmware
  build, static check and emulation.
- Manufacturing: process ranking before design, DFM review after it, a machine and
  process library, and distributor lookups (Digi-Key, Mouser, Nexar, Farnell, Arrow).
- Knowledge: document and datasheet ingestion with cited search.

### Using it

- Dashboard (20 routes), `forge` CLI and interactive TUI, and an offline sample
  workspace.
- Claude Code and Codex plugins, over HTTP or on the local machine, with writes held
  for approval. Every agent session is captured and reviewable.

## 4. In progress

| Theme | What is being built |
|---|---|
| **Work product identity** | One truth per project: stable keys, revisions and baselines extended to every record type |
| **Skill fidelity** | Making each skill and tool do exactly what its documentation says, with tests that prove it |
| **Airflow analysis** | Validating turbulence models, and deriving fluid surfaces from solid models |
| **Generative simulation** | Gazebo and Omniverse simulation behind the opt-in adapters |
| **Headless CAD authoring** | Deeper FreeCAD authoring and a kinematic-solver backend |
| **Hosted plugin** | Moving the hosted-service plugin to the hosted repository, with its own marketplace |
| **Hardening** | Binding every published port of a server install to loopback, behind a tunnel |

## 5. Planned

None of these has a date.

| Theme | Direction |
|---|---|
| **Manufacturing integration** | Per-process capability packs, manufacturing outputs and connectors to fabricators and vendors |
| **Reference library** | Materials, components, stock, organisations, processes, standards and reference designs in one library |
| **Client intelligence mode** | Policy-based approvals so an external agent can be trusted with defined kinds of write |
| **Harness plugins, next versions** | Production authentication, team gateways and relays; claude.ai and ChatGPT connectors; in-chat widgets, design loop, optimisation and manufacturing tools |
| **Target lifecycle** | Typed lifecycle objects, guardrails and evidence-backed maturity levels |
| **Robot-grade simulation** | Modal and contact analysis, motion loads and dynamics |
| **Industrial design depth** | Ergonomics data, so form-factor checks use sourced figures |
| **Workflow quality** | Test suites for generated workflows and the lifecycle |
| **Large twins** | A layout that scales to graphs of hundreds of links |
| **Setup** | An installer that detects the machine and offers the adapters and profiles that suit it (suggested, not scheduled) |

## 6. 25-Discipline Framework

The long-term aim is one agent per engineering discipline. Today seven exist, and
several other disciplines are partly served by tools without a dedicated agent.
Status is judged from the [capability matrix](capability-matrix.md).

### Layer 1: Core engineering

| # | Discipline | Status |
|---|-----------|--------|
| 1 | Product Definition | Partial: requirements and intent harness, no standalone agent |
| 2 | Industrial Design | Released: agent and renders. Ergonomics data not yet available |
| 3 | Mechanical Engineering | Released |
| 4 | Electronics Engineering | Released |
| 5 | Embedded Software and Firmware | Released |
| 6 | Systems Engineering | Partial: power budget and interface tools, no standalone agent |
| 7 | Simulation and Validation | Released |
| 8 | Prototyping and Fabrication | Early: machine library and job dispatch |
| 9 | Testing and Reliability | Partial: test plans and bring-up checklists; no reliability analysis |
| 10 | Manufacturing and Supply Chain | Early: process ranking, DFM and sourcing |
| 11 | Certification and Compliance | Early: checklists, not certification |
| 12 | Lifecycle Support | Not started |

### Layer 2: Productization and business

| # | Discipline | Status |
|---|-----------|--------|
| 13 | Product Management | Not started |
| 14 | Cost Engineering | Early: BOM pricing and manufacturing cost estimates |
| 15 | Supplier Management | Early: distributor lookups and vendor connectors |
| 16 | Operations Planning | Not started |
| 17 | Quality Management | Not started |

### Layer 3: Deployment and field reality

| # | Discipline | Status |
|---|-----------|--------|
| 18 | Field Engineering | Not started |
| 19 | Safety Engineering | Early: hazard analysis records |
| 20 | UX and Ergonomics | Not started |
| 21 | Reliability Engineering | Not started |

### Layer 4: Scale and sustainability

| # | Discipline | Status |
|---|-----------|--------|
| 22 | Regulatory Compliance | Early: CE, UKCA, FCC and PSTI coverage tracking |
| 23 | After-Sales Support | Not started |
| 24 | Product Telemetry | Not started |
| 25 | Sustainability | Not started |

## 7. Not planned

MetaForge will not:

- certify a design or stand in for a notified body, a test lab or physical testing;
- act without approval: any action with a real-world effect is held for a person;
- output anything that cannot be versioned, reviewed and built.

## 8. Risks to the plan

| Risk | How it is handled |
|---|---|
| AI output is wrong or invented | Gates, held writes, sourced material figures, and a completion verdict that treats missing evidence as not passed |
| Tool integrations drift | Every tool goes through the MCP registry, and a test fails when plugin guidance names a tool that is not registered |
| Claims outrun the code | This page and the capability matrix are reviewed together, and a feature is *Released* only when it is listed there |
| Scope grows faster than review capacity | Work is ordered by theme, with the in-progress list kept short |
| Analysis is trusted beyond its validation | Limits are stated per tool (for example, airflow turbulence is not validated) |
