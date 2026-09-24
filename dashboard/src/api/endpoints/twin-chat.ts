import apiClient from '../client';
import { apiUrl } from '../../lib/gatewayConfig';

// ---------------------------------------------------------------------------
// Digital-twin conversation API: project-scoped chat threads on the gateway's
// /v1/chat routes (api_gateway/chat/routes.py). Powers the twin agent drawer.
// ---------------------------------------------------------------------------

export interface TwinChatMessage {
  id: string;
  actor_id: string;
  actor_kind: string;
  content: string;
  status: string;
  graph_ref_node?: string | null;
  graph_ref_type?: string | null;
  graph_ref_label?: string | null;
  created_at: string;
}

export interface TwinChatThreadSummary {
  id: string;
  title: string;
  scope_kind: string;
  scope_entity_id: string;
  last_message_at: string;
  message_count?: number;
}

export interface TwinChatThread extends TwinChatThreadSummary {
  messages: TwinChatMessage[];
}

export interface SendTwinChatMessage {
  content: string;
  tools?: string[];
  provider?: string;
  model?: string;
  graph_ref_node?: string;
  graph_ref_type?: string;
  graph_ref_label?: string;
}

/** GET /chat/threads scoped to a project. */
export async function listTwinChatThreads(projectId: string): Promise<TwinChatThreadSummary[]> {
  const { data } = await apiClient.get<{ threads: TwinChatThreadSummary[] }>('/chat/threads', {
    params: { scope_kind: 'project', entity_id: projectId, per_page: 100 },
  });
  return data.threads;
}

/** GET /chat/threads/{id} with its messages. */
export async function getTwinChatThread(threadId: string): Promise<TwinChatThread> {
  const { data } = await apiClient.get<TwinChatThread>(`/chat/threads/${encodeURIComponent(threadId)}`);
  return data;
}

/** POST /chat/threads: open a new project-scoped twin conversation. */
export async function createTwinChatThread(projectId: string): Promise<TwinChatThread> {
  const { data } = await apiClient.post<TwinChatThread>('/chat/threads', {
    scope_kind: 'project',
    scope_entity_id: projectId,
    title: 'Digital twin workspace',
  });
  return data;
}

/** POST /chat/threads/{id}/messages: runs a full agent turn (no client timeout). */
export async function sendTwinChatMessage(
  threadId: string,
  body: SendTwinChatMessage,
  signal?: AbortSignal,
): Promise<TwinChatMessage> {
  const { data } = await apiClient.post<TwinChatMessage>(
    `/chat/threads/${encodeURIComponent(threadId)}/messages`,
    { ...body, actor_id: 'dashboard-user', actor_kind: 'user' },
    { timeout: 0, signal },
  );
  return data;
}

/** SSE URL for a thread's live agent events. */
export function twinChatStreamUrl(threadId: string): string {
  return apiUrl(`/chat/threads/${encodeURIComponent(threadId)}/stream`);
}
