import { useCallback, useEffect } from 'react';
import { useChatStore } from '@/store/chat-store';
import { useChatThreads, useChatThread, useSendChatMessage } from '@/hooks/use-chat';
import { ChatPanel } from './ChatPanel';
import { ChatThreadList } from './ChatThreadList';

// KC color tokens
const KC = {
  surfaceLow: '#191b22',
  surfaceBorder: 'rgba(65,72,90,0.2)',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  surfaceHigh: '#282a30',
} as const;

/**
 * Persistent right-side chat sidebar — Kinetic Console styled.
 *
 * Two views:
 *   1. Thread list  — shown when no thread is selected.
 *   2. Thread detail — shows a ChatPanel for the active thread.
 *
 * Controlled by useChatStore (sidebarOpen, activeSidebarThreadId).
 */
export function ChatSidebar() {
  const {
    sidebarOpen,
    activeSidebarThreadId,
    typingThreadIds,
    openSidebar,
    closeSidebar,
  } = useChatStore();

  const { data: threadsPage } = useChatThreads(undefined, {
    enabled: sidebarOpen,
  });
  const threads = threadsPage?.data ?? [];

  const { data: activeThread } = useChatThread(
    activeSidebarThreadId ?? undefined,
    { enabled: sidebarOpen && !!activeSidebarThreadId },
  );

  const sendMessage = useSendChatMessage();

  const handleSelectThread = useCallback(
    (threadId: string) => { openSidebar(threadId); },
    [openSidebar],
  );

  const handleBack = useCallback(() => { openSidebar(); }, [openSidebar]);

  const handleSend = useCallback(
    (content: string) => {
      if (!activeSidebarThreadId) return;
      sendMessage.mutate({ threadId: activeSidebarThreadId, payload: { content } });
    },
    [activeSidebarThreadId, sendMessage],
  );

  useEffect(() => {
    if (!sidebarOpen) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') closeSidebar();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [sidebarOpen, closeSidebar]);

  const isTypingInActiveThread =
    !!activeSidebarThreadId && typingThreadIds.has(activeSidebarThreadId);

  return (
    <>
      {/* Backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 md:hidden"
          style={{ background: 'rgba(0,0,0,0.4)' }}
          onClick={closeSidebar}
          aria-hidden="true"
        />
      )}

      {/* Sidebar panel */}
      <aside
        className={`fixed right-0 top-0 z-50 flex h-full flex-col transition-transform duration-200 ease-in-out ${
          sidebarOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{
          width: 380,
          background: KC.surfaceLow,
          borderLeft: `1px solid ${KC.surfaceBorder}`,
        }}
        aria-label="Chat sidebar"
      >
        {/* Header */}
        <div
          className="flex shrink-0 items-center justify-between px-4"
          style={{
            height: 48,
            borderBottom: `1px solid ${KC.surfaceBorder}`,
          }}
        >
          <div className="flex items-center gap-2">
            {activeSidebarThreadId && (
              <button
                type="button"
                onClick={handleBack}
                className="flex h-7 w-7 items-center justify-center rounded transition-colors"
                style={{ color: KC.onSurfaceVariant, background: 'transparent', border: 'none', cursor: 'pointer' }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = KC.surfaceHigh; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
                aria-label="Back to thread list"
              >
                <span className="material-symbols-outlined" style={{ fontSize: 16 }}>arrow_back</span>
              </button>
            )}
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined" style={{ fontSize: 16, color: '#e67e22' }}>
                auto_awesome
              </span>
              <span
                className="font-mono uppercase"
                style={{ fontSize: 11, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}
              >
                {activeSidebarThreadId ? 'Thread' : 'Conversations'}
              </span>
            </div>
          </div>

          <button
            type="button"
            onClick={closeSidebar}
            className="flex h-7 w-7 items-center justify-center rounded transition-colors"
            style={{ color: KC.onSurfaceVariant, background: 'transparent', border: 'none', cursor: 'pointer' }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = KC.surfaceHigh; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; }}
            aria-label="Close chat sidebar"
          >
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>close</span>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden">
          {activeSidebarThreadId && activeThread ? (
            <ChatPanel
              thread={activeThread}
              messages={activeThread.messages}
              compact
              isTyping={isTypingInActiveThread}
              typingAgentName="Agent"
              onSendMessage={handleSend}
            />
          ) : (
            <div className="h-full overflow-y-auto">
              <ChatThreadList
                threads={threads}
                onSelectThread={handleSelectThread}
              />
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
