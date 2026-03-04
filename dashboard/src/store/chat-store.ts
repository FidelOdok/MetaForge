import { create } from 'zustand';

// ---------------------------------------------------------------------------
// State shape
// ---------------------------------------------------------------------------

interface ChatUIState {
  /** Whether the chat sidebar is open. */
  sidebarOpen: boolean;
  /** ID of the thread currently highlighted in the sidebar, if any. */
  activeSidebarThreadId: string | null;
  /**
   * Accumulated streaming content keyed by message ID.
   * Chunks are appended as they arrive via WebSocket; the entry is cleared
   * once the stream completes.
   */
  streamingContent: Record<string, string>;
  /** Set of thread IDs where an agent is currently typing. */
  typingThreadIds: Set<string>;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

interface ChatUIActions {
  /** Open the sidebar, optionally jumping to a specific thread. */
  openSidebar: (threadId?: string) => void;
  /** Close the sidebar and clear the active thread selection. */
  closeSidebar: () => void;
  /** Append a streaming chunk for the given message. */
  appendStreamChunk: (messageId: string, chunk: string) => void;
  /** Remove accumulated streaming content once the stream is done. */
  clearStreamContent: (messageId: string) => void;
  /** Toggle the typing indicator for a thread. */
  setAgentTyping: (threadId: string, isTyping: boolean) => void;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useChatStore = create<ChatUIState & ChatUIActions>((set) => ({
  // -- initial state --
  sidebarOpen: false,
  activeSidebarThreadId: null,
  streamingContent: {},
  typingThreadIds: new Set<string>(),

  // -- actions --
  openSidebar: (threadId) =>
    set({
      sidebarOpen: true,
      activeSidebarThreadId: threadId ?? null,
    }),

  closeSidebar: () =>
    set({
      sidebarOpen: false,
      activeSidebarThreadId: null,
    }),

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
}));
