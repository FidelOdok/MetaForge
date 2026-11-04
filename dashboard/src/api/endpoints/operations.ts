import apiClient from '../client';
import { apiUrl } from '../../lib/gatewayConfig';
import type { OperationCancelResponse, OperationsSnapshotResponse } from '../../types/operations';

/**
 * Project operations (FORGE-785 contract, consumed by FORGE-786).
 *
 * The snapshot is the authoritative picture; the stream carries changes after
 * its `cursor`. A client that is told to `resync`, or that reconnects after an
 * error, refetches the snapshot and resumes from the new cursor.
 */

const enc = encodeURIComponent;

/** `GET /v1/projects/{id}/operations`. */
export async function getOperations(
  projectId: string,
  options: { active?: boolean; limit?: number; signal?: AbortSignal } = {},
): Promise<OperationsSnapshotResponse> {
  const params: Record<string, string> = {};
  if (options.active !== undefined) params.active = String(options.active);
  if (options.limit !== undefined) params.limit = String(options.limit);
  const { data } = await apiClient.get<OperationsSnapshotResponse>(`/projects/${enc(projectId)}/operations`, {
    params,
    signal: options.signal,
  });
  return {
    project_id: data.project_id ?? projectId,
    cursor: data.cursor ?? 0,
    operations: data.operations ?? [],
  };
}

/** The SSE stream URL. `Last-Event-ID` carries the cursor, so no query is needed. */
export function operationsStreamUrl(projectId: string): string {
  return apiUrl(`/projects/${enc(projectId)}/operations/stream`);
}

/** `POST /v1/projects/{id}/operations/{op}/cancel`. 409 when not cancellable. */
export async function cancelOperation(projectId: string, operationId: string): Promise<OperationCancelResponse> {
  const { data } = await apiClient.post<OperationCancelResponse>(
    `/projects/${enc(projectId)}/operations/${enc(operationId)}/cancel`,
  );
  return data;
}
