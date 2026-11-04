import apiClient from '../client';
import { apiUrl } from '../../lib/gatewayConfig';

/**
 * CDE slice 6: a CAD revision's geometry state and its GLB, native CAD and
 * STEP export states (UI-010), plus the exact build and STEP export requests.
 */

export type ExportFormat = 'glb' | 'native' | 'step';
export type ExportStateName = 'READY' | 'STALE' | 'QUEUED' | 'FAILED' | 'NONE';

export interface ExportStateView {
  format: ExportFormat;
  state: ExportStateName;
  detail: string;
  schema?: string | null;
  round_trip?: string | null;
  url?: string | null;
  revision?: number | null;
}

export interface NodeArtifacts {
  node_id: string;
  item_key: string | null;
  revision: number | null;
  head_revision: number | null;
  is_head: boolean;
  geometry_state: string | null;
  badge: string | null;
  authority: string | null;
  validity: { outcome?: string; findings?: { check: string; outcome: string; detail: string }[] } | null;
  latest_attempt: { state?: string; outcome?: string; attempted_at?: string } | null;
  last_validated: { revision: number; node_id: string; item_ref: string; is_head: boolean } | null;
  can_export_step: boolean;
  exports: ExportStateView[];
}

export interface JobReply {
  job_id?: string | null;
  status: string;
  reused?: boolean;
  cache_hit?: boolean | null;
  url?: string | null;
  error?: string | null;
}

export async function getNodeArtifacts(nodeId: string): Promise<NodeArtifacts> {
  const { data } = await apiClient.get<NodeArtifacts>(`/twin/nodes/${nodeId}/exports`);
  return data;
}

export async function requestStepExport(nodeId: string, critical = false): Promise<JobReply> {
  const { data } = await apiClient.post<JobReply>(`/twin/nodes/${nodeId}/exports/step`, { critical });
  return data;
}

export async function requestExactBuild(
  itemKey: string,
  projectId: string,
  baseRevision?: number | null,
): Promise<JobReply> {
  const { data } = await apiClient.post<JobReply>(`/twin/items/${encodeURIComponent(itemKey)}/exact-build`, {
    project_id: projectId,
    base_revision: baseRevision != null ? String(baseRevision) : null,
  });
  return data;
}

/** Where the browser downloads a recorded STEP export. */
export function stepFileHref(nodeId: string): string {
  return apiUrl(`/twin/nodes/${nodeId}/exports/step/file`);
}
