import { useEffect, useRef, useState } from 'react';
import type { ChatMessage } from '@/types/chat';
import {
  useChatThreads,
  useChatThread,
  useCreateChatThread,
  useSendChatMessage,
} from '@/hooks/use-chat';
import { useChatStream } from '@/hooks/use-chat-stream';
import { useChatStore } from '@/store/chat-store';
import { formatRelativeTime } from '@/utils/format-time';
import { ChatPanel } from './ChatPanel';

// ---------------------------------------------------------------------------
// KC tokens
// ---------------------------------------------------------------------------

const KC = {
  surfaceLow: '#191b22',
  surfaceHigh: '#282a30',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  primary: '#e67e22',
  border: 'rgba(65,72,90,0.2)',
};

/**
 * The main general-purpose chat: a conversation list (left) + the active
 * conversation (right), Claude/opencode-style. Each conversation is its own
 * `scopeKind: 'assistant'` thread. Streaming, typing, and the model/tools
 * selector come from the shared ChatPanel + chat store (MET-548).
 */
export function AssistantChat() {
  const { data: threadsPage } = useChatThreads(
    { scopeKind: 'assistant' },
    { staleTime: 10_000 },
  );
  const threads = threadsPage?.data ?? [];

  const [activeId, setActiveId] = useState<string | null>(null);

  const createThread = useCreateChatThread();
  const sendMessage = useSendChatMessage();
  const streamingContent = useChatStore((s) => s.streamingContent);
  const clearStreamContent = useChatStore((s) => s.clearStreamContent);
  const setAgentTyping = useChatStore((s) => s.setAgentTyping);
  const typingThreadIds = useChatStore((s) => s.typingThreadIds);

  function startConversation() {
    createThread.mutate(
      {
        channelId: 'assistant',
        title: 'New chat',
        scope: { kind: 'assistant', entityId: crypto.randomUUID(), label: 'Design Assistant' },
      },
      { onSuccess: (thread) => setActiveId(thread.id) },
    );
  }

  // Select the most recent conversation on mount; create one if none exist so
  // the SSE stream connects before the first message.
  const createdRef = useRef(false);
  useEffect(() => {
    if (activeId) return;
    const first = threads[0];
    if (first) {
      setActiveId(first.id);
      return;
    }
    if (!createdRef.current && !createThread.isPending) {
      createdRef.current = true;
      startConversation();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, threads]);

  const { data: activeThread } = useChatThread(activeId ?? undefined, {
    enabled: !!activeId,
  });
  useChatStream(activeId);

  const streamingText = activeId ? streamingContent[activeId] : undefined;
  const messages: ChatMessage[] = activeThread
    ? [
        ...activeThread.messages,
        ...(activeId && streamingText
          ? [
              {
                id: `streaming-${activeId}`,
                threadId: activeId,
                actor: { id: 'harness-agent', kind: 'agent' as const, displayName: 'Agent' },
                content: streamingText,
                status: 'sending' as const,
                createdAt: new Date().toISOString(),
              },
            ]
          : []),
      ]
    : [];
  const isTyping = !!activeId && typingThreadIds.has(activeId);

  function handleSend(content: string) {
    if (!activeId) return;
    const threadId = activeId;
    sendMessage.mutate(
      { threadId, payload: { content } },
      {
        onSuccess: () => {
          clearStreamContent(threadId);
          setAgentTyping(threadId, false);
        },
      },
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        height: '100%',
        border: `1px solid ${KC.border}`,
        borderRadius: 6,
        overflow: 'hidden',
      }}
    >
      {/* ---- Conversation list ---- */}
      <div
        style={{
          width: 220,
          flexShrink: 0,
          borderRight: `1px solid ${KC.border}`,
          display: 'flex',
          flexDirection: 'column',
          background: KC.surfaceLow,
        }}
      >
        <button
          type="button"
          onClick={startConversation}
          disabled={createThread.isPending}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            margin: 8,
            padding: '7px 10px',
            background: KC.surfaceHigh,
            border: `1px solid ${KC.border}`,
            borderRadius: 6,
            color: KC.onSurface,
            fontSize: 13,
            fontFamily: 'Inter, sans-serif',
            cursor: 'pointer',
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 16, color: KC.primary }}>
            add
          </span>
          New chat
        </button>

        <div style={{ overflowY: 'auto', flex: 1 }}>
          {threads.length === 0 ? (
            <div style={{ padding: 12, fontSize: 12, color: KC.onSurfaceVariant }}>
              No conversations yet.
            </div>
          ) : (
            threads.map((t) => {
              const active = t.id === activeId;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setActiveId(t.id)}
                  style={{
                    display: 'block',
                    width: '100%',
                    textAlign: 'left',
                    padding: '8px 12px',
                    background: active ? KC.surfaceHigh : 'transparent',
                    border: 'none',
                    borderLeft: `2px solid ${active ? KC.primary : 'transparent'}`,
                    color: KC.onSurface,
                    cursor: 'pointer',
                    fontFamily: 'Inter, sans-serif',
                  }}
                >
                  <div
                    style={{
                      fontSize: 13,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {t.title || 'Untitled'}
                  </div>
                  <div style={{ fontSize: 11, color: KC.onSurfaceVariant }}>
                    {formatRelativeTime(t.lastMessageAt ?? t.createdAt)}
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      {/* ---- Active conversation ---- */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {activeThread ? (
          <ChatPanel
            thread={activeThread}
            messages={messages}
            isTyping={isTyping}
            typingAgentName="Design Assistant"
            onSendMessage={handleSend}
          />
        ) : (
          <div
            style={{
              display: 'flex',
              height: '100%',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 13,
              color: KC.onSurfaceVariant,
              fontFamily: 'Inter, sans-serif',
            }}
          >
            {createThread.isPending ? 'Starting Design Assistant…' : 'Select or start a conversation.'}
          </div>
        )}
      </div>
    </div>
  );
}
