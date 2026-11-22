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
export type ApprovalMode = 'off' | 'advise' | 'auto_clean' | 'auto';

export interface ApprovalPolicy {
  project_id: string;
  mode: ApprovalMode;
  reviewer: 'server' | 'client' | 'client_then_server';
  reviewer_model: string;
  expires_at: string | null;
  updated_by: string;
  updated_at: string | null;
  /** FORGE-904: `null` follows the account; `false` turns it off for this project. */
  allow_human_authority?: boolean | null;
  allow_release?: boolean | null;
}

/** What the people who run this MetaForge allow (its environment); read only. */
export interface DeploymentLimits {
  max_mode: ApprovalMode;
  allow_human_authority: boolean;
  allow_release: boolean;
  hourly_caps: Array<{ tool: string; per_hour: number | null; set_by: string }>;
  approval_ttl_seconds: number;
  /** Approval holds are off on this MetaForge (METAFORGE_APPROVALS=off). */
  approvals_off?: boolean;
  set_in: string;
}

export type DelegationKind = 'human_authority' | 'release';

export interface Delegation {
  allowed: boolean;
  decided_by: 'deployment' | 'account' | 'project';
  reason: string;
}

export interface ApprovalPolicyView {
  policy: ApprovalPolicy;
  effective_mode: ApprovalMode;
  /** The highest mode this project may choose: the account's and the deployment's lower. */
  max_mode: ApprovalMode;
  modes: ApprovalMode[];
  account_max_mode?: ApprovalMode | null;
  /** Which layer holds the mode below the project's choice. */
  limited_by?: 'expired' | 'account' | 'deployment' | null;
  delegation?: Partial<Record<DelegationKind, Delegation>>;
  deployment?: DeploymentLimits | null;
}

export interface ApprovalPolicyUpdate {
  mode: ApprovalMode;
  reviewer: ApprovalPolicy['reviewer'];
  reviewer_model: string;
  expires_in_hours?: number;
  allow_human_authority?: boolean | null;
  allow_release?: boolean | null;
}

export interface AccountPolicy {
  account: string;
  max_mode: ApprovalMode | null;
  allow_human_authority: boolean | null;
  allow_release: boolean | null;
  updated_by: string;
  updated_at: string | null;
}

export interface AccountPolicyView {
  policy: AccountPolicy;
  max_mode: ApprovalMode;
  modes: ApprovalMode[];
  allow_human_authority: boolean;
  allow_release: boolean;
  deployment: DeploymentLimits;
}

export interface AccountPolicyUpdate {
  max_mode: ApprovalMode | null;
  allow_human_authority: boolean | null;
  allow_release: boolean | null;
}

/** `GET /v1/account/approval-policy` (FORGE-904): your account's approval settings. */
export async function getAccountApprovalPolicy(): Promise<AccountPolicyView> {
  const { data } = await apiClient.get<AccountPolicyView>('/account/approval-policy');
  return data;
}

/** `PUT /v1/account/approval-policy`. A person's decision: sent as the dashboard. */
export async function putAccountApprovalPolicy(body: AccountPolicyUpdate): Promise<AccountPolicyView> {
  const { data } = await apiClient.put<AccountPolicyView>('/account/approval-policy', body, {
    headers: SURFACE_HEADERS,
  });
  return data;
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
