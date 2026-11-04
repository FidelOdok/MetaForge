import apiClient from '../client';
import type { ImpactResult, InterfaceListResponse } from '../../types/impact';

/**
 * Interface edges and cross-domain impact (FORGE-793).
 *
 * `GET .../interfaces` is the project's dependency graph; `GET .../impact/{ref}`
 * a recorded impact (by id or `KEY@n`). The preview never writes.
 */

const enc = encodeURIComponent;
const base = (projectId: string) => `/v1/twin/projects/${enc(projectId)}`;

export async function getInterfaces(projectId: string, signal?: AbortSignal): Promise<InterfaceListResponse> {
  const { data } = await apiClient.get<InterfaceListResponse>(`${base(projectId)}/interfaces`, { signal });
  return data;
}

export async function getImpact(projectId: string, ref: string, signal?: AbortSignal): Promise<ImpactResult> {
  const { data } = await apiClient.get<ImpactResult>(`${base(projectId)}/impact/${enc(ref)}`, { signal });
  return data;
}

export interface ImpactPreviewBody {
  item_key: string;
  old_revision?: number | null;
  new_revision?: number | null;
  changed_parameters?: string[] | null;
  max_depth?: number;
  include_unaffected?: boolean;
}

export async function previewImpact(projectId: string, body: ImpactPreviewBody): Promise<ImpactResult> {
  const { data } = await apiClient.post<ImpactResult>(`${base(projectId)}/impact/preview`, body);
  return data;
}
