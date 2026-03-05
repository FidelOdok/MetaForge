/** Session and agent run types for the dashboard. */

export type SessionStatus = 'running' | 'completed' | 'failed' | 'pending';

export interface AgentEvent {
  id: string;
  timestamp: string;
  type: 'task_started' | 'task_completed' | 'task_failed' | 'proposal_created';
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
