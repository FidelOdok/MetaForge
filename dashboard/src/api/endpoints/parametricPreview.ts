import apiClient from '../client';
import type { PreviewDefinition } from '../../lib/preview/definition';

/**
 * C3D slice 5: the L2 preview's shared feature definition, and parameter
 * commits from the viewer (drag handle release or the numeric field).
 */

const enc = encodeURIComponent;

export interface ParameterCommitBody {
  /** The parametric sketch revision the browser edited. */
  base_revision: number;
  feature_id?: string | null;
  values?: Record<string, number | string>;
  dimensions?: Record<string, number>;
  /** With a line's length: the browser solver's re-solved element positions (sketch space). */
  geometry?: Record<string, Record<string, unknown>>;
  source: 'drag' | 'numeric';
  /** How long the preview rebuild took, if measured. */
  preview_ms?: number | null;
}

export interface ParameterCommitReply {
  sketch_key: string;
  sketch_ref: string;
  revision: number | null;
  node_id: string;
  base_revision: string;
  feature_id: string;
  classification: { change_class: string; message: string; stage: string };
  build: {
    status: string;
    job_id: string | null;
    operation_id?: string | null;
    skipped?: boolean;
    detail?: string;
  } | null;
}

/** `GET /v1/twin/projects/{id}/parametric-sketches/{key}/preview-definition`. */
export async function getPreviewDefinition(
  projectId: string,
  sketchKey: string,
  opts: { featureId?: string | null; jobId?: string | null } = {},
  signal?: AbortSignal,
): Promise<PreviewDefinition> {
  const params: Record<string, string> = {};
  if (opts.featureId) params.feature_id = opts.featureId;
  if (opts.jobId) params.job_id = opts.jobId;
  const { data } = await apiClient.get<PreviewDefinition>(
    `/twin/projects/${enc(projectId)}/parametric-sketches/${enc(sketchKey)}/preview-definition`,
    { params, signal },
  );
  return data;
}

/** `POST /v1/twin/projects/{id}/parametric-sketches/{key}/parameters`. 409 on a stale base. */
export async function commitParameters(projectId: string, sketchKey: string, body: ParameterCommitBody): Promise<ParameterCommitReply> {
  const { data } = await apiClient.post<ParameterCommitReply>(
    `/twin/projects/${enc(projectId)}/parametric-sketches/${enc(sketchKey)}/parameters`,
    body,
  );
  return data;
}
