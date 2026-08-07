import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { AgentStep } from '@/types/chat';

// ---------------------------------------------------------------------------
// State shape
// ---------------------------------------------------------------------------

interface ChatUIState {
  /**
   * Accumulated streaming content keyed by thread/message id.
   * Chunks are appended as they arrive over SSE; the entry is cleared once the
   * stream completes.
   */
  streamingContent: Record<string, string>;
  /** Agent ReAct steps (tool calls / reasoning) for the in-flight turn, per thread. */
  agentSteps: Record<string, AgentStep[]>;
  /** Set of thread IDs where an agent is currently typing. */
  typingThreadIds: Set<string>;
  /** Selected provider id for outgoing messages (null = server default). */
  selectedProvider: string | null;
  /** Selected model for outgoing messages (null = server default). */
  selectedModel: string | null;
  /** Enabled MCP tool ids (null = all available). */
  enabledTools: string[] | null;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

interface ChatUIActions {
  /** Append a streaming chunk for the given thread/message. */
  appendStreamChunk: (messageId: string, chunk: string) => void;
  /** Remove accumulated streaming content once the stream is done. */
  clearStreamContent: (messageId: string) => void;
  /** Append an agent step (tool call / reasoning) for a thread's current turn. */
  appendAgentStep: (threadId: string, step: AgentStep) => void;
  /** Clear the accumulated agent steps for a thread (new turn / teardown). */
  clearAgentSteps: (threadId: string) => void;
  /** Toggle the typing indicator for a thread. */
  setAgentTyping: (threadId: string, isTyping: boolean) => void;
  /** Set the selected provider + model for outgoing messages. */
  setModel: (provider: string | null, model: string | null) => void;
  /** Set the enabled MCP tool ids (null = all available). */
  setEnabledTools: (toolIds: string[] | null) => void;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

// The model/provider/tools selection is persisted to localStorage so it
// survives a reload (it used to reset every time). This is a per-user,
// client-side preference only — it is intentionally NOT written through
// PUT /v1/harness/selection, which is an admin-scoped endpoint that
// overrides the gateway's system-wide default for every user/session, not
// a per-user setting.
export const useChatStore = create<ChatUIState & ChatUIActions>()(
  persist(
    (set) => ({
      // -- initial state --
      streamingContent: {},
      agentSteps: {},
      typingThreadIds: new Set<string>(),
      selectedProvider: null,
      selectedModel: null,
      enabledTools: null,

      // -- actions --
      appendStreamChunk: (messageId, chunk) =>
        set((state) => ({
          streamingContent: {
            ...state.streamingContent,
            [messageId]: (state.streamingContent[messageId] ?? '') + chunk,
          },
        })),

      clearStreamContent: (messageId) =>
        set((state) => {
          const { [messageId]: _removed, ...rest } = state.streamingContent;
          return { streamingContent: rest };
        }),

      appendAgentStep: (threadId, step) =>
        set((state) => ({
          agentSteps: {
            ...state.agentSteps,
            [threadId]: [...(state.agentSteps[threadId] ?? []), step],
          },
        })),

      clearAgentSteps: (threadId) =>
        set((state) => {
          const { [threadId]: _removed, ...rest } = state.agentSteps;
          return { agentSteps: rest };
        }),

      setAgentTyping: (threadId, isTyping) =>
        set((state) => {
          const next = new Set(state.typingThreadIds);
          if (isTyping) {
            next.add(threadId);
          } else {
            next.delete(threadId);
          }
          return { typingThreadIds: next };
        }),

      setModel: (provider, model) => set({ selectedProvider: provider, selectedModel: model }),

      setEnabledTools: (toolIds) => set({ enabledTools: toolIds }),
    }),
    {
      name: 'metaforge.chat-model-selection',
      version: 1,
      // Only the durable preference persists — streaming/typing state is
      // per-session runtime state and must not survive a reload.
      partialize: (state) => ({
        selectedProvider: state.selectedProvider,
        selectedModel: state.selectedModel,
        enabledTools: state.enabledTools,
      }),
    },
  ),
);
