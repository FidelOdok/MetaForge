import { AxiosError, type AxiosAdapter, type AxiosInstance, type InternalAxiosRequestConfig } from 'axios';

/* Offline sample workspace for the digital twin (`?demo=1`). Illustrative data only: no gateway, solver or agent is called. */

// FORGE-287: illustrative shape of one persisted DesignLoopIteration --
// mirrors twin_core.models.design_loop_iteration.DesignLoopIteration's own
// field names (snake_case; this mock, unlike the rest of this file, mirrors
// the REST route's pass-through shape rather than the dashboard's usual
// camelCase contract, since the real route itself is a thin pass-through).
interface SampleDesignLoopIteration {
  id: string;
  iteration_number: number;
  parameter_name: string;
  parameter_value: number;
  metric: string;
  objective_value: number;
  constraints_status: Record<string, number>;
  feasible: boolean;
  status: 'candidate' | 'converged' | 'infeasible';
  is_winner: boolean;
  approved: boolean;
  approved_by: string | null;
}

interface SampleDesignLoop {
  loop_id: string;
  status: 'optimal' | 'infeasible' | 'already_feasible_at_min';
  iterations: SampleDesignLoopIteration[];
}

// FORGE-290: illustrative shape of one gate-review attempt -- mirrors
// AttemptPromotionResponse's own camelCase REST contract (unlike the
// design-loop mocks above, which mirror the MCP tool's snake_case
// pass-through) since this route DOES wrap its response in a proper
// Pydantic model server-side.
interface SampleRequiredClaimResult {
  requirementId: string;
  requirementName: string;
  decision: 'pass' | 'uncertain' | 'fail' | 'waived';
  detail: string;
  waiverId: string | null;
}

interface SampleMaturityGate {
  gateId: string;
  level: string;
  promoted: boolean;
  blockedReason: string | null;
  decidedBy: string | null;
  comment: string | null;
  createdAt: string;
  results?: SampleRequiredClaimResult[];
}

// FORGE-289 (gap G-G3): illustrative shape of one Decision, mirroring
// api_gateway/twin/decision_routes.py's own GET /v1/decisions pass-through
// (snake_case, same convention the design-loop mocks above use).
interface SampleDecision {
  id: string;
  title: string;
  rationale: string;
  alternatives: { option: string; reason_rejected: string }[];
  parent_refs: string[];
  evidence_refs: string[];
  created_at: string | null;
}

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
  // FORGE-257: two illustrative requirements for the Requirements panel --
  // one clean, one with real lint issues and a bound-conflict, so the
  // sample workspace can demonstrate flags/conflicts/completeness without
  // a live gateway.
  requirementsReport: {
    requirements: [
      {
        id: 'sample-req-mass-max',
        name: 'mass_limit',
        text: 'The drone shall weigh at most 0.8 kg.',
        severity: 'error',
        clarity: 'pass',
        atomicity: 'pass',
        quantified: 'pass',
        traceability: 'pass',
        verificationReady: 'pass',
        conflicts: ['sample-req-mass-min'],
      },
      {
        id: 'sample-req-mass-min',
        name: 'mass_floor',
        text: 'The drone shall weigh at least 1 kg.',
        severity: 'error',
        clarity: 'pass',
        atomicity: 'pass',
        quantified: 'pass',
        traceability: null,
        verificationReady: 'pass',
        conflicts: ['sample-req-mass-max'],
      },
      {
        id: 'sample-req-speed',
        name: 'vague_speed',
        text: 'The drone should fly fast.',
        severity: 'warning',
        clarity: 'fail',
        atomicity: 'pass',
        quantified: 'fail',
        traceability: null,
        verificationReady: 'fail',
        conflicts: [],
      },
    ],
    conflicts: [
      {
        aId: 'sample-req-mass-max',
        aName: 'mass_limit',
        bId: 'sample-req-mass-min',
        bName: 'mass_floor',
        detail: 'one requires at most 0.8kg, the other at least 1kg',
      },
    ],
    completeness: {
      productType: 'generic',
      covered: ['mechanical'],
      missing: ['safety', 'power', 'environmental', 'verification'],
    },
  },
  // FORGE-318: an illustrative evidence matrix -- one FAIL with real
  // numbers, one PASS with tier-0 lineage, one STALE (evidence went stale
  // after a change) -- so the sample workspace demonstrates the ticket's
  // own acceptance example without a live gateway.
  requirementMatrix: {
    rows: [
      {
        requirementId: 'sample-req-mass-max',
        requirementName: 'mass_limit',
        limitText: '<= 0.8 kg',
        status: 'fail',
        detail: 'value 0.94 exceeds limit 0.8 (margin -0.14)',
        artefactIds: ['sample-artefact-frame'],
        evidence: [
          {
            id: 'sample-evidence-mass',
            method: 'twin.rank_sensitivity',
            tier: null,
            value: 0.94,
            limit: 0.8,
            margin: -0.14,
            staleness: 'current',
          },
        ],
        // FORGE-258 (gap G-A2): not declared -- illustrates the "not
        // declared" flag independently of the live pass/fail status.
        verificationMethod: '',
        expectedEvidence: '',
      },
      {
        requirementId: 'sample-req-speed',
        requirementName: 'vague_speed',
        limitText: 'shall fly at ≥ 8 m/s cruise',
        status: 'pass',
        detail: 'value 9.2 within limit 8 (margin 1.2)',
        artefactIds: ['sample-artefact-frame'],
        evidence: [
          {
            id: 'sample-evidence-speed',
            method: 'twin.evaluate_metric',
            tier: 0,
            value: 9.2,
            limit: 8,
            margin: 1.2,
            staleness: 'current',
          },
        ],
        // FORGE-258: a fully-declared requirement -- verification method
        // AND expected evidence set, illustrating the complete case.
        verificationMethod: 'analysis',
        expectedEvidence: 'simulation',
      },
      {
        requirementId: 'sample-req-mass-min',
        requirementName: 'mass_floor',
        limitText: '>= 1 kg',
        status: 'stale',
        detail: 'claim is supported, but at least one cited evidence entity is stale',
        artefactIds: ['sample-artefact-frame'],
        evidence: [
          {
            id: 'sample-evidence-mass-floor',
            method: 'twin.evaluate_metric',
            tier: 0,
            value: 1.05,
            limit: 1,
            margin: 0.05,
            staleness: 'stale',
          },
        ],
        verificationMethod: '',
        expectedEvidence: '',
      },
    ],
  },
  // FORGE-287 (gap G-G1): started design loops live here, keyed by
  // loop_id -- empty until the dashboard's "Start design loop" action
  // creates one (see the POST /design-loop/start handler below).
  designLoops: {} as Record<string, SampleDesignLoop>,
  // FORGE-291 (gap G-G5): illustrative duplicate-commit guard -- maps a
  // JSON-stringified request body to the loop_id it already produced, so
  // resubmitting the exact same form mirrors the real guard's
  // duplicate=true response instead of silently narrating a second run.
  designLoopInputsSeen: {} as Record<string, string>,
  // FORGE-290 (gap G-G4): gate-review attempts, newest first -- empty
  // until the "Attempt promotion" action records one.
  promotionGates: [] as SampleMaturityGate[],
  // FORGE-289 (gap G-G3): decisions keyed by the node id they're related
  // to (a hierarchy node's own id, or a converged design loop's winning
  // iteration id) -- mirrors GET /v1/decisions?related_to=<node_id>'s own
  // "what decisions touch this node" shape. Seeded with one decision on
  // 'sample-hier-upper-arm' so the Structure tab has something to show
  // without first running a design loop; the design-loop entry is added
  // dynamically by the /design-loop/start handler below, same as the real
  // optimizer records a Decision only once it finds a feasible winner.
  decisions: {
    'sample-hier-upper-arm': [
      {
        id: 'sample-decision-wall-thickness',
        title: 'Upper arm wall thickness: 2.5mm (aluminum_6061)',
        rationale:
          'Minimum wall thickness meeting deflection <= 0.5mm and safety factor >= 2 for the upper arm link, found via bisection over the tier-0 hollow-tube hand-calc. Resulting mass: 1.4kg.',
        alternatives: [
          { option: 'wall_thickness_mm=1.0', reason_rejected: 'deflection_margin=-0.18mm, sf_margin=-0.2' },
          { option: 'wall_thickness_mm=1.75', reason_rejected: 'deflection_margin=-0.04mm, sf_margin=0.3' },
        ],
        parent_refs: ['sample-hier-upper-arm'],
        evidence_refs: ['sample-evidence-wall-thickness'],
        created_at: '2026-09-20T10:00:00Z',
      },
    ],
  } as Record<string, SampleDecision[]>,
  // FORGE-313: a small product hierarchy for the Structure tab -- mirrors
  // the ticket's own acceptance example (an "upper_arm"/"shoulder"
  // interface with a tip_deflection quantity, a mass allocation with an
  // owner) so the sample workspace demonstrates the same thing the real
  // arm project's live validation does.
  hierarchyNodes: [
    {
      id: 'sample-hier-arm',
      name: 'Arm',
      kind: 'product',
      parentId: null,
      quantity: null,
      placement: null,
      massKg: 2.3,
      cost: 145.0,
      massBudgetKg: null,
      massOverBudget: null,
      costBudget: null,
      costOverBudget: null,
      massBudgetOwner: null,
      massBudgetDiscipline: null,
      costBudgetOwner: null,
      costBudgetDiscipline: null,
      interfaces: [],
    },
    {
      id: 'sample-hier-upper-arm',
      name: 'upper_arm',
      kind: 'subsystem',
      parentId: 'sample-hier-arm',
      quantity: 1,
      placement: null,
      massKg: 1.4,
      cost: 90.0,
      massBudgetKg: 1.5,
      massOverBudget: false,
      costBudget: null,
      costOverBudget: null,
      massBudgetOwner: 'alice',
      massBudgetDiscipline: 'mechanical',
      costBudgetOwner: null,
      costBudgetDiscipline: null,
      interfaces: [
        {
          otherComponent: 'shoulder',
          interfaceType: 'mechanical',
          description: 'shoulder joint',
          quantities: [{ metric: 'tip_deflection', unit: 'mm', limit: 0.5, op: '<=' }],
        },
      ],
    },
    {
      id: 'sample-hier-shoulder',
      name: 'shoulder',
      kind: 'subsystem',
      parentId: 'sample-hier-arm',
      quantity: 1,
      placement: null,
      massKg: 0.9,
      cost: 55.0,
      massBudgetKg: 0.8,
      massOverBudget: true,
      costBudget: null,
      costOverBudget: null,
      massBudgetOwner: 'bob',
      massBudgetDiscipline: 'mechanical',
      costBudgetOwner: null,
      costBudgetDiscipline: null,
      interfaces: [
        {
          otherComponent: 'upper_arm',
          interfaceType: 'mechanical',
          description: 'shoulder joint',
          quantities: [
            { metric: 'tip_deflection', unit: 'mm', limit: 0.5, op: '<=' },
            { metric: 'J2_torque', unit: 'N*m', limit: 12.0, op: '<=' },
          ],
        },
      ],
    },
  ],
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
function route(
  method: string,
  path: string,
  body: Record<string, unknown>,
  params: Record<string, unknown> = {},
): unknown {
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
    if (path.endsWith('/diff') && path.startsWith('/features/')) {
      // FORGE-270 (gap G-D2): the sample enclosure is the only node with an
      // illustrative SUPERSEDES-linked prior version -- every other sample
      // node correctly has none (404 in the real API, null here).
      if (segment(path, 2) !== 'sample-enclosure') return undefined;
      return {
        currentWorkProductId: 'sample-enclosure',
        previousWorkProductId: 'sample-enclosure-v2',
        changed: { wall_mm: { from_value: 3, to_value: 2 } },
        added: {},
        removed: {},
      };
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
    if (path === '/simulation/results') {
      // FORGE-279: two illustrative revisions of the same analysis, so the
      // version-compare panel has something real to show.
      return {
        results: [
          {
            id: 'sample-fea-rev1',
            name: 'Rev 1 FEA — static 1g',
            maxVonMisesMpa: 62.4,
            maxDisplacementMm: 0.41,
            loadCase: 'static_1g',
            meshStats: { num_nodes: 12500, num_elements: 48000 },
            projectId: s.project.id,
            createdAt: '2026-09-20T10:00:00Z',
            updatedAt: '2026-09-20T10:00:00Z',
          },
          {
            id: 'sample-fea-rev2',
            name: 'Rev 2 FEA — static 1g',
            maxVonMisesMpa: 45.1,
            maxDisplacementMm: 0.29,
            loadCase: 'static_1g',
            meshStats: { num_nodes: 12500, num_elements: 48000 },
            projectId: s.project.id,
            createdAt: '2026-09-22T10:00:00Z',
            updatedAt: '2026-09-22T10:00:00Z',
          },
        ],
        total: 2,
      };
    }
    if (path === '/requirements/quality') return s.requirementsReport;
    if (path === '/requirements/matrix') return s.requirementMatrix;
    if (path === '/twin/hierarchy') return { nodes: s.hierarchyNodes };
    if (/^\/design-loop\/[^/]+$/.test(path)) return s.designLoops[segment(path, 2)];
    if (path === '/promotion') return { gates: s.promotionGates };
    if (path === '/decisions') {
      const relatedTo = String(params.related_to ?? '');
      return { related_to: relatedTo, decisions: s.decisions[relatedTo] ?? [] };
    }
    return undefined;
  }
  if (method === 'post') {
    const fixMatch = path.match(/^\/requirements\/([^/]+)\/fix$/);
    if (fixMatch) {
      const req = s.requirementsReport.requirements.find((r) => r.id === fixMatch[1]);
      if (!req) return undefined;
      return {
        proposedText: `The drone shall fly at a cruise speed of at least 8 m/s.`,
        rationale: 'weak_modal: should; ambiguous: fast',
        conclusions: ['The drone shall fly at a cruise speed of at least 8 m/s.'],
      };
    }
    if (path === '/requirements/constraints') {
      // FORGE-259: the constraint editor's create action -- appends a
      // real no_data row to the illustrative matrix, same as a fresh
      // constraint with no claims yet on the real backend.
      const payload = body as {
        name?: string;
        metric?: string;
        operator?: string;
        limit?: number;
        unit?: string;
        verificationMethod?: string;
        expectedEvidence?: string;
      };
      const id = `sample-req-${Date.now()}`;
      s.requirementMatrix.rows.push({
        requirementId: id,
        requirementName: payload.name ?? 'new_requirement',
        limitText: `${payload.metric ?? ''} ${payload.operator ?? '<='} ${payload.limit ?? ''}${payload.unit ?? ''}`,
        status: 'no_data',
        detail: 'no claim recorded against this requirement',
        artefactIds: [],
        evidence: [],
        // FORGE-258 (gap G-A2).
        verificationMethod: payload.verificationMethod ?? '',
        expectedEvidence: payload.expectedEvidence ?? '',
      });
      return { constraintId: id, setWorkProductId: `sample-set-${id}` };
    }
    if (path === '/features/generate') {
      // FORGE-269: illustrative counts matching the two real macros
      // (domain_agents/shared/design_ir_macros.py) exactly -- bolt_pattern
      // is 6 entities (create_body, sketch, pad, sketch, pocket,
      // polar_pattern), rib is 3 (create_body, sketch, pad).
      const payload = body as { name?: string; feature?: { feature_type?: string } };
      const featureType = payload.feature?.feature_type ?? 'rib';
      const entityCount = featureType === 'bolt_pattern' ? 6 : 3;
      return {
        feature_type: featureType,
        work_product_id: null,
        cad_file: `output/${featureType}_${Date.now()}.step`,
        entity_count: entityCount,
        volume_mm3: featureType === 'bolt_pattern' ? 11760.0 : 900.0,
        surface_area_mm2: featureType === 'bolt_pattern' ? 5200.0 : 1080.0,
        bounding_box: { min_x: -30, min_y: -20, min_z: 0, max_x: 30, max_y: 20, max_z: 5 },
        material: 'aluminum_6061',
        committed: true,
        twin_node_id: `sample-feature-${Date.now()}`,
        model_url: '/samples/flight-controller.glb',
        commit_error: null,
        already_committed: false,
      };
    }
    if (path === '/design-loop/start') {
      // FORGE-291: duplicate-commit guard -- identical inputs (excluding
      // nothing here, since this mock has no record_decision-equivalent
      // toggle) return the prior loop untouched, same shape as the real
      // guard's response.
      const inputsHash = JSON.stringify(body, Object.keys(body).sort());
      const priorLoopId = s.designLoopInputsSeen[inputsHash];
      if (priorLoopId) {
        const priorLoop = s.designLoops[priorLoopId];
        const priorWinner = priorLoop?.iterations.find((it) => it.is_winner) ?? null;
        return {
          loop_id: priorLoopId,
          status: priorLoop?.status ?? 'optimal',
          detail: `duplicate of an earlier run with identical inputs (loop_id=${priorLoopId})`,
          winner: priorWinner,
          candidates: priorLoop?.iterations ?? [],
          iteration_count: priorLoop?.iterations.length ?? 0,
          iteration_ids: priorLoop?.iterations.map((it) => it.id) ?? [],
          max_iterations: (body.maxIterations as number) ?? 60,
          duplicate: true,
        };
      }

      // FORGE-287: a small, illustrative bisection-like trace -- narrows
      // toward a converged winner exactly like the real
      // twin.start_design_loop tool's own bisection search, just without
      // running the real hand-calc against a real work product.
      const loopId = `sample-loop-${Date.now()}`;
      const values = [0.5, 10.25, 5.375, 2.9375, 1.71875, 1.109375, 0.8046875];
      const iterations: SampleDesignLoopIteration[] = values.map((v, i) => {
        const feasible = v <= 1.5;
        const isWinner = i === values.length - 1;
        return {
          id: `${loopId}-${i}`,
          iteration_number: i,
          parameter_name: 'wall_thickness_mm',
          parameter_value: v,
          metric: 'mass_kg',
          objective_value: 0.156 + (v - 0.8046875) * 0.18,
          constraints_status: {
            deflection_margin_mm: feasible ? 0.02 : -0.31,
            sf_margin: feasible ? 0.04 : -0.6,
          },
          feasible,
          status: isWinner ? 'converged' : 'candidate',
          is_winner: isWinner,
          approved: false,
          approved_by: null,
        };
      });
      s.designLoops[loopId] = { loop_id: loopId, status: 'optimal', iterations };
      s.designLoopInputsSeen[inputsHash] = loopId;
      // FORGE-289: the real optimizer records a Decision (with evidence
      // link) for a converged winner -- mirror that here so the Design
      // Loop section's own DecisionList has something to show.
      const winner = iterations[iterations.length - 1];
      if (!winner) return undefined;
      s.decisions[winner.id] = [
        {
          id: `sample-decision-${loopId}`,
          title: `Optimised wall thickness: ${winner.parameter_value.toFixed(4)}mm (aluminum_6061)`,
          rationale:
            `Minimum wall thickness meeting deflection <= 0.5mm and safety factor >= 2, found via bisection over the tier-0 hollow-tube hand-calc. Resulting mass: ${winner.objective_value.toFixed(4)}kg.`,
          alternatives: iterations
            .filter((it) => !it.feasible)
            .map((it) => ({
              option: `wall_thickness_mm=${it.parameter_value.toFixed(4)}`,
              reason_rejected: `deflection_margin=${it.constraints_status.deflection_margin_mm}mm, sf_margin=${it.constraints_status.sf_margin}`,
            })),
          parent_refs: [],
          evidence_refs: [`sample-evidence-${loopId}`],
          created_at: now,
        },
      ];
      return {
        loop_id: loopId,
        status: 'optimal',
        detail: 'minimum feasible wall thickness found via bisection: 0.8047mm',
        winner,
        candidates: iterations,
        iteration_count: iterations.length,
        iteration_ids: iterations.map((it) => it.id),
        max_iterations: (body.maxIterations as number) ?? 60,
        duplicate: false,
      };
    }
    if (path === '/promotion/attempt') {
      // FORGE-290: illustrative evidence-gated approval -- looks each
      // requested requirement up on the SAME live-ish matrix the evidence
      // matrix above renders, blocking on anything but 'pass' (matching
      // the real attempt_promotion's own no_data-blocks discipline),
      // unless the reviewer explicitly rejects (overrides even a pass).
      const payload = body as {
        requiredClaimIds?: string[];
        level?: string;
        decidedBy?: string;
        comment?: string;
        reject?: boolean;
      };
      const ids = payload.requiredClaimIds ?? [];
      const results: SampleRequiredClaimResult[] = ids.map((id) => {
        const row = s.requirementMatrix.rows.find((r) => r.requirementId === id);
        const req = s.requirementsReport.requirements.find((r) => r.id === id);
        const decision: SampleRequiredClaimResult['decision'] =
          row?.status === 'pass' ? 'pass' : row?.status === 'uncertain' || row?.status === 'stale' ? 'uncertain' : 'fail';
        return {
          requirementId: id,
          requirementName: req?.name ?? row?.requirementName ?? id,
          decision,
          detail: row?.detail ?? 'no claim recorded against this requirement',
          waiverId: null,
        };
      });
      const blocking = results.filter((r) => r.decision !== 'pass' && r.decision !== 'waived');
      let promoted = false;
      let blockedReason: string | null = null;
      if (payload.reject) {
        blockedReason = payload.comment ?? `rejected by ${payload.decidedBy}`;
      } else if (blocking.length > 0) {
        blockedReason = blocking.map((r) => `${r.requirementName} (${r.decision}): ${r.detail}`).join('; ');
      } else if (!payload.decidedBy) {
        blockedReason = 'all required claims satisfied, but promotion requires human authority';
      } else {
        promoted = true;
      }
      const gate: SampleMaturityGate = {
        gateId: `sample-gate-${Date.now()}`,
        level: payload.level ?? 'concept',
        promoted,
        blockedReason,
        decidedBy: payload.decidedBy ?? null,
        comment: payload.comment ?? null,
        createdAt: now,
        results,
      };
      s.promotionGates.unshift(gate);
      return gate;
    }
    const approveLoopMatch = path.match(/^\/design-loop\/([^/]+)\/approve$/);
    if (approveLoopMatch && approveLoopMatch[1]) {
      const loop = s.designLoops[approveLoopMatch[1]];
      if (!loop) return undefined;
      const winner = loop.iterations.find((it) => it.is_winner);
      if (!winner) return undefined;
      winner.approved = true;
      winner.approved_by = (body.approvedBy as string) ?? 'you';
      return { ...winner, approved_at: now };
    }
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
    if (path === '/simulation/named-faces') {
      // FORGE-277: a small illustrative box mesh's two named faces — enough
      // for the load-case dialog's 3D face picker to demonstrate a real
      // pick without a live gmsh/freecad-adapter mesh.
      return {
        meshFile: String(body.meshFile ?? ''),
        faces: [
          {
            name: 'Surface1',
            centroidMm: [5, 5, 0],
            normal: [0, 0, -1],
            areaMm2: 100,
            bboxMm: { min: [0, 0, 0], max: [10, 10, 0] },
          },
          {
            name: 'Surface2',
            centroidMm: [5, 5, 5],
            normal: [0, 0, 1],
            areaMm2: 100,
            bboxMm: { min: [0, 0, 5], max: [10, 10, 5] },
          },
        ],
      };
    }
  }
  if (method === 'patch') {
    // FORGE-271: whole-list joint replace on an already-committed node.
    const jointsMatch = path.match(/^\/twin\/nodes\/([^/]+)\/assembly-joints$/);
    if (jointsMatch) {
      const nodeId = jointsMatch[1];
      const node = s.nodes.find((n) => n.id === nodeId) as
        | { assembly?: { parts: unknown[]; joints: unknown[] } }
        | undefined;
      if (!node) return undefined;
      const assembly = node.assembly ?? { parts: [], joints: [] };
      assembly.joints = (body.joints as unknown[]) ?? [];
      node.assembly = assembly;
      return { nodeId, assembly };
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
  const params = (config.params ?? {}) as Record<string, unknown>;
  const data = route(method, path, body, params);
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
