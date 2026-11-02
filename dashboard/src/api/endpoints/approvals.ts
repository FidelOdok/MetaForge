import apiClient from '../client';
import type {
  ApprovalDecisionRequest,
  ApprovalItem,
  ApprovalList,
  ApprovalListFilters,
} from '../../types/approvals';

/** Tells the gateway which surface recorded the decision (audit trail). */
const SURFACE_HEADERS = { 'X-MetaForge-Surface': 'dashboard' } as const;

interface ApprovalListRaw {
  items: ApprovalItem[];
  unscoped_count?: number;
}

/** `GET /v1/approvals`, optionally filtered by status, project and kind. */
export async function listApprovals(filters: ApprovalListFilters = {}): Promise<ApprovalList> {
  const params: Record<string, string> = { status: filters.status ?? 'pending' };
  if (filters.projectId) params.project_id = filters.projectId;
  if (filters.kind) params.kind = filters.kind;
  const { data } = await apiClient.get<ApprovalListRaw>('/approvals', {
    params,
    headers: SURFACE_HEADERS,
  });
  return { items: data.items ?? [], unscopedCount: data.unscoped_count ?? 0 };
}

/** `GET /v1/approvals/{id}`. Ids look like `gate:run_abc`, so they are encoded. */
export async function getApproval(id: string): Promise<ApprovalItem> {
  const { data } = await apiClient.get<ApprovalItem>(`/approvals/${encodeURIComponent(id)}`, {
    headers: SURFACE_HEADERS,
  });
  return data;
}

/** `POST /v1/approvals/{id}/decision`. Returns the updated item. */
export async function decideApproval(
  id: string,
  body: ApprovalDecisionRequest,
): Promise<ApprovalItem> {
  const { data } = await apiClient.post<ApprovalItem>(
    `/approvals/${encodeURIComponent(id)}/decision`,
    body,
    { headers: SURFACE_HEADERS },
  );
  return data;
}

/** FORGE-726: who decides a project's gates. */
export interface ApprovalPolicy {
  project_id: string;
  mode: 'off' | 'advise' | 'auto_clean' | 'auto';
  reviewer: 'server' | 'client' | 'client_then_server';
  reviewer_model: string;
  expires_at: string | null;
  updated_by: string;
  updated_at: string | null;
}

export interface ApprovalPolicyView {
  policy: ApprovalPolicy;
  effective_mode: ApprovalPolicy['mode'];
  max_mode: ApprovalPolicy['mode'];
  modes: ApprovalPolicy['mode'][];
}

export interface ApprovalPolicyUpdate {
  mode: ApprovalPolicy['mode'];
  reviewer: ApprovalPolicy['reviewer'];
  reviewer_model: string;
  expires_in_hours?: number;
}

/** `GET /v1/projects/{id}/approval-policy`. */
export async function getApprovalPolicy(projectId: string): Promise<ApprovalPolicyView> {
  const { data } = await apiClient.get<ApprovalPolicyView>(
    `/projects/${encodeURIComponent(projectId)}/approval-policy`,
  );
  return data;
}

/** `PUT /v1/projects/{id}/approval-policy`. A person's decision: sent as the dashboard. */
export async function putApprovalPolicy(
  projectId: string,
  body: ApprovalPolicyUpdate,
): Promise<ApprovalPolicyView> {
  const { data } = await apiClient.put<ApprovalPolicyView>(
    `/projects/${encodeURIComponent(projectId)}/approval-policy`,
    body,
    { headers: SURFACE_HEADERS },
  );
  return data;
}
