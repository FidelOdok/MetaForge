import apiClient from '../client';
import type { SketchInterpretation } from '../../lib/sketch/model';
import type { SketchFrame } from '../../lib/sketch/compiler';

/**
 * The sketch compiler's gateway calls (FORGE-791).
 *
 * The browser fits and solves; the gateway stores a proposal
 * (`sketch_interpretation`), re-checks the accepted rules when it is
 * promoted (`parametric_sketch`, held for approval) and signs it off. Each
 * call is idempotent per the client id it carries.
 */

const enc = encodeURIComponent;

export interface InterpretationSaved {
  node_id: string;
  item_key: string | null;
  revision: number | null;
  item_ref: string | null;
  interpretation_id: string;
  sketch_ref: string | null;
  plane_id: string | null;
  unit: string;
  operation_id: string;
  duplicate: boolean;
  primitive_count: number;
  constraint_count: number;
  accepted_count: number;
  unsatisfied: string[];
  authority: 'proposal';
}

export interface FeatureParameter {
  status: 'required_input' | 'provided';
  value: number | string | null;
  unit: string | null;
  question: string;
}

export interface FeaturePlanItem {
  id: string;
  op: 'extrude' | 'revolve';
  profile_primitive_ids: string[];
  axis_id: string | null;
  parameters: Record<string, FeatureParameter>;
  note: string;
}

export interface Promoted {
  node_id: string;
  item_key: string | null;
  revision: number | null;
  item_ref: string | null;
  interpretation_ref: string;
  sketch_ref: string | null;
  plane_id: string | null;
  unit: string;
  promote_id: string;
  operation_id: string;
  duplicate: boolean;
  approved: boolean;
  approval_id: string;
  accepted_count: number;
  dropped_count: number;
  feature_plan: FeaturePlanItem[];
  authority: 'human_accepted';
}

/** `POST /v1/twin/projects/{id}/sketches/{key}/interpretations`. */
export async function storeInterpretation(
  projectId: string,
  sketchKey: string,
  interpretation: SketchInterpretation & { frame: SketchFrame },
  baseRevision: string | null,
): Promise<InterpretationSaved> {
  const { data } = await apiClient.post<InterpretationSaved>(
    `/twin/projects/${enc(projectId)}/sketches/${enc(sketchKey)}/interpretations`,
    { interpretation, base_revision: baseRevision },
  );
  return data;
}

/** `POST /v1/twin/projects/{id}/interpretations/{ref}/promote`. */
export async function promoteInterpretation(projectId: string, interpretationRef: string, promoteId: string): Promise<Promoted> {
  const { data } = await apiClient.post<Promoted>(
    `/twin/projects/${enc(projectId)}/interpretations/${enc(interpretationRef)}/promote`,
    { promote_id: promoteId },
  );
  return data;
}

/** `POST /v1/twin/nodes/{id}/approve-parametric-sketch`. */
export async function approveParametricSketch(nodeId: string): Promise<{ node_id: string; approved: boolean; approved_at: string | null }> {
  const { data } = await apiClient.post(`/twin/nodes/${enc(nodeId)}/approve-parametric-sketch`, {});
  return data;
}
