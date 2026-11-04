import type {
  Blocker,
  Build,
  Creation,
  CreationView,
  InspectionCheck,
  Readiness,
  Stage,
} from '../api/endpoints/create';

/**
 * The sample workspace's creation, so the Create view (and presentation
 * mode) shows the whole loop offline: a brief, a route, an inspection plan
 * and a finished build waiting for its last check and the person's
 * acceptance. Readiness follows the gateway's rules closely enough to step
 * through every state; the gateway's `api_gateway/create/readiness.py` is
 * the reference.
 */

const STAGES: Stage[] = [
  'concept',
  'design_in_development',
  'checked_design',
  'ready_for_making',
  'released_for_making',
  'build_completed',
  'accepted_product',
];

const PLAN: InspectionCheck[] = [
  { id: 'holes', feature: 'Mounting holes on a 30.5 mm square', condition: '±0.2 mm', evidence: 'Calliper reading', method: 'measurement', reviewer: 'person', blocking: true },
  { id: 'solder', feature: 'No solder bridges on the MCU', condition: 'none visible at 10×', evidence: 'Macro photo', method: 'camera', reviewer: 'agent', blocking: true },
  { id: 'boot', feature: 'Boots and shows up over USB', condition: 'enumerates as a serial device', evidence: 'Operator check', method: 'operator', reviewer: 'person', blocking: true },
  { id: 'silk', feature: 'Silkscreen legible', condition: '', evidence: 'Photo', method: 'camera', reviewer: 'person', blocking: false },
];

function seed(projectId: string, revisionId: string): Creation {
  const at = '2026-09-22T12:00:00Z';
  const build: Build = {
    id: 'sample-build-1',
    number: 1,
    revision: { work_product_id: revisionId, version: 3, label: 'flight-controller.pcb rev 3' },
    route: 'external',
    process: 'PCB fabrication',
    machine_id: null,
    material: 'FR-4, 1.6 mm, ENIG',
    settings: { layers: '4', copper: '1 oz' },
    estimates: {
      cost: { value: '£38 for 5 boards', basis: 'Quote from the manufacturer connector, 2026-09-18' },
      lead_time: { value: '6 working days', basis: 'Manufacturer’s stated lead time' },
    },
    status: 'completed',
    status_note: 'Boards delivered',
    checks: PLAN,
    release: { approver: 'you', approver_verified: false, surface: 'dashboard', released_at: '2026-09-18T09:00:00Z' },
    evidence: [
      { id: 'ev1', kind: 'measurement', check_id: 'holes', work_product_id: null, upload_id: null, value: 30.48, unit: 'mm', note: 'Calliper, hole centres', recorded_by: 'you', surface: 'dashboard', created_at: at },
      { id: 'ev2', kind: 'photo', check_id: 'solder', work_product_id: null, upload_id: null, value: null, unit: null, note: 'Macro of U1', recorded_by: 'you', surface: 'dashboard', created_at: at },
    ],
    results: {
      holes: { status: 'passed', note: '30.48 mm measured', confidence: null, evidence_ids: ['ev1'], decided_by: 'you', surface: 'dashboard', updated_at: at },
      solder: { status: 'passed', note: 'No bridges seen on any MCU pin', confidence: 0.86, evidence_ids: ['ev2'], decided_by: 'sample-agent', surface: 'agent', updated_at: at },
      boot: { status: 'awaiting_review', note: '', confidence: null, evidence_ids: [], decided_by: '', surface: 'unknown', updated_at: at },
      silk: { status: 'awaiting_review', note: '', confidence: null, evidence_ids: [], decided_by: '', surface: 'unknown', updated_at: at },
    },
    acceptance: null,
    created_at: '2026-09-17T15:00:00Z',
    updated_at: at,
  };
  return {
    project_id: projectId,
    version: 7,
    brief: {
      items: {
        intended_use: { value: 'A flight controller for a 5-inch FPV racing drone', status: 'confirmed', source: 'user' },
        size_and_fit: { value: 'Standard 30.5 × 30.5 mm stack mounting', status: 'confirmed', source: 'user' },
        materials: { value: 'FR-4, lead-free finish', status: 'assumed', source: 'agent' },
        manufacturing_budget: { value: 'Under £50 for the first five', status: 'confirmed', source: 'user' },
        quantity: { value: 'Five prototypes', status: 'confirmed', source: 'user' },
        success_criteria: { value: 'Fits the stack\nNo assembly defects\nBoots over USB', status: 'confirmed', source: 'user' },
        inspection_needs: { value: 'Calliper on the holes, macro photo, plug it in', status: 'confirmed', source: 'user' },
      },
      measurements: [
        { id: 'pitch', label: 'Mounting hole spacing', value: 30.5, unit: 'mm', status: 'user_confirmed', critical: true, source: 'typed' },
        { id: 'height', label: 'Stack height available', value: 8, unit: 'mm', status: 'estimated', critical: false, source: 'photo', scale_reference: 'M3 standoff' },
      ],
      assumptions: [{ id: 'a1', text: 'Four-layer board, so the IMU sits on a quiet ground plane', status: 'open' }],
      references: [],
    },
    route: { route: 'external', process: 'PCB fabrication', machine_id: null, material: 'FR-4, 1.6 mm, ENIG', supplier: 'Sample PCB house', note: '' },
    inspection_plan: PLAN,
    builds: [build],
    history: [
      { at: '2026-09-17T15:00:00Z', actor: 'you', surface: 'dashboard', change: 'build_prepare', summary: 'Build 1 prepared from flight-controller.pcb rev 3' },
      { at: '2026-09-18T09:00:00Z', actor: 'you', surface: 'dashboard', change: 'build_release', summary: 'Build 1 released for making by you' },
      { at: at, actor: 'sample-agent', surface: 'agent', change: 'build_result', summary: 'No solder bridges on the MCU: passed (build 1)' },
    ],
    updated_at: at,
  };
}

function readiness(c: Creation): Readiness {
  const latest = c.builds[c.builds.length - 1];
  const blockers: Blocker[] = [];
  let stage: Stage = 'checked_design';
  let next = 'Prepare a build.';
  if (latest?.acceptance?.decision === 'accept') {
    stage = 'accepted_product';
    next = 'Accepted. Make another, or describe an improvement.';
  } else if (latest?.status === 'completed') {
    stage = 'build_completed';
    for (const chk of latest.checks) {
      const status = latest.results[chk.id]?.status ?? 'awaiting_review';
      if (chk.blocking && status !== 'passed') {
        blockers.push({ id: `check:${chk.id}`, message: `${chk.feature}: ${status.replace('_', ' ')}`, action: 'Add evidence or record the inspection result' });
      }
    }
    next = blockers.length ? 'Inspect the build: add photos or measurements for the open checks.' : 'Every required check passed: review and accept the build.';
  } else if (latest && (latest.status === 'released' || latest.status === 'making')) {
    stage = 'released_for_making';
    next = `Build ${latest.number} is ${latest.status === 'making' ? 'being made' : 'released'}.`;
  } else if (latest?.status === 'preparing') {
    stage = 'ready_for_making';
    next = 'Review the build and release it for making.';
  }
  return { stage, stage_index: STAGES.indexOf(stage), blockers, next_action: next, has_concept: true, has_engineering_model: true };
}

let creation: Creation | null = null;

function current(projectId: string, revisionId: string): Creation {
  if (!creation || creation.project_id !== projectId) creation = seed(projectId, revisionId);
  return creation;
}

function viewOf(c: Creation): CreationView {
  return { creation: c, readiness: readiness(c), brief_keys: [] };
}

function save(c: Creation, summary: string): CreationView {
  c.version += 1;
  c.updated_at = new Date().toISOString();
  c.history.push({ at: c.updated_at, actor: 'you', surface: 'dashboard', change: 'sample', summary });
  return viewOf(c);
}

/** Answer a `/create/...` request from the sample creation; `undefined` = not handled. */
export function routeSampleCreate(
  method: string,
  path: string,
  body: Record<string, unknown>,
  projectId: string,
  revisionId: string,
): unknown {
  const m = path.match(/^\/create\/([^/]+)(?:\/(.*))?$/);
  if (!m) {
    // Nothing in the sample waits for a decision, and no machine is registered.
    if (method === 'get' && path === '/approvals') return { items: [], unscoped_count: 0 };
    return path === '/library/machines' && method === 'get' ? { machines: [] } : undefined;
  }
  if (decodeURIComponent(m[1]!) !== projectId) return undefined;
  const c = current(projectId, revisionId);
  const rest = m[2] ?? '';
  if (method === 'get' && rest === '') return viewOf(c);
  if (method === 'put' && rest === 'brief') {
    c.brief = body.brief as Creation['brief'];
    return save(c, 'Brief changed');
  }
  if (method === 'put' && rest === 'route') {
    c.route = body.route as Creation['route'];
    return save(c, `Route set to ${c.route.route ?? 'none'}`);
  }
  if (method === 'put' && rest === 'inspection-plan') {
    c.inspection_plan = body.checks as InspectionCheck[];
    return save(c, `Inspection plan: ${c.inspection_plan.length} check(s)`);
  }
  if (method === 'post' && rest === 'builds') {
    const number = c.builds.length + 1;
    const now = new Date().toISOString();
    c.builds.push({
      id: `sample-build-${number}`,
      number,
      revision: body.revision as Build['revision'],
      route: c.route.route ?? 'external',
      process: c.route.process,
      machine_id: c.route.machine_id,
      material: c.route.material,
      settings: {},
      estimates: {},
      status: 'preparing',
      status_note: '',
      checks: [...c.inspection_plan],
      release: null,
      evidence: [],
      results: {},
      acceptance: null,
      created_at: now,
      updated_at: now,
    });
    return save(c, `Build ${number} prepared`);
  }
  const b = rest.match(/^builds\/([^/]+)\/(release|status|evidence|accept|results\/([^/]+))$/);
  const build = b ? c.builds.find((x) => x.id === decodeURIComponent(b[1]!)) : undefined;
  if (!b || !build) return undefined;
  const action = b[2]!;
  if (action === 'release') {
    build.status = 'released';
    build.checks = [...c.inspection_plan];
    build.results = Object.fromEntries(build.checks.map((chk) => [chk.id, { status: 'awaiting_review', note: '', confidence: null, evidence_ids: [], decided_by: '', surface: 'dashboard', updated_at: '' }]));
    build.release = { approver: 'you', approver_verified: false, surface: 'dashboard', released_at: new Date().toISOString() };
    return save(c, `Build ${build.number} released for making`);
  }
  if (action === 'status') {
    build.status = body.status as Build['status'];
    return save(c, `Build ${build.number} ${build.status}`);
  }
  if (action === 'evidence') {
    build.evidence.push({ id: `ev${Date.now()}`, kind: body.kind as Build['evidence'][number]['kind'], check_id: (body.check_id as string) ?? null, work_product_id: (body.work_product_id as string) ?? null, upload_id: null, value: (body.value as number) ?? null, unit: (body.unit as string) ?? null, note: String(body.note ?? ''), recorded_by: 'you', surface: 'dashboard', created_at: new Date().toISOString() });
    return save(c, `Evidence added to build ${build.number}`);
  }
  if (action.startsWith('results/') && b[3]) {
    const id = decodeURIComponent(b[3]);
    build.results[id] = { status: body.status as Build['results'][string]['status'], note: String(body.note ?? ''), confidence: null, evidence_ids: build.results[id]?.evidence_ids ?? [], decided_by: 'you', surface: 'dashboard', updated_at: new Date().toISOString() };
    return save(c, `${build.checks.find((x) => x.id === id)?.feature ?? id}: ${String(body.status).replace('_', ' ')}`);
  }
  if (action === 'accept') {
    const unmet = build.checks.filter((chk) => chk.blocking && build.results[chk.id]?.status !== 'passed');
    if (body.decision === 'accept' && unmet.length) return undefined;
    build.acceptance = {
      decision: body.decision === 'accept' ? 'accept' : 'reject',
      reason: String(body.reason ?? ''),
      limitations: build.checks.filter((chk) => !chk.blocking && build.results[chk.id]?.status !== 'passed').map((chk) => `${chk.feature}: awaiting review`),
      approver: 'you',
      approver_verified: false,
      surface: 'dashboard',
      decided_at: new Date().toISOString(),
    };
    return save(c, `Build ${build.number} ${body.decision}ed`);
  }
  return undefined;
}

/** Tests start from the seeded creation again. */
export function resetSampleCreation(): void {
  creation = null;
}
