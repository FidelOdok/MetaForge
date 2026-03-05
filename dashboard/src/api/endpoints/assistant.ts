import apiClient from '../client';

export interface SubmitRequestPayload {
  action: string;
  target_id: string;
  parameters?: Record<string, unknown>;
  session_id?: string;
}

export interface AssistantResponse {
  request_id: string;
  status: string;
  result: Record<string, unknown>;
  errors: string[];
}

export interface RunStatusResponse {
  run_id: string;
  status: string;
  steps: Record<string, {
    status: string;
    agent_code: string;
    task_type: string;
    result: Record<string, unknown>;
    error: string | null;
    started_at: string | null;
    completed_at: string | null;
  }>;
  completed_at: string | null;
}

export interface Proposal {
  change_id: string;
  agent_code: string;
  description: string;
  diff: Record<string, unknown>;
  artifacts_affected: string[];
  status: string;
  session_id: string;
  created_at: string;
  decided_at: string | null;
  decision_reason: string | null;
  reviewer: string | null;
}

export interface ProposalListResponse {
  proposals: Proposal[];
  total: number;
}

export async function submitRequest(payload: SubmitRequestPayload): Promise<AssistantResponse> {
  const { data } = await apiClient.post<AssistantResponse>('/assistant/request', payload);
  return data;
}

export async function getRunStatus(runId: string): Promise<RunStatusResponse> {
  const { data } = await apiClient.get<RunStatusResponse>(`/assistant/request/${runId}`);
  return data;
}

export async function getProposals(sessionId?: string): Promise<ProposalListResponse> {
  const params = sessionId ? { session_id: sessionId } : {};
  const { data } = await apiClient.get<ProposalListResponse>('/assistant/proposals', { params });
  return data;
}

export async function decideProposal(
  changeId: string,
  decision: 'approve' | 'reject',
  reason: string,
  reviewer: string
): Promise<Proposal> {
  const { data } = await apiClient.post<Proposal>(`/assistant/proposals/${changeId}/decide`, {
    change_id: changeId,
    decision,
    reason,
    reviewer,
  });
  return data;
}
