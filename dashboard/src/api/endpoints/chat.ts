import apiClient from '@/api/client';
import type { PaginatedResponse } from '@/types/common';
import type {
  ChatThread,
  ChatMessage,
  ChatChannel,
  ChatScopeKind,
} from '@/types/chat';

// ---------------------------------------------------------------------------
// Request / payload interfaces
// ---------------------------------------------------------------------------

/** Query parameters for listing chat threads. */
export interface GetChatThreadsParams {
  /** Filter by channel ID. */
  channelId?: string;
  /** Filter by scope kind. */
  scopeKind?: ChatScopeKind;
  /** Filter by entity ID within a scope. */
  entityId?: string;
  /** Include archived threads. */
  includeArchived?: boolean;
  /** 1-based page number. */
  page?: number;
  /** Number of items per page. */
  pageSize?: number;
}

/** Payload for creating a new chat thread. */
export interface CreateChatThreadPayload {
  /** Channel the thread belongs to. */
  channelId: string;
  /** Human-readable thread title. */
  title: string;
  /** Scope binding for the thread. */
  scope: {
    kind: ChatScopeKind;
    entityId: string;
    label?: string;
  };
}

/** Payload for sending a message in a chat thread. */
export interface SendChatMessagePayload {
  /** Markdown message content. */
  content: string;
  /** Optional Digital-Twin node reference attached to the message. */
  graphRef?: {
    nodeId: string;
    nodeType: string;
    label: string;
  };
  /** Per-turn harness selection (MET-548) — from the model/tools selector. */
  provider?: string | null;
  model?: string | null;
  tools?: string[] | null;
}

// ---------------------------------------------------------------------------
// Response mappers (backend snake_case → frontend camelCase)
// ---------------------------------------------------------------------------

/* eslint-disable @typescript-eslint/no-explicit-any */

function mapMessage(raw: any): ChatMessage {
  return {
    id: raw.id,
    threadId: raw.thread_id ?? raw.threadId,
    actor: raw.actor ?? {
      id: raw.actor_id ?? 'unknown',
      kind: raw.actor_kind ?? 'system',
      displayName: raw.actor_kind === 'agent' ? raw.actor_id : raw.actor_id ?? 'Unknown',
    },
    content: raw.content,
    status: raw.status === 'delivered' ? 'delivered' : 'sent',
    createdAt: raw.created_at ?? raw.createdAt,
    updatedAt: raw.updated_at ?? raw.updatedAt,
    graphRef: raw.graph_ref_node
      ? { nodeId: raw.graph_ref_node, nodeType: raw.graph_ref_type ?? '', label: raw.graph_ref_label ?? '' }
      : raw.graphRef,
  };
}

function mapThread(raw: any): ChatThread {
  return {
    id: raw.id,
    scope: raw.scope ?? {
      kind: raw.scope_kind ?? 'session',
      entityId: raw.scope_entity_id ?? '',
      label: raw.title,
    },
    channelId: raw.channel_id ?? raw.channelId,
    title: raw.title,
    messages: (raw.messages ?? []).map(mapMessage),
    participants: raw.participants ?? [],
    createdAt: raw.created_at ?? raw.createdAt,
    lastMessageAt: raw.last_message_at ?? raw.lastMessageAt,
    archived: raw.archived ?? false,
  };
}

function mapChannel(raw: any): ChatChannel {
  return {
    id: raw.id,
    name: raw.name,
    scopeKind: raw.scope_kind ?? raw.scopeKind,
    unreadCount: raw.unread_count ?? raw.unreadCount ?? 0,
  };
}

/* eslint-enable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

/**
 * List chat threads with optional filtering and pagination.
 *
 * GET /chat/threads
 */
export async function getChatThreads(
  params?: GetChatThreadsParams,
): Promise<PaginatedResponse<ChatThread>> {
  const qp: Record<string, string | number | boolean> = {};
  if (params?.channelId) qp.channel_id = params.channelId;
  if (params?.scopeKind) qp.scope_kind = params.scopeKind;
  if (params?.entityId) qp.entity_id = params.entityId;
  if (params?.includeArchived) qp.include_archived = params.includeArchived;
  if (params?.page) qp.page = params.page;
  if (params?.pageSize) qp.per_page = params.pageSize;

  const response = await apiClient.get('/chat/threads', { params: qp });
  const raw = response.data;

  const threads: ChatThread[] = (raw.threads ?? []).map(mapThread);
  const total: number = raw.total ?? threads.length;
  const page: number = raw.page ?? 1;
  const pageSize: number = raw.per_page ?? 20;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return { data: threads, total, page, pageSize, totalPages };
}

/**
 * Fetch a single chat thread by ID (includes messages).
 *
 * GET /chat/threads/:id
 */
export async function getChatThread(id: string): Promise<ChatThread> {
  const response = await apiClient.get(`/chat/threads/${id}`);
  return mapThread(response.data);
}

/**
 * Create a new chat thread.
 *
 * POST /chat/threads
 */
export async function createChatThread(
  payload: CreateChatThreadPayload,
): Promise<ChatThread> {
  const body = {
    scope_kind: payload.scope.kind,
    scope_entity_id: payload.scope.entityId,
    title: payload.title,
  };
  const response = await apiClient.post('/chat/threads', body);
  return mapThread(response.data);
}

/**
 * Send a message in an existing chat thread.
 *
 * POST /chat/threads/:threadId/messages
 */
export async function sendChatMessage(
  threadId: string,
  payload: SendChatMessagePayload,
): Promise<ChatMessage> {
  const body: Record<string, unknown> = {
    content: payload.content,
    actor_id: 'current-user',
    actor_kind: 'user',
  };
  if (payload.graphRef) {
    body.graph_ref_node = payload.graphRef.nodeId;
    body.graph_ref_type = payload.graphRef.nodeType;
    body.graph_ref_label = payload.graphRef.label;
  }
  if (payload.provider) body.provider = payload.provider;
  if (payload.model) body.model = payload.model;
  if (payload.tools != null) body.tools = payload.tools;
  const response = await apiClient.post(
    `/chat/threads/${threadId}/messages`,
    body,
  );
  return mapMessage(response.data);
}

/**
 * List all chat channels.
 *
 * GET /chat/channels
 */
export async function getChatChannels(): Promise<ChatChannel[]> {
  const response = await apiClient.get('/chat/channels');
  const raw = response.data;
  return (raw.channels ?? raw).map(mapChannel);
}
