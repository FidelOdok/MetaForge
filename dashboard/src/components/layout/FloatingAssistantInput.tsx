import { useState, useCallback } from 'react';
import { useChatStore } from '@/store/chat-store';
import { useCreateChatThread, useSendChatMessage } from '@/hooks/use-chat';

/**
 * Persistent floating chat input pill rendered at the bottom-centre of every
 * main page (via AppLayout).  Matches the Kinetic Console spec:
 *   - 520 px wide, 44 px tall, fully rounded
 *   - Glass-morphism background (rgba 40,42,48 at 0.92 opacity + blur 16 px)
 *   - auto_awesome icon + "Ask the design assistant…" placeholder
 *   - Orange (#e67e22) send button (arrow_upward)
 *
 * Interaction:
 *   - Click anywhere in the pill → opens the chat sidebar (thread list)
 *   - Type + Enter / click send → creates a global assistant thread,
 *     sends the first message, then opens the sidebar on that thread
 */
export function FloatingAssistantInput() {
  const [value, setValue] = useState('');
  const { openSidebar } = useChatStore();
  const createThread = useCreateChatThread();
  const sendMessage = useSendChatMessage();

  const handleSubmit = useCallback(async () => {
    const text = value.trim();
    if (!text) {
      openSidebar();
      return;
    }
    try {
      const thread = await createThread.mutateAsync({
        channelId: 'assistant',
        title: text.length > 60 ? `${text.slice(0, 57)}…` : text,
        scope: { kind: 'assistant', entityId: 'global', label: 'Design Assistant' },
      });
      await sendMessage.mutateAsync({
        threadId: thread.id,
        payload: { content: text },
      });
      openSidebar(thread.id);
      setValue('');
    } catch {
      // On error (e.g. backend offline) still open the sidebar
      openSidebar();
    }
  }, [value, openSidebar, createThread, sendMessage]);

  const isPending = createThread.isPending || sendMessage.isPending;

  return (
    // Fixed bottom-centre, offset left by the 48 px nav rail
    <div
      style={{
        position: 'fixed',
        bottom: 40,
        left: 48,
        right: 0,
        display: 'flex',
        justifyContent: 'center',
        zIndex: 50,
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          width: 520,
          height: 44,
          background: 'rgba(40,42,48,0.92)',
          backdropFilter: 'blur(16px)',
          WebkitBackdropFilter: 'blur(16px)',
          borderRadius: 9999,
          padding: '0 16px',
          pointerEvents: 'auto',
        }}
      >
        {/* Sparkle icon */}
        <span
          className="material-symbols-outlined"
          style={{ fontSize: 16, color: '#9a9aaa', flexShrink: 0, userSelect: 'none' }}
        >
          auto_awesome
        </span>

        {/* Text input */}
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void handleSubmit();
            }
          }}
          placeholder="Ask the design assistant..."
          disabled={isPending}
          style={{
            flex: 1,
            background: 'transparent',
            border: 'none',
            outline: 'none',
            fontSize: 13,
            color: '#e2e2eb',
            fontFamily: 'Inter, sans-serif',
          }}
        />

        {/* Send button */}
        <button
          type="button"
          onClick={() => void handleSubmit()}
          disabled={isPending}
          style={{
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 28,
            height: 28,
            borderRadius: '50%',
            background: isPending ? 'rgba(230,126,34,0.5)' : '#e67e22',
            border: 'none',
            cursor: isPending ? 'default' : 'pointer',
            transition: 'background 0.15s',
          }}
          aria-label="Send message"
        >
          <span
            className="material-symbols-outlined"
            style={{ fontSize: 14, color: '#fff', userSelect: 'none' }}
          >
            arrow_upward
          </span>
        </button>
      </div>
    </div>
  );
}
