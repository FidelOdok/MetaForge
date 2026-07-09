import { useState, useCallback, useMemo } from 'react';
import type { ChatMessage, ChatThread, ChatScopeKind } from '@/types/chat';
import {
  useChatThreads,
  useChatThread,
  useCreateChatThread,
  useSendChatMessage,
} from '@/hooks/use-chat';
import { useChatStore } from '@/store/chat-store';
import { useChatStream } from '@/hooks/use-chat-stream';

// Re-export ChatScopeKind so consumers do not need a separate import.
export type { ChatScopeKind } from '@/types/chat';

// ---------------------------------------------------------------------------
// Options & result types
// ---------------------------------------------------------------------------

export interface UseScopedChatOptions {
  /** The scope kind that binds this chat to a specific entity type. */
  scopeKind: ChatScopeKind;
  /** The ID of the entity (approval, BOM entry, twin node, session, etc.). */
  entityId: string;
  /** Optional agent code to associate with the chat (e.g. 'ME', 'SC'). */
  defaultAgentCode?: string;
  /** Human-readable label shown in the thread title. */
  label?: string;
  /** Channel ID to file the thread under. Defaults to `scopeKind`. */
  channelId?: string;
}

export interface UseScopedChatResult {
  /** The resolved thread for this scope, or `null` when none exists yet. */
  thread: ChatThread | null;
  /** Messages within the thread (empty array when no thread). */
  messages: ChatMessage[];
  /** Whether an agent is currently typing in this thread. */
  isTyping: boolean;
  /** Send a new user message into the thread. */
  sendMessage: (content: string) => void;
  /** Create a new thread for this scope (call when no thread exists). */
  createThread: () => void;
  /** `true` while the thread list or single-thread query is in flight. */
  isLoading: boolean;
  /** `true` while the create-thread mutation is in flight. */
  isCreating: boolean;
  /** `true` while the send-message mutation is in flight. */
  isSending: boolean;
}

// ---------------------------------------------------------------------------
// Hook implementation
// ---------------------------------------------------------------------------

/**
 * Manages chat state for a specific scope.
 *
 * The hook queries for existing threads matching the given `scopeKind` +
 * `entityId`, exposes the first matching thread (if any), and provides
 * `createThread` / `sendMessage` callbacks that integration panels can wire
 * directly to their UI.
 */
export function useScopedChat({
  scopeKind,
  entityId,
  defaultAgentCode,
  label,
  channelId,
}: UseScopedChatOptions): UseScopedChatResult {
  // ------------------------------------------------------------------
  // 1. Discover an existing thread for this scope
  // ------------------------------------------------------------------

  const threadsQuery = useChatThreads(
    { scopeKind, entityId, pageSize: 1 },
    { staleTime: 15_000 },
  );

  const existingThreadId = threadsQuery.data?.data[0]?.id ?? null;

  // Fetch the full thread (with messages) when one exists.
  const threadQuery = useChatThread(existingThreadId ?? undefined, {
    staleTime: 10_000,
  });

  const thread = threadQuery.data ?? null;
  const messages: ChatMessage[] = thread?.messages ?? [];

  // ------------------------------------------------------------------
  // 2. Typing indicator from the global Zustand store
  // ------------------------------------------------------------------

  const typingThreadIds = useChatStore((s) => s.typingThreadIds);
  const isTyping = existingThreadId ? typingThreadIds.has(existingThreadId) : false;

  // Subscribe to the thread's SSE stream so the agent answer renders
  // token-by-token (MET-548). Accumulated deltas live in the chat store.
  useChatStream(existingThreadId);

  const streamingText = useChatStore((s) =>
    existingThreadId ? s.streamingContent[existingThreadId] : undefined,
  );
  const clearStreamContent = useChatStore((s) => s.clearStreamContent);
  const clearAgentSteps = useChatStore((s) => s.clearAgentSteps);
  const setAgentTyping = useChatStore((s) => s.setAgentTyping);

  // ------------------------------------------------------------------
  // 3. Optimistic local message queue (before server confirms)
  // ------------------------------------------------------------------

  const [optimisticMessages, setOptimisticMessages] = useState<ChatMessage[]>([]);

  // Provisional agent bubble rendered from the live stream, shown until the
  // real message lands via refetch (cleared on send success).
  const streamingMessage: ChatMessage | null =
    existingThreadId && streamingText
      ? {
          id: `streaming-${existingThreadId}`,
          threadId: existingThreadId,
          actor: { id: 'harness-agent', kind: 'agent', displayName: 'Agent' },
          content: streamingText,
          status: 'sending',
          createdAt: new Date().toISOString(),
        }
      : null;

  const allMessages = useMemo(
    () => [
      ...messages,
      ...optimisticMessages,
      ...(streamingMessage ? [streamingMessage] : []),
    ],
    [messages, optimisticMessages, streamingMessage],
  );

  // ------------------------------------------------------------------
  // 4. Mutations
  // ------------------------------------------------------------------

  const createThreadMutation = useCreateChatThread();
  const sendMessageMutation = useSendChatMessage();

  /**
   * Create a new thread scoped to this entity.
   */
  const createThread = useCallback(() => {
    const agentLabel = defaultAgentCode ? ` with ${defaultAgentCode}` : '';
    const title = label
      ? `${label}${agentLabel}`
      : `${scopeKind} ${entityId}${agentLabel}`;

    createThreadMutation.mutate({
      channelId: channelId ?? scopeKind,
      title,
      scope: { kind: scopeKind, entityId, label },
    });
  }, [
    createThreadMutation,
    channelId,
    scopeKind,
    entityId,
    label,
    defaultAgentCode,
  ]);

  /**
   * Send a user message to the current thread. If no thread exists yet this
   * is a no-op (the UI should surface the "Start conversation" CTA instead).
   */
  const sendMessage = useCallback(
    (content: string) => {
      if (!existingThreadId || content.trim().length === 0) return;

      // New turn — drop the previous turn's tool-call timeline.
      clearAgentSteps(existingThreadId);

      // Optimistic message so the UI feels instant.
      const optimistic: ChatMessage = {
        id: `optimistic-${Date.now()}`,
        threadId: existingThreadId,
        actor: {
          id: 'current-user',
          kind: 'user',
          displayName: 'You',
        },
        content,
        status: 'sending',
        createdAt: new Date().toISOString(),
      };

      setOptimisticMessages((prev) => [...prev, optimistic]);

      sendMessageMutation.mutate(
        { threadId: existingThreadId, payload: { content } },
        {
          onSuccess: () => {
            // Remove the optimistic message once the server copy lands.
            setOptimisticMessages((prev) =>
              prev.filter((m) => m.id !== optimistic.id),
            );
            // The turn is complete (POST resolves after the full harness turn);
            // drop the provisional streaming bubble so the refetched, persisted
            // message is the single source of truth.
            clearStreamContent(existingThreadId);
            setAgentTyping(existingThreadId, false);
          },
          onError: () => {
            // Mark the optimistic message as failed.
            setOptimisticMessages((prev) =>
              prev.map((m) =>
                m.id === optimistic.id ? { ...m, status: 'error' as const } : m,
              ),
            );
          },
        },
      );
    },
    [existingThreadId, sendMessageMutation, clearStreamContent, clearAgentSteps, setAgentTyping],
  );

  // ------------------------------------------------------------------
  // 5. Return
  // ------------------------------------------------------------------

  return {
    thread,
    messages: allMessages,
    isTyping,
    sendMessage,
    createThread,
    isLoading: threadsQuery.isLoading || threadQuery.isLoading,
    isCreating: createThreadMutation.isPending,
    isSending: sendMessageMutation.isPending,
  };
}
