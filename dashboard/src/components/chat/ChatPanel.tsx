import { useEffect, useRef } from 'react';
import { type ChatThread, type ChatMessage } from '@/types/chat';
import { ChatMessageBubble } from './ChatMessageBubble';
import { TypingIndicator } from './TypingIndicator';
import { ChatComposer } from './ChatComposer';

// ---------------------------------------------------------------------------
// KC color tokens
// ---------------------------------------------------------------------------

const KC = {
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  border: 'rgba(65,72,90,0.2)',
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ChatPanelProps {
  /** The thread being displayed (provides scope / title metadata). */
  thread: ChatThread;
  /** Messages to render inside the panel. */
  messages: ChatMessage[];
  /** Use compact layout with reduced padding / smaller text. */
  compact?: boolean;
  /** Whether an agent is currently typing in this thread. */
  isTyping?: boolean;
  /** Name of the agent that is typing (shown in the indicator). */
  typingAgentName?: string;
  /** Called when the user submits a new message. */
  onSendMessage?: (content: string) => void;
  /** Called when the user clicks a Digital Twin graph-ref badge. */
  onGraphRefClick?: (nodeType: string, nodeId: string) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Primary chat UI composite.
 *
 * Combines a scrollable message list, typing indicator, and composer into
 * a single panel that can be embedded in the sidebar or on a full page.
 */
export function ChatPanel({
  thread,
  messages,
  compact = false,
  isTyping = false,
  typingAgentName = 'Agent',
  onSendMessage,
  onGraphRefClick,
}: ChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom whenever new messages arrive or typing starts
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages.length, isTyping]);

  return (
    <div style={{ display: 'flex', height: '100%', flexDirection: 'column' }}>
      {/* ---- Header ---- */}
      <div
        style={{
          flexShrink: 0,
          borderBottom: `1px solid ${KC.border}`,
          padding: compact ? '8px 12px' : '10px 16px',
        }}
      >
        <h3
          style={{
            margin: 0,
            fontWeight: 500,
            color: KC.onSurface,
            fontSize: compact ? '13px' : '14px',
            fontFamily: 'Inter, sans-serif',
          }}
        >
          {thread.title}
        </h3>
        {thread.scope.label && (
          <span
            style={{
              fontSize: '11px',
              color: KC.onSurfaceVariant,
              fontFamily: 'Inter, sans-serif',
            }}
          >
            {thread.scope.label}
          </span>
        )}
      </div>

      {/* ---- Message list ---- */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: compact ? '8px 0' : '12px 0',
          // Thin scrollbar
          scrollbarWidth: 'thin',
          scrollbarColor: 'rgba(65,72,90,0.4) transparent',
        }}
      >
        {messages.length === 0 && (
          <div
            style={{
              display: 'flex',
              height: '100%',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <p
              style={{
                fontSize: '13px',
                color: KC.onSurfaceVariant,
                fontFamily: 'Inter, sans-serif',
              }}
            >
              No messages yet. Start the conversation.
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <ChatMessageBubble
            key={msg.id}
            message={msg}
            onGraphRefClick={onGraphRefClick}
          />
        ))}

        {isTyping && (
          <div style={{ padding: '0 16px' }}>
            <TypingIndicator agentName={typingAgentName} />
          </div>
        )}
      </div>

      {/* ---- Composer ---- */}
      {onSendMessage && (
        <ChatComposer
          onSend={onSendMessage}
          compact={compact}
          placeholder={`Message #${thread.title}...`}
        />
      )}
    </div>
  );
}
