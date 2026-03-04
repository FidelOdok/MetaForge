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
}

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
  const response = await apiClient.get<PaginatedResponse<ChatThread>>(
    '/chat/threads',
    { params },
  );
  return response.data;
}

/**
 * Fetch a single chat thread by ID (includes messages).
 *
 * GET /chat/threads/:id
 */
export async function getChatThread(id: string): Promise<ChatThread> {
  const response = await apiClient.get<ChatThread>(`/chat/threads/${id}`);
  return response.data;
}

/**
 * Create a new chat thread.
 *
 * POST /chat/threads
 */
export async function createChatThread(
  payload: CreateChatThreadPayload,
): Promise<ChatThread> {
  const response = await apiClient.post<ChatThread>('/chat/threads', payload);
  return response.data;
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
  const response = await apiClient.post<ChatMessage>(
    `/chat/threads/${threadId}/messages`,
    payload,
  );
  return response.data;
}

/**
 * List all chat channels.
 *
 * GET /chat/channels
 */
export async function getChatChannels(): Promise<ChatChannel[]> {
  const response = await apiClient.get<ChatChannel[]>('/chat/channels');
  return response.data;
}
