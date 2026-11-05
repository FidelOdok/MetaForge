import apiClient from '../client';
import { nodeNamedFileUrl } from './twin';

/**
 * The CAD design loop: a model writes CadQuery for one component, it is built,
 * checked, rendered and reviewed by a vision critic, and revised until it is
 * satisfied or a cap is reached. A run is a gateway job whose iterations arrive
 * as operation events; past the hourly cap the gateway answers 409
 * `spend_confirmation_required`. The result is a proposed version: nothing
 * changes until the person picks *Use this version*.
 */

const enc = encodeURIComponent;
const SURFACE_HEADERS = { 'X-MetaForge-Surface': 'dashboard' } as const;

export type StopReason = 'satisfied' | 'iteration_cap' | 'spend_cap' | 'needs_input' | 'failed';

export interface LoopQuestion {
  name: string;
  question: string;
  source?: string;
}

export interface CritiqueIssue {
  severity: 'blocking' | 'major' | 'minor';
  view?: string | null;
  feature?: string | null;
  description: string;
  suggested_change: string;
}

export interface LoopDfmFinding {
  rule_id?: string;
  rule_title?: string;
  severity?: string;
  message?: string;
  measured?: number | string | null;
  limit?: number | string | null;
  unit?: string;
}

export interface LoopIteration {
  n: number;
  script?: string;
  summary?: string;
  focus?: string;
  assumptions?: { name: string; value: unknown; unit?: string; why?: string }[];
  questions?: LoopQuestion[];
  build?: { ok: boolean; error?: string; step_sha256?: string };
  validity?: { outcome: string; findings?: { check?: string; outcome?: string; detail?: string }[] };
  measurements?: {
    size_mm?: { x: number; y: number; z: number } | null;
    volume_mm3?: number | null;
    dfm_measure?: { min_wall_mm?: number | null } | null;
  };
  dimension_checks?: { axis: string; kind: string; wanted: string; measured: number; ok: boolean }[];
  dfm?: { status?: string; kind?: string; profile?: string; note?: string; findings?: LoopDfmFinding[] };
  render?: {
    ok: boolean;
    error?: string;
    sheet?: string | null;
    views?: { view: string; label?: string; file?: string | null }[];
  };
  critique?: { satisfied: boolean; verdict: string; reasons?: string[]; issues?: CritiqueIssue[] };
  gate?: {
    critic_satisfied: boolean;
    validity_pass: boolean;
    no_blocking_dfm: boolean;
    dimensions_in_tolerance: boolean;
    passed: boolean;
  };
  models?: Record<string, { provider?: string | null; model?: string | null; cost_usd?: number | null }>;
}

export interface LoopResult {
  status: 'proposed' | 'accepted' | 'rejected';
  iteration: number;
  script?: string;
  cad_key: string;
  base_revision: string | null;
  satisfied: boolean;
  open_issues: string[];
  assumptions: { name: string; value: unknown; unit?: string }[];
  validated: false;
}

export interface LoopRun {
  node_id: string;
  name: string;
  component_id: string | null;
  component_name: string | null;
  created_at: string | null;
  status: 'running' | 'finished';
  stop_reason: StopReason | null;
  stop_words?: string;
  stop_detail: string | null;
  questions?: LoopQuestion[];
  iterations: LoopIteration[];
  result: LoopResult | null;
  budget?: { model_calls: number; spend_usd: number; max_model_calls: number; max_spend_usd: number; unpriced_calls: number };
  decision?: { decision: 'accept' | 'reject'; by: string; at: string } | null;
  context?: { sketch_ref?: string | null; cad_key?: string; base_revision?: string | null; manufacturing_context_ref?: string };
}

export interface SpendInfo {
  used_last_hour: number;
  cap: number | null;
  policy_mode: string;
  policy_would_approve: boolean;
}

export interface LoopRuns {
  runs: LoopRun[];
  active_jobs: { job_id: string; status: string }[];
  spend: SpendInfo;
  note: string;
}

export interface LoopStarted {
  job_id: string | null;
  status: string;
  operation_id: string | null;
  spend: SpendInfo;
}

export interface StartLoopBody {
  sketch_key?: string;
  component_id?: string;
  intent?: string;
  answers?: Record<string, number | string>;
  max_iterations?: number;
  confirm_spend?: boolean;
}

export const cadLoopKeys = {
  project: (projectId: string, componentId?: string | null) => ['cad-loops', projectId, componentId ?? ''] as const,
};

const base = (projectId: string) => `/twin/projects/${enc(projectId)}/cad-loops`;

export async function getCadLoops(projectId: string, componentId?: string | null, signal?: AbortSignal): Promise<LoopRuns> {
  const { data } = await apiClient.get<LoopRuns>(base(projectId), {
    params: componentId ? { component_id: componentId } : undefined,
    signal,
  });
  return data;
}

export async function startCadLoop(projectId: string, body: StartLoopBody): Promise<LoopStarted> {
  const { data } = await apiClient.post<LoopStarted>(base(projectId), body, { headers: SURFACE_HEADERS });
  return data;
}

export async function decideCadLoop(projectId: string, nodeId: string, decision: 'accept' | 'reject'): Promise<LoopRun> {
  const { data } = await apiClient.post<LoopRun>(`${base(projectId)}/${enc(nodeId)}/decision`, { decision }, { headers: SURFACE_HEADERS });
  return data;
}

/** A rendered view stored on the run's record. */
export function loopImageUrl(nodeId: string, file: string): string {
  return nodeNamedFileUrl(nodeId, file);
}

/** The plain words for why a run stopped. */
export const STOP_TEXT: Record<StopReason, string> = {
  satisfied: 'Done: the review is satisfied and every check passed',
  iteration_cap: 'Stopped at the iteration limit with issues still open',
  spend_cap: 'Stopped at the spend limit with issues still open',
  needs_input: 'Needs your answer to go on',
  failed: 'Could not go on',
};
