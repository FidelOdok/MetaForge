import {
  useQuery,
  useMutation,
  useQueryClient,
  type UseQueryOptions,
  type UseMutationOptions,
} from '@tanstack/react-query';
import type { PaginatedResponse } from '@/types/common';
import type { ChatThread, ChatMessage, ChatChannel } from '@/types/chat';
import {
  getChatThreads,
  getChatThread,
  getChatChannels,
  createChatThread,
  sendChatMessage,
  type GetChatThreadsParams,
  type CreateChatThreadPayload,
  type SendChatMessagePayload,
} from '@/api/endpoints/chat';

// ---------------------------------------------------------------------------
// Query-key factory — keeps cache keys consistent and easy to invalidate
// ---------------------------------------------------------------------------

export const chatKeys = {
  all: ['chat'] as const,
  threads: (params?: GetChatThreadsParams) =>
    [...chatKeys.all, 'threads', params ?? {}] as const,
  thread: (id: string) => [...chatKeys.all, 'thread', id] as const,
  channels: () => [...chatKeys.all, 'channels'] as const,
};

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

/**
 * Fetch a paginated list of chat threads.
 *
 * - `staleTime`: 30 s (matches default QueryClient staleTime).
 * - Consumers can override any option via `options`.
 */
export function useChatThreads(
  params?: GetChatThreadsParams,
  options?: Omit<
    UseQueryOptions<PaginatedResponse<ChatThread>, Error>,
    'queryKey' | 'queryFn'
  >,
) {
  return useQuery<PaginatedResponse<ChatThread>, Error>({
    queryKey: chatKeys.threads(params),
    queryFn: () => getChatThreads(params),
    staleTime: 30_000,
    ...options,
  });
}

/**
 * Fetch a single chat thread by ID (includes messages).
 *
 * - `staleTime`: 10 s (threads with active chat need fresher data).
 * - Disabled when `id` is falsy so callers can conditionally fetch.
 */
export function useChatThread(
  id: string | undefined,
  options?: Omit<
    UseQueryOptions<ChatThread, Error>,
    'queryKey' | 'queryFn'
  >,
) {
  return useQuery<ChatThread, Error>({
    queryKey: chatKeys.thread(id ?? ''),
    queryFn: () => getChatThread(id!),
    staleTime: 10_000,
    enabled: !!id,
    ...options,
  });
}

/**
 * Fetch the list of chat channels.
 *
 * - `staleTime`: 60 s (channels change infrequently).
 */
export function useChatChannels(
  options?: Omit<
    UseQueryOptions<ChatChannel[], Error>,
    'queryKey' | 'queryFn'
  >,
) {
  return useQuery<ChatChannel[], Error>({
    queryKey: chatKeys.channels(),
    queryFn: getChatChannels,
    staleTime: 60_000,
    ...options,
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

/**
 * Create a new chat thread.
 *
 * Invalidates both the thread list and channel list on success so unread
 * counts and new-thread data stay current.
 */
export function useCreateChatThread(
  options?: Omit<
    UseMutationOptions<ChatThread, Error, CreateChatThreadPayload>,
    'mutationFn'
  >,
) {
  const queryClient = useQueryClient();

  return useMutation<ChatThread, Error, CreateChatThreadPayload>({
    mutationFn: (payload) => createChatThread(payload),
    onSuccess: (...args) => {
      void queryClient.invalidateQueries({ queryKey: chatKeys.threads() });
      void queryClient.invalidateQueries({ queryKey: chatKeys.channels() });
      options?.onSuccess?.(...args);
    },
    ...options,
  });
}

/** Variables for the send-message mutation. */
interface SendChatMessageVars {
  threadId: string;
  payload: SendChatMessagePayload;
}

/**
 * Send a message to an existing chat thread.
 *
 * Invalidates the specific thread (to pick up the new message) and the
 * thread list (to update `lastMessageAt` ordering).
 */
export function useSendChatMessage(
  options?: Omit<
    UseMutationOptions<ChatMessage, Error, SendChatMessageVars>,
    'mutationFn'
  >,
) {
  const queryClient = useQueryClient();

  return useMutation<ChatMessage, Error, SendChatMessageVars>({
    mutationFn: ({ threadId, payload }) => sendChatMessage(threadId, payload),
    onSuccess: (...args) => {
      void queryClient.invalidateQueries({
        queryKey: chatKeys.thread(args[1].threadId),
      });
      void queryClient.invalidateQueries({ queryKey: chatKeys.threads() });
      options?.onSuccess?.(...args);
    },
    ...options,
  });
}
