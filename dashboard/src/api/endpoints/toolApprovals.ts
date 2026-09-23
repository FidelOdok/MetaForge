import apiClient from '../client';

/**
 * A `requires_approval` tool call paused mid-chat-turn (twin.commit_geometry,
 * twin.record_decision, project.create/update/delete — FORGE-33). Mirrors
 * `api_gateway/runs/schemas.py`'s `RunResponse` — the same shape design-flow
 * gate approvals use (`endpoints/runs.ts`), just a different REST surface
 * (`/v1/chat/tool_approvals`, not `/v1/runs`) since this pauses a live chat
 * turn, not a design-flow run.
 */
export interface ToolApprovalRun {
  id: string;
  status: string;
  request: { tool: string; arguments: Record<string, unknown> };
  created_at: number;
  updated_at: number;
  error: string | null;
  approval_reason: string | null;
  result: Record<string, unknown> | null;
  history: string[];
}

export interface ToolApprovalListResponse {
  runs: ToolApprovalRun[];
}

/** Every tool call currently awaiting a decision (`GET /v1/chat/tool_approvals`
 * already filters to `awaiting_approval` server-side). */
export async function getPendingToolApprovals(): Promise<ToolApprovalListResponse> {
  const { data } = await apiClient.get<ToolApprovalListResponse>('/chat/tool_approvals');
  return data;
}

export async function decideToolApproval(
  runId: string,
  decision: 'approve' | 'reject'
): Promise<ToolApprovalRun> {
  const { data } = await apiClient.post<ToolApprovalRun>(`/chat/tool_approvals/${runId}`, {
    decision,
  });
  return data;
}
