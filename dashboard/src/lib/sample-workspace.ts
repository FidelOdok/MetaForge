import { AxiosError, type AxiosAdapter, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios';

/* Offline sample workspace for the digital twin (`?demo=1`). Illustrative data only: no gateway, solver or agent is called. */

// Generated from the hosted console sample workspace (illustrative Drone FC data).
const SAMPLE_WORKSPACE_SEED = {
  nodes: [
    {
      id: 'sample-pcb',
      name: 'flight-controller.pcb',
      domain: 'electronics',
      status: 'valid',
      type: 'work_product',
      properties: {
        wp_type: 'cad_model',
        revision: 3,
        mass_g: 28,
        supply_v: 5,
        source: 'Illustrative design',
      },
      updatedAt: '2026-09-22T12:00:00Z',
    },
    {
      id: 'sample-enclosure',
      name: 'enclosure.step',
      domain: 'mechanical',
      status: 'changed',
      type: 'work_product',
      properties: {
        wp_type: 'cad_model',
        revision: 3,
        material: 'Aluminium 6061',
        wall_mm: 2,
        mass_g: 84,
        source: 'Concept geometry',
      },
      updatedAt: '2026-09-22T12:10:00Z',
    },
    {
      id: 'sample-power',
      name: '5V power budget',
      domain: 'electronics',
      status: 'violation',
      type: 'constraint',
      properties: {
        peak_current_a: 2.61,
        limit_a: 2.4,
        margin_percent: -8.8,
        evidence: 'Illustrative calculation',
      },
      updatedAt: '2026-09-22T12:20:00Z',
    },
    {
      id: 'sample-clearance',
      name: 'Connector clearance',
      domain: 'mechanical',
      status: 'violation',
      type: 'constraint',
      properties: {
        required_mm: 1,
        measured_mm: 0.7,
        evidence: 'Illustrative geometry check',
      },
      updatedAt: '2026-09-22T12:30:00Z',
    },
    {
      id: 'sample-imu',
      name: 'IMU mount',
      domain: 'mechanical',
      status: 'valid',
      type: 'work_product',
      properties: {
        wp_type: 'cad_model',
        material: 'Nylon',
        mass_g: 6,
        source: 'Concept geometry',
      },
      updatedAt: '2026-09-22T11:00:00Z',
    },
    {
      id: 'sample-battery',
      name: '4S battery pack',
      domain: 'electronics',
      status: 'valid',
      type: 'work_product',
      properties: {
        capacity_mah: 2200,
        nominal_voltage_v: 14.8,
        mass_g: 210,
        source: 'Example specifications',
      },
      updatedAt: '2026-09-22T11:10:00Z',
    },
    {
      id: 'sample-firmware',
      name: 'telemetry-control.yaml',
      domain: 'firmware',
      status: 'changed',
      type: 'work_product',
      properties: {
        telemetry_hz: 10,
        estimated_current_ma: 340,
        revision: 4,
      },
      updatedAt: '2026-09-22T11:20:00Z',
    },
    {
      id: 'sample-thermal',
      name: 'Regulator thermal result',
      domain: 'simulation',
      status: 'stale',
      type: 'work_product',
      properties: {
        peak_temperature_c: 68,
        ambient_c: 25,
        revision_tested: 2,
        source: 'Synthetic result; no solver executed',
      },
      updatedAt: '2026-09-22T11:30:00Z',
    },
    {
      id: 'sample-stress',
      name: 'IMU shock result',
      domain: 'simulation',
      status: 'valid',
      type: 'work_product',
      properties: {
        shock_g: 40,
        peak_stress_mpa: 31,
        allowable_mpa: 48,
        source: 'Synthetic result; no solver executed',
      },
      updatedAt: '2026-09-22T10:00:00Z',
    },
    {
      id: 'sample-runtime',
      name: 'Flight duration requirement',
      domain: 'requirements',
      status: 'unknown',
      type: 'constraint',
      properties: {
        minimum_minutes: 18,
        verification: 'Not tested',
      },
      updatedAt: '2026-09-22T10:10:00Z',
    },
    {
      id: 'sample-bom',
      name: 'Flight-controller BOM',
      domain: 'supply_chain',
      status: 'valid',
      type: 'work_product',
      properties: {
        cost_gbp: 124.6,
        parts: 18,
        pricing: 'Illustrative only',
      },
      updatedAt: '2026-09-22T10:20:00Z',
    },
    {
      id: 'sample-drone',
      name: 'drone-assembly.urdf',
      domain: 'mechanical',
      status: 'valid',
      type: 'work_product',
      properties: {
        wp_type: 'robot_description',
        source: 'Concept robot with primitive geometry; simplified physics',
      },
      updatedAt: '2026-09-22T10:30:00Z',
      assembly: {
        parts: [
          {
            node_id: 'sample-pcb',
            link_name: 'base',
            material: 'aluminum',
          },
          {
            node_id: 'sample-imu',
            link_name: 'rotor',
            material: 'nylon',
          },
        ],
        joints: [
          {
            name: 'rotor_joint',
            type: 'revolute',
            base: 'base',
            follower: 'rotor',
            axis: [0, 0, 1],
            anchor: [0.12, 0, 0.03],
            limits: {
              lower: -3.14,
              upper: 3.14,
            },
          },
        ],
      },
    },
  ],
  relationships: [
    {
      id: 'sample-edge-0',
      sourceId: 'sample-pcb',
      targetId: 'sample-power',
      type: 'constrained_by',
      label: 'constrained by',
    },
    {
      id: 'sample-edge-1',
      sourceId: 'sample-pcb',
      targetId: 'sample-clearance',
      type: 'constrained_by',
      label: 'constrained by',
    },
    {
      id: 'sample-edge-2',
      sourceId: 'sample-pcb',
      targetId: 'sample-enclosure',
      type: 'contains',
      label: 'contains',
    },
    {
      id: 'sample-edge-3',
      sourceId: 'sample-pcb',
      targetId: 'sample-imu',
      type: 'contains',
      label: 'contains',
    },
    {
      id: 'sample-edge-4',
      sourceId: 'sample-battery',
      targetId: 'sample-power',
      type: 'depends_on',
      label: 'depends on',
    },
    {
      id: 'sample-edge-5',
      sourceId: 'sample-firmware',
      targetId: 'sample-power',
      type: 'depends_on',
      label: 'depends on',
    },
    {
      id: 'sample-edge-6',
      sourceId: 'sample-thermal',
      targetId: 'sample-pcb',
      type: 'validates',
      label: 'validates',
    },
    {
      id: 'sample-edge-7',
      sourceId: 'sample-stress',
      targetId: 'sample-imu',
      type: 'validates',
      label: 'validates',
    },
    {
      id: 'sample-edge-8',
      sourceId: 'sample-runtime',
      targetId: 'sample-battery',
      type: 'depends_on',
      label: 'depends on',
    },
    {
      id: 'sample-edge-9',
      sourceId: 'sample-bom',
      targetId: 'sample-pcb',
      type: 'contains',
      label: 'contains',
    },
    {
      id: 'sample-edge-10',
      sourceId: 'sample-drone',
      targetId: 'sample-pcb',
      type: 'contains',
      label: 'contains',
    },
    {
      id: 'sample-edge-11',
      sourceId: 'sample-drone',
      targetId: 'sample-enclosure',
      type: 'contains',
      label: 'contains',
    },
    {
      id: 'sample-edge-12',
      sourceId: 'sample-firmware',
      targetId: 'sample-pcb',
      type: 'implements',
      label: 'implements',
    },
  ],
  project: {
    id: 'sample-drone-fc',
    name: 'Drone FC · sample',
    description: 'Illustrative flight-controller engineering workspace.',
    status: 'active',
    agent_count: 3,
    created_at: '2026-09-13T09:00:00Z',
    last_updated: '2026-09-22T12:00:00Z',
    work_products: [
      {
        id: 'sample-pcb',
        name: 'flight-controller.pcb',
        type: 'cad_model',
        status: 'valid',
        updated_at: '2026-09-22T12:00:00Z',
      },
      {
        id: 'sample-enclosure',
        name: 'enclosure.step',
        type: 'cad_model',
        status: 'changed',
        updated_at: '2026-09-22T12:10:00Z',
      },
      {
        id: 'sample-power',
        name: '5V power budget',
        type: 'document',
        status: 'violation',
        updated_at: '2026-09-22T12:20:00Z',
      },
      {
        id: 'sample-clearance',
        name: 'Connector clearance',
        type: 'document',
        status: 'violation',
        updated_at: '2026-09-22T12:30:00Z',
      },
      {
        id: 'sample-imu',
        name: 'IMU mount',
        type: 'cad_model',
        status: 'valid',
        updated_at: '2026-09-22T11:00:00Z',
      },
      {
        id: 'sample-battery',
        name: '4S battery pack',
        type: 'document',
        status: 'valid',
        updated_at: '2026-09-22T11:10:00Z',
      },
      {
        id: 'sample-firmware',
        name: 'telemetry-control.yaml',
        type: 'document',
        status: 'changed',
        updated_at: '2026-09-22T11:20:00Z',
      },
      {
        id: 'sample-thermal',
        name: 'Regulator thermal result',
        type: 'document',
        status: 'stale',
        updated_at: '2026-09-22T11:30:00Z',
      },
      {
        id: 'sample-stress',
        name: 'IMU shock result',
        type: 'document',
        status: 'valid',
        updated_at: '2026-09-22T10:00:00Z',
      },
      {
        id: 'sample-runtime',
        name: 'Flight duration requirement',
        type: 'document',
        status: 'unknown',
        updated_at: '2026-09-22T10:10:00Z',
      },
      {
        id: 'sample-bom',
        name: 'Flight-controller BOM',
        type: 'document',
        status: 'valid',
        updated_at: '2026-09-22T10:20:00Z',
      },
      {
        id: 'sample-drone',
        name: 'drone-assembly.urdf',
        type: 'robot_description',
        status: 'valid',
        updated_at: '2026-09-22T10:30:00Z',
      },
    ],
  },
  threads: [
    {
      id: 'sample-thread-power',
      title: '5V rail over budget',
      scope_kind: 'project',
      scope_entity_id: 'sample-drone-fc',
      last_message_at: '2026-09-22T12:00:00Z',
      messages: [
        {
          id: 'sample-m1',
          actor_kind: 'user',
          actor_id: 'sample-user',
          content: 'Why is the 5V rail over budget? Show the trade-offs before changing the board.',
          status: 'completed',
          graph_ref_node: 'sample-pcb',
          graph_ref_label: 'flight-controller.pcb',
          created_at: '2026-09-22T11:45:00Z',
        },
        {
          id: 'sample-m2',
          actor_kind: 'agent',
          actor_id: 'sample-agent',
          content:
            'Sample analysis: peak draw is 2.61 A against a 2.40 A limit — 8.8% over budget.\n\n• Telemetry increased from 180 to 340 mA.\n• The added GPS draws 110 mA.\n• The latest enclosure revision leaves 0.7 mm connector clearance against a 1.0 mm requirement.\n\nOption A: reduce telemetry frequency and verify current on the bench.\nOption B: move GPS to the 3V3 rail, then check regulator headroom and interface levels.\n\nA proposal is ready in Changes. These are illustrative figures; no measurement or simulation has run.',
          status: 'completed',
          graph_ref_node: 'sample-power',
          graph_ref_label: '5V power budget',
          created_at: '2026-09-22T11:46:00Z',
        },
      ],
    },
    {
      id: 'sample-thread-clearance',
      title: 'Enclosure clearance',
      scope_kind: 'project',
      scope_entity_id: 'sample-drone-fc',
      last_message_at: '2026-09-21T16:00:00Z',
      messages: [
        {
          id: 'sample-m3',
          actor_kind: 'agent',
          actor_id: 'sample-agent',
          content:
            'Sample review: revise the connector cutout from 0.7 mm to at least 1.0 mm clearance, then regenerate the geometry check. The old evidence must be marked stale.',
          status: 'completed',
          graph_ref_node: 'sample-power',
          graph_ref_label: '5V power budget',
          created_at: '2026-09-22T11:46:00Z',
        },
      ],
    },
  ],
  proposal: {
    change_id: 'sample-proposal',
    agent_code: 'mechanical',
    description: 'Reduce telemetry from 10 Hz to 1 Hz; recheck current and control latency',
    diff: {
      'sample-firmware': {
        telemetry_hz: {
          before: 10,
          after: 1,
        },
      },
      validation_plan: [
        'Recompute 5V peak current',
        'Bench-check telemetry latency',
        'Revalidate thermal evidence',
      ],
    },
    work_products_affected: ['sample-firmware', 'sample-power', 'sample-thermal'],
    status: 'pending',
    session_id: 'sample-session',
    project_id: 'sample-drone-fc',
    created_at: '2026-09-22T12:00:00Z',
    decided_at: null,
    decision_reason: null,
    reviewer: null,
  },
  sessions: [
    {
      id: 'sample-session',
      agent_code: 'Engineering review · sample',
      task_type: 'Power budget review',
      status: 'completed',
      started_at: '2026-09-22T11:40:00Z',
      completed_at: '2026-09-22T12:00:00Z',
      run_id: null,
      events: [
        {
          id: 'event1',
          timestamp: '2026-09-22T12:00:00Z',
          type: 'observation',
          agent_code: 'mechanical',
          message: 'Illustrative review found a 0.21 A current-budget overrun.',
          data: {},
        },
        {
          id: 'event2',
          timestamp: '2026-09-22T12:00:00Z',
          type: 'decision',
          agent_code: 'mechanical',
          message: 'Keep baseline unchanged; request proposal review.',
          data: {},
        },
      ],
    },
  ],
  modelMetadata: {
    parts: [
      {
        name: 'Enclosure base',
        meshName: 'Enclosure base',
        children: [],
      },
      {
        name: 'Enclosure wall -43',
        meshName: 'Enclosure wall -43',
        children: [],
      },
      {
        name: 'Enclosure wall 43',
        meshName: 'Enclosure wall 43',
        children: [],
      },
      {
        name: 'End wall -30',
        meshName: 'End wall -30',
        children: [],
      },
      {
        name: 'End wall 30',
        meshName: 'End wall 30',
        children: [],
      },
      {
        name: 'Flight controller PCB',
        meshName: 'Flight controller PCB',
        children: [],
      },
      {
        name: 'STM32 processor',
        meshName: 'STM32 processor',
        children: [],
      },
      {
        name: 'IMU module',
        meshName: 'IMU module',
        children: [],
      },
      {
        name: '5V regulator',
        meshName: '5V regulator',
        children: [],
      },
      {
        name: 'Signal connector 0',
        meshName: 'Signal connector 0',
        children: [],
      },
      {
        name: 'Capacitor 0',
        meshName: 'Capacitor 0',
        children: [],
      },
      {
        name: 'Signal connector 1',
        meshName: 'Signal connector 1',
        children: [],
      },
      {
        name: 'Capacitor 1',
        meshName: 'Capacitor 1',
        children: [],
      },
      {
        name: 'Signal connector 2',
        meshName: 'Signal connector 2',
        children: [],
      },
      {
        name: 'Capacitor 2',
        meshName: 'Capacitor 2',
        children: [],
      },
      {
        name: 'Signal connector 3',
        meshName: 'Signal connector 3',
        children: [],
      },
      {
        name: 'Capacitor 3',
        meshName: 'Capacitor 3',
        children: [],
      },
      {
        name: 'Signal connector 4',
        meshName: 'Signal connector 4',
        children: [],
      },
      {
        name: 'Capacitor 4',
        meshName: 'Capacitor 4',
        children: [],
      },
      {
        name: 'Signal connector 5',
        meshName: 'Signal connector 5',
        children: [],
      },
      {
        name: 'Capacitor 5',
        meshName: 'Capacitor 5',
        children: [],
      },
      {
        name: 'Signal connector 6',
        meshName: 'Signal connector 6',
        children: [],
      },
      {
        name: 'Capacitor 6',
        meshName: 'Capacitor 6',
        children: [],
      },
      {
        name: 'Signal connector 7',
        meshName: 'Signal connector 7',
        children: [],
      },
      {
        name: 'Capacitor 7',
        meshName: 'Capacitor 7',
        children: [],
      },
      {
        name: 'USB connector',
        meshName: 'USB connector',
        children: [],
      },
      {
        name: 'Mount -33 -21',
        meshName: 'Mount -33 -21',
        children: [],
      },
      {
        name: 'Mount -33 21',
        meshName: 'Mount -33 21',
        children: [],
      },
      {
        name: 'Mount 33 -21',
        meshName: 'Mount 33 -21',
        children: [],
      },
      {
        name: 'Mount 33 21',
        meshName: 'Mount 33 21',
        children: [],
      },
      {
        name: 'Transparent cover',
        meshName: 'Transparent cover',
        children: [],
      },
    ],
    materials: [],
    stats: {
      triangleCount: 372,
      fileSize: 58000,
    },
  },
  urdf: '<?xml version="1.0"?><robot name="sample_drone"><link name="base"><inertial><mass value="0.8"/><inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.02"/></inertial><visual><geometry><box size="0.18 0.14 0.04"/></geometry><material name="body"><color rgba="0.3 0.35 0.42 1"/></material></visual><collision><geometry><box size="0.18 0.14 0.04"/></geometry></collision></link><link name="rotor"><inertial><mass value="0.06"/><inertia ixx="0.001" ixy="0" ixz="0" iyy="0.001" iyz="0" izz="0.001"/></inertial><visual><geometry><box size="0.16 0.018 0.006"/></geometry><material name="orange"><color rgba="1 0.35 0.04 1"/></material></visual><collision><geometry><box size="0.16 0.018 0.006"/></geometry></collision></link><joint name="rotor_joint" type="revolute"><parent link="base"/><child link="rotor"/><origin xyz="0.12 0 0.03"/><axis xyz="0 0 1"/><limit lower="-3.14" upper="3.14" effort="1" velocity="4"/></joint></robot>',
};

// ── Sample mode flag ────────────────────────────────────────────────────────
// `?demo=1` on first load switches the whole session to the illustrative
// workspace (evaluated once, like the hosted console). Everything lives in
// memory, so it resets on refresh.

export const SAMPLE_PROJECT_ID = SAMPLE_WORKSPACE_SEED.project.id;
export const SAMPLE_PROJECT_NAME = SAMPLE_WORKSPACE_SEED.project.name;
export const SAMPLE_MODEL_NAME = 'Scripted sample assistant';

let sampleMode =
  typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('demo') === '1';

/** True when the dashboard is serving the offline sample workspace. */
export function isSampleMode(): boolean {
  return sampleMode;
}

/** Test hook: toggle sample mode and reset the in-memory workspace. */
export function setSampleModeForTests(enabled: boolean): void {
  sampleMode = enabled;
  resetSampleWorkspace();
}

type SampleState = typeof SAMPLE_WORKSPACE_SEED;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

let state: SampleState = clone(SAMPLE_WORKSPACE_SEED);

/** Restore the pristine sample workspace (what a refresh does). */
export function resetSampleWorkspace(): void {
  state = clone(SAMPLE_WORKSPACE_SEED);
}

/** Static files served from `public/samples/` in sample mode. */
export function sampleFileUrl(nodeId: string): string {
  return nodeId === 'sample-drone' ? '/samples/drone.urdf' : '/samples/sample-evidence.json';
}

// ── Axios adapter ───────────────────────────────────────────────────────────

const SAMPLE_REVISIONS = [
  {
    revision: 3,
    created_at: '2026-09-22T11:00:00Z',
    content_hash: 'sample-r3',
    change_description: 'Sample: connector and telemetry revision',
    metadata_snapshot: {},
  },
  {
    revision: 2,
    created_at: '2026-09-20T10:00:00Z',
    content_hash: 'sample-r2',
    change_description: 'Sample: baseline evidence attached',
    metadata_snapshot: {},
  },
];

const SCRIPTED_REPLY = `Scripted sample response — no live agent or solver was called.

The example has two open issues: 2.61 A peak current exceeds the 2.40 A limit, and connector clearance is 0.7 mm against a 1.0 mm requirement.

Review the existing proposal in Changes, inspect the linked constraints, or switch to Model to explore the concept enclosure. All figures are illustrative.`;

function segment(path: string, index: number): string {
  return decodeURIComponent(path.split('/')[index] ?? '');
}

/** Resolve one request against the in-memory workspace; `undefined` = unsupported. */
function route(method: string, path: string, body: Record<string, unknown>): unknown {
  const now = new Date().toISOString();
  const s = state;
  if (method === 'get') {
    if (path === '/health') {
      return { status: 'healthy', version: 'sample', uptime_seconds: 0, timestamp: now, components: [] };
    }
    if (path === '/projects') return { projects: [s.project], total: 1 };
    if (path === `/projects/${s.project.id}`) return s.project;
    if (path === '/twin/nodes') return { nodes: s.nodes, total: s.nodes.length };
    if (path === '/twin/relationships') return { relationships: s.relationships };
    if (/^\/twin\/nodes\/[^/]+$/.test(path)) return s.nodes.find((n) => n.id === segment(path, 3));
    if (path.endsWith('/versions')) {
      return { work_product_id: segment(path, 3), revisions: SAMPLE_REVISIONS, total: SAMPLE_REVISIONS.length };
    }
    if (path.endsWith('/model')) {
      return { glb_url: '/samples/flight-controller.glb', metadata: s.modelMetadata, cached: true };
    }
    if (path.endsWith('/file')) {
      return path.includes('sample-drone')
        ? s.urdf
        : JSON.stringify(s.nodes.find((n) => n.id === segment(path, 3)), null, 2);
    }
    if (path.endsWith('/link')) return null;
    if (path === '/harness/providers') {
      return {
        active_provider: 'sample',
        active_model: SAMPLE_MODEL_NAME,
        providers: [{ id: 'sample', family: 'sample', configured: true, base_url: null }],
      };
    }
    if (path === '/harness/models') return { models: [{ id: SAMPLE_MODEL_NAME }] };
    if (path === '/harness/tools') {
      return [
        {
          id: 'twin.propose_change',
          name: 'Propose sample change',
          server: 'Sample twin',
          capability: 'Local illustrative proposal',
        },
      ];
    }
    if (path === '/chat/threads') return { threads: s.threads };
    if (/^\/chat\/threads\/[^/]+$/.test(path)) return s.threads.find((t) => t.id === segment(path, 3));
    if (path === '/assistant/proposals') return { proposals: [s.proposal], total: 1 };
    if (path === '/sessions') return { sessions: s.sessions, total: s.sessions.length };
    if (path === `/sessions/${s.sessions[0]?.id}`) return s.sessions[0];
    return undefined;
  }
  if (method === 'post') {
    if (path === '/chat/threads') {
      const thread = {
        id: `sample-thread-${Date.now()}`,
        title: 'New sample conversation',
        scope_kind: 'project',
        scope_entity_id: s.project.id,
        last_message_at: now,
        messages: [] as SampleState['threads'][number]['messages'],
      };
      s.threads.push(thread as SampleState['threads'][number]);
      return thread;
    }
    if (path.endsWith('/messages')) {
      const thread = s.threads.find((t) => t.id === segment(path, 3));
      if (!thread) return undefined;
      const message = {
        id: `sample-message-${Date.now()}`,
        actor_kind: 'user',
        actor_id: 'sample-user',
        content: String(body.content ?? ''),
        status: 'completed',
        graph_ref_node: String(body.graph_ref_node ?? ''),
        graph_ref_label: String(body.graph_ref_label ?? ''),
        created_at: now,
      };
      const messages = thread.messages as unknown as (typeof message)[];
      messages.push(message);
      messages.push({ ...message, id: `${message.id}-reply`, actor_kind: 'agent', actor_id: 'sample-agent', content: SCRIPTED_REPLY });
      thread.last_message_at = now;
      return message;
    }
    if (path === `/assistant/proposals/${s.proposal.change_id}/decide`) {
      const approve = body.decision === 'approve';
      (s.proposal as { status: string }).status = approve ? 'approved' : 'rejected';
      if (approve) {
        for (const node of s.nodes) {
          if (node.id === 'sample-firmware') (node.properties as Record<string, unknown>).telemetry_hz = 1;
          if (node.id === 'sample-power' || node.id === 'sample-thermal') (node as { status: string }).status = 'stale';
        }
      }
      return s.proposal;
    }
  }
  return undefined;
}

/** Axios adapter that answers every request from the sample workspace. */
export const sampleAdapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
  const path = (config.url ?? '').split('?')[0] ?? '';
  const method = (config.method ?? 'get').toLowerCase();
  const body = (typeof config.data === 'string' ? JSON.parse(config.data || '{}') : config.data ?? {}) as Record<
    string,
    unknown
  >;
  const data = route(method, path, body);
  if (data === undefined) {
    throw new AxiosError(
      'This action is unavailable in sample mode. Exit sample mode to use your gateway.',
      'SAMPLE_UNSUPPORTED',
      config,
    );
  }
  return { data: clone(data), status: 200, statusText: 'OK', headers: {}, config };
};

const installed = new WeakSet<AxiosInstance>();

/** Route an axios instance through the sample adapter while sample mode is on (idempotent). */
export function installSampleAdapter(client: AxiosInstance): void {
  if (installed.has(client)) return;
  installed.add(client);
  client.interceptors.request.use((config) => {
    if (sampleMode) config.adapter = sampleAdapter;
    return config;
  });
}
