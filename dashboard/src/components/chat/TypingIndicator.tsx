// ---------------------------------------------------------------------------
// KC color token
// ---------------------------------------------------------------------------

const KC_ON_SURFACE_VARIANT = '#9a9aaa';

interface TypingIndicatorProps {
  agentName: string;
}

/**
 * Animated typing indicator that shows when an agent is composing a message.
 *
 * Renders three dots with staggered bounce animations alongside the agent name.
 */
export function TypingIndicator({ agentName }: TypingIndicatorProps) {
  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '8px',
        padding: '6px 12px',
        fontSize: '12px',
        color: KC_ON_SURFACE_VARIANT,
        fontFamily: 'Inter, sans-serif',
      }}
    >
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '2px' }} aria-hidden="true">
        <span
          style={{
            display: 'inline-block',
            width: '5px',
            height: '5px',
            borderRadius: '50%',
            background: KC_ON_SURFACE_VARIANT,
            animation: 'metaforge-typing-bounce 1.2s ease-in-out infinite',
            animationDelay: '0ms',
          }}
        />
        <span
          style={{
            display: 'inline-block',
            width: '5px',
            height: '5px',
            borderRadius: '50%',
            background: KC_ON_SURFACE_VARIANT,
            animation: 'metaforge-typing-bounce 1.2s ease-in-out infinite',
            animationDelay: '200ms',
          }}
        />
        <span
          style={{
            display: 'inline-block',
            width: '5px',
            height: '5px',
            borderRadius: '50%',
            background: KC_ON_SURFACE_VARIANT,
            animation: 'metaforge-typing-bounce 1.2s ease-in-out infinite',
            animationDelay: '400ms',
          }}
        />
      </span>
      <span>{agentName} is typing...</span>

      {/* Inline keyframes — scoped to this component via a unique name */}
      <style>{`
        @keyframes metaforge-typing-bounce {
          0%, 60%, 100% { transform: translateY(0); }
          30% { transform: translateY(-4px); }
        }
      `}</style>
    </div>
  );
}
