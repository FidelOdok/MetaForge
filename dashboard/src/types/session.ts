/** Session and agent run types for the dashboard. */

// 'abandoned' comes from the stale-session sweep (MET-510) when a session is
// left open past its liveness window.
export type SessionStatus = 'running' | 'completed' | 'failed' | 'pending' | 'abandoned';

export interface AgentEvent {
  id: string;
  timestamp: string;
  // The gateway's SessionEventResponse.type is an unconstrained string, not a
  // closed enum -- the MCP capture vocabulary (thought/action/decision/
  // observation/error/result) and the legacy workflow-run vocabulary
  // (task_started/task_completed/task_failed/proposal_created) both flow
  // through here. Treat it as open so an unrecognized future value degrades
  // to a sane default instead of silently rendering nothing (MET-675).
  type: string;
  agentCode: string;
  message: string;
  data?: Record<string, unknown>;
}

export interface AgentSession {
  id: string;
  agentCode: string;
  taskType: string;
  status: SessionStatus;
  startedAt: string;
  completedAt?: string;
  events: AgentEvent[];
  runId?: string;
}
