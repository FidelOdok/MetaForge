import axios from 'axios';

import apiClient from '../client';
import type { ApprovalDecision, HarnessRun, RunStatus } from '../../types/run';

interface RunRaw {
  id: string;
  status: string;
  request: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  error: string | null;
  approval_reason: string | null;
  result: Record<string, unknown> | null;
  history: string[];
}

interface RunListRaw {
  runs: RunRaw[];
  /** Runs omitted for having no project, when the list was scoped to one. */
  unscoped_count?: number;
}

function mapRun(raw: RunRaw): HarnessRun {
  return {
    id: raw.id,
    status: raw.status as RunStatus,
    request: raw.request ?? {},
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    error: raw.error ?? undefined,
    approvalReason: raw.approval_reason ?? undefined,
    result: raw.result ?? undefined,
    history: (raw.history ?? []) as RunStatus[],
  };
}

/** List all runs via `GET /v1/runs`. */
/** Runs, optionally scoped to one project.
 *
 * `unscopedCount` is how many were left out for carrying no project. It is
 * returned rather than discarded so the page can say so: a run that silently
 * vanishes when you pick a project reads as "there are none".
 */
export interface RunList {
  runs: HarnessRun[];
  unscopedCount: number;
}

export async function listRuns(projectId?: string): Promise<RunList> {
  const { data } = await apiClient.get<RunListRaw>('/runs', {
    params: projectId ? { project_id: projectId } : undefined,
  });
  return { runs: data.runs.map(mapRun), unscopedCount: data.unscoped_count ?? 0 };
}

/**
 * Fetch one run via `GET /v1/runs/{id}`; undefined if the gateway says 404.
 * Any other failure is rethrown so the page can show "could not be loaded"
 * instead of a misleading "not found".
 */
export async function getRun(id: string): Promise<HarnessRun | undefined> {
  try {
    const { data } = await apiClient.get<RunRaw>(`/runs/${id}`);
    return mapRun(data);
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) return undefined;
    throw err;
  }
}

/** Create a run via `POST /v1/runs`. */
export async function createRun(
  request: Record<string, unknown> = {},
  start = true,
): Promise<HarnessRun> {
  const { data } = await apiClient.post<RunRaw>('/runs', { request, start });
  return mapRun(data);
}

/** Approve or reject a paused run via `POST /v1/runs/{id}/approval`. */
export async function submitRunApproval(
  id: string,
  decision: ApprovalDecision,
): Promise<HarnessRun> {
  const { data } = await apiClient.post<RunRaw>(`/runs/${id}/approval`, { decision });
  return mapRun(data);
}
