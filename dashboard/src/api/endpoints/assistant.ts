import apiClient from '../client';

export interface Proposal {
  change_id: string;
  agent_code: string;
  description: string;
  diff: Record<string, unknown>;
  work_products_affected: string[];
  status: string;
  session_id: string;
  project_id: string | null;
  created_at: string;
  decided_at: string | null;
  decision_reason: string | null;
  reviewer: string | null;
}

export interface ProposalListResponse {
  proposals: Proposal[];
  total: number;
}

export async function getProposals(projectId?: string): Promise<ProposalListResponse> {
  const params = projectId ? { project_id: projectId } : {};
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
