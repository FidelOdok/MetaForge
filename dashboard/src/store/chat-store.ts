import { create } from 'zustand';
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

export const useChatStore = create<ChatUIState & ChatUIActions>((set) => ({
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
}));
