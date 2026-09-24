/**
 * Design-flow catalog for the "Start a design run" wizard.
 *
 * The gateway has no flow-listing route, so this mirrors the launchable flows
 * defined in `orchestrator/design_flow/spec.py` (`FLOWS`). Phase titles match
 * the spec's `Phase.title`; every phase there ends in a human review `Gate`.
 * Keep this list in sync when a flow is added or re-phased.
 */

export interface DesignFlow {
  id: string;
  name: string;
  description: string;
  phases: string[];
}

const HARDWARE_V1: DesignFlow = {
  id: 'hardware_v1',
  name: 'Hardware & robotics',
  description: 'A multidisciplinary flow for a complete hardware system.',
  phases: [
    'Intent',
    'Stakeholder needs',
    'Requirements',
    'Preliminary feasibility',
    'System architecture',
    'Concept selection',
    'Mechanical design',
    'Electronics',
    'Firmware & control',
    'Simulation & V&V',
    'Manufacturing preparation',
  ],
};

/** The flow the wizard preselects. */
export const DEFAULT_DESIGN_FLOW = HARDWARE_V1;

export const DESIGN_FLOWS: DesignFlow[] = [
  HARDWARE_V1,
  {
    id: 'mech_v1',
    name: 'Mechanical design',
    description: 'A focused flow for a load-bearing part or mechanical subsystem.',
    phases: [
      'Intent',
      'Stakeholder needs',
      'Requirements',
      'Preliminary feasibility',
      'Mechanical design',
      'Simulation & V&V',
    ],
  },
];

export function findDesignFlow(id: unknown): DesignFlow | undefined {
  return DESIGN_FLOWS.find((f) => f.id === id);
}

/**
 * Build the `request` body for `POST /v1/runs`. The gateway only drives a run
 * through the design-flow executor when the request names a `flow` (or
 * `kind: "design_flow"`); a bare goal would start and never progress (MET-671).
 */
export function buildDesignFlowRequest(
  goal: string,
  projectId: string,
  flowId: string,
): Record<string, unknown> {
  if (!goal.trim() || !projectId.trim()) {
    throw new Error('A goal and project are required.');
  }
  if (!findDesignFlow(flowId)) {
    throw new Error('Select a supported design flow.');
  }
  return { kind: 'design_flow', flow: flowId, goal: goal.trim(), project_id: projectId };
}
