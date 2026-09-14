/**
 * Outbound links and headline facts for the marketing site.
 *
 * Centralised so copy edits and a domain change are one-file jobs, and so the
 * phase claims below sit next to the tests that guard them. Those claims are
 * load-bearing: MetaForge's CLAUDE.md lists specific things the project must
 * never overstate (Phase 1 is 6-7 disciplines, KiCad is read-only until Phase
 * 2, the timeline is six months), and a landing page is exactly where such
 * claims drift. `src/__tests__/claims.test.ts` fails the build if they do.
 */

export const DOCS_URL = 'https://fidelodok.github.io/MetaForge/';
export const GITHUB_URL = 'https://github.com/FidelOdok/MetaForge';

/** Where "Open the dashboard" points. Set per Vercel environment. */
export const DASHBOARD_URL = import.meta.env.VITE_DASHBOARD_URL || DOCS_URL;

export const NAV_LINKS = [
  { label: 'How it works', href: '#how-it-works' },
  { label: 'Capabilities', href: '#capabilities' },
  { label: 'Roadmap', href: '#roadmap' },
  { label: 'Docs', href: DOCS_URL, external: true },
] as const;

/** The layers a request passes through, top to bottom. Mirrors docs/architecture.md. */
export const PIPELINE = [
  {
    name: 'Human intent',
    detail: 'A PRD and a constraints file. Plain text you write and review.',
    icon: 'edit_note',
  },
  {
    name: 'Gateway',
    detail: 'The HTTP/WebSocket front door. FastAPI.',
    icon: 'router',
  },
  {
    name: 'Orchestrator',
    detail: 'Resolves dependencies between disciplines and drives the propose-validate-refine loop.',
    icon: 'account_tree',
  },
  {
    name: 'Domain agents',
    detail: 'One specialist per engineering discipline. No generalist doing everything badly.',
    icon: 'smart_toy',
  },
  {
    name: 'Skills',
    detail: 'Atomic, schema-validated, independently testable units of expertise.',
    icon: 'extension',
  },
  {
    name: 'MCP + tool adapters',
    detail: 'KiCad, FreeCAD, CalculiX, SPICE — real tools, in containers, never invoked directly.',
    icon: 'handyman',
  },
  {
    name: 'Digital Twin',
    detail: 'The work-product graph that owns all design state. Versioned, diffable, queryable.',
    icon: 'hub',
  },
] as const;

/** What actually works today. Deliberately conservative — see the module note. */
export const CAPABILITIES = [
  {
    title: 'Mechanical',
    icon: 'precision_manufacturing',
    body: 'Parametric CAD authoring through FreeCAD and CadQuery, STEP-first with part names and assembly structure preserved. Meshing and stress validation via CalculiX.',
    tag: 'First vertical',
  },
  {
    title: 'Electronics',
    icon: 'memory',
    body: 'KiCad ERC and DRC runs, netlist and BOM export, Gerber output. Read-only against your KiCad files — MetaForge reports, it does not rewrite your schematic.',
    tag: 'Read-only in Phase 1',
  },
  {
    title: 'Supply chain',
    icon: 'inventory_2',
    body: 'Parametric and intent-driven component search across DigiKey and Mouser, with live pricing and availability resolved into the BOM.',
  },
  {
    title: 'Knowledge',
    icon: 'psychology',
    body: 'Ingest datasheets and reference material, then search it semantically. Component choices are recorded against the part they justify.',
  },
  {
    title: 'Compliance',
    icon: 'verified_user',
    body: 'UKCA, CE, FCC and PSTI checklists with an evidence trail, so the certification file is assembled as you design rather than reconstructed afterwards.',
  },
  {
    title: 'Review trail',
    icon: 'history',
    body: 'Every agent session is captured — reasoning, actions and decisions — into the digital thread. Design choices persist as typed, reviewable records.',
  },
] as const;

export const PRINCIPLES = [
  {
    title: 'Local-first',
    icon: 'home_work',
    body: 'The gateway, the agents and the tool containers run on your machine or your own box. Your design files stay where you put them. The hosted dashboard is a viewer you point at your own gateway — there is no MetaForge cloud holding your IP.',
  },
  {
    title: 'Git-native',
    icon: 'commit',
    body: 'Everything is a file in your repository: the requirements, the constraints, the decision log, the exported manufacturing set. Review a design change the way you review code, because it is a diff.',
  },
  {
    title: 'Human-in-the-loop',
    icon: 'front_hand',
    body: 'Read-only by default. Agents propose; you approve at gate checkpoints. Autonomous mode exists for when you want the loop to run, and it still stops at the gates you set.',
  },
  {
    title: 'Real tools, not descriptions of tools',
    icon: 'build',
    body: 'An agent that says a bracket passes is worth nothing. MetaForge runs CalculiX and hands you the result. Every tool adapter is a container with a version you can pin.',
  },
] as const;

export const PHASES = [
  {
    id: 'phase-1',
    label: 'Phase 1',
    version: 'v0.1 – v0.3',
    status: 'current' as const,
    headline: '6–7 specialist agents across 6–7 core disciplines',
    points: [
      'Electronics-heavy products: IoT, drones, embedded',
      'KiCad read-only — ERC, DRC, BOM and Gerber export',
      'Mechanical agent as the first end-to-end vertical: CAD → FEA → Digital Twin',
      'Six months total — three to four building, one to two on testing and docs',
    ],
  },
  {
    id: 'phase-2',
    label: 'Phase 2',
    version: 'v0.4 – v0.6',
    status: 'next' as const,
    headline: '19 agents, 19 disciplines',
    points: [
      'KiCad write capabilities',
      'Industrial Design and Prototyping disciplines added',
      'IDE assistants for VS Code, KiCad and FreeCAD',
    ],
  },
  {
    id: 'phase-3',
    label: 'Phase 3',
    version: 'v0.7 – v1.0',
    status: 'later' as const,
    headline: 'All 25 disciplines',
    points: ['The full engineering taxonomy, one specialist agent per discipline'],
  },
] as const;
