import { useState, useRef, useCallback, type KeyboardEvent } from 'react';

// ---------------------------------------------------------------------------
// KC color tokens
// ---------------------------------------------------------------------------

const KC = {
  surface: '#111319',
  surfaceHigh: '#282a30',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  primaryContainer: '#e67e22',
  border: 'rgba(65,72,90,0.2)',
  borderStrong: 'rgba(65,72,90,0.3)',
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ChatComposerProps {
  /** Called when the user submits a message. */
  onSend: (content: string) => void;
  /** Placeholder text for the textarea. */
  placeholder?: string;
  /** Whether the composer is disabled (e.g., while sending). */
  disabled?: boolean;
  /** Compact mode reduces padding and font size. */
  compact?: boolean;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Chat message composer with a textarea and send button.
 *
 * - `Enter` sends the message.
 * - `Shift+Enter` inserts a newline.
 * - Trims whitespace before sending; empty messages are not sent.
 */
export function ChatComposer({
  onSend,
  placeholder = 'Type a message...',
  disabled = false,
  compact = false,
}: ChatComposerProps) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    if (trimmed.length === 0 || disabled) return;
    onSend(trimmed);
    setValue('');
    // Reset textarea height after clearing
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [value, disabled, onSend]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  /** Auto-grow the textarea as the user types (up to a max). */
  const handleInput = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const maxHeight = compact ? 80 : 140;
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  }, [compact]);

  const isEmpty = value.trim().length === 0;

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-end',
        gap: '8px',
        borderTop: `1px solid ${KC.border}`,
        background: KC.surface,
        padding: compact ? '6px 10px' : '10px 12px',
      }}
    >
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          handleInput();
        }}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        rows={1}
        style={{
          flex: 1,
          resize: 'none',
          borderRadius: '6px',
          border: `1px solid ${KC.borderStrong}`,
          background: KC.surfaceHigh,
          color: KC.onSurface,
          padding: compact ? '6px 10px' : '7px 10px',
          fontSize: '13px',
          fontFamily: 'Inter, sans-serif',
          outline: 'none',
          cursor: disabled ? 'not-allowed' : 'text',
          opacity: disabled ? 0.5 : 1,
          lineHeight: '1.5',
        }}
        onFocus={(e) => (e.currentTarget.style.borderColor = KC.primaryContainer)}
        onBlur={(e) => (e.currentTarget.style.borderColor = KC.borderStrong)}
      />

      <button
        type="button"
        onClick={handleSend}
        disabled={disabled || isEmpty}
        aria-label="Send message"
        style={{
          display: 'flex',
          flexShrink: 0,
          alignItems: 'center',
          justifyContent: 'center',
          width: compact ? '32px' : '36px',
          height: compact ? '32px' : '36px',
          borderRadius: '6px',
          background: KC.primaryContainer,
          color: KC.surface,
          border: 'none',
          cursor: disabled || isEmpty ? 'not-allowed' : 'pointer',
          opacity: disabled || isEmpty ? 0.4 : 1,
          transition: 'opacity 0.15s',
        }}
      >
        {/* Arrow-up / Send icon (inline SVG) */}
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ width: compact ? '14px' : '16px', height: compact ? '14px' : '16px' }}
        >
          <path d="m5 12 7-7 7 7" />
          <path d="M12 19V5" />
        </svg>
      </button>
    </div>
  );
}
