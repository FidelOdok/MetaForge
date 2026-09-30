/**
 * Design-flow catalogue (FORGE-395).
 *
 * This file used to *be* the catalogue: a hand-maintained copy of
 * `orchestrator/design_flow/spec.py` under a comment reading "Keep this list
 * in sync when a flow is added or re-phased". It was not in sync, and nothing
 * could have said so — `design_v1` (the default) was absent and four phase
 * titles were paraphrased. It now fetches.
 */

import apiClient from '../client';
import type { DesignFlow, DesignFlowCatalog } from '../../types/design-flows';

export async function getDesignFlows(): Promise<DesignFlowCatalog> {
  const { data } = await apiClient.get<DesignFlowCatalog>('/design-flows');
  return data;
}

/**
 * Build the `request` body for `POST /v1/runs`. The gateway only drives a run
 * through the design-flow engine when the request names a `flow` (or
 * `kind: "design_flow"`); a bare goal would start and never progress (MET-671).
 *
 * The flow is validated against the fetched catalogue rather than a local
 * list, so "select a supported design flow" means supported by the gateway
 * that is about to run it.
 */
export function buildDesignFlowRequest(
  goal: string,
  projectId: string,
  flowId: string,
  flows: DesignFlow[],
): Record<string, unknown> {
  if (!goal.trim() || !projectId.trim()) {
    throw new Error('A goal and project are required.');
  }
  if (!flows.some((f) => f.id === flowId)) {
    throw new Error('Select a supported design flow.');
  }
  return { kind: 'design_flow', flow: flowId, goal: goal.trim(), project_id: projectId };
}
