import { type ChatMessage, type ChatGraphRef } from '@/types/chat';
import { formatRelativeTime } from '@/utils/format-time';

// ---------------------------------------------------------------------------
// KC color tokens
// ---------------------------------------------------------------------------

const KC = {
  surface: '#111319',
  surfaceHigh: '#282a30',
  surfaceLowest: '#0c0e14',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  primaryContainer: '#e67e22',
  border: 'rgba(65,72,90,0.2)',
  error: '#ffb4ab',
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ChatMessageBubbleProps {
  message: ChatMessage;
  /** Called when the user clicks a Digital Twin graph reference badge. */
  onGraphRefClick?: (nodeType: string, nodeId: string) => void;
}

// ---------------------------------------------------------------------------
// Simple markdown rendering
// ---------------------------------------------------------------------------

/**
 * Minimal markdown-to-JSX renderer.
 *
 * Handles the subset of markdown that appears in chat messages:
 * - **bold**
 * - *italic* / _italic_
 * - `inline code`
 * - Fenced code blocks (``` ... ```)
 *
 * This intentionally avoids a full markdown library to keep the bundle small.
 */
function renderMarkdown(text: string): React.ReactNode[] {
  const lines = text.split('\n');
  const nodes: React.ReactNode[] = [];
  let codeBlockLines: string[] | null = null;
  let codeKey = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;

    // Fenced code block toggle
    if (line.startsWith('```')) {
      if (codeBlockLines !== null) {
        // Closing fence
        nodes.push(
          <pre
            key={`code-${codeKey++}`}
            style={{
              margin: '4px 0',
              overflowX: 'auto',
              borderRadius: '4px',
              background: KC.surfaceLowest,
              padding: '10px 12px',
              fontSize: '11px',
              fontFamily: 'Roboto Mono, monospace',
              color: KC.onSurface,
              border: `1px solid ${KC.border}`,
            }}
          >
            <code>{codeBlockLines.join('\n')}</code>
          </pre>,
        );
        codeBlockLines = null;
      } else {
        // Opening fence
        codeBlockLines = [];
      }
      continue;
    }

    if (codeBlockLines !== null) {
      codeBlockLines.push(line);
      continue;
    }

    // Inline formatting
    nodes.push(
      <span key={`line-${i}`}>
        {i > 0 && <br />}
        {renderInlineMarkdown(line)}
      </span>,
    );
  }

  // Unclosed code block — render whatever was collected
  if (codeBlockLines !== null && codeBlockLines.length > 0) {
    nodes.push(
      <pre
        key={`code-${codeKey}`}
        style={{
          margin: '4px 0',
          overflowX: 'auto',
          borderRadius: '4px',
          background: KC.surfaceLowest,
          padding: '10px 12px',
          fontSize: '11px',
          fontFamily: 'Roboto Mono, monospace',
          color: KC.onSurface,
          border: `1px solid ${KC.border}`,
        }}
      >
        <code>{codeBlockLines.join('\n')}</code>
      </pre>,
    );
  }

  return nodes;
}

/**
 * Replace inline markdown tokens with styled spans.
 *
 * Handles: **bold**, *italic*, _italic_, `code`
 */
function renderInlineMarkdown(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  // Regex handles: **bold**, *italic*, _italic_, `code`
  const regex = /(\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_|`(.+?)`)/g;
  let lastIndex = 0;
  let key = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    // Text before the match
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }

    if (match[2] !== undefined) {
      // **bold**
      parts.push(<strong key={key++}>{match[2]}</strong>);
    } else if (match[3] !== undefined) {
      // *italic*
      parts.push(<em key={key++}>{match[3]}</em>);
    } else if (match[4] !== undefined) {
      // _italic_
      parts.push(<em key={key++}>{match[4]}</em>);
    } else if (match[5] !== undefined) {
      // `code`
      parts.push(
        <code
          key={key++}
          style={{
            borderRadius: '3px',
            background: KC.surfaceLowest,
            padding: '1px 4px',
            fontSize: '11px',
            fontFamily: 'Roboto Mono, monospace',
            color: KC.onSurface,
          }}
        >
          {match[5]}
        </code>,
      );
    }

    lastIndex = match.index + match[0].length;
  }

  // Trailing text
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : [text];
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Circular avatar showing a user initial or agent code. */
function ActorAvatar({
  label,
  variant,
}: {
  label: string;
  variant: 'user' | 'agent';
}) {
  const bg =
    variant === 'user'
      ? { background: KC.primaryContainer, color: KC.surface }
      : { background: '#2a3a2e', color: '#3dd68c' };

  return (
    <span
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: '28px',
        height: '28px',
        flexShrink: 0,
        borderRadius: '50%',
        fontSize: '11px',
        fontWeight: 600,
        fontFamily: 'Inter, sans-serif',
        ...bg,
      }}
    >
      {label}
    </span>
  );
}

/** Clickable badge for a Digital Twin graph reference. */
function GraphRefBadge({
  graphRef,
  onClick,
}: {
  graphRef: ChatGraphRef;
  onClick?: (nodeType: string, nodeId: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onClick?.(graphRef.nodeType, graphRef.nodeId)}
      style={{
        marginTop: '4px',
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        borderRadius: '12px',
        border: `1px solid rgba(230,126,34,0.3)`,
        background: 'rgba(230,126,34,0.1)',
        padding: '2px 8px',
        fontSize: '11px',
        fontWeight: 500,
        color: KC.primaryContainer,
        cursor: 'pointer',
        fontFamily: 'Inter, sans-serif',
      }}
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 16 16"
        fill="currentColor"
        style={{ width: '10px', height: '10px' }}
      >
        <path d="M8 1a2.5 2.5 0 0 0-1 4.8V7H5a2 2 0 0 0-2 2v.7A2.5 2.5 0 1 0 5 12.3V9h6v3.3a2.5 2.5 0 1 0 2-2.6V9a2 2 0 0 0-2-2H9V5.8A2.5 2.5 0 0 0 8 1Z" />
      </svg>
      <span>{graphRef.label}</span>
      <span style={{ color: KC.onSurfaceVariant }}>({graphRef.nodeType})</span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

/**
 * Chat message bubble with layout that varies by actor kind.
 *
 * - **user**: right-aligned, primary-container background, user initial avatar
 * - **agent**: left-aligned, surface-high background, agent code badge
 * - **system**: center-aligned, surface-high/50 background, italic text
 */
export function ChatMessageBubble({
  message,
  onGraphRefClick,
}: ChatMessageBubbleProps) {
  const { actor, content, createdAt, graphRef, status } = message;

  // --- System messages ---
  if (actor.kind === 'system') {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '4px 16px' }}>
        <div
          style={{
            maxWidth: '420px',
            borderRadius: '6px',
            background: 'rgba(40,42,48,0.5)',
            padding: '6px 14px',
            textAlign: 'center',
            fontSize: '12px',
            fontStyle: 'italic',
            color: KC.onSurfaceVariant,
            border: `1px solid ${KC.border}`,
          }}
        >
          {renderMarkdown(content)}
          <div style={{ marginTop: '3px', fontSize: '10px', color: KC.onSurfaceVariant }}>
            {formatRelativeTime(createdAt)}
          </div>
        </div>
      </div>
    );
  }

  const isUser = actor.kind === 'user';

  // Derive avatar label: first initial for users, agent code for agents
  const avatarLabel = isUser
    ? actor.displayName.charAt(0).toUpperCase()
    : actor.agentCode ?? actor.displayName.slice(0, 2).toUpperCase();

  const bubbleStyle: React.CSSProperties = isUser
    ? {
        borderRadius: '10px 10px 2px 10px',
        background: 'rgba(230,126,34,0.15)',
        border: `1px solid rgba(230,126,34,0.2)`,
        color: KC.onSurface,
        padding: '8px 12px',
        fontSize: '13px',
        lineHeight: '1.5',
      }
    : {
        borderRadius: '10px 10px 10px 2px',
        background: KC.surfaceHigh,
        color: KC.onSurface,
        padding: '8px 12px',
        fontSize: '13px',
        lineHeight: '1.5',
      };

  return (
    <div
      style={{
        display: 'flex',
        gap: '8px',
        padding: '6px 16px',
        flexDirection: isUser ? 'row-reverse' : 'row',
      }}
    >
      {/* Avatar */}
      <ActorAvatar label={avatarLabel} variant={isUser ? 'user' : 'agent'} />

      {/* Bubble */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          maxWidth: '75%',
          alignItems: isUser ? 'flex-end' : 'flex-start',
        }}
      >
        {/* Sender name */}
        <span
          style={{
            marginBottom: '2px',
            fontSize: '10px',
            fontWeight: 500,
            color: KC.onSurfaceVariant,
            fontFamily: 'Inter, sans-serif',
          }}
        >
          {actor.displayName}
        </span>

        <div style={bubbleStyle}>
          {renderMarkdown(content)}

          {/* Graph reference badge */}
          {graphRef && (
            <div style={{ marginTop: '4px', textAlign: isUser ? 'right' : 'left' }}>
              <GraphRefBadge graphRef={graphRef} onClick={onGraphRefClick} />
            </div>
          )}
        </div>

        {/* Footer: timestamp + status */}
        <div
          style={{
            marginTop: '2px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            fontSize: '10px',
            color: KC.onSurfaceVariant,
          }}
        >
          <span>{formatRelativeTime(createdAt)}</span>
          {status === 'sending' && <span>Sending...</span>}
          {status === 'error' && (
            <span style={{ color: KC.error }}>Failed to send</span>
          )}
        </div>
      </div>
    </div>
  );
}
