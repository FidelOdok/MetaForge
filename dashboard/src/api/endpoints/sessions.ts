import apiClient from '../client';
import type { AgentSession } from '../../types/session';

interface SessionResponseRaw {
  id: string;
  agent_code: string;
  task_type: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  events: Array<{
    id: string;
    timestamp: string;
    type: string;
    agent_code: string;
    message: string;
    data?: Record<string, unknown>;
  }>;
  run_id: string | null;
}

interface SessionListResponseRaw {
  sessions: SessionResponseRaw[];
  total: number;
}

function mapSession(raw: SessionResponseRaw): AgentSession {
  return {
    id: raw.id,
    agentCode: raw.agent_code,
    taskType: raw.task_type,
    status: raw.status as AgentSession['status'],
    startedAt: raw.started_at,
    completedAt: raw.completed_at ?? undefined,
    runId: raw.run_id ?? undefined,
    events: raw.events.map((e) => ({
      id: e.id,
      timestamp: e.timestamp,
      type: e.type as 'task_started' | 'task_completed' | 'task_failed' | 'proposal_created',
      agentCode: e.agent_code,
      message: e.message,
      data: e.data,
    })),
  };
}

export async function getSessions(): Promise<AgentSession[]> {
  const { data } = await apiClient.get<SessionListResponseRaw>('/sessions');
  return data.sessions.map(mapSession);
}

export async function getSession(id: string): Promise<AgentSession | undefined> {
  try {
    const { data } = await apiClient.get<SessionResponseRaw>(`/sessions/${id}`);
    return mapSession(data);
  } catch {
    return undefined;
  }
}
