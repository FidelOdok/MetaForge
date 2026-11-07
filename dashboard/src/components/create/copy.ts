import type {
  BriefKey,
  BuildStatus,
  CreationView,
  InspectionMethod,
  MakeRoute,
  MeasurementStatus,
  ResultStatus,
  Stage,
} from '../../api/endpoints/create';
import type { ApprovalKind } from '../../types/approvals';

/** Plain words for everything the Create view shows. One place, so the tone stays the same. */

/**
 * The four journey milestones Create shows (16.2 item 2). They summarise
 * where a creation stands; they are not gates and not pages. Concept, CAD,
 * electronics, firmware, simulation and making work may overlap, and the
 * mechanisms behind them (readiness, decisions, making, inspection) are the
 * same as before.
 */
export const MILESTONES = ['Imagine', 'Develop', 'Check', 'Make'] as const;
export type Milestone = (typeof MILESTONES)[number];

/** Where a creation is, as a milestone index; 4 means all four are done (accepted). */
export function milestoneIndex(view: CreationView | undefined): number {
  if (!view) return 0;
  return STAGE_MILESTONE[view.readiness.stage];
}

const STAGE_MILESTONE: Record<Stage, number> = {
  concept: 0,
  design_in_development: 1,
  checked_design: 2,
  ready_for_making: 3,
  released_for_making: 3,
  build_completed: 3,
  accepted_product: 4,
};

/** One short summary per milestone, from the same readiness and records. */
export function milestoneSummaries(view: CreationView | undefined): Record<Milestone, string> {
  if (!view) return { Imagine: 'Studio and sketches', Develop: 'Not started', Check: 'Not yet', Make: 'Not yet' };
  const { readiness, creation } = view;
  const latest = creation.builds[creation.builds.length - 1];
  const index = STAGE_ORDER.indexOf(readiness.stage);
  const checked = index >= STAGE_ORDER.indexOf('checked_design');
  const open = readiness.blockers.length;
  return {
    Imagine: readiness.has_concept ? 'Concept captured' : 'Studio and sketches',
    Develop: readiness.has_engineering_model ? 'Dimensioned model' : 'Not started',
    Check: checked ? 'Checks recorded' : readiness.has_engineering_model && open ? `${open} to resolve` : 'Not yet',
    Make: latest ? BUILD_STATUS_COPY[latest.status] : readiness.stage === 'ready_for_making' ? 'Ready for making' : 'Not yet',
  };
}

const STAGE_ORDER: Stage[] = [
  'concept',
  'design_in_development',
  'checked_design',
  'ready_for_making',
  'released_for_making',
  'build_completed',
  'accepted_product',
];

export const BRIEF_COPY: Record<BriefKey, { label: string; question: string; placeholder: string }> = {
  intended_use: {
    label: 'Intended use',
    question: 'What problem does it solve, and who uses it?',
    placeholder: 'Holds my headphones under the desk',
  },
  size_and_fit: {
    label: 'Size and fit',
    question: 'How big can it be, and what must it fit?',
    placeholder: 'Clamps a 25 mm desk edge, under 120 mm long',
  },
  appearance: { label: 'Appearance', question: 'Style, colour and finish?', placeholder: 'Matte black, rounded edges' },
  materials: { label: 'Materials', question: 'Any material you want or must avoid?', placeholder: 'PETG or PLA' },
  design_budget: {
    label: 'Design budget',
    question: 'How much may the design work spend (model and compute)?',
    placeholder: 'Up to £5 of generation credits',
  },
  manufacturing_budget: {
    label: 'Making budget',
    question: 'How much may making it cost?',
    placeholder: 'Under £10 a piece',
  },
  quantity: {
    label: 'Quantity',
    question: 'A prototype, a one-off or repeat production?',
    placeholder: 'One prototype, then 20',
  },
  making_context: {
    label: 'How you can make it',
    question: 'Machines you have, or suppliers you prefer?',
    placeholder: 'Prusa MK4 at home',
  },
  success_criteria: {
    label: 'Success criteria',
    question: 'What must be true for it to be good enough? One per line.',
    placeholder: 'Holds 400 g without bending\nSlides on by hand',
  },
  inspection_needs: {
    label: 'How it will be checked',
    question: 'How will you check the physical result?',
    placeholder: 'Photo from the side, measure the clamp gap',
  },
};

export const MEASUREMENT_STATUS_COPY: Record<MeasurementStatus, string> = {
  user_confirmed: 'User-confirmed',
  estimated: 'Estimated',
  unconfirmed: 'Unconfirmed',
};

export const ROUTE_COPY: Record<MakeRoute, { label: string; purpose: string }> = {
  home: { label: 'Make at home', purpose: 'Your own equipment, with guided hands-on steps.' },
  workshop: { label: 'Use my workshop', purpose: 'Registered in-house machines and processes.' },
  external: { label: 'Have it made', purpose: 'An external manufacturer quotes and makes it.' },
};

export const METHOD_COPY: Record<InspectionMethod, string> = {
  camera: 'Camera observation',
  measurement: 'Calibrated measurement',
  telemetry: 'Machine telemetry',
  operator: 'Operator confirmation',
  instrument: 'Test or instrument',
};

export const RESULT_COPY: Record<ResultStatus, string> = {
  passed: 'Passed',
  failed: 'Failed',
  inconclusive: 'Inconclusive',
  awaiting_review: 'Awaiting review',
};

export const BUILD_STATUS_COPY: Record<BuildStatus, string> = {
  preparing: 'Being prepared',
  released: 'Released for making',
  making: 'Being made',
  completed: 'Making finished',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

/**
 * The kind of commitment a waiting decision is. They are kept apart so a
 * design approval is never mistaken for spending money or moving a machine.
 */
export type Commitment = 'design' | 'sharing' | 'spending' | 'machine' | 'files' | 'process';

export const COMMITMENT_COPY: Record<Commitment, string> = {
  design: 'Design approval',
  sharing: 'External file sharing',
  spending: 'Spending',
  machine: 'Physical machine action',
  files: 'Adding files',
  process: 'Process checkpoint',
};

const MACHINE_TOOLS = /^(library\.(start|send|dispatch)|octoprint|moonraker|printer|machine)/;
const SPEND_TOOLS = /^(tripo|meshy|zoo|slant3d\.(quote|order)|vendors?\.)/;
const SHARE_TOOLS = /(\.share|\.send|upload_to|vendors?\.handoff)/;

export function commitmentOf(kind: ApprovalKind, detail: Record<string, unknown>): Commitment {
  if (kind === 'upload') return 'files';
  if (kind === 'gate' || kind.startsWith('flow')) return 'process';
  if (kind === 'tool_call' || kind === 'human_authority') {
    const tool = String(detail.tool ?? '');
    if (MACHINE_TOOLS.test(tool)) return 'machine';
    if (SPEND_TOOLS.test(tool)) return 'spending';
    if (SHARE_TOOLS.test(tool)) return 'sharing';
  }
  return 'design';
}
